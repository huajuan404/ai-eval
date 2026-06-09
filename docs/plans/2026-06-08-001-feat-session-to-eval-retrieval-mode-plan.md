---
type: feat
status: completed
origin: docs/plans/2026-06-04-001-feat-session-to-eval-case-skill-plan.md
created: 2026-06-08
---

# session-to-eval：描述驱动的检索式蒸馏

## 问题框定

当前 skill 是"推"模型：用户喊"把刚才的任务抽成 case"，它把**当前一个 session** 的全部任务倒出来供挑选。前提是目标任务必须恰好发生在当前 session。

实测碰壁（2026-06-08）：用户在 `defect_pipeline_service` 项目里想抽"工单描述→是否线上问题→分级"的 LLM 推理 case，但当前 session 只开了 ai-twin，没有该任务 → skill 无法满足。

升级为"拉"模型：用户给一句**意图描述**，skill 自己去找——当前 session → 本项目历史 session（Claude + Codex 双端）→ 命中后抽"输入/预期输出/验证方式"；抽不全则主动挖项目 CLAUDE.md/README/代码搞清"数据怎么拿"，仍不够才回头问用户。

后半程（分类→重建→生成→校验）完全复用，本计划只新增前置的"定位 + 检索 + 补全"层。

## 范围边界（非目标）

- 不替用户执行生产数据库查询：只挖"怎么取数"配方，落成 `setup.sh`，真正取数人工确认。
- 不改判分/重建/校验后半程逻辑（`derive_signals`/`classify_task`/`reconstruct`/`next_sequence_number`/`validate_case` 一行不改）。
- 不做跨 session 的多任务依赖编排（沿用现有逐 case 生成）。

## 关键决策（用户已拍板，2026-06-08）

- **D1 检索范围**：默认仅当前项目。**跨项目为线索驱动的主动询问**——skill 扫完当前项目发现信息不足、且发现线索指向其他项目可能有该信息时，**主动 AskUserQuestion 问是否跨项目搜集**；不依赖用户预先拨 `--all-projects` 开关。
- **D2 Codex 历史扫描上限**：默认近 90 天 + 条数上限，可调（逐 rollout 开首行有成本，必须限量 + 时间盒）。
- **D3 缺口挖掘深度**：轻挖优先（CLAUDE.md/README + 顶层扫），挖不到再深挖（grep 进代码找表名/字段）。**仅涉及当前项目的深挖不设确认门、直接挖**，只在实时 log 里展示当前阶段；跨项目信息收集仍走 D1 的主动询问。

## 既证事实（实现依据，已验证）

- **cwd 编码 bug**：`encode_cwd` 只做 `/`→`-`，但 Claude Code 把 `_`（及其它非字母数字）也编码成 `-`。实测 `…/defect_pipeline_service` 的目录是 `…-defect-pipeline-service`，当前实现定位失败。编码有损不可逆（`defect-pipeline-service` 反推不出原名）。
- **Codex 项目归属**：Codex sessions 平铺在 `~/.codex/sessions/YYYY/MM/DD/`，不按项目分目录。已验证每个 `rollout-*.jsonl` 首行 `session_meta.payload.cwd` 带工作目录 → 读首行即可把 Codex 历史归到项目。

## 实现单元

### U1：修 cwd 编码 + 真 cwd 兜底匹配（地基）

- **Goal**：让 Claude session 定位对任意含 `_`/`.` 的项目路径都成立。
- **Files**：
  - Modify `case-gen/scripts/session_extract.py`（`encode_cwd`）
  - Test `tests/test_session_extract_claude.py`（追加编码用例）
- **Approach**：`encode_cwd` 改为 `re.sub(r'[^a-zA-Z0-9]', '-', cwd)`。新增 `resolve_claude_project_dir(cwd, projects_base)`：先试编码目录；不存在则扫所有 project 目录、读各目录最新 session 首条记录的 `cwd` 字段做精确匹配（编码有损，兜底用真 cwd 才万无一失）。`locate_claude_session` 改用它。
- **Execution note**：test-first（编码规则是确定性纯函数，先写 `_`/`.`/混合用例）。
- **Patterns**：现有 `locate_claude_session` / `read_jsonl_tolerant`。
- **Test scenarios**：`encode_cwd` 对 `a_b`/`a.b`/`a/b_c.d` 的输出；兜底匹配在编码目录缺失时读真 cwd 命中；两条路径都查不到时 `SessionNotFound`。
- **Verification**：`python3 -m pytest tests/test_session_extract_claude.py`；对真实 `defect_pipeline_service` cwd 能定位到 `-defect-pipeline-service` 目录。

### U2：`session_index.py`——双端全历史枚举 + 名片 + 预筛（确定性检索脊梁）

- **Goal**：把"按项目枚举所有历史 session、为每个建轻量名片、按关键词预筛"做成纯确定性、可单测的库。
- **Files**：
  - Create `case-gen/scripts/session_index.py`
  - Test `tests/test_session_index.py`
- **Approach**（frozen dataclass + 多小文件，不塞进已 715 行的 session_extract）：
  - `SessionCard(frozen)`：`host / path / mtime / cwd / first_user_goal / files_touched / tools_used / terms`。
  - `enumerate_claude_sessions(cwd, projects_base, limit)`：用 U1 的目录解析，glob `*.jsonl`，mtime 倒序。
  - `enumerate_codex_sessions(cwd, sessions_base, *, days=90, limit=200)`：glob `**/rollout-*.jsonl`，mtime 倒序，逐文件**只读首行** `session_meta.payload.cwd` 匹配项目；过 `days`/`limit` 即停（D2 时间盒）。
  - `build_card(path, host)`：只读必要行（首个用户目标 + 文件/工具名），不全量 digest。
  - `prefilter(cards, terms) -> ranked`：关键词命中（goal/terms/files/tools）的廉价打分，返回 Top-K。
  - `scan_all_projects_for_terms(terms, *, days, limit) -> [(project_cwd, hit_score)]`：跨项目候选发现，**只为 D1 的线索检测**，同样只读首行 + 名片级，不解析正文。
- **Execution note**：test-first（纯确定性）。
- **Patterns**：`session_extract.read_jsonl_tolerant`、`locate_codex_session` 的 glob、`Digest` 的 frozen 风格。
- **Test scenarios**：Claude 枚举按 mtime 倒序且含全部；Codex 按 cwd 过滤、`days`/`limit` 生效；名片只读首行不崩在半写行；`prefilter` 命中排序；`scan_all_projects_for_terms` 返回跨项目候选且不读正文。
- **Verification**：`python3 -m pytest tests/test_session_index.py`；对本机 `~/.codex/sessions` 真数据 smoke 跑一次按 cwd 过滤。

### U3：SKILL.md——检索模式编排（双入口 + 精排 + 回显 + 缺口挖掘 + 兜底）

- **Goal**：把检索式流程写进 skill 大脑，复用后半程。
- **Files**：Modify `case-gen/SKILL.md`
- **Approach**：
  1. **入口分派**：触发参数为空 → 老的"当前 session 全量"模式；带描述 → 检索模式。触发描述扩写：`/session-to-eval <意图描述>` 即检索。
  2. **检索编排**：当前 session 名片预筛 → 不中则 `enumerate_*` 本项目历史 → `prefilter` Top-K → LLM 语义精排锁定 session+切片。
  3. **命中回显（铁律）**：重建前必回显"我认为指的是 `<path>`(<日期>)里那段 …，对吗？"，用户确认才动。
  4. **D1 跨项目主动询问**：本项目检索不足且 `scan_all_projects_for_terms` 有跨项目命中 → AskUserQuestion"本项目信息不足，发现 `<项目>` 可能有，是否跨项目搜集？"。用户同意才扩。
  5. **D3 缺口挖掘**：digest 缺输入/真值 → 轻挖（CLAUDE.md/README + 顶层）→ 挖不到深挖（grep 字段/表名）。**本项目深挖不问、直接挖，实时 log 展示阶段**（"[挖掘] 轻扫 CLAUDE.md…" / "[挖掘] 深挖 step2_analyze 字段…"）。产出取数配方 → `setup.sh` + `expected` 真值或精确 TODO。
  6. **AskUserQuestion 兜底**：挖不动时问得具体（带已挖到的表名/列名）。
- **Patterns**：现有 SKILL.md"编排"章节、`reconstruct` 的 setup_stub/ground_truth_external 约定。
- **Test scenarios**：N/A（prose 文档；逻辑正确性靠 U2 单测 + 真数据 smoke）。
- **Verification**：对真实"工单分级"需求端到端走查；`validate_case.py` 对产物绿。

### U4：真数据 smoke + 文档同步

- **Goal**：交付面向产物验证，且知识库对齐。
- **Files**：
  - Modify `README.md`（"从 session 自动蒸馏用例"节加检索模式一句话）
  - Modify `CLAUDE.md`（case-gen 节加 `session_index.py` 与检索模式）
- **Approach**：跑全 pytest + ruff；对本机真 session 跑一次检索式 smoke（CLI 入口），收集结果写进汇报。
- **Verification**：`python3 -m pytest`、`python3 -m ruff check bench/ case-gen/ tests/`、真数据 smoke 通过。

## 风险与依赖

- **全历史扫描放大脱敏面**：翻的 session 越多潜在密钥暴露越多 → 沿用 `scrub_secrets`，README 仍标"未审计"。
- **检索误判**：靠"命中回显确认"兜底。
- **Codex 逐文件读首行成本**：靠 D2 时间盒 + 上限。
- **深挖把项目代码读进上下文**：仅本项目、只读，受 D3 阶段日志透明化约束。
- 依赖 U1（地基）先于 U2/U3。

## 验证策略

- 单元：U1（编码/兜底）、U2（枚举/名片/预筛/跨项目候选）。
- 集成/真数据：本机 Claude + Codex 历史各 smoke 一次；对"工单分级"需求端到端产出草稿 case 并 `validate_case` 绿。
- 全量：`python3 -m pytest`、`ruff check`。

## 待实现时解决（execution-time unknowns）

- 关键词预筛的中文分词策略（先用空白/标点切 + 关键 term 直配，足够即可；不引第三方分词）。
- LLM 精排喂多少张名片为上限（先 Top-8，按 token 调）。
- `setup.sh` 取数配方的具体形态（SQL 模板 vs 注释说明），依挖到的项目约定定。
