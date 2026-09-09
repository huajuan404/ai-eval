# 鹈鹕骑自行车：SVG 视觉诊断

核心提示词来自 Simon Willison 于 2024-10-25 公开的用例：
`Generate an SVG of a pelican riding a bicycle`。

- 原始来源：https://simonwillison.net/2024/Oct/25/pelicans-on-a-bicycle/
- 原始集合：https://github.com/simonw/pelican-bicycle
- 适用边界：https://simonwillison.net/2026/Jul/16/kimi-k3/

本仓库适配为同一组 Runner 的 agent 任务：保留核心提示，只增加保存 `pelican.svg`、自行编写矢量图、无外部资源的交付约定。
不提供示例图，不调用图像生成模型代替被测模型，不将结果作为总体智能或泛化能力证据。

`check.sh` 校验 SVG/XML、活动内容及外部资源限制，并用固定版本 CairoSVG 2.8.2 生成 `render.png`。
运行环境需要 `uv` 和系统 Cairo 库；Python 3.12 与 Python 依赖由 uv 的隔离脚本环境管理，不全局安装。
渲染图固定为 1024×768，遵循 SVG 的 preserveAspectRatio；白底展示。
不使用 ImageMagick MSVG：真实产物验证发现它对 CSS stroke、dasharray 和字体支持不完整，可能报错或悄悄丢失图形。
CairoSVG 对部分滤镜和字体仍有支持边界，报告保留原始 SVG；出现差异时按渲染器限制解释。
确定性 PASS 仅代表格式与渲染通过。裁判必须实际查看 PNG，再按四维各 0–5 分给出参考分；无法看图则不评分。
