# 产品需求：Sprint Retro 自动聚合

## 背景

我司每两周一次 sprint retrospective，目前流程是：
- 每个工程师手动填一个 Google Form（耗时 5-10 分钟，**完成率 < 40%**）
- Scrum Master 人工汇总
- 周会过 1 小时，30% 的人在会上才看到内容

痛点：完成率低、汇总成本高、会上信息密度低。

## 目标

做一套**自动 retro 聚合系统**，让 retro 准备时间从「1 人天 / 双周」压到「零人工」。每个 sprint 结束前一天自动生成 retro 草稿，开会时大家直接讨论、修订、出 action items。

## 输入数据源（真实存在的产品环境）

1. **Jira sprint board**：每个 engineer 的 tickets（done / in-progress / carry-over）
2. **GitHub PR 数据**：同 sprint 范围内的 PR（merged / open / review 轮次 / review 平均时长）
3. **Slack 频道**（sprint channel）的消息：open-ended 抱怨、感谢、问题
4. **既有 Google Form 历史 retro**（过去 6 个月的数据，用作 few-shot 风格参考）

## 必做

- 自动判定 sprint 范围（基于 Jira sprint 起止日，不靠人工传参）
- **必须**含聚类输出：把零散的 PR / tickets / Slack 抱怨聚成 3-7 个主题（如"CI 慢"、"测试覆盖"、"团队沟通"），每个主题下列证据
- 输出 markdown retro 草稿，可直接 fork 编辑
- 在 sprint 结束前 24h 自动跑，#retro 频道发链接

## 一些模糊点（**不要在方案里回避，要明确写假设**）

1. **数据稀疏怎么办？**（某周 Slack 消息很少、PR 很少）
2. **谁来 review retro 草稿？**（scrummaster？所有 engineer？仅 leadership？）
3. **聚类粒度**：是 3-5 个粗主题好，还是 7-10 个细主题好？
4. **敏感信息**：Slack 抱怨可能含人名/情绪，怎么处理？
5. **历史数据接入成本**：4 个数据源是同步接，还是 MVP 先接 1-2 个？

## MVP 边界（你定的，方案要遵守）

- MVP 只接 **Jira + GitHub** 两个源（最干净）
- Slack 推迟到 v2
- 旧 Google Form 数据**不用**（先 cold start）
- 不做用户系统；用 SSO 拿身份

## 期望输出

你是一位有 5 年 full-stack 经验的 tech lead，要给团队出一份**带依赖顺序的实施计划**，不是 PRD，不是技术选型清单。要回答：
- 顺序：先做什么后做什么，为什么
- 每一步的"完成定义"（怎么算这步真的干完了）
- 跨步骤的依赖
- 风险点
- 哪些决定必须由人做（不能由 LLM 替你定）

约 600-1000 字。
