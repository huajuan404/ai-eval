# 2026-08-29-001-visual-ui-coding

## 测什么

把 GLM-5.3-Flash 官方“视觉驱动的 UI Coding”推荐体验落成端到端评测：模型只拿到同一产品的四张截图，必须从空目录初始化 Next.js + TypeScript，识别页面关系与共享设计体系，做出可运行应用，并用视觉反馈迭代。

这里比较的是完整的“启动器 + 模型”工作流，包含视觉理解、项目初始化、依赖安装、编码、浏览器操作和自我验证；不能把结果解释成裸模型的单一视觉能力。

任务来源：[智谱 GLM-5.3-Flash 官方文档](https://docs.bigmodel.cn/cn/guide/models/vlm/glm-5.3-flash)。`task.md` 保留原任务的操作性要求和顺序，但未整段复制外部文档。

## 输入

`input/reference/` 中包含 Tracegrid 的四张原创参考图：

1. 桌面总览；
2. 桌面运行列表，状态筛选器展开；
3. 桌面单次运行详情；
4. 390×844 移动端，导航抽屉展开。

截图由选手不可见的 `oracle/reference-app/` 生成。修改参考视觉时必须改参考应用，再运行：

```bash
cd oracle/reference-app
npm ci
npm run build
cd ../../../..
node cases/2026-08-29-001-visual-ui-coding/oracle/render_reference.mjs
```

不要手工修 PNG。

## 判分

- `check.sh`：验证唯一 Next.js + React + TypeScript 工程、依赖已安装、生产构建成功、应用可启动；随后用真实 Chrome 走通总览、筛选、详情与移动导航，固定 viewport 截图，并以 ImageMagick 的归一化 RMSE 换算视觉相似度（`1 - RMSE`），检查四屏是否达到最低门槛。
- `ai_eval_check_report.json`：四个页面各是一条 item，保留交互结果和视觉相似度，便于报告按数据轴展开。
- LLM judge：五维各 0–5 分，评设计还原、页面系统、交互状态、响应式与工程验证；只作高于完成门的质量梯度。

## 运行依赖

- Node.js 22+ 与 npm；
- Chrome 或 Chromium；
- ImageMagick 7（命令为 `magick`）；
- Python 3.11+ 与 ai-eval 自身依赖。

check 不会替选手联网安装依赖。官方任务要求完成后启动应用；提交时仍未安装依赖或无法生产构建，视为任务未完成。

check 使用评测机上的隔离 headless Chrome/Chromium profile，并把浏览器版本写入结构化报告。参考 PNG 由 lockfile 固定的 Playwright Chromium 生成，因此跨机器仍可能存在字体与浏览器渲染差异；`0.55` 只是宽松完成底线，精细品质由同一次运行中的 judge 与人工并排查看。

## 运行

```bash
./run.sh -c 2026-08-29-001-visual-ui-coding -r codex,claude --repeat 2
```

该 case 尚需真实多模型校准视觉相似度下限；当前 `0.55` 是对抗式正确实现/白底破坏实现的初始完成门，不能单独证明模型梯度。
