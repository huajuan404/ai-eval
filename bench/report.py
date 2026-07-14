"""自包含 HTML 报告 —— 一次评测任务的全部结果，一页看清。

设计约定：
- 单文件、零外部依赖（内联 CSS，原生 <details> 展开，无 JS/CDN）；
  直接 file:// 打开即可，raw.txt / artifacts 用相对链接指向同目录 cells/。
- 三个比较轴统一呈现：Runner（模型选型）、Prompt variant（方案迭代）、
  Data item（数据集条目，来自 check report 的 items[]）。
- 与计分卡同一数据源（RunRecord / RunnerComparison / completion），只做渲染不做新统计。
"""

from __future__ import annotations

import html
import json
from collections import defaultdict
from datetime import date
from pathlib import Path

from .case import Case
from .comparison import RunnerComparison
from .completion import CellCompletion, cell_completion, runner_completion
from .layout import RunLayout
from .record import RunRecord
from .scorecard import (
    CellAgg,
    _aggregate,
    _cell_label,
    _fmt,
    _judging_summary,
    _task_brief,
    _winners,
)

_DASH = "—"


class ReportError(ValueError):
    """HTML 报告生成或重建失败。"""


def _e(value: object) -> str:
    return html.escape(str(value), quote=True)


def _completion_class(comp: CellCompletion) -> str:
    if comp.rate is None:
        return "na"
    if comp.rate >= 1:
        return "ok"
    if comp.rate <= 0:
        return "bad"
    return "warn"


def _cell_href(record: RunRecord) -> str:
    return (
        f"cells/{record.case}/{record.variant_label}/"
        f"{record.runner_label}/repeat-{record.repeat_index}/"
    )


_CSS = """
:root{--bg:#ffffff;--fg:#1b1f24;--muted:#667085;--line:#e4e7ec;--card:#f8fafc;
--ok:#158a44;--ok-bg:#e7f6ec;--bad:#c9312b;--bad-bg:#fdebea;--warn:#b96b00;
--warn-bg:#fdf3e2;--na:#98a2b3;--na-bg:#f2f4f7;--accent:#175cd3;}
@media (prefers-color-scheme: dark){:root{--bg:#101418;--fg:#e6e9ee;--muted:#98a2b3;
--line:#2b3440;--card:#171d24;--ok:#5cc98a;--ok-bg:#12301e;--bad:#f08981;--bad-bg:#3a1715;
--warn:#e8ab52;--warn-bg:#332405;--na:#7a8699;--na-bg:#1d2530;--accent:#7ab3ff;}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",
"Hiragino Sans GB","Microsoft YaHei",sans-serif;padding:0 24px 64px;}
main{max-width:1100px;margin:0 auto}
h1{font-size:24px;margin:32px 0 4px}
h2{font-size:19px;margin:40px 0 12px;padding-top:16px;border-top:1px solid var(--line)}
h3{font-size:16px;margin:24px 0 8px}
.meta{color:var(--muted);font-size:13px;margin-bottom:8px}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}
.chip{background:var(--card);border:1px solid var(--line);border-radius:999px;
padding:2px 12px;font-size:13px;color:var(--muted)}
.chip b{color:var(--fg)}
.tablewrap{overflow-x:auto;border:1px solid var(--line);border-radius:8px;margin:8px 0}
table{border-collapse:collapse;width:100%;font-size:13.5px}
th,td{padding:7px 12px;text-align:left;border-bottom:1px solid var(--line);
white-space:nowrap;vertical-align:top}
tr:last-child td{border-bottom:none}
th{background:var(--card);font-weight:600;position:sticky;top:0}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.badge{display:inline-block;border-radius:6px;padding:1px 8px;font-size:12.5px;font-weight:600}
.badge.ok{color:var(--ok);background:var(--ok-bg)}
.badge.bad{color:var(--bad);background:var(--bad-bg)}
.badge.warn{color:var(--warn);background:var(--warn-bg)}
.badge.na{color:var(--na);background:var(--na-bg)}
.note{color:var(--muted);font-size:13px;margin:6px 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;
padding:12px 16px;margin:10px 0}
details{border:1px solid var(--line);border-radius:8px;margin:8px 0;background:var(--card)}
details>summary{cursor:pointer;padding:8px 14px;font-weight:600;font-size:13.5px}
details>.body{padding:0 14px 12px}
pre{background:var(--bg);border:1px solid var(--line);border-radius:6px;
padding:10px;font-size:12.5px;overflow-x:auto;white-space:pre-wrap;word-break:break-word}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
code{background:var(--na-bg);border-radius:4px;padding:0 5px;font-size:12.5px}
.winner{font-weight:600}
"""


def _chip(label: str, value: object) -> str:
    return f'<span class="chip">{_e(label)} <b>{_e(value)}</b></span>'


def _badge(text: str, klass: str) -> str:
    return f'<span class="badge {klass}">{_e(text)}</span>'


def _completion_badge(comp: CellCompletion) -> str:
    return _badge(comp.display, _completion_class(comp))


def _render_summary(
    by_case: dict[str, dict[tuple[str, str], list[RunRecord]]],
    cases: dict[str, Case],
    case_names: list[str],
) -> str:
    """完成率总览矩阵：行 = runner@variant，列 = case，末列 = 跨用例汇总。"""
    comp: dict[str, dict[str, CellCompletion]] = defaultdict(dict)
    for case_name in case_names:
        case_obj = cases.get(case_name)
        if case_obj is None:
            continue
        for (variant, runner), recs in by_case.get(case_name, {}).items():
            comp[f"{runner}@{variant}"][case_name] = cell_completion(recs, case_obj)
    if not comp:
        return ""
    rows: list[str] = []
    for label in sorted(comp):
        cells = comp[label]
        summary = runner_completion(label, list(cells.values()))
        tds = "".join(
            f"<td>{_completion_badge(cells[cn]) if cn in cells else _DASH}</td>"
            for cn in case_names
        )
        rows.append(
            f"<tr><td><code>{_e(label)}</code></td>{tds}"
            f"<td>{_e(summary.display)}</td></tr>"
        )
    heads = "".join(f"<th>{_e(cn)}</th>" for cn in case_names)
    return (
        "<h2>任务完成率总览</h2>"
        '<p class="note">完成 = 通过用例权威判据（check 通过 / judge ≥ 阈值 / 核心维达标）；'
        "「未评」不冤判为未完成，也不计入分母。</p>"
        '<div class="tablewrap"><table><thead><tr><th>Runner@Variant</th>'
        f"{heads}<th>跨用例汇总</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
    )


def _render_metrics_table(
    cells: list[CellAgg],
    completion: dict[tuple[str, str], CellCompletion],
    winners: dict[str, str],
) -> str:
    rows: list[str] = []
    for c in cells:
        comp = completion.get((c.variant_label, c.runner_label))
        comp_html = _completion_badge(comp) if comp else _DASH
        pass_str = (
            f"{int(round(c.check_pass_rate * c.samples))}/{c.samples}"
            if c.check_pass_rate is not None
            else _DASH
        )
        has_tokens = any(t is not None for t in c.in_tokens + c.out_tokens)
        tok = (
            f"{_fmt(c.in_tokens, as_int=True)} / {_fmt(c.out_tokens, as_int=True)}"
            if has_tokens
            else _DASH
        )
        label = _cell_label(c)
        mark = " 🏆" if label in winners.values() else ""
        model = f' <span class="note">({_e(c.runner_model)})</span>' if c.runner_model else ""
        rows.append(
            f'<tr><td><code>{_e(c.runner_label)}</code>{model}</td>'
            f"<td><code>{_e(c.variant_label)}</code>{mark}</td>"
            f'<td class="num">{c.samples}</td><td>{comp_html}</td>'
            f'<td class="num">{_e(pass_str)}</td>'
            f'<td class="num">{_e(_fmt(c.judge_scores))}</td>'
            f'<td class="num">{_e(_fmt(c.durations, as_int=True))}</td>'
            f'<td class="num">{_e(tok)}</td>'
            f'<td class="num">{_e(_fmt(c.costs))}</td>'
            f'<td class="num">{_e(_fmt(c.files_changed, as_int=True))}</td></tr>'
        )
    return (
        '<div class="tablewrap"><table><thead><tr>'
        "<th>Runner</th><th>Variant</th><th class='num'>轮次</th><th>任务完成</th>"
        "<th class='num'>check</th><th class='num'>judge</th><th class='num'>耗时(ms)</th>"
        "<th class='num'>tokens in/out</th><th class='num'>cost($)</th>"
        "<th class='num'>files±</th>"
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
    )


def _render_comparisons(comparisons: list[RunnerComparison]) -> str:
    if not comparisons:
        return ""
    parts = ["<h3>Prompt 轴：variant 配对比较</h3>"]
    for comparison in comparisons:
        if comparison.unavailable_reason:
            parts.append(
                f'<p class="note">Runner <code>{_e(comparison.runner)}</code>：'
                f"配对比较不可用 — {_e(comparison.unavailable_reason)}。</p>"
            )
            continue
        parts.append(
            f'<p>Runner <code>{_e(comparison.runner)}</code>：'
            f"baseline=<code>{_e(comparison.baseline)}</code> → "
            f"candidate=<code>{_e(comparison.candidate)}</code>，"
            f"{_badge(f'fixed {comparison.fixed}', 'ok')} "
            f"{_badge(f'regressed {comparison.regressed}', 'bad' if comparison.regressed else 'na')}"
            "</p>"
            '<p class="note">描述性比较，条目 / 重复运行不当作独立样本，不做显著性推断。</p>'
        )
        rows = []
        for row in comparison.rows:
            status_class = {"fixed": "ok", "regressed": "bad"}.get(row.status, "na")
            rows.append(
                f"<tr><td><code>{_e(row.item_id)}</code></td>"
                f'<td class="num">{row.baseline_correct}/{comparison.repeats}</td>'
                f'<td class="num">{row.candidate_correct}/{comparison.repeats}</td>'
                f'<td class="num">{row.baseline_disagreement:.3f}</td>'
                f'<td class="num">{row.candidate_disagreement:.3f}</td>'
                f"<td>{_badge(row.status, status_class)}</td></tr>"
            )
        parts.append(
            '<div class="tablewrap"><table><thead><tr><th>Item</th>'
            "<th class='num'>baseline 对</th><th class='num'>candidate 对</th>"
            "<th class='num'>baseline 分歧</th><th class='num'>candidate 分歧</th>"
            f"<th>状态</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
        )
    return "".join(parts)


def _render_items_grid(case_records: dict[tuple[str, str], list[RunRecord]]) -> str:
    """数据轴：item × (runner@variant) 通过网格（来自 check report 的 items[]）。"""
    columns: list[tuple[str, str]] = []
    per_col: dict[tuple[str, str], dict[str, tuple[int, int]]] = {}
    for key in sorted(case_records):
        stats: dict[str, list[bool]] = defaultdict(list)
        for record in case_records[key]:
            for item in record.check.report.get("items") or []:
                stats[str(item["id"])].append(bool(item["correct"]))
        if stats:
            columns.append(key)
            per_col[key] = {i: (sum(v), len(v)) for i, v in stats.items()}
    if not columns:
        return ""
    item_ids = sorted({i for col in per_col.values() for i in col})
    heads = "".join(
        f"<th class='num'>{_e(f'{runner}@{variant}')}</th>" for variant, runner in columns
    )
    rows = []
    for item_id in item_ids:
        tds = []
        for key in columns:
            got = per_col[key].get(item_id)
            if got is None:
                tds.append(f"<td>{_DASH}</td>")
                continue
            passed, total = got
            klass = "ok" if passed == total else ("bad" if passed == 0 else "warn")
            tds.append(f"<td class='num'>{_badge(f'{passed}/{total}', klass)}</td>")
        rows.append(f"<tr><td><code>{_e(item_id)}</code></td>{''.join(tds)}</tr>")
    return (
        "<h3>数据轴：item 级明细</h3>"
        '<p class="note">每格 = 该条数据在此 runner@variant 下通过的 repeat 数；'
        "哪类输入拖垮了哪个组合一目了然。</p>"
        '<div class="tablewrap"><table><thead><tr><th>Item</th>'
        f"{heads}</tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
    )


def _render_cell_details(records: list[RunRecord]) -> str:
    parts = ["<h3>逐格明细</h3>"]
    for record in sorted(
        records, key=lambda r: (r.runner_label, r.variant_label, r.repeat_index)
    ):
        status = _badge("错误", "bad") if record.is_error else _badge("正常", "ok")
        check = (
            _badge("check ✓", "ok")
            if record.check.passed
            else (_badge("check ✗", "bad") if record.check.ran else _badge("无 check", "na"))
        )
        judge = (
            _badge(
                f"judge {record.judge.score:g}"
                + (f"/{record.judge.max:g}" if record.judge.max else ""),
                "na",
            )
            if record.judge and record.judge.ran and record.judge.score is not None
            else ""
        )
        href = _cell_href(record)
        summary = (
            f"<code>{_e(record.runner_label)}</code> · "
            f"<code>{_e(record.variant_label)}</code> · repeat-{record.repeat_index} "
            f"　{status} {check} {judge}"
        )
        body: list[str] = [
            f'<p class="note">耗时 {record.duration_ms} ms · exit={record.exit_code}'
            + (
                f" · tokens {record.usage.effective_input or _DASH}/"
                f"{record.usage.output_tokens or _DASH}"
                if record.usage
                else ""
            )
            + f' · <a href="{_e(href)}run.json">run.json</a>'
            f' · <a href="{_e(href)}raw.txt">raw.txt</a>'
            f' · <a href="{_e(href)}artifacts/">artifacts/</a></p>'
        ]
        if record.check.detail:
            body.append(f"<p>check 详情：</p><pre>{_e(record.check.detail)}</pre>")
        if record.judge and record.judge.reasoning:
            dims = (
                f'<p class="note">维度分：<code>{_e(json.dumps(record.judge.dimensions, ensure_ascii=False))}</code></p>'
                if record.judge.dimensions
                else ""
            )
            body.append(
                f"{dims}<p>裁判理由（advisory，已脱敏）：</p>"
                f"<pre>{_e(record.judge.reasoning)}</pre>"
            )
        parts.append(
            f"<details><summary>{summary}</summary>"
            f'<div class="body">{"".join(body)}</div></details>'
        )
    return "".join(parts)


def build_report_html(
    *,
    run_id: str,
    records: list[RunRecord],
    cases: dict[str, Case],
    comparisons: dict[str, list[RunnerComparison]],
    judge_label: str = _DASH,
    skipped: list | None = None,
) -> str:
    """从与计分卡相同的数据源渲染单文件 HTML 报告。"""
    by_case: dict[str, dict[tuple[str, str], list[RunRecord]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for record in records:
        by_case[record.case][(record.variant_label, record.runner_label)].append(record)
    case_names = sorted(by_case)
    runner_set = sorted({r.runner_label for r in records})
    variant_set = sorted({r.variant_label for r in records})

    sections: list[str] = []
    sections.append(_render_summary(by_case, cases, case_names))

    for case_name in case_names:
        case_obj = cases.get(case_name)
        case_records = by_case[case_name]
        sections.append(f"<h2>用例：{_e(case_name)}</h2>")
        if case_obj is not None:
            title, brief = _task_brief(case_obj)
            head = _e(title) + (f" — {_e(brief)}" if brief else "")
            notes = []
            if case_obj.evaluation.generalizes is False:
                notes.append("本用例不是泛化证据，不用于宣称总体效果或通用赢家。")
            note_html = "".join(f'<p class="note">⚠️ {_e(n)}</p>' for n in notes)
            sections.append(
                f'<div class="card"><b>{head}</b>'
                f'<p class="note">类型 <code>{_e(case_obj.class_)}</code> · '
                f"判分：{_e(_judging_summary(case_obj))}</p>{note_html}</div>"
            )
        cells = [_aggregate(recs) for recs in case_records.values()]
        cells.sort(key=lambda c: (c.runner_label, c.variant_label))
        completion = (
            {key: cell_completion(recs, case_obj) for key, recs in case_records.items()}
            if case_obj is not None
            else {}
        )
        winners = (
            {}
            if case_obj is not None and case_obj.evaluation.generalizes is False
            else _winners(cells)
        )
        sections.append(_render_metrics_table(cells, completion, winners))
        if winners:
            bits = [
                f"{ {'quality': '质量', 'latency': '速度', 'cost': '成本'}[k] }="
                f'<span class="winner">{_e(v)}</span>'
                for k, v in winners.items()
            ]
            sections.append(f'<p class="note">每维赢家：{"，".join(bits)}（结论由你判定）</p>')
        sections.append(_render_comparisons(comparisons.get(case_name, [])))
        sections.append(_render_items_grid(case_records))
        sections.append(_render_cell_details([r for recs in case_records.values() for r in recs]))

    skipped = skipped or []
    if skipped:
        rows = "".join(
            f"<tr><td>{_e(s.case)}</td><td><code>{_e(s.runner_label)}</code></td>"
            f"<td><code>{_e(s.variant_label)}</code></td><td>{_e(s.reason)}</td></tr>"
            for s in skipped
        )
        sections.append(
            "<h2>跳过的组合</h2>"
            '<div class="tablewrap"><table><thead><tr><th>用例</th><th>Runner</th>'
            f"<th>Variant</th><th>原因</th></tr></thead><tbody>{rows}</tbody></table></div>"
        )

    chips = (
        _chip("用例", len(case_names))
        + _chip("runner", len(runner_set))
        + _chip("variant", len(variant_set))
        + _chip("运行格数", len(records))
        + _chip("裁判", judge_label)
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="data:,">
<title>评测报告 {_e(run_id)}</title>
<style>{_CSS}</style>
</head>
<body><main>
<h1>评测报告</h1>
<p class="meta">run <code>{_e(run_id)}</code> · 生成于 {date.today().isoformat()}</p>
<div class="chips">{chips}</div>
<p class="note">比较口径：每行是「启动器 + 模型」捆绑，不是裸模型；
harness 是已知混淆变量。裁判分为 advisory，有 check 时以 check 为质量锚。</p>
{"".join(sections)}
</main></body>
</html>
"""


# ── 离线重建（--report <run_id>）────────────────────────


def find_run_layout(run_id: str, report_roots: list[Path]) -> RunLayout:
    """在候选产物根中定位既有 run；找不到则显式报错。"""
    for root in report_roots:
        layout = RunLayout(root, run_id)
        if layout.manifest_path.is_file():
            return layout
    searched = ", ".join(str(r / "runs" / run_id) for r in report_roots)
    raise ReportError(f"找不到 run '{run_id}'；已查找: {searched}")


def load_run_records(layout: RunLayout) -> list[RunRecord]:
    """从 run 目录读回全部 cell 的 run.json。"""
    paths = sorted(layout.cells_dir.glob("*/*/*/repeat-*/run.json"))
    if not paths:
        raise ReportError(f"run '{layout.run_id}' 没有任何 cell 记录: {layout.cells_dir}")
    return [RunRecord.from_json(p.read_text(encoding="utf-8")) for p in paths]
