# ai-eval — 可换模型的端到端 agent benchmark

> 回答一个本质问题：**面对我自己的真实任务，该用哪个模型？**
> 不是 SWE-bench 那种公开榜单，是一手、可复现、贴合自己工作流的对比。

## 核心理念

一个用例 = 一个**端到端 agent 任务** + 一个**可换的启动器**。
benchmark 只替换模型/启动器，重跑同一任务，采集四维指标并产出多模型并排计分卡。

- **用例与模型解耦**：用例只声明「任务 + 校验 + 裁判 rubric + 输入」，不绑定模型。
- **启动器抽象**：`runner`（跑用例）和 `judge`（裁判）共用同一套档案注册表与配置。
- **比较口径诚实**：比的是「启动器 + 模型」捆绑，不是裸模型（harness 是已知混淆变量）。

## 快速开始

```bash
# 1. 看有哪些 runner 档案和用例
./run.sh -l

# 2. 跑一个用例，多模型对比（每格跑 2 次取中位数 + 离散度）
./run.sh -c 2026-06-02-001-fizzbuzz -r codex,claude --repeat 2

# 3. 计分卡生成在 scorecards/<date>.md；把表现写进模型档案：
./run.sh -c 2026-06-02-001-fizzbuzz -r codex --write-profiles
```

依赖：Python 3.11+、PyYAML、`claude` CLI、`codex` CLI、PATH 上的 `c`（模型切换器）。

## 启动器（4 类）

在 `runners.yaml` 注册命名档案，runner 与 judge 共用：

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

## 目录结构

```
ai-eval/
├── run.sh                 # 薄入口 → python3 -m bench
├── config.yaml            # 一次运行配置（runners/cases/judge/repeat/dimensions）
├── runners.yaml           # 命名 runner 档案注册表
├── bench/                 # Python 编排器包
│   ├── registry.py        # 档案加载 + 密钥校验
│   ├── adapters/          # claude / codex / c / command 适配器
│   ├── case.py            # 用例加载 + 隔离 workdir
│   ├── orchestrator.py    # 矩阵 × repeat 执行 + 指标
│   ├── scoring.py         # check + 裁判
│   ├── scorecard.py       # 计分卡 + 档案写入
│   ├── scrub.py           # 密钥脱敏
│   └── record.py          # run record schema
├── cases/<name>/          # 用例（case.yaml/input/prompts/check.sh/expected/output）
├── models/<label>.md      # 模型档案（计分卡沉淀目标）
├── scorecards/            # 生成的计分卡（gitignored，按需分享）
└── tests/                 # pytest
```

## 加一个用例

在 `cases/<name>/` 建 `case.yaml`：

```yaml
name: <name>
task:
  type: prompt          # prompt | skill | slash | custom
  prompt_file: prompts/task.md
check:
  type: script          # script | none
  script: check.sh      # 退出 0 = 通过
judge:
  enabled: true
  rubric_file: prompts/rubric.md
  dimensions: [correctness, code_quality]
# requires_engine: claude   # 仅限某引擎时声明，矩阵会跳过不兼容格标 N/A
# repeat: 3                  # 覆盖全局 repeat
```

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
  发现公开 + 私有 case；私有 case 的 `output/` 也落在私有路径，产物不外泄。
- 建议把私有 cases 目录单独做成一个**私有 git 仓**（内部可共享、有版本）。
- **case-gen skill 默认把蒸馏出的 case 写到私有路径**（`config.toml` 的 `private_cases_path`），
  除非你显式要求"放公开"——蒸馏自真实 session 的 case 天然可能含敏感数据。
- 安全网：公开仓的 `cases/_private/` 前缀已 gitignore，万一内部 case 误放进公开 `cases/` 也提交不上去。

## 从 session 自动蒸馏用例（case-gen skill）

手写用例慢。`case-gen/` 提供一个**可移植 skill**，两种入口：

- **倒出模式**：说"把刚才的任务抽成 eval case"，蒸馏**当前 session**里的 1..N 个任务。
- **检索模式**：给一句意图描述（如"把判断工单是否线上问题并分级的推理抽成 case"），skill 自动在
  当前 session 与**本项目历史 session**（Claude Code + Codex 双端）里检索命中任务；缺输入/真值时主动挖
  项目 CLAUDE.md/README/代码补全；本项目信息不足、线索指向他项目时**主动问你**是否跨项目搜集。

两种入口都兼容 Claude Code 与 Codex，蒸馏成对齐上面契约的**草稿 case** 写进 `cases/`。

```bash
# 一次性安装（软链进 ~/.claude/skills/）
cp case-gen/config.example.toml case-gen/config.toml   # 填入本仓库绝对路径
bash case-gen/install.sh
```

诚实边界：transcript 里没有外部验证过的 ground-truth、大型工作集无法完整复原，
所以产物默认是**草稿级**——能精确复原的精确复原，不能的产出 `setup.sh` 桩 + `ground-truth TODO`
+ 人工确认点，区分度由人在落地前签字。落盘前 `validate_case.py` 做静态结构校验
（只证明能被 bench 加载，**不**证明能跑出有意义的分）。详见 `case-gen/SKILL.md`。

## 安全

- 子进程用最小环境，剔除无关凭证（每个 launcher 只放行自己的 auth）。
- 原始输出、裁判理由、check 详情进入可分享产物前脱敏。
- 所有 `cases/*/output/` 产物已 gitignore。

## 已知边界

- **比的是「启动器+模型」捆绑**，非裸模型；harness（system prompt/工具/agent loop）是混淆变量。
- **单一裁判**存在跨家族偏置；裁判分作 advisory，优先 check 锚。
- **本机 c/claude 加载大量插件**会抬高 token/耗时基线，跨 launcher 不完全可比。
- 价值随用例量增长——单个用例只说明该任务上的对比，不代表模型整体优劣。

## License

MIT
