# ai-eval — Best Model Choice

## 核心目标

让 AI 工作者快速判断**面对自己的真实任务该用哪个模型**。
一手、可复现、接地气、客观——区别于 SWE-bench 之类的公开榜单。

详细用法见 `README.md`。本文件只记开发约定。

## 架构一句话

一个用例 = 模型无关的端到端 agent 任务；`runners.yaml` 是命名启动器注册表（runner 与 judge 共用）；
`bench/` 编排器跑 `runners × cases × repeat` 矩阵，每格隔离工作目录、采四维指标、三层判分，
最后出多模型并排计分卡。**比的是「启动器+模型」捆绑，不是裸模型。**

## 关键模块（bench/）

- `registry.py` — runner 档案加载 + 明文密钥校验
- `adapters/` — claude / codex / c / command 四类启动器适配器（统一 run record 契约，各自命令构造）
- `case.py` — 用例加载 + 隔离 workdir + 快照 diff（含忽略列表）
- `orchestrator.py` — 矩阵 × repeat 执行 + 墙钟 + 指标 + 最小环境
- `scoring.py` — check 脚本 + LLM 裁判（抗注入 / advisory / 同源标注）
- `completion.py` — 任务完成度（check / judge 折成「完成了没」一维：单 cell 通过率 + 跨 case 等权汇总）
- `scorecard.py` — 任务完成度列 + 跨用例完成率汇总 + 每维赢家 + 权衡摘要（不自动聚合）+ 模型档案写入
- `scrub.py` — 密钥脱敏（record 与计分卡共用）

## 启动器编号（config.env，由 `c` 切换器使用）

| 编号 | 模型 |
|------|------|
| 0 | MiniMax-M3 |
| 1 | deepseek-v4-pro |
| 2 | glm-5.1 |
| 3 | mimo-v2.5-pro |
| 4 | claude-opus-4-6 |
| 5 | glm-5 |
| 6 | claude-sonnet-4-6 |
| 7 | claude-opus-4-7 |
| 8 | qwen2.5:0.5b（本地弱模型基线，做评测「地板」） |

`runners.yaml` 用 `c` 类档案引用这些编号（如 `glm-5.1 → config: 2`）。各编号对应的端点 / 凭证 / 本机搭建
都在 `c` 切换器的 config.env 里，属本机环境，不在本仓库记录（编号 8 还需本机额外服务，跨机不可移植）。

## case-gen/（从 session 蒸馏 case 的可移植 skill）

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
- `config.toml`（gitignore，从 `config.example.toml` 复制）记 `ai_eval_path`；`bash install.sh` 双端软链进
  `~/.claude/skills` 与 `~/.codex/skills`，源码原地生效。
- 诚实边界：ground-truth 与大工作集多需外部 → 默认产 setup.sh/expected 桩 + TODO；校验门只证明能加载，**不**证明能跑出有意义的分。

## 开发约定

- Python 3.11+；不可变优先（dataclass frozen + replace）；多个小文件 > 大文件。
- 新增功能写测试（pytest，`tests/` 下）；`./run.sh` 是薄入口委托给 `python3 -m bench`。
- 运行测试：`python3 -m pytest`；lint：`python3 -m ruff check bench/ case-gen/ tests/`。
- `cases/*/output/`、`scorecards/`、`case-gen/config.toml` 为生成产物 / 本机配置，已 gitignore。
- 文档只写在 `README.md` 和本文件，不新增散落 md（用例自己的 README、`case-gen/SKILL.md` 除外）。

## 运行

```bash
./run.sh -l                                   # 列出 runner 档案与用例
./run.sh -c <case> -r codex,claude --repeat 2 # 多模型对比
./run.sh -c <case> -r codex --write-profiles  # 表现写入 models/<label>.md
```
