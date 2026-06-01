# 奔驰Mark (BMW) - Best Model Choice

## 核心目标

让AI工作者能**快速判断何时该用哪个模型**。

不是官方benchmark，是**一手真实工作案例**。比标准测试更真实，比别人经验更贴合自己。

## 工作流

```
1. 用 c 脚本切换模型
   c 0          # MiniMax
   c 7          # Opus

2. 在对应模型下运行用例
   cd cases/2026-04-29-xxx
   # 读取 README.md 看如何运行

3. 复盘记录到 models/*.md
```

## 目录结构

```
my_benchmark/
├── CLAUDE.md              # 本文件
├── cases/                 # 真实案例（按日期命名）
│   └── [case-name]/
│       ├── README.md      # 案例说明、运行方式
│       ├── input/         # 输入文件
│       ├── prompts/       # prompts
│       ├── expected/      # 期望输出
│       └── output/        # 运行结果（按模型分目录）
│           ├── model-0/   # MiniMax 输出
│           ├── model-7/   # Opus 输出
│           └── ...
├── scripts/
│   └── run.sh            # 测试运行脚本
├── config.yaml           # 测试配置
└── models/               # 模型使用经验
│   ├── minimax.md         # MiniMax 使用经验
│   ├── glm.md             # GLM 使用经验
│   └── claude-opus.md     # Claude Opus 使用经验
└── (models/ 关联到 ~/tools/config.env 配置)
```

## 已有模型配置 (~/tools/config.env)

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

## 运行用例

```bash
# 运行全部案例（按 config.yaml 配置）
./run.sh

# 运行指定案例
./run.sh -c 2026-04-29-xxx

# 只列出可用案例
./run.sh -l
```

config.yaml 中配置要跑哪些模型和案例。

## 模型档案格式

每个模型一个md文件，记录：
- **擅长场景**：什么任务适合用它
- **不擅长场景**：什么任务不该用它
- **关键差异**：和其他模型对比的独特之处
- **具体案例**：本项目中验证过的案例链接
