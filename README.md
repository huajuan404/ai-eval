# ai-eval (BMW - Best Model Choice)

> 一手真实工作案例驱动的 AI 模型选型 benchmark。

## 目标

让 AI 工作者能**快速判断何时该用哪个模型**。

不是官方 benchmark，是**一手真实工作案例**。比标准测试更真实，比别人经验更贴合自己。

## 工作流

```bash
# 1. 切换模型（依赖 ~/tools/c 脚本和 ~/tools/config.env）
c 0          # MiniMax
c 7          # Opus

# 2. 在对应模型下运行用例
cd cases/2026-04-29-xxx
# 读取 README.md 看如何运行

# 3. 复盘记录到 models/*.md
```

## 目录结构

```
ai-eval/
├── CLAUDE.md              # 项目说明
├── README.md              # 本文件
├── LICENSE                # MIT 许可证
├── cases/                 # 真实案例（按日期命名）
│   └── [case-name]/
│       ├── README.md      # 案例说明、运行方式
│       ├── input/         # 输入文件
│       ├── prompts/       # prompts
│       ├── expected/      # 期望输出
│       └── output/        # 运行结果（按模型分目录）
├── prompts/               # 公共 prompts 资源
├── models/                # 模型使用经验档案
├── scripts/
│   └── run.sh             # 案例运行入口
└── config.yaml            # 测试配置
```

## 已配置模型 (~/tools/config.env)

| 编号 | 模型 | 用途 |
|------|------|------|
| 0 | MiniMax-M2.7-highspeed | 日常快速任务 |
| 1 | GLM (bigmodel) | |
| 2 | GLM (z.ai) | |
| 3 | Claude Sonnet 4.5 | |
| 4 | Claude Opus 4.6 | |
| 5 | GLM-5 | |
| 6 | Claude Sonnet 4.6 | |
| 7 | Claude Opus 4.7 | |

## 运行

```bash
./run.sh                  # 显示帮助
./run.sh -l               # 列出所有案例
./run.sh -c <case-name>   # 运行指定案例
```

## 模型档案格式

每个模型一个 `.md`，记录：
- **擅长场景**：什么任务适合用它
- **不擅长场景**：什么任务不该用它
- **关键差异**：和其他模型对比的独特之处
- **具体案例**：本项目中验证过的案例链接

## License

MIT
