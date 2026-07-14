---
name: session-to-eval
description: 把 session 里执行过的真实任务蒸馏成 ai-eval 可执行的 eval case。两种入口——①倒出模式：用户说"把刚才的任务抽成 eval case""抽成 case""turn this into an eval case"，蒸馏当前 session；②检索模式：用户给一句意图描述（如"把判断工单是否线上问题并分级的推理抽成 case"），自动在当前 session 与本项目历史 session（Claude Code + Codex 双端）中检索命中任务，缺输入/真值时主动挖项目 CLAUDE.md/README/代码补全，本项目信息不足时主动询问是否跨项目。语义识别 1..N 个任务，生成对齐 ai-eval 契约的草稿 case（case.yaml / prompts / check 或 rubric / input / README），落盘前做静态结构校验。
---

# session-to-eval

把"刚刚在这个 session 里发生过的真实任务"反向蒸馏成 ai-eval 能跑的 eval case。

**核心诚实边界**：自动蒸馏天然弱在两处——transcript 里没有外部验证过的 ground-truth，
大型工作集（如既有仓库）无法从 transcript 完整复原。所以产物默认是**草稿级 case**：
能精确复原的精确复原，不能的产出带 TODO 的桩 + 人工确认点，区分度由人在落地前签字。

依赖：Python 3.11+；本机有 Claude Code 或 Codex 的 session log；已配置 `ai_eval_path`（见 `config.example.toml`）。

---

## 何时触发

**倒出模式**——用户要把"刚发生的任务"沉淀成评测用例：
- "把刚才你执行的任务抽成一个 eval case"
- "把刚才那个任务抽成 case" / "抽成 eval case"
- "turn this into an eval case" / "make a benchmark case from what we just did"

**检索模式**——用户给一句**意图描述**指明想抽什么（目标不一定在当前 session）：
- "把判断工单是否线上问题并分级的推理过程抽成 case"
- "/session-to-eval <对某段任务的描述>"
- 任何"我想把 <某能力 / 某段推理 / 某次任务> 做成 eval case"且目标可能在历史里。

判定：触发参数 / 描述为空 → 倒出模式；带实质描述 → 检索模式（见"检索模式（描述驱动）"章节）。

不触发：用户在讨论 ai-eval 的代码本身、或要手写一个全新 case 而非从 session 蒸馏。

---

## 工作流（概览）

完整编排见下方各章节，骨架如下（详细步骤由 SKILL 的"编排"章节给出）：

1. `scripts/session_extract.py` 定位并解析当前 session → 结构化 digest（剥除触发轮）。
2. 语义分段识别 1..N 个任务；确定性首过分类器 + 复核给出每个任务的 class 与判分策略。
3. 多任务时展示清单，用户挑选要落地的任务。
4. 逐任务：资产重建（session 优先 / 覆盖门桩 / 合成兜底）+ ground-truth 分诊 + 脱敏。
5. 按"类别→判分策略矩阵"生成 case 目录写入**落点根**（默认私有 `private_cases_path`，除非用户要公开；见"产物生成 / 落点"）。
6. `scripts/validate_case.py` 做静态结构校验 + 廉价断言。
7. 区分度人工签字 → 报告落点、待补 TODO、如何跑。

详细步骤见下文"任务分段与归类""产物生成""编排（完整工作流）"三节。

---

## Case 契约（生成产物必须严格对齐）

一个 case = `<ai_eval_path>/cases/<YYYY-MM-DD-NNN-name>/` 目录，由 ai-eval 的 `bench.case.load_case` 加载。
**生成器必须按此契约产出，否则 case 跑不起来。**

### 目录命名

`YYYY-MM-DD-NNN-<descriptive-name>`：日期 + 当日序号（3 位零填充，从 001 起，扫当日已有目录取下一个）
+ kebab-case 短名（3-5 词）。例：`2026-06-05-001-sprint-retro-plan`。

### `case.yaml` 字段

```yaml
schema_version: 2
class: reasoning             # 可省略，默认 coding
task: prompts/task.md        # prompt/custom 的最简写法
check: check.sh              # 可选；退出码 0 = 通过
judge: prompts/rubric.md     # 可选；rubric 含 JSON 输出约定
requires_engine: claude      # 可选；仅某引擎可跑时声明，矩阵跳过不兼容格
repeat: 3                    # 可选；覆盖全局 repeat
expected:                    # 可选；YAML **dict 字段**（不是目录！）。ground-truth，check.sh / judge 读
  commit: 374478a            # 例：tool-using 的真值
  max_score: 25              # 例：reasoning 的 rubric 满分
  synthesized: false         # 本 skill 约定：input 是否含 LLM 合成资产
```

**`expected` 是 case.yaml 里的 YAML dict 字段，不是文件系统目录。** `bench.case.Case` 只有 `input_dir` 与
`verify_dir` 两个目录属性，没有 `expected_dir`。

### 目录语义

| 目录 | 进选手 workdir？ | 用途 |
|---|---|---|
| `input/` | 是（运行时拷入隔离 workdir） | 任务输入资产（脚手架、工作集）。选手能读能改 |
| `verify/` | **否** | 只读校验基准（如测试）。check 前由编排器还原到产物目录，选手改不了——防篡改 |
| `prompts/` | 否（task.md 作为 prompt 投喂；rubric 给 judge） | task.md / rubric.md |
| `output/` | —（生成产物） | 各 runner 的运行产物，已 gitignore |

### 产物落点约定（生成 check.sh / rubric 时必知）

- 编排器把任务 prompt 投给 runner，runner 的最终回答文本写到产物目录的 **`OUTPUT.txt`**（不脱敏，避免误伤答案 hash）。
- check.sh 从 **当前工作目录（cwd = 产物目录）** 运行；纯分析任务从 `OUTPUT.txt` 读答案
  （如 tool-using 抽 `ANSWER: <x>` 行比对 `expected` 字段）；编码任务直接对产物文件跑测试。
- judge 按 rubric 给分，rubric 必须要求返回 JSON：
  `{"score": <n>, "max": <n>, "dimensions": {...}, "reasoning": "<简述>"}`。

### 三类范本（生成时一一对应）

- `cases/2026-06-02-001-fizzbuzz` — coding：`input/` 脚手架 + `verify/` 只读测试 + `check.sh` 跑测试 + rubric。
- `cases/2026-06-03-003-planning` — reasoning：`check: none` + rubric N 维 + `expected` 字段（max_score 等）。
- `cases/2026-06-03-001-git-bisect-bug-hunt` — tool-using：`input/repo`（由 `setup.sh` clone）+ check.sh 抽 `ANSWER:`
  比对 `expected.commit`。其真值与工作集均来自**外部**，不在 transcript 里——印证大工作集/外部真值需桩 + 人工补。

---

## 分类规则（任务 → class → 判分策略）

按"产物形态 + 验证可得性"分流，确定性首过分类器 `classify_task()` 先判，歧义样本由 LLM 复核改判。

| class | 信号 | 判分策略 | ground-truth 来源 |
|---|---|---|---|
| coding | 有可跑测试的代码产物（改了 `.py`/`.js`… 且存在测试或测试可写） | check.sh 跑测试 + rubric | oracle 测试多为外部 → 默认桩 + TODO |
| tool-using | 工具调用密集、靠 CLI/工具调查得结论、无文件产物、答案是单值（hash/数字/名字） | check.sh 抽 `ANSWER:` 比 `expected` + rubric | 真值多为外部 → 默认桩 + TODO |
| reasoning | 纯思辨 / 算账 / 结构化拆解，长文输出无确定唯一答案 | rubric N 维（check: none） | rubric 阈值人工设 |
| writing | 长文 / 方案 / 文案 | rubric N 维（check: none） | rubric 阈值人工设 |

判定优先级（首过分类器实现）：
1. 工作集是被调查对象、答案为单值、工具调用为主 → `tool-using`。
2. 有代码文件被创建/修改且能配可执行测试 → `coding`。
3. 输出是长文方案/文案 → `writing`。
4. 其余思辨/计算/拆解 → `reasoning`。

边界（coding vs tool-using）：既改了代码又靠工具调查的，首过给候选 + 低置信，交 LLM 复核定夺。

> 矩阵是默认选路：reasoning 任务若恰有确定性可校验答案，可升级为 check+judge 并用。

---

## 任务分段与归类（编排步骤）

`session_extract.py` 产出 digest 后，按下面步骤把它切成 1..N 个任务并归类：

### 1. 语义分段

以"**用户给出一个独立目标 + 一段 AI 完成过程**"为一个任务边界。规则：
- 一个新的用户目标轮（不是追问/纠错）开启一个新任务。
- 追问、纠错、继续同一目标的轮次并入同一任务。
- **交叉任务**：一段过程同时服务多个目标时，拆成多个任务并标注依赖关系。
- digest 已剥除触发轮，分段只在触发**之前**的内容上做。

### 2. 确定性首过分类 + LLM 复核

对每个任务切片：
1. 用 `derive_signals(turns, file_events, writing_intent=<你的语义判断>)` 派生信号——
   其中 `writing_intent` 是"长文/方案/文案"的语义判断，由你（LLM）填，确定性信号自动算。
2. 用 `classify_task(signals)` 拿首过结果 `Classification{cls, confidence, reason}`。
3. **`confidence == "low"` 时你必须复核改判**：
   - `coding` 低置信（改了代码 + 重度调查 + 单值答案并存）→ 判断这个任务"真正要评测什么"：
     是代码正确性（coding）还是调查能力（tool-using）？
   - `reasoning` 低置信（长文但无 writing 意图）→ 区分这是结构化推理/方案（reasoning）还是
     纯长文写作/文案（writing）。

### 3. 展示任务清单供挑选（R7）

把识别到的任务列成清单，每条含：**编号 + 一句话目标 + 拟定 class + 拟定判分策略 + 是否低置信复核过**。
多任务时让用户挑选要落地的（默认全选），每个挑中的任务各生成一个 case。

---

## 产物生成（按契约写 case 目录）

对每个挑中的任务，按其 class 走对应生成路径。先定**落点根目录**（公开 vs 私有）：

### 🔒 落点：默认私有，除非用户显式要求公开

蒸馏自真实 session 的 case **天然可能含敏感数据**（公司工单、内部代码、业务规则）。所以：

- **`config.toml` 配了 `private_cases_path` → 默认写私有路径**（公开仓之外，不进 git）。
- 用户**显式**说"放公开 / 放 cases / public / 开源 / 这个能开源"时，才写 `ai_eval_path/cases/`。
- `private_cases_path` 没配且用户没说公开 → 提示用户"未配私有路径，这个 case 要放公开仓 `cases/` 吗？"，确认后再落（避免把敏感 case 默认塞进公开仓）。

```python
import tomllib
cfg = tomllib.load(open(f"{skill_dir}/config.toml", "rb"))   # load_config() 亦可
# 默认私有；用户显式要公开则用 ai_eval_path/cases
cases_root = cfg.get("private_cases_path") or f"{cfg['ai_eval_path']}/cases"  # 见上：未配私有要先问

from session_extract import next_sequence_number, case_dirname
seq = next_sequence_number(cases_root, date_str)              # 扫当日已有目录取下一个
name = case_dirname(date_str, seq, "<kebab-短名>")             # 2026-06-09-001-foo
case_dir = f"{cases_root}/{name}"
```

### 通用规则（所有 class）

- **task.md 模型无关**：剥掉指向具体模型/启动器（claude/codex/opus 等）、本机绝对路径的措辞。
- **task.md 自包含（正向校验，不只看 token 缺失）**：task 引用的每个资产都必须在 `input/` 内；
  不得依赖宿主专属动词/工具或本机工作区布局。做不到 → 见"可移植性分诊"。
- **🚨 无答案泄漏（最高优先级，所有 class 必过）**：`input/` 与 `task.md` 里**只放原始信号**
  （现象、对话记录、配置、原始字段），**禁止放任何派生结论**——见下方"泄漏闸"。
  真值只活在 `expected`（对选手不可见）。选手要做的判断，绝不能已经写在它读得到的地方。
- **🧱 框架/case 泾渭分明（task.md 只装"最原始的任务"）**：`task.md` 是用户原本面对的那个任务，
  **不得**把评测框架的验证机制、判分口径、产物落点契约塞进给模型的 prompt。具体禁项：
  - ❌ **为方便机器判分而加的输出格式契约**——如"把结果/路径写到 OUTPUT.txt 第一行，格式 `KEY: <值>`"。
    框架已自动把模型最终回复捕获为 `OUTPUT.txt`，无需指示模型；判分要定位产物，由 **check.sh / judge 侧**
    自己解决（确定性路径推导 / 读 cwd / rubric 让 judge 读），**不靠模型回显**。
    （例外：该输出格式本就是原始任务的一部分——如任务本身要求"输出 JSON"——才保留。）
  - ❌ **评分维度预告 / "评测器会怎么判你"**——把 rubric 维度、及格线、check 项写进 task.md = 教模型应试，
    污染原始任务、抬高所有选手的下限、压扁区分度。判分标准只活在 `prompts/rubric.md` 与 `check.sh`。
  - ❌ **"评测器会自动跑 X"之类元话术**——模型不该知道自己在被评测。
  判据：把 task.md 给一个不知道 ai-eval 存在的人看，他读到的应该正好是"用户当初要做的事"，多一句框架味的引导都算污染。
  若框架确实需要定位/隔离产物而模型没回显，那是**框架/case 配置侧**的事（确定性路径、env 透传、产物快照），
  不是往 prompt 里加指令。做不到 per-runner 确定性 check 时，宁可让 judge 按 runner 隔离判完成度，也不污染任务。
- **资产**：调 `reconstruct(...)` 拿 `ReconstructionResult`；`setup_stub` 为真则写 `setup.sh` 桩
  + 在 README 标"工作集需外部获取"；`needs_review` 资产先经 U7 人工确认再落盘。
  合成资产用 `synthesized_asset(...)`，并在 `case.yaml` 置 `expected.synthesized: true` + README 标注。
- **README.md**（单 case，允许的 md）：任务说明 + 判分方式 + 待补 TODO（ground-truth / setup.sh / 合成资产）
  + "input/ 为 session 派生，未审计敏感数据，分享前请人工 review"。

### coding

```yaml
# case.yaml
schema_version: 2
class: coding
task: prompts/task.md
check: check.sh
judge:
  rubric: prompts/rubric.md
  dimensions: [correctness, code_quality]
expected: {synthesized: <bool>}
```
- `input/` 放起始脚手架（reconstruct 的 read 前态）。
- `verify/` 放只读测试——**通常不在 session 里**（oracle 多为外部）→ 写测试桩 + README "ground-truth TODO：补全 verify/ 测试"。
- `check.sh` 跑 `verify/` 的测试（退 0=pass）；从 cwd 运行。

### tool-using

```yaml
schema_version: 2
class: tool-using
task: prompts/task.md
check: check.sh
judge:
  rubric: prompts/rubric.md
  dimensions: [accuracy, investigation_process]
expected: {answer: "<TODO 外部真值>", synthesized: <bool>}
```
- task.md 约定模型把结论写成 `ANSWER: <值>` 一行。这**不违反**"框架/case 泾渭分明"——
  此处 `<值>` 就是任务本身要交付的答案,只是规范了它的格式(命中上文的例外);
  区别于"把产物路径回显给框架定位"那种纯插管子的契约,后者禁止。
- `check.sh` 从 `OUTPUT.txt` 抽 `ANSWER:` 比对 `expected` 字段。
- 工作集多为外部大仓库 → `setup.sh` 桩；真值外部 → README "ground-truth TODO：填 expected 真值"。

### reasoning / writing

```yaml
schema_version: 2
class: reasoning   # 或 writing
task: prompts/task.md
judge:
  rubric: prompts/rubric.md
  dimensions: [<维度...>]
expected: {max_score: <N>, passing_threshold: <M>}
```
- 无确定性 check；rubric N 维，必含 JSON 输出约定。
- `expected.max_score`/`passing_threshold` 是人工设的评分锚点（reasoning/writing 的"真值"是 rubric 阈值）。
- 模型回答落 `OUTPUT.txt`，judge 据 rubric 评分。

### 可移植性分诊（宿主耦合任务）

若任务依赖某 skill/slash 调用、宿主专属工具、或本机工作区布局，无法做到自包含：
- 能解耦成通用 prompt → 解耦。
- 不能 → 在 `case.yaml` 置 `requires_engine: <claude|codex>`，README 标注"可移植性受限"。
- 既不能解耦又无法置 requires_engine 跑通 → 明确告知用户该任务不适合自动蒸馏，跳过。

---

## 检索模式（描述驱动）

用户给了意图描述、目标任务**不一定在当前 session** 时走这里。脚本 `scripts/session_index.py`
提供确定性检索脊梁（双端枚举 + 名片 + 关键词预筛 + 跨项目候选）；语义精排与缺口挖掘由你（LLM）做。
**实时把当前阶段打给用户**（"[检索] 扫本项目历史…""[挖掘] 轻扫 CLAUDE.md…"），让过程可见。

### R1. 先看当前 session

先按"编排 / 1. 提取 session"取当前 session digest，用描述里的关键词判断是否已命中。
命中 → 直接进重建，不必翻历史。

### R2. 枚举本项目历史 + 预筛（默认仅当前项目）

```bash
python3 scripts/session_index.py --cwd "$PWD" --query "<用户的意图描述>" --top-k 8
```
双端枚举本项目历史 session（Claude 编码目录含真 cwd 兜底；Codex 读首行 `session_meta.cwd` 匹配，
默认近 90 天 + 条数上限），关键词预筛返回 Top-K 名片（host / path / 首个用户目标 / 文件 / 工具）。

### R3. 语义精排锁定

读 Top-K 名片，按用户描述**语义**判定哪个 session、其中哪段切片真正命中
（名片是近似信号，别只信关键词分）。选定后：

```bash
python3 scripts/session_extract.py --session <命中的 path> --host <claude|codex>
```
取完整 digest，再按"任务分段与归类"切出目标任务切片。

### R4. 命中回显确认（铁律）

重建前**必须**回显并等用户确认：
> "我认为你指的是 `<path>`（<日期>）里那段：<一句话目标>，对吗？"

检索可能错——未确认不许重建。

### R5. 本项目信息不足 → 跨项目主动询问（D1）

若当前项目（当前 session + 历史）找不到、或信息不足以拼出 case，**不要**让用户自己去想跨项目：

```bash
python3 scripts/session_index.py --cwd "$PWD" --query "<描述>" --cross
```
扫所有项目名片，返回**可能含该信息的他项目** `(cwd, score)`。有命中 → 用 AskUserQuestion 主动问：
> "本项目里信息不足。我发现 `<他项目>` 可能有相关内容，要我跨项目去搜集吗？"

用户同意才对那个 cwd 重跑 R2–R4。无跨项目命中 → 进 R6 挖掘，或如实告知信息不足。

### R6. 缺口挖掘（D3，输入 / 真值不全时）

命中任务的 digest 缺"输入"或"真值"时别直接放弃。**仅涉及当前项目的挖掘不设确认门、直接挖**，只把阶段打给用户：

1. **轻挖**：读本项目 `CLAUDE.md` / `README.md` + 顶层结构，找"输入从哪来、真值在哪记"
   （例：工单原文在哪张表；`is_online_issue` / `severity` 对应哪些标注字段）。
2. **挖不到 → 深挖**：grep 进代码找表名 / 字段 / prompt 定义（本项目内直接做，log 显示"[挖掘] 深挖…"）。
3. 挖到 → 产**取数配方**：
   - `setup.sh`：把取数落成可执行 / 可 dry-run 的脚本（SQL 模板等）。**碰真实库的动作需人工确认后才跑**（守危险操作红线）。
   - `expected`：历史里有样本真值就填；没有就留 **TODO + 精确配方**（不是空 TODO）。
   - 真值本就是确定性标注（如 DB 的 `is_online`/`severity` 列）→ 这类任务升级为 **check + rubric 并用**，不止 rubric。
4. **挖不动 → AskUserQuestion 兜底**，且问得具体（带已挖到的表名 / 列名）：
   > "我在代码里看到工单来自 `t_xxx`、标注列是 `is_online_issue`/`severity`；要我采样 N 行做 input，还是你有现成 fixture？"

### R6.5 泄漏闸（落盘前必过，所有 class）

蒸馏真实任务最大的暗坑：**源数据里常带"派生结论字段"**（生产管线/人工已经写好的分析、根因、定级），
一旦把它搬进 `input/` 或 `task.md`，任务就退化成"把结论换个格式"——**再弱的模型都过，区分度归零**。

落盘前对**每个 `input/` 资产 + `task.md`** 扫一遍，按两层处理：

1. **结构硬拦（确定性）**：凡字段名/小节命中**结论类 denylist** —— `problem_analysis` / `analysis` /
   `root_cause` / `根因` / `结论` / `conclusion` / `定级` / `severity` / `label` / `judgment` / `verdict` /
   `is_*`（与判定同名的布尔）等 —— **一律不进 `input`/`task`**，移入 `expected`（oracle）。
2. **语义软查（advisory）**：把 `expected` 的判定值与理由，与 `input`/`task` 文本比对；若某句**语义等同于
   模型该自己推出的结论**（哪怕换了措辞），标红并向用户确认"这句疑似含答案，剥离吗？"。
3. **可用工具**：`python3 scripts/leak_check.py <case_dir>` 做确定性自查（expected 值是否字面/近义出现在
   input/task）。它有 `expected` 故能精确判污染——这是**唯一**能测"答案∈输入"的地方（runner 看不到 expected）。

只保留**原始信号**：现象描述、对话/日志原文、配置、产品路径。判断本身永远只在 `expected`。

### R7. 汇入通用后半程

锁定 + 确认 + 补全后，复用下面"产物生成""校验门""区分度签字""报告"——但**先过 R6.5 泄漏闸**，
且"区分度签字"按下方纠偏后的判据执行（**单次通过证明不了区分度**）。

---

## 编排（完整工作流）

触发后按序执行。脚本在 `scripts/` 下，用 `python3` 调用。

> **入口分派**：触发参数 / 描述为空 → 倒出模式，按本节 0→8 顺序走。
> 带实质意图描述 → 检索模式，先走"检索模式（描述驱动）"R1–R6 锁定并补全目标任务，
> 再从本节"5. 生成 case"接入后半程（2/3 的分段挑选已由检索锁定，不再全量倒出）。

### 0. 读配置

`load_config()` 拿 `ai_eval_path`（必填）与 `private_cases_path`（可选，私有 case 默认落点）。
缺 `config.toml` → 提示 `cp config.example.toml config.toml` 并填 `ai_eval_path`，然后**安全停止**。
落点优先级见"产物生成 / 落点"：**默认私有，用户显式要公开才进 `cases/`**。

### 1. 提取 session

```bash
python3 scripts/session_extract.py        # 自动定位当前 session
# 或显式： python3 scripts/session_extract.py --session <path> --host claude|codex
```
**回显所读 session 路径**供用户确认（启发式选了最新 mtime，可能并发误选）。
digest 已剥除触发轮（"抽成 case"那句及其后）。

### 2. 分段 + 归类

见"任务分段与归类"。对每个任务 `derive_signals` + `classify_task`，低置信复核。

### 3. 展示任务清单 → 用户挑选（R7）

列出编号 + 目标 + class + 判分策略 + 是否复核过；多任务让用户挑（默认全选）。

### 4. 逐任务：资产重建 + ground-truth 分诊

- 调 `reconstruct(turns, file_events, content_store, project_cwd=...)`。
- `setup_stub` → 写 `setup.sh` 桩 + README 标"工作集需外部获取"。
- 资产 `needs_review` 为真 → **先向用户人工确认脱敏结果再落盘**（regex 脱敏不可能穷尽）。
- 缺前态的小输入 → 你（LLM）合成等价内容，用 `synthesized_asset(rel, content)` 落盘，
  并在 `case.yaml` 置 `expected.synthesized: true`。
- ground-truth 默认需外部 → 按"产物生成"留 expected/verify 桩 + README TODO。

### 5. 生成 case

见"产物生成 / 落点"：先按"默认私有，除非用户要公开"定 `cases_root`，再用
`next_sequence_number(cases_root, ...)` + `case_dirname` 定目录，按 class 模板写文件到 `<cases_root>/<name>/`。

### 6. 校验门

```bash
python3 scripts/validate_case.py <case_dir>
```
返回 `valid`（能加载 + 无结构错误）与 `complete`（无草稿 TODO）。
- `valid=False` → 按 errors **回修产物后重校验**（循环直到 valid）。
- `valid=True, complete=False` → 保留 todos，进下一步（草稿 case，待人工补真值）。

### 7. 区分度人工签字（产品门）

先过 **R6.5 泄漏闸**（`python3 scripts/leak_check.py <case_dir>`），再谈区分度。

**🚫 反向判据警告（务必牢记）**：**"模型答对了" ≠ "case 有区分度"。** 这两者在以下情况是**相反**的：

- **输入含派生结论时**，模型答对是**污染的铁证**，不是区分度——绝不能用"实测一把全对"宣布 case 成立。
- **答案恰是多数类/默认值时**（如本域大量工单都判 `false`），一个"永远输出默认类"的退化基线也能过——
  这是**平凡性**，不是区分度。

**🎲 概率性任务 → 多输入，不要单输入（最先判这条）**：
若被蒸馏的任务本质是"对**某一类实例**做判断 / 分类 / 预测"（答案随实例而变、单个实例可能被运气
或某个固定猜测命中），则 case 应捕获**多个代表性输入实例**（覆盖不同结果 / 类别），而非单一实例——
单实例分不清"真会做"和"蒙对一次"。判据：一个无推理的固定策略能不能在你的输入上靠运气得分？
能 → 继续加输入实例，直到固定策略不可能靠猜赢。
反之，答案**唯一确定、靠猜不可能命中**的任务（某个确切 commit hash、某处确切 bug 定位），单输入即可。
此时 `input/` 放多份实例、`expected` 记每份真值、check/judge 按多份聚合。

reasoning / tool-using 类的区分度，靠下面**正向证据**确认，缺一不可签可信：
1. **去泄漏**：`leak_check.py` 干净（input/task 里没有答案）。
2. **赢地板**：挂一个**故意很笨的小模型**当基线，跑同一组输入；真模型要在**多个实例上明显跑赢它**
   （跑赢 base rate）才算有本事。⚠️ 不要用"hardcode 固定答案"的假基线——那等于自己挑真值：挑对了它过、
   挑错了它挂，结论全凭你拍，毫无意义。用真的弱模型，让它自己去蒙。
3. **见分化**：≥3 个不同档位模型跑出**对错分布或分数梯度**；一条平的 100% / 0% 都不算区分器。

把 rubric / expected / 上面三项证据摆给用户：
- 三项齐 + 用户确认 → 标记**可信 case**。
- 任一缺失 / 真值待补 → 标记**草稿**，不计入可信语料（README 注明缺哪项）。

### 8. 报告

逐 case 报告：
- 落点路径、class、判分方式（check / judge / 二者）。
- `valid` / `complete` 状态。
- **待补 TODO**：ground-truth / setup.sh / 合成资产 / 未审计提示。
- **如何跑**：`./run.sh -c <case名> -r <runners> --repeat 2`。

> 诚实原则：能精确复刻的精确复刻，不能的产出桩 + TODO + 人工确认点。
> `valid` 只意味结构合法能加载，**不**意味能跑出有意义的分——那要靠人工签字 + 后续 dry-run。
