# 财报研报同题考试（中船特气 688146.SH 2026 中报）

三模型同题横评，agent 长任务综合能力测试（检索+核数+建模+排版+交付）。结论已用于公众号文章《斩杀线换主人了》（content_professor/generated/2026-08-29/）。

> 类型说明：这是**手工评测记录型 case**（人工跑三个 harness、人工按协议评估），暂未接入 bench 自动编排。待 PR #10 的 bench 框架合并后，如需自动化再按新 schema 补 case.yaml/check.sh。

## 运行方式

同一段 prompt（见 `prompts/prompt.md`）分别在三个 harness/模型组合下运行，各自独立完成全流程并交付 PDF + Excel。

| 选手 | harness | 产物位置 |
|---|---|---|
| GPT-5.6 Sol (high) | codex | `~/Desktop/code/股市/codex/output/pdf/` + `outputs/zhongchuan_20260827/`（注意 output 与 outputs 是两个目录） |
| Kimi K3 (max) | kimi | `~/Desktop/code/股市/kimi/` |
| GLM-5.3-Flash (max) | glm | `~/Desktop/code/股市/glm/` |

## 评估协议（可复用）

1. **穷尽产物目录再评分**——本次曾因只查 `output/`（单数）漏掉 `outputs/` 里的 Excel，误判 codex 未交付，教训记档
2. 指令遵循按 prompt 显式要求逐条对照表
3. 数据准确性做三方交叉验证（核心财务数字互相对得上才可信）
4. Excel 扔 LibreOffice 强制重算，验证公式驱动是否属实
5. 美观度渲染 PDF 首页+内页拼图目检，不凭文字描述推测
6. 成本用各家导出的真实 usage 日志 × 任务当日生效 API 价，不用牌价粗估

## 结论速览（2026-08-29 定稿）

| 维度 | 🥇 | 🥈 | 🥉 |
|---|---|---|---|
| 指令遵循 | 三家全部达标（勾稽颗粒度 kimi 25 项最细） | | |
| 完整性 | GLM Flash（19 页+独家解禁发现） | Kimi | GPT Sol |
| 准确性 | GPT Sol（追溯调整口径） | Kimi | GLM Flash（分项加总瑕疵） |
| 美观度 | GPT Sol（研报级+Excel 最美） | Kimi | GLM Flash（零图表） |
| 实算成本 | GLM Flash ≤$0.2 | Kimi $3.79 | GPT Sol $12.14 |

关键发现：三家核心数据完全一致（无编造）；GLM Flash 独家挖出 10/21 控股股东解禁 957 亿（另两家全漏）；agent 长任务账单大头是缓存回喂（Sol 账单 71% 是缓存读取）。

完整评估过程与数据见 `~/Desktop/content_professor/topics/glm-53-flash/material.md`。
