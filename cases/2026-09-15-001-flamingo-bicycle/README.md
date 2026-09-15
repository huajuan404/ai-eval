# 火烈鸟骑自行车：SVG 动画诊断

同一个模型无关的任务，用不同 runner 生成可独立播放的 SVG，观察车轮旋转、踏板旋转、腿脚同步和无缝循环。

## 固定输入与交付

唯一输入为 [`prompts/task.md`](prompts/task.md)：第一段逐字保留用户给定的英文提示词，第二段仅约定保存为 `flamingo.svg`。
工作目录从空目录开始；不提供参考图、已有 SVG、骨架代码或预设动画周期。模型可自行选择 SMIL 或 SVG 内的 CSS。

本任务与 [`卡皮巴拉骑自行车`](../2026-09-15-002-capybara-bicycle/README.md) 分别作为独立 case。
Opus 5 与 Opus 5.2 是本题的两个历史产物来源。

## 评审边界

本次固化 case 定义与输入，使用 [`prompts/rubric.md`](prompts/rubric.md) 声明五项观察标准。
采用 judge 评审；未增加自动动画 checker，也未设置自动判定“完成”的分数阈值。
裁判必须读取 SVG 并在浏览器中实际观察动画；无法观察时返回空分，不能仅靠静态 PNG 或源码证明腿脚同步和无缝循环。
分数仅用于该 SVG 动画任务的诊断，不代表模型整体能力。

运行入口（裁判需具备浏览器动画观察能力）：

```bash
./run.sh -c 2026-09-15-001-flamingo-bicycle -r <runner> -j <judge>
```

## 已有手工产物的来源

用户提供的原始文件保留在本机仓库根目录，本次不导入正式 run、不作为模型输入；下表记录原文件名与 SHA-256，便于后续核对。

| 来源标签（用户提供） | 原文件名 | SHA-256 |
|---|---|---|
| Opus 5 | `opus-5-flamingo-bicycle.svg` | `cf5c1b30a594b515a3d6065bc3e957ec0d5c1489ed287554e551b3f2214bb708` |
| Opus 5.2 | `opus-5.2-flamingo-bicyle.svg` | `b803e214ea79b53e878271308a7e342c4a7718f72ca533b47e8c7e0f5a9f1dee` |

Opus 5.2 由用户在另一台机器手动生成，当前没有正式 runner。上述标签不代表已核验的模型 API ID；
两份产物都没有在本次重新运行、评分或补写耗时、token、成本。后续通过框架运行的产物写入 `runs/<run_id>/`。
