---
name: session-to-eval
description: 把当前 session 里执行过的任务蒸馏成 ai-eval 可执行的 eval case。当用户说"把刚才的任务抽成 eval case""把刚才你执行的任务抽成 case""抽成 eval case""turn this into an eval case"时使用。读取 Claude Code 或 Codex 的 session log，语义识别 1..N 个任务，生成对齐 ai-eval 契约的草稿 case（含 case.yaml / prompts / check 或 rubric / input / README），落盘前做静态结构校验。
---

# session-to-eval

把"刚刚在这个 session 里发生过的真实任务"反向蒸馏成 ai-eval 能跑的 eval case。

**核心诚实边界**：自动蒸馏天然弱在两处——transcript 里没有外部验证过的 ground-truth，
大型工作集（如既有仓库）无法从 transcript 完整复原。所以产物默认是**草稿级 case**：
能精确复原的精确复原，不能的产出带 TODO 的桩 + 人工确认点，区分度由人在落地前签字。

依赖：Python 3.11+；本机有 Claude Code 或 Codex 的 session log；已配置 `ai_eval_path`（见 `config.example.toml`）。

---

## 何时触发

用户表达"把刚才的任务沉淀成评测用例"的意图，典型措辞：
- "把刚才你执行的任务抽成一个 eval case"
- "把刚才那个任务抽成 case" / "抽成 eval case"
- "turn this into an eval case" / "make a benchmark case from what we just did"

不触发：用户在讨论 ai-eval 的代码本身、或要手写一个全新 case 而非从 session 蒸馏。

---

## 工作流（概览）

完整编排见下方各章节，骨架如下（详细步骤由 SKILL 的"编排"章节给出）：

1. `scripts/session_extract.py` 定位并解析当前 session → 结构化 digest（剥除触发轮）。
2. 语义分段识别 1..N 个任务；确定性首过分类器 + 复核给出每个任务的 class 与判分策略。
3. 多任务时展示清单，用户挑选要落地的任务。
4. 逐任务：资产重建（session 优先 / 覆盖门桩 / 合成兜底）+ ground-truth 分诊 + 脱敏。
5. 按"类别→判分策略矩阵"生成 case 目录写入 `<ai_eval_path>/cases/`。
6. `scripts/validate_case.py` 做静态结构校验 + 廉价断言。
7. 区分度人工签字 → 报告落点、待补 TODO、如何跑。

> 注：本章节是骨架占位，详细的"任务分段与归类""产物生成""编排"步骤分别在下文对应章节展开。

---

## Case 契约（生成产物必须严格对齐）

一个 case = `<ai_eval_path>/cases/<YYYY-MM-DD-NNN-name>/` 目录，由 ai-eval 的 `bench.case.load_case` 加载。
**生成器必须按此契约产出，否则 case 跑不起来。**

### 目录命名

`YYYY-MM-DD-NNN-<descriptive-name>`：日期 + 当日序号（3 位零填充，从 001 起，扫当日已有目录取下一个）
+ kebab-case 短名（3-5 词）。例：`2026-06-05-001-sprint-retro-plan`。

### `case.yaml` 字段

```yaml
name: <name>                 # 通常同目录名
class: reasoning             # reasoning | coding | tool-using | writing（四类之一，必填且合法）
task:
  type: prompt               # prompt | skill | slash | custom
  prompt_file: prompts/task.md   # prompt/custom 必填；指向任务 prompt 文件
  # skill 类用 skill: <名> + args；slash 类用 command: <命令> + args
check:
  type: script               # script | none
  script: check.sh           # script 类必填；退出码 0 = 通过；从 cwd（产物目录）运行
judge:
  enabled: true              # 是否启用 LLM 裁判
  rubric_file: prompts/rubric.md   # enabled 时必填；rubric 文本含 JSON 输出约定
  dimensions: [correctness, code_quality]   # 评分维度名
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
