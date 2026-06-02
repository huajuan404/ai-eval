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
- `scorecard.py` — 每维赢家 + 权衡摘要（不自动聚合）+ 模型档案写入
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

`runners.yaml` 用 `c` 类档案引用这些编号（如 `glm-5.1 → config: 2`）。

## 开发约定

- Python 3.11+；不可变优先（dataclass frozen + replace）；多个小文件 > 大文件。
- 新增功能写测试（pytest，`tests/` 下）；`./run.sh` 是薄入口委托给 `python3 -m bench`。
- 运行测试：`python3 -m pytest`；lint：`python3 -m ruff check bench/ tests/`。
- `cases/*/output/`、`scorecards/` 为生成产物，已 gitignore。
- 文档只写在 `README.md` 和本文件，不新增散落 md（用例自己的 README 除外）。

## 运行

```bash
./run.sh -l                                   # 列出 runner 档案与用例
./run.sh -c <case> -r codex,claude --repeat 2 # 多模型对比
./run.sh -c <case> -r codex --write-profiles  # 表现写入 models/<label>.md
```
