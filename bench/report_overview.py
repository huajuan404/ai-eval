"""Report overview: completion, comparable evidence, and local artifact entry points."""

from __future__ import annotations

import hashlib
import html
from pathlib import Path
from statistics import mean
from urllib.parse import quote

from .case import Case
from .completion import cell_completion, repeat_pass
from .record import RunRecord
from .scorecard import _task_brief
from .scrub import scrub_text


def esc(value: object) -> str:
    return html.escape(scrub_text(str(value)), quote=True)


def detail_id(record: RunRecord) -> str:
    key = (record.case, record.variant_label, record.runner_label, record.repeat_index)
    return "cell-" + hashlib.sha256(repr(key).encode()).hexdigest()[:20]


def case_id(name: str) -> str:
    return "case-" + hashlib.sha256(name.encode()).hexdigest()[:20]


def task_title(case: Case) -> str:
    title, _ = _task_brief(case)
    if title == case.name:
        title = next(
            (s.strip() for s in case.task.prompt.splitlines() if s.strip().startswith("任务：")),
            case.name,
        )
    return title.removeprefix("任务：").replace("`", "")


def elapsed(records: list[RunRecord]) -> str:
    seconds = mean(r.duration_ms for r in records) / 1000
    return f"{seconds:.1f} 秒" if seconds < 60 else f"{seconds / 60:.1f} 分"


def score(records: list[RunRecord]) -> str:
    judges = [r.judge for r in records if not r.is_error and r.judge
              and r.judge.ran and r.judge.score is not None]
    if not judges:
        return "未评分"
    maxima = {j.max for j in judges}
    if len(maxima) != 1:
        return "评分量纲不同"
    maximum = next(iter(maxima))
    result = f"{mean(j.score for j in judges):g}"  # type: ignore[arg-type]
    if maximum:
        result += f" / {maximum:g}"
    if len(judges) != len(records):
        result += f" · {len(judges)}/{len(records)} 已评"
    return result


def output_href(record: RunRecord, case: Case) -> str | None:
    """Only expose the declared HTML deliverable, contained in this cell's artifacts."""
    declared = (case.expected or {}).get("output_file")
    if not isinstance(declared, str) or not record.artifacts_dir:
        return None
    relative = Path(declared)
    if relative.is_absolute() or ".." in relative.parts or relative.suffix.lower() not in (".html", ".htm"):
        return None
    root = Path(record.artifacts_dir).resolve()
    target = (root / relative).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        return None
    parts = ("cells", record.case, record.variant_label, record.runner_label,
             f"repeat-{record.repeat_index}", "artifacts", *relative.parts)
    if any(p in ("", ".", "..") for p in parts):
        return None
    return "/".join(quote(p, safe="") for p in parts)


def _status(records: list[RunRecord], case: Case) -> tuple[str, str]:
    comp = cell_completion(records, case)
    if comp.evaluated == 0:
        return "na", "— 未评"
    if comp.unevaluated:
        return "na", f"{comp.passes}/{comp.evaluated} 通过 · {comp.unevaluated} 未评"
    if comp.rate == 1:
        return "ok", "✓ PASS" if comp.total == 1 else f"✓ {comp.passes}/{comp.total} PASS"
    if comp.rate == 0:
        return "bad", "× 执行错误" if all(r.is_error for r in records) else "× FAIL"
    return "warn", f"{comp.passes} PASS · {comp.evaluated - comp.passes} FAIL"


def _cell(records: list[RunRecord], case: Case, fastest: bool) -> str:
    ordered = sorted(records, key=lambda r: r.repeat_index)
    klass, label = _status(ordered, case)
    ids = " ".join(detail_id(r) for r in ordered)
    tiles = "".join(
        f'<span class="repeat-tile {"ok" if verdict is True else "bad" if verdict is False else "na"}" '
        f'title="repeat-{r.repeat_index}: '
        f'{"passed" if verdict is True else "failed" if verdict is False else "not evaluated"}"></span>'
        for r in ordered for verdict in [repeat_pass(r, case)]
    )
    return (
        f'<td class="result-cell {klass}"><a class="matrix-link" href="#{detail_id(ordered[0])}" '
        f'data-records="{ids}" data-title="{esc(ordered[0].runner_label)} · {esc(task_title(case))}">'
        f'<span class="verdict">{esc(label)}</span><span class="cell-score">{esc(score(ordered))}</span>'
        f'<span class="cell-time">{esc(elapsed(ordered))}'
        f'{" · 最快" if fastest else ""}</span><span class="repeat-strip" aria-hidden="true">{tiles}</span>'
        '</a></td>'
    )


def render_overview(records: list[RunRecord], cases: dict[str, Case], run_id: str) -> str:
    pairs = sorted({(r.runner_label, r.variant_label) for r in records})
    names = sorted({r.case for r in records if r.case in cases})
    variants = {v for _, v in pairs}
    verdicts = [repeat_pass(r, cases[r.case]) for r in records if r.case in cases]
    passes = sum(v is True for v in verdicts)
    failures = sum(v is False for v in verdicts)
    unknown = len(verdicts) - passes - failures
    all_passed = bool(verdicts) and passes == len(verdicts)
    title = "全部通过判据，差异藏在完成方式里。" if all_passed else "哪些任务做成了，一眼看清。"
    heads = "".join(
        f'<th scope="col"><span class="runner-name">{esc(r)}</span>'
        f'<small>{esc(v) if len(variants) > 1 or v != "default" else "Runner + Model"}</small></th>'
        for r, v in pairs
    )
    rows, galleries = [], []
    for name in names:
        case = cases[name]
        groups = {(runner, variant): [r for r in records if r.case == name
                   and r.runner_label == runner and r.variant_label == variant]
                  for runner, variant in pairs}
        eligible = [rs for rs in groups.values() if rs
                    and all(repeat_pass(r, case) is True for r in rs)]
        fastest = {
            variant: min((mean(r.duration_ms for r in rs) for rs in eligible
                          if rs[0].variant_label == variant), default=None)
            for variant in variants
        }
        cells = []
        for pair in pairs:
            rs = groups[pair]
            cells.append(_cell(rs, case, bool(
                case.evaluation.generalizes is not False and rs in eligible
                and mean(r.duration_ms for r in rs) == fastest[pair[1]]
            )) if rs else '<td class="result-cell na"><span class="missing-cell">— 未运行</span></td>')
        core = (case.expected or {}).get("completion", {}).get("core_dimensions")
        criterion = "核心维度 / 检查判据" if core else (
            "基础检查 · 参考分独立展示" if case.check.type == "script" else "裁判阈值判据"
        )
        rows.append(
            f'<tr><th scope="row"><a href="#{case_id(name)}" data-case-link>{esc(task_title(case))}</a>'
            f'<small>{criterion}</small></th>{"".join(cells)}</tr>'
        )
        if any(output_href(r, case) for r in records if r.case == name):
            cards = []
            for runner, variant in pairs:
                rs = sorted(groups[(runner, variant)], key=lambda r: r.repeat_index)
                if not rs:
                    cards.append(f'<article class="work"><h4>{esc(runner)}</h4><p>未运行</p></article>')
                    continue
                # Same first planned/observed repeat for all runners; never cherry-pick a later success.
                first_index = min(r.repeat_index for r in records if r.case == name)
                first = next((r for r in rs if r.repeat_index == first_index), None)
                href = output_href(first, case) if first else None
                action = (f'<a class="work-open" href="{esc(href)}" target="_blank" rel="noopener noreferrer">'
                          '打开原始作品 ↗</a>') if href else '<span class="work-unavailable">本轮无 HTML 产物</span>'
                label = f"{runner} · {variant}" if len(variants) > 1 or variant != "default" else runner
                cards.append(
                    f'<article class="work"><h4>{esc(label)}</h4><div class="preview-empty">'
                    '<span class="preview-symbol" aria-hidden="true">&lt;/&gt;</span>'
                    f'<span>{"HTML 已生成" if href else "作品缺失"}</span><small>首屏尚未采集</small></div>'
                    f'<div class="work-measures"><strong>{esc(score(rs))}</strong><span>{esc(elapsed(rs))}</span></div>'
                    f'{action}<small class="work-repeat">展示 repeat-{first_index} · 参考评分，详见裁判说明</small></article>'
                )
            galleries.append(
                f'<section class="work-section"><div class="section-heading"><div><span class="eyebrow">OUTPUTS</span>'
                f'<h2>{esc(task_title(case))}</h2></div><a href="#{case_id(name)}" data-case-link>要求与评分依据 ↗</a></div>'
                '<p class="gallery-note">真实 HTML 产物入口 · 本次未保存首屏截图，视觉与交互表现请打开作品查看。</p>'
                f'<div class="work-grid" style="--runner-count:{len(pairs)}">{"".join(cards)}</div></section>'
            )
    return (
        '<section class="overview" aria-label="评测结论总览"><header class="report-masthead">'
        '<a class="wordmark" href="#">AI<span>EVAL</span><i> / FIELD REPORT</i></a>'
        f'<span class="run-stamp">{esc(run_id)} · {len(pairs)} 组启动器 · {len(names)} 个任务</span></header>'
        '<div class="report-lead"><div><span class="eyebrow">REAL TASKS. VISIBLE DIFFERENCES.</span>'
        f'<h1>{title}</h1><p>比较能否完成，也比较参考评分与耗时。点击任意结果，查看判定证据。</p></div>'
        f'<div class="run-total"><strong>{passes}<span> / {len(verdicts)}</span></strong><span>次运行通过'
        f' · {failures} 失败 · {unknown} 未评</span></div></div>'
        '<div class="matrix-heading"><h2>用例通过矩阵</h2><div class="matrix-legend">'
        '<span class="legend-ok">✓ 通过</span><span class="legend-warn">◐ 部分轮次通过</span>'
        '<span class="legend-bad">× 失败</span><span>— 未评 / 缺失</span></div></div>'
        f'<div class="overview-table"><table class="result-grid case-matrix" style="--runner-count:{len(pairs)}">'
        f'<thead><tr><th scope="col">真实任务<small>状态 / 参考分 / 耗时</small></th>{heads}</tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table></div>'
        '<p class="matrix-footnote">每格保留逐轮结果；多轮时分数与耗时为均值。参考分不参与着色，不跨任务合成总分。'
        '“最快”仅比较该任务所有轮次均通过的组合；单次运行不代表稳定性。</p>'
        f'{"".join(galleries)}</section>'
    )


CSS = """
:root{--bg:#111416;--fg:#e9edee;--muted:#9ba6aa;--line:#30383b;--card:#191e21;
--ok:#92d6b4;--ok-bg:#192e27;--ok-line:#315744;--bad:#eda7a1;--bad-bg:#352323;
--bad-line:#68403a;--warn:#e4c17f;--warn-bg:#342d21;--warn-line:#675438;
--na:#acb5bb;--na-bg:#24292d;--na-line:#3c4449;--accent:#d3e5df;color-scheme:dark}
body{padding:0 32px 48px;font-size:14px;line-height:1.5}
main{max-width:1540px}a{color:var(--fg)}a:focus-visible,button:focus-visible,summary:focus-visible{
outline:2px solid var(--ok);outline-offset:4px}button{font:inherit;cursor:pointer}
.report-masthead{display:flex;justify-content:space-between;align-items:center;gap:16px;height:60px;border-bottom:1px solid var(--line)}
.wordmark{font-size:20px;font-weight:780;letter-spacing:-1px}.wordmark span{color:#92d6b4}
.wordmark i{font-size:10px;font-style:normal;letter-spacing:2px;margin-left:14px;color:var(--muted)}
.run-stamp{font:11px ui-monospace,SFMono-Regular,monospace;color:var(--muted)}
.report-lead{display:flex;justify-content:space-between;align-items:center;gap:30px;padding:20px 0 20px}
.eyebrow{font:10px ui-monospace,SFMono-Regular,monospace;letter-spacing:1.8px;color:var(--muted)}
.report-lead h1{font-size:clamp(23px,2.2vw,32px);line-height:1.25;letter-spacing:-1px;margin:9px 0 10px;font-weight:650}
.report-lead p{margin:0;color:var(--muted);font-size:13px}.run-total{display:flex;flex-direction:column;flex-shrink:0;text-align:right}
.run-total>strong{font-size:44px;font-weight:550;line-height:1.2;font-variant-numeric:tabular-nums}
.run-total strong span{font-size:25px;color:var(--muted)}.run-total>span{color:var(--muted);font-size:11px;margin-top:6px}
.matrix-heading,.section-heading{display:flex;align-items:center;justify-content:space-between;gap:16px}
.matrix-heading h2,.section-heading h2{border:0;padding:0;margin:0;font-size:16px;font-weight:550}
.matrix-legend{display:flex;gap:18px;font-size:11px;color:var(--muted)}.legend-ok{color:var(--ok)}
.legend-warn{color:var(--warn)}.legend-bad{color:var(--bad)}
.overview-table{overflow-x:auto;margin-top:12px;border:1px solid var(--line);border-radius:6px}
.overview-table table{width:100%;table-layout:fixed;min-width:calc(205px + var(--runner-count) * 120px)}
.overview-table thead th{font-size:12px;vertical-align:bottom;background:#1a2023;padding:12px 10px;font-weight:550;white-space:normal}
.overview-table thead th:first-child{width:205px}.runner-name{display:block;overflow-wrap:anywhere;line-height:1.4}
.overview-table th small{display:block;color:var(--muted);font-size:10px;font-weight:400;margin-top:5px}
.overview-table th[scope=row]{min-width:0;max-width:none;position:static;width:205px;padding:13px 16px;background:#181e20;font-size:12px;font-weight:500;vertical-align:middle}
.overview-table th[scope=row] a{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.overview-table td.result-cell{min-width:0;border-left:1px solid var(--line);border-right:0;box-shadow:none}
.matrix-link{display:flex;flex-direction:column;align-items:flex-start;gap:4px;padding:8px 12px;color:inherit;min-height:86px;transition:background .15s}
.matrix-link:hover{background:#ffffff08;text-decoration:none}.verdict{font-size:11px;font-weight:600}
.cell-score{font-size:19px;font-weight:550;line-height:1.3;color:var(--fg);font-variant-numeric:tabular-nums}
.cell-time{font-size:11px;color:var(--muted)}.matrix-link .repeat-strip{max-width:88px;gap:3px;margin-top:3px}
.matrix-link .repeat-tile{height:3px;border:0;box-shadow:none}.missing-cell{display:block;padding:25px 12px;font-size:12px}
.matrix-footnote{font-size:11px;color:var(--muted);margin:9px 0 0}.work-section{margin-top:18px}
.section-heading .eyebrow{font-size:9px;display:block;margin-bottom:5px}.section-heading>a{font-size:11px;color:var(--muted)}
.gallery-note{font-size:11px;color:var(--muted);margin:8px 0 12px}
.work-grid{display:grid;grid-template-columns:repeat(var(--runner-count),minmax(0,1fr));gap:12px}
.work{min-width:0}.work h4{font-size:11px;font-weight:550;margin:0 0 8px;overflow-wrap:anywhere;min-height:32px}
.preview-empty{height:100px;border:1px solid var(--line);border-radius:4px;background:linear-gradient(135deg,#21292c,#181d20);display:flex;flex-direction:column;align-items:center;justify-content:center;gap:3px;color:var(--muted);font-size:11px}
.preview-symbol{font:24px ui-monospace,SFMono-Regular,monospace;color:#647779;line-height:1.2;margin-bottom:6px}
.preview-empty small{font-size:10px;color:var(--muted)}.work-measures{display:flex;justify-content:space-between;align-items:center;gap:6px;margin:9px 0 7px;font-variant-numeric:tabular-nums}
.work-measures strong{font-size:15px;font-weight:500}.work-measures span{font-size:10px;color:var(--muted)}
.work-open{display:block;text-align:center;border:1px solid var(--line);border-radius:3px;padding:7px 3px;font-size:11px;transition:background .15s}
.work-open:hover{background:#2a3537;text-decoration:none}.work-unavailable{font-size:11px;color:var(--muted)}
.work-repeat{display:block;font-size:10px;line-height:1.5;color:var(--muted);margin-top:7px}
.evidence-heading{margin:34px 0 14px;border-top:1px solid var(--line);padding-top:22px;font-size:18px;font-weight:500}
.report-context{font-size:12px;color:var(--muted);margin:20px 0}.report-context summary{font-size:12px;font-weight:400}
.case-section>summary{padding:15px 18px;font-size:14px;font-weight:500}.case-section>.case-body{padding:0 18px 18px}
.case-section .tablewrap th{position:static}.case-section{scroll-margin-top:20px}
.result-dialog{position:fixed;inset:0 0 0 auto;margin:0;height:100dvh;max-height:100dvh;width:min(680px,94vw);max-width:94vw;border:0;border-left:1px solid var(--line);padding:0;background:var(--bg);color:var(--fg)}
.result-dialog::backdrop{background:#0008;backdrop-filter:blur(3px)}
.dialog-head{position:sticky;top:0;background:var(--bg);padding:20px 24px;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;gap:14px;z-index:1}
.dialog-head h2{border:0;padding:0;margin:0;font-size:17px;overflow-wrap:anywhere}.dialog-head button{border:1px solid var(--line);border-radius:4px;background:var(--card);color:var(--fg);padding:7px 12px;white-space:nowrap}
.dialog-body{padding:12px 24px 30px}.dialog-body pre{font-size:12px}.dialog-body code{overflow-wrap:anywhere}
@media(min-width:1600px){.matrix-link{min-height:100px}.preview-empty{height:145px}}
@media(max-width:1000px){body{padding:0 18px 32px}.work-grid{grid-template-columns:repeat(3,minmax(0,1fr))}.run-stamp{max-width:260px;text-align:right}.report-lead{gap:15px}.matrix-legend{gap:10px}}
@media(max-width:600px){body{padding:0 12px 28px}.wordmark i{display:none}.run-stamp{font-size:9px;max-width:180px}.report-lead{align-items:flex-start;flex-direction:column;padding:22px 0}.run-total{text-align:left;flex-direction:row;align-items:center;gap:12px}.run-total>strong{font-size:30px}.matrix-heading{align-items:flex-start;flex-direction:column;gap:7px}.work-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.section-heading{align-items:flex-start}.section-heading h2{font-size:14px}.section-heading>a{flex-shrink:0}.dialog-body{padding:12px}.dialog-head{padding:16px}}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;transition:none!important}}
"""

SCRIPT = """
<script id="report-interactions">
(() => {
  const dialog = document.getElementById('result-dialog');
  const body = document.getElementById('result-dialog-body');
  const title = document.getElementById('result-dialog-title');
  function reveal(target) {
    let element = target;
    while (element) {
      if (element.tagName === 'DETAILS') element.open = true;
      element = element.parentElement;
    }
  }
  document.addEventListener('click', event => {
    const result = event.target.closest('[data-records]');
    if (result && dialog && typeof dialog.showModal === 'function') {
      event.preventDefault();
      body.replaceChildren();
      title.textContent = result.dataset.title;
      result.dataset.records.split(' ').forEach(id => {
        const source = document.getElementById(id);
        if (!source) return;
        const copy = source.cloneNode(true);
        copy.removeAttribute('id');
        copy.open = true;
        body.append(copy);
      });
      dialog.showModal();
      return;
    }
    const link = event.target.closest('a[href^="#"]');
    if (link) {
      const target = document.getElementById(link.getAttribute('href').slice(1));
      if (target) reveal(target);
    }
  });
  document.getElementById('result-dialog-close').addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', event => {
    if (event.target === dialog && event.clientX < dialog.getBoundingClientRect().left) dialog.close();
  });
  const initial = document.getElementById(location.hash.slice(1));
  if (initial) { reveal(initial); initial.scrollIntoView(); }
})();
</script>
"""

DIALOG = """
<dialog id="result-dialog" class="result-dialog" aria-labelledby="result-dialog-title">
<header class="dialog-head"><h2 id="result-dialog-title">运行证据</h2>
<button type="button" id="result-dialog-close" autofocus>关闭 ×</button></header>
<div id="result-dialog-body" class="dialog-body"></div></dialog>
"""
