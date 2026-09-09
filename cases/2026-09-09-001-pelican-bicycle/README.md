# 鹈鹕骑自行车：SVG 视觉诊断

核心提示词来自 Simon Willison 于 2024-10-25 公开的用例：
`Generate an SVG of a pelican riding a bicycle`。

- 原始来源：https://simonwillison.net/2024/Oct/25/pelicans-on-a-bicycle/
- 原始集合：https://github.com/simonw/pelican-bicycle
- 适用边界：https://simonwillison.net/2026/Jul/16/kimi-k3/

本仓库适配为同一组 Runner 的 agent 任务：保留核心提示，只增加保存 `pelican.svg`、自行编写矢量图、无外部资源的交付约定。
不提供示例图，不调用图像生成模型代替被测模型，不将结果作为总体智能或泛化能力证据。

`check.sh` 校验 SVG/XML、活动内容及外部资源限制，并用本机 ImageMagick MSVG 渲染器生成 `render.png`。
运行环境需要 Python 3 和 `magick`。渲染图固定为 1024×768，保留比例、白底补边。
MSVG 对高级 SVG/CSS 的支持可能与浏览器不同，报告应保留原始 SVG；出现渲染差异时按渲染器限制解释。
确定性 PASS 仅代表格式与渲染通过。裁判必须实际查看 PNG，再按四维各 0–5 分给出参考分；无法看图则不评分。
