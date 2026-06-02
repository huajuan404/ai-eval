# 用例: zhipu-renewal（判官驱动的分析推理）

智谱 Coding Plan 续订差价与首订折扣的数学/策略分析题。与 fizzbuzz（check 驱动编码）互补：
本用例**无确定性 check**，靠 LLM 裁判按 5 星 rubric 评分。

## 任务

模型回答 Q1–Q4（差价核验、5% 折扣残值证明、续订时机对比、薅羊毛策略）。
纯 prompt 无文件产出——模型最终回答由编排器写入 `artifacts/OUTPUT.txt` 供裁判读取。

## 判分

- **check**：none。
- **judge**：`prompts/rubric.md` 含 5 星标准 + 参考答案（ground truth），裁判据此判级。
  维度：基础核验 / 折扣分析 / 差异解释 / 策略洞察；score = 星级（1–5），max=5。

## 运行

```bash
./run.sh -c 2026-06-02-002-zhipu-renewal -r codex,claude,glm-5.1 --repeat 2 -j claude
```

## 用途

验证开放型推理任务的 judge 路径，并适合横向对比各模型的数学严谨度与洞察力
（这类题恰恰区分"能算对"与"能看穿套路"的模型）。
