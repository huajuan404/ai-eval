# GLM Visual UI Coding Case Design

## 目标

把智谱 GLM-5.3-Flash 官方“视觉驱动的 UI Coding”推荐体验转成一个公开、可复现、可判分的 ai-eval case。任务保持端到端：选手只得到同一产品的四张截图，从空目录初始化 Next.js + TypeScript，完成多页面应用，运行后按截图自行比较和修正。

## 输入与任务边界

- Case 名：`2026-08-29-001-visual-ui-coding`，`class: coding`，schema v2。
- `input/reference/` 只包含四张 PNG：桌面总览、桌面运行列表且筛选器展开、桌面运行详情、移动端侧栏展开。
- 不提供脚手架、依赖、数据文件、设计 token 或组件提示。
- 任务提示保留官方任务的全部操作性要求和顺序：分析设计体系与页面关系，使用 Next.js + TypeScript 实现，启动后逐页截图比较并持续修正，最后说明覆盖范围、运行方式和差异。
- 官方原文来自外部文档；仓库中的 task 使用忠实改写而非整段复制，并在 README 标注来源。

## 参考产品

产品名为 Tracegrid，是 AI 模型运行分析工具。四个画面共享同一套导航、数据和暗色技术控制台设计。

- 视觉 thesis：低照度运维控制室；近黑矿物表面、酸性薄荷绿遥测、等宽数字、纤细发光轨迹。
- 内容：运行次数、成功率、P50 延迟、成本、模型分布、运行列表、筛选状态、单次运行时间线。
- 交互：桌面导航、状态筛选弹层、运行详情跳转、移动端抽屉。
- 参考应用源码放在选手不可见的 `oracle/reference-app/`。PNG 由 `oracle/render_reference.mjs` 生成；修改视觉时必须改生成器并重渲染，不手修 PNG。

## 判分

`check.sh` 只做可客观复现的完成门：

1. 工作区中恰有一个非 `node_modules` 的 `package.json`，且声明 Next.js、React 与 TypeScript。
2. 已安装依赖，`npm run build` 成功；check 不替选手联网安装。
3. 启动应用后，Playwright 能完成四条用户路径：打开总览、展开筛选、进入运行详情、在移动端打开侧栏。
4. 固定 Chromium 与固定 viewport 截取四张实际图，使用 ImageMagick SSIM 与参考图比较；每屏记录 item 结果，总体必须达到视觉底线。

LLM judge 在 check 之后读取参考图、实际截图和源码，按五维各 0–5 分：设计系统还原、页面关系与共享组件、交互状态、响应式、工程与自我验证。check 是“任务是否真正跑通”的锚，judge 提供质量梯度。

## 可复现性与错误处理

- npm 版本全部锁定，参考应用提交 lockfile。
- 渲染脚本使用本机 Chromium/Playwright，固定 viewport、时区、颜色模式并等待字体与动画稳定。
- check 将结构化结果写入 `ai_eval_check_report.json`；缺依赖、构建失败、服务未启动、元素不可操作或渲染失败都返回明确错误，不把环境异常伪装成视觉低分。
- check 用 `AI_EVAL_CASE_DIR` 读取隐藏 oracle；runner 看不到 oracle。
- README 明确 Node、Chromium/Playwright 与 ImageMagick 依赖，且说明该 case 比较完整 Agent 工作流，不是裸模型视觉能力。

## 对抗式验收

- 正例：把 `oracle/reference-app/` 复制到隔离目录，安装依赖后运行 check，四条路径与视觉底线均通过。
- 反例一：删除 Next.js 声明或构建脚本，check 必须失败。
- 反例二：保留功能但把主题改成白底，构建和交互仍通过，视觉门必须失败。
- 反例三：静态截图或只有单页，详情导航和移动抽屉必须失败。
- 运行 `validate_case.py`、针对性 pytest、全量 pytest 和 ruff。

## 不做

- 不为本 case 新增 protocol；只有第二个 case 证明机制可复用后再抽取。
- 不修改 runner 或全局框架。
- 不把评分维度、路径约定或测试选择器写进给选手的 prompt。
