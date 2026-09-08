# ai-eval — 可换模型的端到端 agent benchmark

> 回答一个本质问题：**面对我自己的真实任务，该用哪个模型？**

## 为什么需要 ai-eval

每次新模型发布，铺天盖地的跑分都在回答同一个问题：「这个模型有多强？」
但你真正想问的是另一个问题：**「我手上有一个具体任务，该用哪个模型？」**
这两个问题之间隔着一道鸿沟。看清这道鸿沟，先把任务分成两类：

- **探索性任务追求上限。** 目标是提高人的上限：让 AI 助力你做以前做不到的事——
  后端写前端、前端写后端，加速任务达成、扩展能力范围。这类任务的选型很简单：
  **智能越强越好**，因为探索追求的是把事情做对，上限就是一切。
- **重复性任务追求性价比。** SOP 已经跑通、历史上验证过能做对的固定场景——
  工单分级、代码巡检、报表生成、公司内部各种用 AI 自动化的流程。这时智能不再是瓶颈，
  **成本和速度才是**。用旗舰模型去跑一个中端模型就能干好的活，是「智能浪费」；
  而这类任务的量，远大于探索性任务。

ai-eval 为后一类任务而生，回答的就是：**如何用「刚刚好」的智能完成重复性任务？**

### 通用 benchmark 是底座，不是答案

公开榜单是一份通用试卷，是有用的底座：SWE-bench 上一个模型 80 分、另一个 50 分，
强弱一眼可辨。但 80 分对 75 分呢？如果 80 分的贵、75 分的便宜，75 分的够不够干好
**你的**任务？通用试卷答不了——每个人、每个业务、每个组织内部的任务千差万别，
长尾、特异，注定不在任何公开评测集里，却真实存在于企业和个人的日常工作中。

所以 ai-eval 不做（也没必要做）大规模、统计学意义上的 benchmark——那是公开榜单的事。
它做两件公开榜单做不了的事：

1. **拿你自己的真实任务当试卷**：一手、可复现、贴合你的工作流，直接产出
   「这个任务上，哪个模型完成了、花了多少 token、多少时间」的并排答案。
2. **让任何人都能构建属于自己的评测集**：通过 skill 从历史 session 里自动蒸馏任务
   （见下文 session-to-eval skill），把「建评测」的成本从手写降到一句话——评测能力不再是少数团队的专利。

## 核心理念

一个用例 = 一个**端到端 agent 任务** + 一个**可换的启动器**。
benchmark 只替换模型/启动器，重跑同一任务，采集四维指标并产出多模型并排计分卡。

- **用例与模型解耦**：用例只声明「任务 + 校验 + 裁判 rubric + 输入」，不绑定模型。
- **启动器抽象**：`runner`（跑用例）和 `judge`（裁判）共用同一套档案注册表与配置。
- **比较口径诚实**：比的是「启动器 + 模型」捆绑，不是裸模型（harness 是已知混淆变量）。

### 一个矩阵、三个比较轴

执行模型是一个 `runner × prompt variant × data item` 三维矩阵。case 永远只是
「任务 + 数据 + 真值 + 判分」，**在比什么由运行时的选择决定**：

| 比较轴 | 你想回答的问题 | 怎么触发 | 结果看哪里 |
|---|---|---|---|
| **Runner 轴**（评模型/provider） | 这个任务该用哪个模型/启动器？ | `-r a,b,c` 传多个 runner | 完成率矩阵、四维每维赢家 |
| **Prompt 轴**（评方案） | 新 prompt 修复了什么、退化了什么？ | `--variants x,y` 固定 runner | variant 配对表（fixed/regressed） |
| **数据轴**（评数据） | 哪类输入拖垮了哪个组合？ | case 的 check report 产出 `items[]` | item 级明细网格 |

三个轴可叠加：一次运行既可以多 runner × 多 variant，item 明细自动细分到每格。

## 快速开始

从使用目的看，case 分为两大类。它们共用同一个 `case.yaml` schema 和执行框架，
不是两个互不兼容的技术类型：

1. **模型选型 case**：固定任务和判分方式，同时运行多个 runner，回答“这个任务该用哪个模型/启动器”。
2. **方案迭代 case**：固定 runner 和数据集，同时运行多个 prompt variant，回答“新 prompt 是否修复旧错误、是否引入退化”。

先列出本机实际可用的 case、runner 和 variant：

```bash
./run.sh -l
```

### 第一类：模型选型 case

给 `-r` 传入多个 runner。没有指定 `--variants` 时，框架只运行该 case 的默认 variant：

```bash
# 同一个端到端任务分别交给 codex 和 claude；每个组合运行 2 次
./run.sh \
  -c 2026-06-02-001-fizzbuzz \
  -r codex,claude \
  --repeat 2
```

结果重点看计分卡中的任务完成率、check/judge、耗时和 token。这里比较的是
“runner + 模型”整体，不是脱离启动器的裸模型。

### 第二类：方案迭代 case

给 `-r` 传入一个固定 runner，用 `--variants` 选择要配对比较的 prompt 版本：

```bash
# 同一个 runner、同一批数据，比较 baseline 与 candidate
./run.sh \
  -c <case名> \
  -r <runner名> \
  --variants baseline,candidate \
  --repeat 5
```

variant 名以 `./run.sh -l` 显示的 `[variants=...]` 为准；`--variants '*'` 运行该 case
声明的全部版本。不传 `--variants` 只跑默认版本，**不会产生 variant 配对比较**。
如果 `case.yaml` 已声明 `repeat`，以 case 的值为准；否则使用命令行的 `--repeat`。

这类 case 的计分卡除常规指标外，还会显示每个条目的 `fixed`（新版本修复）与
`regressed`（新版本退化）。比较结论只适用于该 case 声明的数据和评测范围，不自动外推。

例如，本仓库配套私有库中的 Step2 prompt variant case 可这样运行：

```bash
./run.sh \
  -c 2026-07-13-001-step2-prompt-variant \
  -r minimax-m3-c0-direct \
  --variants original,v4
```

一次运行的全部结果（manifest、每格产物、计分卡、HTML 报告）自包含在
`runs/<run_id>/`（公开 case → 仓库根，私有 case → 私有根，见下文目录地图）；
`scorecards/` 另存一份计分卡副本便于浏览历史，同日重复运行不会互相覆盖。
仅公开模型选型结果可按需写入模型档案：

```bash
./run.sh -c 2026-06-02-001-fizzbuzz -r codex --write-profiles
```

依赖：Python 3.11+、PyYAML、`claude` CLI、`codex` CLI、PATH 上的 `c`（模型切换器）。

## 启动器（4 类）

在 `runners.yaml` 注册命名档案，runner 与 judge 共用：

runner 使用统一注册表，不区分公开或私有；私有性只属于 case 数据。本机缺少某个 runner
依赖或配置时会明确失败，可先用 `./run.sh -l` 查看并通过 `-r` 选择本机可用档案。

| launcher | 说明 | 关键字段 |
|---|---|---|
| `claude` | `claude -p --output-format json` | `model?` |
| `codex` | `codex exec --json`（默认 `-s workspace-write`，否则写不了文件） | `sandbox?`, `model?` |
| `c` | 复用 PATH 上的 `c <config>`（透传无头参数给 claude） | `config`（config.env 索引） |
| `command` | 通用模板（sf cli / 自定义斜杠命令 / 任意外部 agent） | `template`, `metrics: none` |

凭证用 `${ENV_VAR}` 插值，**禁止明文密钥写入 runners.yaml**（注册表会拒绝）。

## 四维指标

- **质量/正确性**：用例 `check` 脚本（确定性 pass/fail）+ LLM 裁判（advisory）。
- **速度/耗时**：编排器墙钟，永远可得。
- **token**：跨家可比的真实基础——`in` 含缓存读写（`cache_read`/`cache_creation`），不再漏算
  （旧实现把缓存丢了，sonnet 一次显示 6、实际 13 万）。各字段分存，供未来精确计价。
- **成本**：仅真 Anthropic 计费的 `claude` 启动器可信；`c` 路由的第三方/本地模型显示「—」
  （claude 自报 cost 是按 Claude 定价的影子，非真实成本，本地免费模型也会报出钱）；codex 无 cost。
- **agentic 行为**：轮数、`files_changed`（无方向诊断量，与 check/judge 并读）。

## 判分（三层）

1. **check**：用例自带可验证脚本，退出码 0 = 通过。
2. **judge**：LLM 裁判按 rubric 打分，作 **advisory**（有 check 时以 check 为质量锚）；
   选手产物用 `<contestant_output>` 分隔块包裹标注不可信（抗注入）；同源标 `same_source`。
3. **人工备注**：run record 预留 `human_note`。

## 计分卡

- **任务完成度**（最直观的结果信号）：把 check / judge 折成「任务干成了没」一维——
  `check 通过` 或 `judge ≥ expected.passing_threshold`（或 `expected.completion.core_dimensions` 核心维达标）= 完成。
  单用例显示 `✅完成 / ❌未完成`（repeat>1 显示通过率 `2/3`）；多用例顶部出「任务完成率」汇总（各用例等权，如 `8/10 (80%)`）。
  judge 没跑成 / 无判据 → 标「未评」，**不冤判为未完成**。
- 多模型并排：runner（启动器+模型）标签 × 四维 + **每维赢家** + **权衡摘要**。
- **不自动聚合**四维单一分——四维不可通约，结论由你判定（完成度是单独的结果维，不是四维加权）。
- `repeat>1` → 中位数 + 离散度，check 报 pass 率。
- 进入可分享 markdown 前对裁判理由/原始输出**脱敏**。

## HTML 报告

每次运行结束自动在 `runs/<run_id>/report.html` 生成**单文件、零外部依赖**的交互报告，
浏览器直接打开即可（与计分卡同一数据源，只做渲染）：

> `scorecard.md` 是默认可分享摘要；`report.html` 可能包含任务输入与模型输出正文，
> 必须按 case 的保密级别保存，不应未经复核直接外发。

- **首屏**：Case × Runner 矩阵，同格展示完成状态、参考分与耗时；多 variant 分列，
  部分未评不显示全绿，最快标记只比较同 variant 下所有轮次均通过的组合；
- **作品入口**：HTML 用例直接链接到本轮声明的产物；没有保存截图时明确显示“首屏尚未采集”，
  不自动执行模型生成的页面，不把源码参考分当作视觉实测；
- **Prompt 轴**：variant 配对表，fixed / regressed 逐 item 列出；
- **数据轴**：item × runner@variant 通过网格，哪类输入拖垮了哪个组合一目了然；
- **逐格明细**：点击矩阵格子打开侧边证据面板，展示全部已记录轮次；原有任务详情仍可展开，
  查看 check 详情、裁判理由（已脱敏）、耗时/token，
  并有相对链接直达同目录 `cells/` 下的 `run.json` / `raw.txt` / `artifacts/`。

对历史 run 重建（不重跑、不重判分）：

```bash
./run.sh --report <run_id>
```

## 目录地图：什么在哪

拨乱反正的两条铁律：**case 目录 = 纯定义**（评什么、喂什么、怎么判，永不写入运行产物）；
**一次运行 = 一个自包含目录** `runs/<run_id>/`（这次评测的一切都在里面）。

| 你想找… | 在哪 |
|---|---|
| case 本身（任务 prompt） | `cases/<name>/prompts/`（variants 各一个文件），入口 `case.yaml` |
| 选手可见的输入/数据集 | `cases/<name>/input/`（运行时拷入隔离 workdir） |
| 预期结果（真值） | 单一预期值 → `case.yaml` 的 `expected:`；批量标签 → `oracle/`（选手不可见）；可执行基准 → `verify/`（check 前强制还原，防篡改） |
| 评测标准 | 确定性 → `check.sh`（或 protocol 的 check）；主观 → `prompts/rubric.md`（judge） |
| 在比 runner / prompt / 数据？ | 由运行选择决定（见「一个矩阵、三个比较轴」），case 不用改 |
| 一次评测的全部结果 | `runs/<run_id>/`（公开 case → 仓库根；私有 case → 私有根） |
| 每次 LLM 请求的原始返回 | `runs/<run_id>/cells/<case>/<variant>/<runner>/repeat-<N>/raw.txt`（脱敏启动器流） |
| 模型最终答案纯文本 | 同上 cell 目录 `artifacts/OUTPUT.txt`（不脱敏，供 check/judge 读） |
| 单格结构化记录（指标+判分） | 同上 cell 目录 `run.json` |
| 汇总视图 | `runs/<run_id>/report.html`（交互）与 `scorecard.md`（纯文本，`scorecards/` 有副本） |

```
ai-eval/
├── run.sh                 # 薄入口 → python3 -m bench
├── config.yaml            # 一次运行配置（runners/cases/judge/repeat/dimensions）
├── runners.yaml           # 命名 runner 档案注册表
├── bench/                 # Python 编排器包
│   ├── registry.py        # 档案加载 + 密钥校验
│   ├── adapters/          # claude / codex / c / command 适配器
│   ├── case.py            # v1/v2 用例加载 + 隔离 workdir
│   ├── protocol.py        # 可复用的声明式 case 机制
│   ├── layout.py          # 一次运行的产物布局（runs/<run_id>/ 唯一出处）
│   ├── orchestrator.py    # 预校验 RunPlan + 矩阵执行
│   ├── comparison.py      # variant 配对比较
│   ├── scoring.py         # check + 裁判
│   ├── scorecard.py       # markdown 计分卡 + 档案写入
│   ├── report.py          # 自包含 HTML 报告（--report 可重建）
│   ├── scrub.py           # 密钥脱敏
│   └── record.py          # run record schema
├── cases/<name>/          # 用例纯定义：case.yaml + prompts/ + input/ + oracle/ + verify/
├── protocols/<name>/      # 多个 case 共用时才需要的运行/check 机制
├── runs/<run_id>/         # 一次运行的全部结果（gitignored；私有 case 落私有根）
│   ├── run_manifest.json  #   选择、完整性锁、状态
│   ├── cells/...          #   每格 run.json + raw.txt + artifacts/
│   ├── scorecard.md       #   计分卡原件
│   └── report.html        #   HTML 报告
├── models/<label>.md      # 模型档案（计分卡沉淀目标）
├── scorecards/            # 计分卡浏览副本（gitignored，按需分享）
└── tests/                 # pytest
```

## 加一个用例

在 `cases/<name>/` 建 `case.yaml`。最小 case v2 只需声明任务和判分入口，`name` 默认取目录名，
`class` 默认是 `coding`：

```yaml
schema_version: 2
task: prompts/task.md
check: check.sh
judge: prompts/rubric.md
# requires_engine: claude   # 仅限某引擎时声明，矩阵会跳过不兼容格标 N/A
# repeat: 3                  # 覆盖全局 repeat
```

同一数据比较多个 prompt 时只增加 variant，不复制 case：

```yaml
schema_version: 2
task:
  variants:
    baseline: prompts/baseline.md
    candidate: prompts/candidate.md
  default: candidate
evaluation:
  role: calibration
  generalizes: false
  comparison: {method: item_exact_match, baseline: baseline, candidate: candidate}
  unit_of_analysis: item
  independent_unit: batch
```

`item_exact_match` 明确要求 check report 提供 `items[].id/actual/correct`。数值、rubric 或生成式结果
不能套用这个方法，应先由领域 check 产出指标，再新增有明确定义的比较方法。

只有当多个 case 反复复制同一运行脚本、check 或 variant 参数时，才在同仓库
`protocols/<name>/protocol.yaml` 定义一个声明式 protocol，并让 case 增加 `protocol: <name>`。
protocol 只复用机制；dataset、prompt、oracle 和评测范围仍由 case 明确声明。旧 case v1 保持兼容。
schema v2 必须至少有一个可用的 check 或 judge；声明 variant comparison 时还必须显式写
`role`、`generalizes`、比较方法和统计单位，框架不替使用者猜测这些结论边界。

输入资产放 `input/`（运行时拷入隔离 workdir，不污染源）。
**只读校验资产**（如基准测试）放 `verify/`——它**不进入选手 workdir**，check 前还原到产物目录，
确保选手无法通过改测试来骗取 check pass。

## 私有用例（公司内部 / 不可开源）

公开仓只放可开源的 case；内部 case（真实工单、内部代码、业务规则）放在**仓库之外**，物理隔离杜绝误提交。

```bash
cp private.env.example private.env        # 改成你的私有 cases 根（仓库外的绝对路径）
# private.env 里：export AI_EVAL_PRIVATE_CASES="/abs/path/to/private/cases"
./run.sh -l                                # 公开 + 私有 case 一起列出（私有标 🔒）
./run.sh -c <私有case名> -r ...            # 公开/私有 case 同样跑
```

- `run.sh` 自动 source gitignored 的 `private.env`，框架经 `AI_EVAL_PRIVATE_CASES`（`:` 分隔可多个根）
  发现公开 + 私有 case；私有 case 的 `runs/`（含 report.html）和 `scorecards/` 都落在私有根，产物不外泄。
- 建议把私有 cases 目录单独做成一个**私有 git 仓**（内部可共享、有版本）。
- **session-to-eval skill 默认把蒸馏出的 case 写到私有路径**（`config.toml` 的 `private_cases_path`），
  除非你显式要求"放公开"——蒸馏自真实 session 的 case 天然可能含敏感数据。
- 安全网：公开仓的 `cases/_private/` 前缀已 gitignore，万一内部 case 误放进公开 `cases/` 也提交不上去。

## 从 session 自动蒸馏用例（session-to-eval skill）

手写用例慢。`.claude/skills/session-to-eval/` 提供一个**可移植 skill**，两种入口：

- **倒出模式**：说"把刚才的任务抽成 eval case"，蒸馏**当前 session**里的 1..N 个任务。
- **检索模式**：给一句意图描述（如"把判断工单是否线上问题并分级的推理抽成 case"），skill 自动在
  当前 session 与**本项目历史 session**（Claude Code + Codex 双端）里检索命中任务；缺输入/真值时主动挖
  项目 CLAUDE.md/README/代码补全；本项目信息不足、线索指向他项目时**主动问你**是否跨项目搜集。

两种入口都兼容 Claude Code 与 Codex，蒸馏成对齐上面契约的**草稿 case** 写进 `cases/`。

```bash
# 一次性安装（软链进 ~/.claude/skills/、~/.codex/skills/、~/.agents/skills/）
cp .claude/skills/session-to-eval/config.example.toml .claude/skills/session-to-eval/config.toml   # 填入本仓库绝对路径
bash .claude/skills/session-to-eval/install.sh
```

诚实边界：transcript 里没有外部验证过的 ground-truth、大型工作集无法完整复原，
所以产物默认是**草稿级**——能精确复原的精确复原，不能的产出 `setup.sh` 桩 + `ground-truth TODO`
+ 人工确认点，区分度由人在落地前签字。落盘前 `validate_case.py` 做静态结构校验
（只证明能被 bench 加载，**不**证明能跑出有意义的分）。详见 `.claude/skills/session-to-eval/SKILL.md`。

## 安全

- 子进程用最小环境，剔除无关凭证（每个 launcher 只放行自己的 auth）。
- 凭证类敏感信息进入报告或计分卡前脱敏；`report.html` 仍可能包含业务正文，按 case 保密级别保存。
- `scorecard.md` 是默认可分享摘要；`report.html`、`cells/`、`run.json` 与 `artifacts/` 不应未经复核直接外发。
- 运行产物 `runs/` 与计分卡副本 `scorecards/` 已 gitignore（历史遗留的 `cases/*/output/` 同样忽略，仅只读保留）。

## 已知边界

- **比的是「启动器+模型」捆绑**，非裸模型；harness（system prompt/工具/agent loop）是混淆变量。
- **单一裁判**存在跨家族偏置；裁判分作 advisory，优先 check 锚。
- **本机 c/claude 加载大量插件**会抬高 token/耗时基线，跨 launcher 不完全可比。
- 价值随用例量增长——单个用例只说明该任务上的对比，不代表模型整体优劣。

## License

MIT
