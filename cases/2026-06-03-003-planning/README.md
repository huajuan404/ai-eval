# 用例: planning（Sprint Retro 自动聚合 — 实施计划）

判官驱动的开放型推理 / 规划用例，与 zhipu-renewal 同 class（reasoning）
但打的是另一个能力：**结构化拆解 + 依赖判断 + 显式假设**。

## 任务

扮演 5 年 full-stack 经验的 tech lead，把同目录 `spec.md`（一个 Sprint Retro
自动化系统的产品需求）转成**带依赖顺序的实施计划**。

纯 prompt，无代码产物。模型最终回答由编排器写入
`artifacts/OUTPUT.txt` 供裁判读取。

## 为什么是 reasoning class

- 纯 prompt 进，纯文本出
- 无副作用（agent 不改文件、不调工具）
- 评测完全靠 LLM 裁判的 5 维 rubric

## 与 zhipu-renewal 的区别

| | zhipu-renewal | planning |
|---|---|---|
| 输入 | 一段数字矛盾 | 一段产品需求 |
| 核心能力 | 算得对 / 抠字眼 | 结构化拆解 / 显式假设 |
| Rubric 维度 | 数学 + 策略 | 顺序 / DoD / 模糊 / 风险 / 人机 |

## Rubric（5 维 × 5 分 = 25）

见 `prompts/rubric.md`。passing threshold：≥15 视为良好。

## 参考答案（仅供 judge 参考）

见 `prompts/expected.md`。
