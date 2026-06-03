# 参考答案（不是唯一答案，仅供 judge 参考）

## 顺序与依赖

1. **数据接入层（必须先做）** — Jira + GitHub 的 webhook / API 集成，加 rate limit / 重试 / 死信
   - DoD：能稳定拉取过去 8 周的两个数据源数据，单元测试覆盖 auth / pagination / 错误重试
2. **数据规范化层** — 两种数据源 → 统一的内部 schema（event、actor、timestamp、source、payload）
   - DoD：所有原始数据有规范化映射，schema 有 OpenAPI spec
3. **聚类 / 摘要 MVP** — 用现成的 embedding + LLM 摘要，先不做"自动判定 sprint 范围"（手动传参即可）
   - DoD：输入过去 8 周数据，能稳定产出 3-7 个主题，每个主题有原始证据链
4. **手动 sprint 范围判定** — 不自动化，先支持手动传参（cron 跑）
   - DoD：CLI 接受 `--sprint-start --sprint-end`，输出 retro markdown
5. **#retro 频道自动推送** — 接入 Slack，先做最简版（只发链接）
   - DoD：sprint 结束前 24h 自动跑，频道收到消息
6. **review checkpoint** — scrimmaster 收到后必须人工 review、修订、出 action items
   - DoD：开 retro 会时人改一版定稿

依赖：1 → 2 → 3（强）→ 4（弱）→ 5（弱）→ 6（必有人参与）

## 完成定义

- 1-2 是"代码完成"（测试覆盖率 ≥ 80%，集成测试通过）
- 3 是"产品完成"（真实跑过去 8 周数据，主题质量可看）
- 4-5 是"产品完成"（实际跑过、scrimmaster 看过）

## 模糊点假设

1. **数据稀疏** — 主题数小于 3 时，自动补"团队健康度"占位主题（"本周无明显主题，建议大家谈一下"）；不强行聚类
2. **Reviewer** — scrimmaster 一人即可，**先不引入全员 review**（v2 再加）
3. **聚类粒度** — 默认 3-7，**用户可在 CLI 覆盖**（`--num-themes 5`）
4. **敏感信息** — **不主动去识别**（避免误删）；Slack 抱怨里的 @ 人名保留，理由：让 scrimmaster 自己看到是谁在抱怨
5. **历史数据** — **不接旧 Google Form**（cold start 损失可接受；接的话需要先清洗 + 重新主题映射，ROI 低）

## 风险

- **风险 1：Jira/GitHub API 限流 / 改动** — 限流脚本会挂；对策：每次跑前先看 healthcheck，挂了 fall back 到上次的缓存
- **风险 2：LLM 幻觉产生看起来合理但错的总结** — 必须在 UI 上始终保留"原始证据"链接到 Jira ticket / PR
- **风险 3：scrimmaster 不看 retro** — 这才是产品最大风险（流程问题不是技术问题）；对策：周会开始时由 scrimmaster 当场打开 retro，让大家先看 2 分钟

## 必须由人决定的事

1. **聚类主题的"标签"**（"CI 慢"、"沟通不足"）— LLM 可以给候选，但最终用哪个词必须人定。理由：标签会进入公司文化
2. **敏感信息处理策略** — 是否删人名、是否删情绪化词汇。理由：涉及公司伦理 + 法律
3. **历史数据接入的 ROI 判断** — 接旧数据要花 2 人周，是否值得？必须 leadership 拍板
