# ai-eval — Best Model Choice

## 核心目标

让 AI 工作者快速判断**面对自己的真实任务该用哪个模型**。
一手、可复现、接地气、客观——区别于 SWE-bench 之类的公开榜单。

详细用法见 `README.md`。本文件只记开发约定。

## 架构一句话

一个用例 = 模型无关的端到端 agent 任务；根目录 `runners.yaml` 是唯一的命名启动器注册表（runner 与 judge 共用，
不区分公开或私有 runner）；
`bench/` 先生成并校验不可变 `RunPlan`，再跑 `runners × cases × variants × repeat` 矩阵；每格隔离工作目录、采四维指标、三层判分，
最后出多模型并排计分卡 + 自包含 HTML 报告。**比的是「启动器+模型」捆绑，不是裸模型。**

两条布局铁律：**case 目录 = 纯定义**（永不写入运行产物）；**一次运行 = 一个自包含目录**
`<report_root>/runs/<run_id>/`（manifest + cells/ 每格产物 + scorecard.md + report.html；
公开 case 的 report_root 是仓库根，私有 case 是私有根）。三个比较轴（runner / prompt variant / data item）
由运行时选择决定，case 不区分类型。

## 关键模块（bench/）

- `registry.py` — runner 档案加载 + 明文密钥校验
- `adapters/` — claude / codex / c / command 四类启动器适配器（统一 run record 契约，各自命令构造）
- `case.py` — 严格 case v2 + 旧 v1 兼容加载、variant 与评测语义、隔离 workdir；`discover_cases` 多根扫描（公开 `cases/` +
  环境变量 `AI_EVAL_PRIVATE_CASES` 指向的仓库外私有根，同名冲突公开优先，`is_private_case` 标记）
- `protocol.py` — 同仓库 `protocols/<name>/` 声明式复用层，只承载 runtime/check/variant 参数/run contract，不动态加载插件
- `layout.py` — `RunLayout`：一次运行全部产物路径的唯一出处（`runs/<run_id>/` 布局）
- `orchestrator.py` — 全矩阵预校验 `RunPlan` + variant 调度 + 墙钟 + 指标 + 最小环境
- `comparison.py` — variant 配对与跨 repeat 完整性校验；scorecard 只渲染结果
- `run_manifest.py` — case/protocol 完整性锁、通用 request manifest v2 与旧 v1 校验
- `scoring.py` — check 脚本 + LLM 裁判（抗注入 / advisory / 同源标注）
- `completion.py` — 任务完成度（check / judge 折成「完成了没」一维：单 cell 通过率 + 跨 case 等权汇总）
- `scorecard.py` — 任务完成度列 + 跨用例完成率汇总 + 每维赢家 + 权衡摘要（不自动聚合）+ 模型档案写入
- `report.py` / `report_charts.py` / `report_previews.py` — 普通 run 默认生成浅色 HTML 报告：
  Case 并排条形图、参考分/耗时切换、固定 Runner 识别色、HTML/SVG 内嵌与放大预览；
  保留辅助矩阵、variant/item 比较与逐格证据。报告界面零外部依赖，内嵌 HTML 可有自身资源依赖。
- `report_view.py` — 按运行目录的 `report_view.json` 选择展示用例，校验来源和 case 锁；
  `--report` 重建与 `--rejudge` 重判都保留此视图，不改原始运行选择。
- `scrub.py` — 密钥脱敏（record 与计分卡共用）

## 启动器编号（config.env，由 `c` 切换器使用）

| 编号 | 当前 `runners.yaml` 档案 |
|------|--------------------------|
| 0 | `minimax-m3` |
| 1 | `deepseek-v4-flash-vision-exp` |
| 2 | `glm-5.3-official` |
| 3 | `kimi-k3` |
| 4 | `glm-5.2` |
| 5 | `glm-5.3` |
| 6 | `glm-5.3-flash` |
| 7 | `deepseek-v4.1-flash` |
| 8 | `deepseek-v4-pro` |
| 9 | `deepseek-v4-flash`（本机暂缺 CONFIG_9）|

编号 2/4/6 走智谱官方直连；`glm-5.3` 走 ai-keeping 中转（背后同为官方 glm-5.3）。
档案标签 = 该编号当前实际启动的模型，本机 config.env 换模型时两边同步改名（旧标签重跑会静默落到别的模型上）。
`runners.yaml` 用 `c` 类档案引用这些编号（如 `glm-5.3-flash → config: 6`）。`minimax-m3-c0-direct`
是复用 lane 0 配置的 `command` runner，不改变 `minimax-m3` 的 Agent 路径语义。各编号对应的端点、凭证和
本机搭建都在 `c` 切换器的 config.env 里，属本机环境，不在本仓库记录（编号 8 还需本机额外服务，跨机不可移植）。

## .claude/skills/session-to-eval/（从 session 蒸馏 case 的可移植 skill）

独立子系统，两种入口：**倒出模式**（"把刚才的任务抽成 eval case"，蒸馏当前 session）与**检索模式**
（给一句意图描述，自动在当前 session + 本项目历史 session 双端检索命中任务，缺输入/真值时挖项目 CLAUDE.md/README/代码补全，
本项目不足时主动问是否跨项目）。读 session log（Claude Code + Codex 双端）语义识别 1..N 个任务，
蒸馏成对齐 case 契约的**草稿 case** 写进 `cases/`。

- `scripts/session_extract.py` — 双 CLI 定位+解析→token 受限 digest；含触发轮 cutoff、`cat -n` 剥离、
  扩展密钥脱敏（6 类，超 `bench/scrub.py`）、确定性首过分类器 `classify_task`、资产重建覆盖门、`next_sequence_number`。
  cwd 编码按 `[^a-zA-Z0-9]→-`（与 Claude 实测一致，含 `_`/`.`）+ `resolve_claude_project_dir` 读真 cwd 兜底。
- `scripts/session_index.py` — 检索模式确定性脊梁：双端按项目枚举全历史（Claude 编码目录；Codex 读首行
  `session_meta.cwd` 匹配 + 90 天/上限时间盒）、`SessionCard` 轻量名片、中文 bigram 切词 + 关键词 `prefilter`、
  `scan_all_projects_for_terms` 跨项目候选发现（D1 线索）。语义精排与缺口挖掘由 SKILL.md 交 LLM 做。
- `scripts/validate_case.py` — 路径信任校验 + `bench.case.load_case` 静态加载 + 廉价断言；区分 `valid`（能加载）与 `complete`（真值已补）。
- `SKILL.md` — 触发描述（倒出 + 检索双入口）+ 内联 case 契约 + 四类生成模板 + 完整编排 + 检索模式 R1–R7。
- `config.toml`（gitignore，从 `config.example.toml` 复制）记 `ai_eval_path`；`bash install.sh` 三端软链进
  `~/.claude/skills`、`~/.codex/skills` 与 `~/.agents/skills`，源码原地生效。
- 诚实边界：ground-truth 与大工作集多需外部 → 默认产 setup.sh/expected 桩 + TODO；校验门只证明能加载，**不**证明能跑出有意义的分。

## 开发约定

- Python 3.11+；不可变优先（dataclass frozen + replace）；多个小文件 > 大文件。
- 新 case 优先用严格且极简的 `schema_version: 2`；只有至少两个 case 共享同一机制时才新增 protocol。旧 v1 只做兼容维护。
- 新增功能写测试（pytest，`tests/` 下）；`./run.sh` 是薄入口委托给 `python3 -m bench`。
- 运行测试：`python3 -m pytest`；lint：`python3 -m ruff check bench/ .claude/skills/session-to-eval/ tests/`。
- `runs/`、`scorecards/`、`.claude/skills/session-to-eval/config.toml`、`private.env` 为生成产物 / 本机配置，已 gitignore
  （历史遗留 `cases/*/output/` 同样忽略，仅只读保留，新运行不再写入）。
- **私有 case**（公司内部 / 不可开源）放**仓库外**：`private.env`（gitignore，由 `run.sh` 自动 source）
  设 `AI_EVAL_PRIVATE_CASES` 指向仓库外的私有 cases 根；session-to-eval skill **默认写私有路径**（`config.toml` 的
  `private_cases_path`），除非用户显式要求公开。公开仓 `cases/_private/` 前缀已 gitignore 作误放兜底。
- 文档只写在 `README.md` 和本文件，不新增散落 md（用例自己的 README、`.claude/skills/session-to-eval/SKILL.md` 除外）。

## 运行

```bash
./run.sh -l                                   # 列出 runner 档案与用例
./run.sh -c <case> -r codex,claude --repeat 2 # 多模型对比
./run.sh -c <case> -r <runner> --variants a,b # 固定 runner 比较 prompt variant
./run.sh -c <case> -r codex --write-profiles  # 表现写入 models/<label>.md
./run.sh --report <run_id>                    # 对已完成 run 重建 runs/<run_id>/report.html
./run.sh --rejudge <run_id> -j <judge>        # 换裁判重判（只跑 judge，不重跑评测与 check）
```
