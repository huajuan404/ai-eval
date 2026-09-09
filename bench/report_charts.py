"""Same-case runner comparisons on shared zero-based axes; no external assets."""

from __future__ import annotations

from collections import defaultdict
from statistics import mean

from .case import Case
from .completion import repeat_pass
from .record import RunRecord
from .report_overview import (
    _complete_score,
    _status,
    case_id,
    detail_id,
    esc,
    runner_color,
    runner_marker,
    score,
)


def render_case_charts(records: list[RunRecord], case: Case) -> str:
    grouped: dict[str, dict[str, list[RunRecord]]] = defaultdict(lambda: defaultdict(list))
    for record in records:
        grouped[record.variant_label][record.runner_label].append(record)
    blocks = []
    for variant, groups in sorted(grouped.items()):
        if len(groups) < 2:
            continue
        complete = {name: _complete_score(rs) for name, rs in groups.items()}
        maxima = {s[1] for s in complete.values() if s is not None}
        common_max = next(iter(maxima)) if len(maxima) == 1 else None
        durations = {name: mean(r.duration_ms for r in rs) / 60000 for name, rs in groups.items()}
        speed_eligible = {name for name, rs in groups.items() if durations[name] > 0
                          and all(repeat_pass(r, case) is True for r in rs)}
        # One axis per metric per variant, never a per-runner scale.
        max_duration = max((durations[n] for n in speed_eligible), default=0)
        panels = []
        key = case_id(case.name + "\0" + variant)
        for metric in ("score", "time"):
            values = {
                name: (complete[name][0] if common_max and complete[name] else None)
                if metric == "score" else (durations[name] if name in speed_eligible else None)
                for name in groups
            }
            ordered = sorted(groups, key=lambda name: (
                values[name] is None,
                (-values[name] if metric == "score" else values[name]) if values[name] is not None else 0,
                name,
            ))
            ceiling = (common_max or 0) if metric == "score" else max_duration
            rows = []
            for name in ordered:
                rs = sorted(groups[name], key=lambda r: r.repeat_index)
                status_class, status_text = _status(rs, case)
                value = values[name]
                width = 100 * value / ceiling if value is not None and ceiling else 0
                bar = (f'<span class="comparison-bar" style="width:{width:.3f}%;'
                       f'background:{runner_color(name)}"></span>') if value is not None else (
                    '<span class="comparison-unavailable">'
                    + ("未全部通过，不参加耗时排序" if metric == "time" else
                       "评分量纲不同" if len(maxima) > 1 else "缺少完整评分") + '</span>'
                )
                display = score(rs) if metric == "score" else f"{durations[name]:.2f} 分"
                ids = " ".join(detail_id(r) for r in rs)
                rows.append(
                    f'<a class="comparison-row" href="#{detail_id(rs[0])}" data-records="{ids}" '
                    f'data-title="{esc(name)} · {esc(variant)}" data-runner="{esc(name)}">'
                    f'<span class="comparison-name">{runner_marker(name)}{esc(name)}'
                    f'<small class="comparison-status {status_class}">{esc(status_text)}</small></span>'
                    f'<span class="comparison-track">{bar}</span>'
                    f'<span class="comparison-value">{esc(display)}</span></a>'
                )
            axis = f"{ceiling:g}" if metric == "score" else f"{ceiling:.2f} 分钟"
            panels.append(
                f'<section class="comparison-panel panel-{metric}" aria-label="'
                f'{"参考分从高到低" if metric == "score" else "完成耗时从短到长"}">'
                f'<div class="comparison-axis"><span>0</span><span>{axis if ceiling else "暂无可比数据"}</span></div>'
                + "".join(rows) + '</section>'
            )
        blocks.append(
            f'<div class="case-charts"><h3>Runner 对比 <small>variant: {esc(variant)}</small></h3>'
            '<p class="note">颜色识别 Runner；参考分越高越好，耗时越短越好。'
            '同一图共用从 0 开始的刻度，多轮取均值；未全部通过的组合保留耗时但不参加速度排序。</p>'
            f'<fieldset class="chart-metric"><legend class="chart-sr-only">对比指标 · {esc(variant)}</legend>'
            f'<input type="radio" name="{key}-metric" id="{key}-score" class="metric-score" checked>'
            f'<label for="{key}-score">参考分 ↓</label>'
            f'<input type="radio" name="{key}-metric" id="{key}-time" class="metric-time">'
            f'<label for="{key}-time">耗时 ↑</label>'
            f'{"".join(panels)}</fieldset></div>'
        )
    return "".join(blocks)


CSS = """
.case-charts{margin:18px 0 24px;padding:0 0 20px;border-bottom:1px solid var(--line)}
.case-charts h3{font-size:18px;font-weight:550;margin:14px 0 6px}
.case-charts h3 small{font:12px -apple-system,BlinkMacSystemFont,sans-serif;color:var(--muted);margin-left:10px}
.chart-metric{border:0;padding:0;margin:14px 0 0;min-width:0}
.chart-sr-only,.chart-metric>input{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap}
.chart-metric>label{display:inline-block;padding:7px 14px;border:1px solid var(--line);border-radius:4px;color:var(--muted);cursor:pointer;font-size:12px;margin:0 4px 14px 0}
.chart-metric>input:checked+label{background:#262626;border-color:#262626;color:white}
.chart-metric>input:focus-visible+label{outline:2px solid #666;outline-offset:3px}
.comparison-panel{display:none}.metric-score:checked~.panel-score,.metric-time:checked~.panel-time{display:block}
.comparison-axis{display:flex;justify-content:space-between;margin:0 125px 6px 230px;font-size:11px;color:var(--muted)}
.comparison-row{display:grid;grid-template-columns:210px minmax(0,1fr) 105px;gap:20px;align-items:center;padding:9px 0;color:var(--fg);border-top:1px solid #f1f1f1}
.comparison-row:hover{background:#fafafa;text-decoration:none}
.comparison-name{font-size:12px;overflow-wrap:anywhere}.comparison-status{display:block;font-size:10px;color:var(--muted);margin:3px 0 0 15px}
.comparison-status.bad{color:var(--bad)}.comparison-status.warn{color:var(--warn)}
.comparison-track{display:block;min-height:24px;border-left:1px solid #bbb;background:linear-gradient(to right,transparent calc(100% - 1px),#ededed 0);background-size:25% 100%}
.comparison-bar{display:block;height:24px;border-radius:0 2px 2px 0}
.comparison-value{text-align:right;font-size:13px;font-variant-numeric:tabular-nums}
.comparison-unavailable{display:block;font-size:11px;line-height:24px;padding-left:8px;color:var(--muted)}
@media(max-width:700px){.comparison-row{grid-template-columns:135px minmax(0,1fr) 70px;gap:10px}.comparison-axis{margin-left:145px;margin-right:80px}.comparison-value{font-size:11px}.comparison-name{font-size:11px}.comparison-track{min-height:20px}.comparison-bar{height:20px}}
@media(max-width:420px){.comparison-row{grid-template-columns:105px minmax(0,1fr) 60px;gap:8px}.comparison-axis{margin-left:113px;margin-right:68px}.comparison-unavailable{font-size:10px;line-height:1.4}}
"""
