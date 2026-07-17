"""计分卡生成 + 模型档案写入（R13/R14/R18/R21，KTD9/KTD11/KTD12）。

- 多模型并排：runner（启动器+模型）标签、四维、每维赢家、权衡摘要；
- 不自动聚合单一分（KTD12）：结论由用户判定；
- repeat N → 中位数 + 离散度，check 算 pass 率；
- 比较口径声明 harness 为已知混淆变量；
- 进入可分享 markdown 前对裁判理由脱敏。
"""

from __future__ import annotations

import html
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .case import Case
from .comparison import RunnerComparison, compare_case_variants
from .completion import CellCompletion, cell_completion, runner_completion
from .orchestrator import MatrixResult
from .record import RunRecord
from .scrub import scrub_text, scrub_truncate

_DASH = "—"


def _md_cell(value: object) -> str:
    """脱敏并转义 Markdown 表格中的不可信单元格文本。"""
    return (
        html.escape(scrub_text(str(value)), quote=True)
        .replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("`", "\\`")
        .replace("\r\n", "<br>")
        .replace("\r", "<br>")
        .replace("\n", "<br>")
    )

# 用例名前缀 `YYYY-MM-DD-NNN-`（日期+序号）对文件名是噪音，留尾巴即可。
_CASE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-\d+-")


def _case_slug(name: str) -> str:
    return _CASE_PREFIX_RE.sub("", name)


def scorecard_filename(today: str, case_names: list[str], runner_labels: list[str]) -> str:
    """`{date}-{cases}-{runners}.md`——同日不同组合不再互相覆盖。

    适度简写防爆名：用例多于 1 个 → `{首个}+{余数}`；runner 多于 3 个 → `{N}runners`。
    """
    cases = sorted(set(case_names))
    runners = sorted(set(runner_labels))
    if not cases:
        cpart = "nocase"
    elif len(cases) == 1:
        cpart = _case_slug(cases[0])
    else:
        cpart = f"{_case_slug(cases[0])}+{len(cases) - 1}"
    if not runners:
        rpart = "norunner"
    elif len(runners) <= 3:
        rpart = "+".join(runners)
    else:
        rpart = f"{len(runners)}runners"
    return f"{today}-{cpart}-{rpart}.md"


def unique_scorecard_path(sc_dir: Path, filename: str) -> Path:
    """文件名占用时追加 `-2`/`-3`……，identical 组合重跑也不覆盖旧卡。"""
    path = sc_dir / filename
    if not path.exists():
        return path
    stem = path.stem
    n = 2
    while True:
        candidate = sc_dir / f"{stem}-{n}{path.suffix}"
        if not candidate.exists():
            return candidate
        n += 1


def _median(values: list[float]) -> float | None:
    nums = [v for v in values if v is not None]
    return statistics.median(nums) if nums else None


def _fmt(values: list[float | None], *, as_int: bool = False) -> str:
    """中位数 [min–max]；全 None → —。"""
    nums = [v for v in values if v is not None]
    if not nums:
        return _DASH
    med = statistics.median(nums)
    show = f"{int(round(med))}" if as_int else f"{med:g}"
    if len(nums) > 1 and min(nums) != max(nums):
        lo = int(round(min(nums))) if as_int else f"{min(nums):g}"
        hi = int(round(max(nums))) if as_int else f"{max(nums):g}"
        return f"{show} [{lo}–{hi}]"
    return show


@dataclass
class CellAgg:
    runner_label: str
    variant_label: str
    runner_model: str
    samples: int
    execution_successes: int
    durations: list[float | None]
    in_tokens: list[float | None]
    out_tokens: list[float | None]
    costs: list[float | None]
    files_changed: list[float | None]
    check_pass_rate: float | None  # 0..1, None if no check
    check_samples: int
    judge_scores: list[float | None]
    judge_model: str
    reasonings: list[str]

    @property
    def duration_med(self) -> float | None:
        return _median(self.durations)

    @property
    def cost_med(self) -> float | None:
        return _median(self.costs)

    @property
    def judge_score_med(self) -> float | None:
        return _median(self.judge_scores)


def _aggregate(records: list[RunRecord]) -> CellAgg:
    evaluated = [r for r in records if not r.is_error and r.check.ran]
    checks = [r.check.passed for r in evaluated]
    pass_rate = (sum(1 for c in checks if c) / len(checks)) if checks else None
    j_model = next(
        (r.judge.model for r in records if not r.is_error and r.judge and r.judge.model), ""
    )
    return CellAgg(
        runner_label=records[0].runner_label,
        variant_label=records[0].variant_label,
        runner_model=records[0].runner_model,
        samples=len(records),
        execution_successes=sum(not r.is_error for r in records),
        durations=[float(r.duration_ms) for r in records],
        in_tokens=[r.usage.effective_input if r.usage else None for r in records],  # 含缓存的真实输入
        out_tokens=[r.usage.output_tokens if r.usage else None for r in records],
        costs=[r.usage.cost_usd if r.usage else None for r in records],
        files_changed=[float(r.agentic.files_changed) for r in records],
        check_pass_rate=pass_rate,
        check_samples=len(checks),
        judge_scores=[
            r.judge.score
            for r in records
            if not r.is_error and r.judge and r.judge.ran and r.judge.score is not None
        ],
        judge_model=j_model,
        reasonings=[
            r.judge.reasoning
            for r in records
            if not r.is_error and r.judge and r.judge.reasoning
        ],
    )


def _winners(cells: list[CellAgg]) -> dict[str, str]:
    """每维赢家（仅 quality/latency/cost；agentic 为无方向诊断不评赢家）。"""
    out: dict[str, str] = {}
    # 存在执行失败时，耗时/成本可能只是“快速失败”，check/judge 也只是幸存样本；
    # 表格保留这些实际尝试数据，但不授予赢家，避免奖励不可靠 cell。
    eligible = [c for c in cells if c.execution_successes == c.samples]

    # quality：优先 check pass 率，其次 judge 分
    rated = [
        c for c in eligible if c.check_pass_rate is not None or c.judge_score_med is not None
    ]
    if rated:
        def q_key(c: CellAgg):
            return (
                c.check_pass_rate if c.check_pass_rate is not None else -1,
                c.judge_score_med if c.judge_score_med is not None else -1,
            )
        best_quality = max(q_key(cell) for cell in rated)
        quality_winners = [cell for cell in rated if q_key(cell) == best_quality]
        if len(quality_winners) == 1:
            out["quality"] = _cell_label(quality_winners[0])

    lat = [c for c in eligible if c.duration_med is not None]
    if lat:
        best_latency = min(c.duration_med for c in lat)
        latency_winners = [c for c in lat if c.duration_med == best_latency]
        if len(latency_winners) == 1:
            out["latency"] = _cell_label(latency_winners[0])

    cost = [c for c in eligible if c.cost_med is not None]
    # 只有 ≥2 个 runner 报了成本数据，选「最省」才有意义；
    # 否则（例如只有 claude 启动器的 2 个模型有成本，c 路由的全部显示「—」）
    # 会产生"opus 最省"这种从残缺数据得出的误导结论。
    if len(cost) >= 2:
        best_cost = min(c.cost_med for c in cost)
        cost_winners = [c for c in cost if c.cost_med == best_cost]
        if len(cost_winners) == 1:
            out["cost"] = _cell_label(cost_winners[0])

    return out


def _bundle_label(cell: CellAgg) -> str:
    return f"{cell.runner_label}" + (f" ({cell.runner_model})" if cell.runner_model else "")


def _cell_label(cell: CellAgg) -> str:
    return f"{cell.runner_label}@{cell.variant_label}"


def _truncate(text: str, limit: int) -> str:
    text = text.strip().replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _task_brief(case: Case) -> tuple[str, str]:
    """从 task.md 抽 (标题, 一句话简介)：首个 # 标题 + 其后第一段正文行。"""
    title, brief = "", ""
    for line in case.task.prompt.splitlines():
        s = line.strip()
        if not s:
            continue
        if not title and s.startswith("#"):
            title = s.lstrip("# ").strip()
            continue
        if title and not s.startswith(("#", ">", "|", "```")):
            brief = s
            break
    return (title or case.name), brief


def _input_summary(case: Case) -> str:
    d = case.input_dir
    files = sorted(p.name for p in d.iterdir() if p.is_file()) if d.is_dir() else []
    if not files:
        return "无（纯 prompt 任务）"
    shown = "、".join(f"`{_md_cell(f)}`" for f in files[:6])
    tail = f" 等 {len(files)} 项" if len(files) > 6 else ""
    return f"input/ {len(files)} 份：{shown}{tail}"


def _expected_output_summary(case: Case) -> str:
    return "任务定义指定的结果"


def _judging_summary(case: Case) -> str:
    parts = []
    if case.check.type == "script":
        parts.append(f"确定性 check（`{_md_cell(case.check.script)}`）")
    else:
        parts.append("无确定性 check")
    if case.judge.enabled:
        dims = "、".join(_md_cell(value) for value in case.judge.dimensions) or "—"
        parts.append(f"judge {len(case.judge.dimensions)} 维（{dims}）")
    exp = case.expected or {}
    comp = (exp.get("completion") or {}).get("core_dimensions") or {}
    if comp:
        crit = "、".join(f"{_md_cell(k)}≥{_md_cell(v)}" for k, v in comp.items())
        parts.append(f"完成度=核心维 {crit}")
    elif case.check.type == "script":
        parts.append("完成度=check 通过")
    elif exp.get("passing_threshold") is not None:
        mx = exp.get("max_score")
        parts.append(f"完成度=judge ≥ {exp['passing_threshold']}" + (f"/{mx}" if mx else ""))
    return "；".join(parts)


def _task_card(case: Case, run_id: str) -> list[str]:
    """单用例的「任务说明卡」：简介 + 输入/期望产出/判分 + 完整输出指引。"""
    title, brief = _task_brief(case)
    head = f"**{_md_cell(title)}**" + (f" — {_md_cell(brief)}" if brief else "")
    return [
        "### 📋 任务说明",
        "",
        head,
        "",
        "| 项 | 内容 |",
        "|---|---|",
        f"| 类型 | {_md_cell(case.class_)} |",
        f"| 输入 | {_input_summary(case)} |",
        f"| 期望产出 | {_expected_output_summary(case)} |",
        f"| 判分 | {_judging_summary(case)} |",
        "",
        f"📂 **本地完整输出（原始证据，不可外发）**：`runs/{_md_cell(run_id)}/cells/{_md_cell(case.name)}/"
        "<variant>/<runner>/repeat-<N>/`"
        "（相对评测产物根；本计分卡原件与 cells/ 同级），"
        "其中 `artifacts/OUTPUT.txt` 为模型最终答案，`run.json` 为结构化记录，"
        "`raw.txt` 为脱敏启动器流。",
        "",
    ]


def build_scorecard(
    result: MatrixResult,
    *,
    judge_label: str = "claude",
    cases: dict[str, Case] | None = None,
    comparisons: dict[str, list[RunnerComparison]] | None = None,
) -> str:
    """从矩阵结果生成可分享 markdown 计分卡。

    传入 `cases`（name→Case）即可算「任务完成度」列（核心结果信号）；省略则退化为旧版四维表。
    """
    cases = cases or {}
    by_case: dict[str, dict[tuple[str, str], list[RunRecord]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for rec in result.records:
        by_case[rec.case][(rec.variant_label, rec.runner_label)].append(rec)
    if comparisons is None:
        comparisons = {
            case_name: compare_case_variants(case, by_case.get(case_name, {}))
            for case_name, case in cases.items()
            if case.evaluation.comparison is not None
        }

    # 预算各 case×runner 完成度（需 case 对象）；comp_accum 供跨 case 汇总表
    comp_by_case: dict[str, dict[tuple[str, str], CellCompletion]] = {}
    comp_accum: dict[str, list[CellCompletion]] = defaultdict(list)
    for case_name, case_records in by_case.items():
        case_obj = cases.get(case_name)
        if case_obj is None:
            continue
        m: dict[tuple[str, str], CellCompletion] = {}
        for key, recs in case_records.items():
            cc = cell_completion(recs, case_obj)
            m[key] = cc
            variant, label = key
            comp_accum[f"{label}@{variant}"].append(cc)
        comp_by_case[case_name] = m

    skipped_by_case: dict[str, list] = defaultdict(list)
    for sk in result.skipped:
        skipped_by_case[sk.case].append(sk)

    all_cases = sorted(set(by_case) | set(skipped_by_case))
    runner_set = sorted({r.runner_label for r in result.records})

    lines: list[str] = []
    lines.append(f"# 计分卡 ({date.today().isoformat()})")
    lines.append("")
    lines.append(
        "> **比较口径**：每行是「启动器 + 模型」捆绑，不是裸模型；"
        "harness（system prompt / 工具 / agent loop）是已知混淆变量，"
        "结论勿上升为裸模型优劣。"
    )
    lines.append(
        "> **分享边界**：本 scorecard.md 是默认可分享摘要；`report.html` 可能包含"
        "任务输入与模型输出正文，须按 case 保密级别保存；`cells/`、`run.json` 与 "
        "`artifacts/` 不可直接外发。"
    )
    lines.append(
        f"> **裁判**：{_md_cell(judge_label)}（advisory；有 check 时以 check pass 率为质量锚）。"
        " **结论不自动聚合**——每维赢家仅供参考，最终选择由你判定。"
    )
    lines.append(
        f"> 参与 runner：{', '.join(_md_cell(value) for value in runner_set) or '(无)'}；"
        f"用例数：{len(all_cases)}。"
    )
    lines.append("")

    # 多用例：顶部任务总览表（一句话 + 指向下方各用例详情）
    overview = [cn for cn in all_cases if cn in cases]
    if len(overview) > 1:
        lines.append("## 任务总览")
        lines.append("")
        lines.append("| 用例 | 简介 |")
        lines.append("|---|---|")
        for cn in overview:
            title, brief = _task_brief(cases[cn])
            one = title + (f" — {brief}" if brief else "")
            lines.append(f"| `{_md_cell(cn)}` | {_md_cell(_truncate(one, 80))} |")
        lines.append("")

    # 跨用例「任务完成率」汇总（多用例时最直观的横排结果信号；单用例时见下方表内列即可）
    if len(all_cases) > 1 and comp_accum:
        summary = [runner_completion(label, cs) for label, cs in comp_accum.items()]
        summary.sort(key=lambda rc: (rc.rate if rc.rate is not None else -1.0), reverse=True)
        lines.append("## 任务完成率（跨用例汇总）")
        lines.append("")
        lines.append(
            "> 完成 = 对用例权威判据（check 通过 / judge ≥ 阈值 / 核心判据）的通过；各用例等权，"
            "无判据的用例不计入分母。"
        )
        lines.append("")
        lines.append("| Runner (启动器+模型) | 完成/适用 (率) |")
        lines.append("|---|---|")
        for rc in summary:
            lines.append(f"| {_md_cell(rc.runner_label)} | {_md_cell(rc.display)} |")
        lines.append("")

    for case_name in all_cases:
        lines.append(f"## 用例: {_md_cell(case_name)}")
        lines.append("")
        case_obj = cases.get(case_name)
        if case_obj is not None:
            lines.extend(
                _task_card(case_obj, result.run_id)
            )  # 任务简介 + 输入/输出/判分 + 完整输出指引
            scope = case_obj.evaluation.scope
            role = case_obj.evaluation.role
            if role:
                lines.append(f"> **评测角色**：`{_md_cell(role)}`。")
            if scope:
                lines.append(f"> **评测范围**：`{_md_cell(scope)}`。")
            if case_obj.evaluation.generalizes is False:
                lines.append("> 本用例不是泛化证据，不用于宣称总体效果或通用赢家。")
            if case_obj.schema_version >= 2:
                lines.append(
                    "> **统计口径**：分析单位 "
                    f"`{_md_cell(case_obj.evaluation.unit_of_analysis)}`，独立单位 "
                    f"`{_md_cell(case_obj.evaluation.independent_unit)}`；"
                    "下表“运行轮次”不是独立样本量。"
                )
            if role or scope or case_obj.schema_version >= 2:
                lines.append("")
        case_records = by_case.get(case_name, {})
        cells = [_aggregate(recs) for recs in case_records.values()]
        cells.sort(key=lambda c: (c.runner_label, c.variant_label))
        comp_by_runner = comp_by_case.get(case_name, {})

        lines.append(
            "| Runner (启动器+模型) | Variant | 运行轮次 | 执行成功 | 任务完成 | check pass | judge 分 | 耗时(ms) | "
            "tokens(in✦/out) | cost($) | files_changed◇ |"
        )
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for c in cells:
            comp_key = (c.variant_label, c.runner_label)
            comp_str = comp_by_runner[comp_key].display if comp_key in comp_by_runner else _DASH
            pass_str = (
                f"{int(round(c.check_pass_rate * c.check_samples))}/{c.check_samples}"
                if c.check_pass_rate is not None
                else _DASH
            )
            judge_str = _fmt(c.judge_scores) if c.judge_score_med is not None else _DASH
            has_tokens = any(t is not None for t in c.in_tokens + c.out_tokens)
            tok_str = (
                f"{_fmt(c.in_tokens, as_int=True)}/{_fmt(c.out_tokens, as_int=True)}"
                if has_tokens
                else _DASH
            )
            lines.append(
                f"| {_md_cell(_bundle_label(c))} | `{_md_cell(c.variant_label)}` | {c.samples} | "
                f"{c.execution_successes}/{c.samples} | {comp_str} | {pass_str} | {judge_str} | "
                f"{_fmt(c.durations, as_int=True)} | {tok_str} | "
                f"{_fmt(c.costs)} | {_fmt(c.files_changed, as_int=True)} |"
            )
        for sk in skipped_by_case.get(case_name, []):
            lines.append(
                f"| {_md_cell(sk.runner_label)} | `{_md_cell(sk.variant_label)}` | — | "
                "N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A |"
            )
        lines.append("")
        lines.append(
            "✦ in 为真实总输入（含缓存读写 cache_read/creation）；cost 仅对真 Anthropic 计费的 claude 启动器显示，"
            "c 路由的第三方/本地模型显示「—」（claude 自报 cost 是按 Claude 定价的影子，非真实成本）。"
        )
        lines.append("◇ files_changed 为无方向诊断量（含创建/修改/删除），仅与 check/judge 并读，不单独评优劣。")
        lines.append("")

        winners = (
            {}
            if case_obj is not None
            and case_obj.evaluation.generalizes is False
            else _winners(cells)
        )
        if winners:
            parts = []
            if "quality" in winners:
                parts.append(f"质量={winners['quality']}")
            if "latency" in winners:
                parts.append(f"速度={winners['latency']}")
            if "cost" in winners:
                cost_cells = [c for c in cells if c.cost_med is not None]
                ratio = f"{len(cost_cells)}/{len(cells)}"
                parts.append(f"成本={winners['cost']}（仅 {ratio} 有数据）")
            lines.append(f"**每维赢家**：{_md_cell('，'.join(parts))}")
            lines.append("")
            lines.append(f"**权衡**：{_md_cell(_tradeoff_sentence(winners))} 结论由你判定。")
            lines.append("")

        # 裁判理由（脱敏）
        reasoning_cells = [c for c in cells if c.reasonings]
        if reasoning_cells:
            lines.append("<details><summary>裁判理由（advisory，已脱敏）</summary>")
            lines.append("")
            for c in reasoning_cells:
                for rsn in c.reasonings:
                    lines.append(
                        f"- **{_md_cell(_cell_label(c))}**: "
                        f"{_md_cell(scrub_truncate(rsn, 600))}"
                    )
            lines.append("")
            lines.append("</details>")
            lines.append("")

        if case_obj is not None:
            lines.extend(_render_variant_comparisons(comparisons.get(case_name, [])))

    return "\n".join(lines)


def _render_variant_comparisons(comparisons: list[RunnerComparison]) -> list[str]:
    lines: list[str] = []
    for comparison in comparisons:
        if comparison.unavailable_reason:
            lines.extend(
                [
                    "### Variant 配对比较",
                    "",
                    f"Runner `{_md_cell(comparison.runner)}`：paired comparison unavailable；"
                    f"{_md_cell(comparison.unavailable_reason)}。",
                    "",
                ]
            )
            continue
        if comparison.method == "item_value_diff":
            lines.extend(
                [
                    "### Variant 配对比较",
                    "",
                    f"Runner `{_md_cell(comparison.runner)}`："
                    f"baseline=`{_md_cell(comparison.baseline)}`，"
                    f"candidate=`{_md_cell(comparison.candidate)}`；分歧={comparison.changed}/"
                    f"{len(comparison.rows) - comparison.unavailable_items}；"
                    f"不可比较={comparison.unavailable_items}。",
                    "",
                    "> 数据库 baseline 快照仅作分层与参照，不是真值；本表只描述两种 prompt "
                    "的输出差异，优劣需人工复核。逐条业务值仅在受 case 保密级别约束的 "
                    "report.html 中展示。",
                    "",
                ]
            )
            continue
        lines.extend(
            [
                "### Variant 配对比较",
                "",
                    f"Runner `{_md_cell(comparison.runner)}`："
                    f"baseline=`{_md_cell(comparison.baseline)}`，"
                    f"candidate=`{_md_cell(comparison.candidate)}`；"
                    f"strict fixed={comparison.fixed}，"
                f"regressed={comparison.regressed}。",
                "",
                    f"> 方法 `{_md_cell(comparison.method)}`；"
                    f"分析单位 `{_md_cell(comparison.unit_of_analysis)}`，"
                    f"独立单位 `{_md_cell(comparison.independent_unit)}`。"
                    "结果仅作描述性比较，不把条目或重复运行"
                "当作独立样本，不做显著性推断。",
                "",
                "| ID | Baseline correct | Candidate correct | Baseline disagreement | Candidate disagreement | 状态 |",
                "|---|---:|---:|---:|---:|---|",
            ]
        )
        for row in comparison.rows:
            lines.append(
                f"| `{_md_cell(row.item_id)}` | {row.baseline_correct}/{comparison.repeats} | "
                f"{row.candidate_correct}/{comparison.repeats} | "
                f"{row.baseline_disagreement:.3f} | {row.candidate_disagreement:.3f} | "
                f"{_md_cell(row.status)} |"
            )
        lines.append("")
    return lines


def _tradeoff_sentence(winners: dict[str, str]) -> str:
    uniq = set(winners.values())
    if len(uniq) == 1:
        only = next(iter(uniq))
        return f"{only} 在所评维度上全面占优；仍需结合你对各维度的权重判断。"
    bits = []
    if "quality" in winners:
        bits.append(f"{winners['quality']} 质量最高")
    if "latency" in winners:
        bits.append(f"{winners['latency']} 最快")
    if "cost" in winners:
        bits.append(f"{winners['cost']} 最省")
    return "；".join(bits) + "——各有所长。"


# ── 模型档案写入入口（R14）────────────────────────────
_PROFILE_TEMPLATE = """# {label}

> 模型档案。记录该「启动器+模型」捆绑在本 benchmark 中的真实表现。

## 擅长场景

## 不擅长场景

## 关键差异

## 评测记录
"""


def write_model_profile(models_dir: str | Path, label: str, summary: str) -> Path:
    """把一次运行表现追加到 models/<label>.md（遵循 README 档案格式）。"""
    root = Path(models_dir)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{label}.md"
    if not path.exists():
        path.write_text(_PROFILE_TEMPLATE.format(label=label), encoding="utf-8")
    content = path.read_text(encoding="utf-8")
    entry = f"\n- {date.today().isoformat()}: {summary.strip()}"
    if "## 评测记录" in content:
        content = content.rstrip() + entry + "\n"
    else:
        content = content.rstrip() + "\n\n## 评测记录\n" + entry + "\n"
    path.write_text(content, encoding="utf-8")
    return path
