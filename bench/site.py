"""从公开结果账本（`docs/data/`）生成 GitHub Pages 静态站点。

    docs/index.html                 首页：用例列表 + 模型概览
    docs/cases/<case>/index.html    用例页：多模型并排交付物、通过与否、分数、耗时、成本、裁判原文、历史
    docs/models/<runner>/index.html 模型页：该模型在所有用例上的最新表现
    docs/data/index.json            汇总索引（生成产物，不手改）
    docs/assets/report*.svg         README 首图，由 docs/data/featured.json 选格

账本是唯一数据源；页面里出现的每个数字都能回到 docs/data/runs/<run_id>.json 的某条记录。
`--check` 只比对不写入，供 CI 与提交前校验产物是否与数据一致。

用法：
    python3 -m bench.site [--site docs] [--check]
"""

from __future__ import annotations

import argparse
import html
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .case import Case, CaseError, load_case
from .models import ModelBook
from .publish import LEDGER_SCHEMA, hashes, public_text, sha256
from .report_overview import runner_color
from .report_previews import CSS as PREVIEW_CSS
from .report_previews import DIALOG as PREVIEW_DIALOG
from .report_previews import SCRIPT as PREVIEW_SCRIPT
from .report_previews import render_html_preview
from .scorecard import _task_brief
from .showcase_svg import render_showcase

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "docs"
REPO_URL = "https://github.com/huajuan404/donebench"
PREVIEW_SUFFIXES = {".svg", ".html", ".htm"}
CLASS_LABELS = {"coding": "编码", "reasoning": "推理", "tool-using": "工具调用", "writing": "写作"}


class SiteError(ValueError):
    pass


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


@dataclass(frozen=True)
class CaseInfo:
    id: str
    title: str
    brief: str
    class_: str
    core: bool
    exists: bool
    max_score: float | None
    criteria: str


@dataclass
class Ledger:
    site: Path
    runs: list[dict[str, Any]]
    records: list[dict[str, Any]]
    cases: dict[str, CaseInfo]
    book: ModelBook
    featured: dict[str, Any] | None = None
    latest: dict[tuple[str, str], list[dict[str, Any]]] = field(default_factory=dict)
    history: dict[tuple[str, str], list[list[dict[str, Any]]]] = field(default_factory=dict)

    def runner_name(self, label: str) -> str:
        return self.book.display(label)


# ------------------------------------------------------------------ 账本读取


def _case_info(name: str) -> CaseInfo:
    directory = ROOT / "cases" / name
    if not (directory / "case.yaml").is_file():
        return CaseInfo(name, name, "", "", False, False, None, "用例已从仓库移除，仅保留历史记录")
    try:
        case = load_case(directory)
    except CaseError as exc:
        raise SiteError(f"用例 {name} 无法加载：{exc}") from exc
    title, brief = _task_brief(case)
    if title == case.name or not brief:
        readme_title, readme_brief = _readme_brief(directory / "README.md")
        title = readme_title if title == case.name and readme_title else title
        brief = brief or readme_brief
    return CaseInfo(
        id=name, title=title.removeprefix("任务：").removeprefix("用例: ").removeprefix("用例：").replace("`", ""), brief=brief,
        class_=case.class_, core=case.core, exists=True,
        max_score=_number((case.expected or {}).get("max_score")), criteria=_criteria_text(case),
    )


def _readme_brief(readme: Path) -> tuple[str, str]:
    """task.md 没有标题时退回用例 README：首个一级标题 + 其后第一段正文。"""
    if not readme.is_file():
        return "", ""
    title, brief = "", ""
    for line in readme.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s:
            continue
        if not title and s.startswith("# "):
            title = s[2:].strip()
            continue
        if title and not s.startswith(("#", ">", "|", "```", "-", "*", "!")):
            brief = s
            break
    return title, brief


def _criteria_text(case: Case) -> str:
    expected = case.expected or {}
    core = (expected.get("completion") or {}).get("core_dimensions")
    if core:
        return "核心维度达标：" + "、".join(f"{k} ≥ {v}" for k, v in core.items())
    if case.check.type == "script":
        return "确定性 check 脚本判定；裁判分仅作参考"
    threshold = expected.get("passing_threshold")
    if threshold is not None:
        return f"裁判参考分 ≥ {threshold}"
    return "未设通过判据，只展示参考分"


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


REQUIRED = {"case", "runner_label", "run_id", "variant_label", "repeat_index", "started_at", "duration_ms",
            "is_error", "judge", "check", "verdict", "cost_usd", "artifacts", "output", "usage"}


def load_ledger(site: Path = SITE, book: ModelBook | None = None) -> Ledger:
    site = Path(site)
    runs_dir = site / "data" / "runs"
    runs: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    for path in sorted(runs_dir.glob("*.json")) if runs_dir.is_dir() else []:
        document = json.loads(path.read_text(encoding="utf-8"))
        if document.get("schema_version") != LEDGER_SCHEMA or document.get("run_id") != path.stem:
            raise SiteError(f"账本文件格式不符：{path.name}")
        public_text(json.dumps(document, ensure_ascii=False), hashes(document.get("records", [])))
        for row in document["records"]:
            missing = REQUIRED - set(row)
            if missing:
                raise SiteError(f"{path.name} 记录缺少字段 {sorted(missing)}")
            if row["run_id"] != document["run_id"]:
                raise SiteError(f"{path.name} 含其他运行的记录")
            paths = set()
            for artifact in row["artifacts"]:
                relative = Path(artifact["path"])
                target = site / relative
                if relative.parts[:2] != ("data", "cells") or ".." in relative.parts or target.is_symlink():
                    raise SiteError(f"交付物路径非法：{artifact['path']}")
                if not target.is_file() or not target.resolve().is_relative_to(site.resolve()):
                    raise SiteError(f"交付物缺失：{artifact['path']}")
                if sha256(target.read_bytes()) != artifact["sha256"]:
                    raise SiteError(f"交付物已被修改，与账本哈希不符：{artifact['path']}")
                paths.add(artifact["path"])
            if row["output"] is not None and row["output"] not in paths:
                raise SiteError(f"output 不在交付物清单内：{row['output']}")
            records.append(row)
        runs.append(document)
    cases = {name: _case_info(name) for name in sorted({r["case"] for r in records})}
    featured_path = site / "data" / "featured.json"
    featured = json.loads(featured_path.read_text(encoding="utf-8")) if featured_path.is_file() else None
    ledger = Ledger(site=site, runs=runs, records=records, cases=cases, book=book or ModelBook.load(), featured=featured)
    _index_cells(ledger)
    return ledger


def _index_cells(ledger: Ledger) -> None:
    grouped: dict[tuple[str, str], dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for row in ledger.records:
        grouped[(row["case"], row["runner_label"])][row["run_id"]].append(row)
    for key, by_run in grouped.items():
        ordered = sorted(by_run.values(), key=lambda rows: min(r["started_at"] for r in rows))
        ledger.history[key] = ordered
        ledger.latest[key] = ordered[-1]


# ------------------------------------------------------------------ 汇总


@dataclass(frozen=True)
class Verdicts:
    passes: int
    fails: int
    unknown: int

    @property
    def evaluated(self) -> int:
        return self.passes + self.fails

    @property
    def label(self) -> str:
        if self.evaluated == 0:
            return "未评"
        if self.fails == 0:
            return "通过" if self.evaluated == 1 else f"{self.passes}/{self.evaluated} 通过"
        if self.passes == 0:
            return "未通过" if self.evaluated == 1 else f"0/{self.evaluated} 通过"
        return f"{self.passes}/{self.evaluated} 通过"

    @property
    def klass(self) -> str:
        if self.evaluated == 0:
            return "na"
        if self.fails == 0:
            return "ok"
        return "bad" if self.passes == 0 else "warn"


def verdicts(rows: list[dict[str, Any]]) -> Verdicts:
    values = [r["verdict"] for r in rows]
    return Verdicts(passes=sum(v is True for v in values), fails=sum(v is False for v in values),
                    unknown=sum(v is None for v in values))


def mean(values: list[float | None]) -> float | None:
    clean = [v for v in values if isinstance(v, (int, float))]
    return sum(clean) / len(clean) if clean else None


def score_of(rows: list[dict[str, Any]]) -> tuple[float, float] | None:
    scores = [r["judge"]["score"] for r in rows if r.get("judge") and r["judge"].get("ran") and r["judge"].get("score") is not None]
    maxes = {r["judge"]["max"] for r in rows if r.get("judge") and r["judge"].get("max")}
    if not scores or len(maxes) != 1:
        return None
    return sum(scores) / len(scores), float(next(iter(maxes)))


def fmt_duration(ms: float | None) -> str:
    if ms is None:
        return "—"
    seconds = round(ms / 1000)
    return f"{seconds // 60} 分 {seconds % 60:02d} 秒" if seconds >= 60 else f"{seconds} 秒"


def fmt_cost(value: float | None) -> str:
    if value is None:
        return "—"
    if value == 0:
        return "$0"
    if value < 0.01:
        return f"${value:.4f}"
    return f"${value:.2f}" if value < 100 else f"${value:,.0f}"


def fmt_tokens(usage: dict[str, Any] | None) -> str:
    if not usage:
        return "—"
    total = usage.get("total_tokens")
    if total is None:
        return "—"
    return f"{total / 1000:.0f}k" if total >= 1000 else str(total)


def total_tokens(rows: list[dict[str, Any]]) -> float | None:
    return mean([(r.get("usage") or {}).get("total_tokens") for r in rows])


def cell_cost(rows: list[dict[str, Any]]) -> float | None:
    return mean([r.get("cost_usd") for r in rows])


def badge(v: Verdicts) -> str:
    return f'<span class="badge {v.klass}">{esc(v.label)}</span>'


# ------------------------------------------------------------------ 页面骨架

CSS = """
:root{--bg:#fff;--fg:#1b1f24;--muted:#667085;--line:#e4e7ec;--card:#f8fafc;--ok:#176a51;--ok-bg:#ddf2e9;--bad:#a63f50;--bad-bg:#f8e1e5;--warn:#885c16;--warn-bg:#f8edcf;--na:#6f7d91;--na-bg:#f0f3f7;--accent:#175cd3}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;padding:0 24px 64px}
main{max-width:1180px;margin:0 auto}a{color:inherit}a:hover{color:var(--accent)}
.masthead{display:flex;justify-content:space-between;align-items:center;padding:18px 0;border-bottom:1px solid var(--line);font-size:12px;color:var(--muted)}
.wordmark{font-weight:750;font-size:18px;letter-spacing:-.5px;color:var(--fg);text-decoration:none}.wordmark span{font-weight:400}.wordmark i{font-style:normal;font-size:11px;letter-spacing:2px;margin-left:8px;color:var(--muted)}
.crumbs a{text-decoration:none;margin-right:6px}.eyebrow{font-size:11px;letter-spacing:2px;color:var(--muted);text-transform:uppercase}
h1{font-size:26px;margin:26px 0 6px}h2{font-size:18px;margin:38px 0 12px;padding-top:14px;border-top:1px solid var(--line)}h3{font-size:15px;margin:22px 0 8px}
.lead{color:var(--muted);margin:0 0 8px;font-size:14px}.meta{color:var(--muted);font-size:12px;margin:0}
table{border-collapse:collapse;width:100%;font-size:13px;margin:8px 0 4px}th,td{text-align:left;padding:9px 10px;border-bottom:1px solid var(--line);vertical-align:top}th{font-weight:550;color:var(--muted);font-size:12px}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}tr:hover td{background:#fafafa}
.badge{display:inline-block;padding:2px 8px;border-radius:3px;font-size:11px;font-weight:600;white-space:nowrap}
.badge.ok{color:var(--ok);background:var(--ok-bg)}.badge.bad{color:var(--bad);background:var(--bad-bg)}.badge.warn{color:var(--warn);background:var(--warn-bg)}.badge.na{color:var(--na);background:var(--na-bg)}
.tag{display:inline-block;padding:1px 7px;border:1px solid var(--line);border-radius:3px;font-size:11px;color:var(--muted);margin-left:6px}.tag.core{border-color:#222;color:#222}
.runner-marker{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:7px;vertical-align:baseline}
.work-section{margin:12px 0 4px}.work-grid{display:grid;gap:22px}.work{border:1px solid var(--line);border-radius:6px;padding:14px;background:#fff}
.work h4{margin:0 0 8px;font-size:14px;display:flex;justify-content:space-between;align-items:center}.work-measures{display:flex;flex-wrap:wrap;gap:6px 14px;margin:10px 0 6px;font-size:12px;color:var(--muted)}.work-measures strong{font-size:16px;color:var(--fg);font-variant-numeric:tabular-nums}
.work-open{display:block;margin:8px 0 4px;text-align:center;padding:8px;border:1px solid var(--line);border-radius:4px;text-decoration:none;font-size:12px}.work-open:hover{border-color:#222}
.files{font-size:12px;color:var(--muted);margin:6px 0 0}.files a{text-decoration:none;margin-right:10px}
details{margin:8px 0}summary{cursor:pointer;font-size:12px;color:var(--muted)}pre{white-space:pre-wrap;word-break:break-word;font-size:12px;line-height:1.55;background:var(--card);padding:12px;border-radius:4px;border:1px solid var(--line);margin:8px 0 0}
.dims{display:flex;flex-wrap:wrap;gap:4px 10px;font-size:11px;color:var(--muted);margin:4px 0}.dims b{color:var(--fg);font-weight:600}
.spark{display:block;width:100%;max-width:520px;height:120px}.spark text{font-size:10px;fill:var(--muted)}.spark .axis{stroke:var(--line)}
.note{font-size:12px;color:var(--muted)}.preview-empty{display:flex;align-items:center;justify-content:center;background:var(--card);border:1px dashed var(--line);border-radius:5px;color:var(--muted);font-size:12px;height:160px}
footer{margin-top:60px;padding-top:16px;border-top:1px solid var(--line);font-size:12px;color:var(--muted)}
@media(max-width:760px){body{padding:0 12px 40px}.work-grid{grid-template-columns:1fr!important}h1{font-size:22px}table{display:block;overflow-x:auto}}
"""


def page(*, title: str, body: str, prefix: str, crumbs: list[tuple[str, str]], description: str = "") -> str:
    crumb_html = "".join(f'<a href="{esc(href)}">{esc(text)}</a> / ' for text, href in crumbs)
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{esc(title)} · DoneBench</title>'
        f'<meta name="description" content="{esc(description or title)}">'
        f'<style>{CSS}{PREVIEW_CSS}</style></head><body><main>'
        f'<header class="masthead"><a class="wordmark" href="{esc(prefix)}index.html">DONE <span>BENCH</span><i>/ 这活干成了没</i></a>'
        f'<span class="crumbs">{crumb_html}<a href="{REPO_URL}">GitHub ↗</a></span></header>'
        f'{body}'
        f'<footer>DoneBench · 真实个人任务的模型评测库。每个数字都对应 <a href="{esc(prefix)}data/index.json">docs/data</a> 里的一条公开记录。'
        f'参考分由 LLM 裁判给出，通过与否按用例声明的判据判定，均不代表模型总体能力。站点生成于 {generated} UTC。</footer>'
        f'</main>{PREVIEW_DIALOG}{PREVIEW_SCRIPT}</body></html>'
    )


def marker(label: str) -> str:
    return f'<span class="runner-marker" style="background:{runner_color(label)}" aria-hidden="true"></span>'


def case_href(prefix: str, case: str) -> str:
    return f"{prefix}cases/{case}/index.html"


def model_href(prefix: str, runner: str) -> str:
    return f"{prefix}models/{runner}/index.html"


# ------------------------------------------------------------------ 首页


def render_index(ledger: Ledger) -> str:
    cases = sorted(ledger.cases.values(), key=lambda c: (not c.core, c.id), reverse=False)
    runners = sorted({r["runner_label"] for r in ledger.records})

    def case_row(info: CaseInfo) -> str:
        cells = {runner: ledger.latest.get((info.id, runner)) for runner in runners}
        evaluated = [(runner, rows) for runner, rows in cells.items() if rows]
        summary = Verdicts(
            passes=sum(verdicts(rows).fails == 0 and verdicts(rows).evaluated > 0 for _, rows in evaluated),
            fails=sum(verdicts(rows).passes == 0 and verdicts(rows).evaluated > 0 for _, rows in evaluated),
            unknown=sum(verdicts(rows).evaluated == 0 for _, rows in evaluated),
        )
        passers = [(runner, rows) for runner, rows in evaluated if verdicts(rows).evaluated and verdicts(rows).fails == 0]
        cheapest = min((r for r in passers if cell_cost(r[1]) is not None), key=lambda r: cell_cost(r[1]), default=None)
        fastest = min(passers, key=lambda r: mean([x["duration_ms"] for x in r[1]]) or 0, default=None)
        last = max(max(r["started_at"] for r in rows) for _, rows in evaluated)[:10] if evaluated else "—"
        cheapest_html = (f'{marker(cheapest[0])}{esc(ledger.runner_name(cheapest[0]))} <span class="note">{fmt_cost(cell_cost(cheapest[1]))}</span>'
                         if cheapest else '<span class="note">—</span>')
        fastest_html = (f'{marker(fastest[0])}{esc(ledger.runner_name(fastest[0]))} <span class="note">{fmt_duration(mean([x["duration_ms"] for x in fastest[1]]))}</span>'
                        if fastest else '<span class="note">—</span>')
        tag = '<span class="tag core">核心集</span>' if info.core else ""
        klass = f'<span class="tag">{esc(CLASS_LABELS.get(info.class_, info.class_))}</span>' if info.class_ else ""
        return (
            f'<tr><td><a href="{esc(case_href("", info.id))}">{esc(info.title)}</a>{tag}{klass}'
            f'<br><span class="note">{esc(info.brief[:80])}</span></td>'
            f'<td class="num">{len(evaluated)}</td>'
            f'<td>{badge(Verdicts(summary.passes, summary.fails, summary.unknown)) if evaluated else "—"} '
            f'<span class="note">{summary.passes} 通过 · {summary.fails} 未通过 · {summary.unknown} 未评</span></td>'
            f'<td>{cheapest_html}</td><td>{fastest_html}</td><td class="num">{esc(last)}</td></tr>'
        )

    head = ('<thead><tr><th>任务</th><th class="num">模型数</th><th>结果</th><th>最便宜的通过者</th>'
            '<th>最快的通过者</th><th class="num">最近运行</th></tr></thead>')
    core_rows = [case_row(c) for c in cases if c.core]
    other_rows = [case_row(c) for c in cases if not c.core]
    sections = []
    if core_rows:
        sections.append(f'<h2>核心集 <span class="note">每个新模型必跑</span></h2><table>{head}<tbody>{"".join(core_rows)}</tbody></table>')
    if other_rows:
        sections.append(f'<h2>{"更多用例" if core_rows else "用例"}</h2><table>{head}<tbody>{"".join(other_rows)}</tbody></table>')

    model_rows = []
    for runner in runners:
        cells = [(case, rows) for (case, label), rows in ledger.latest.items() if label == runner]
        v = Verdicts(
            passes=sum(verdicts(rows).evaluated > 0 and verdicts(rows).fails == 0 for _, rows in cells),
            fails=sum(verdicts(rows).evaluated > 0 and verdicts(rows).passes == 0 for _, rows in cells),
            unknown=sum(verdicts(rows).evaluated == 0 for _, rows in cells),
        )
        costs = [cell_cost(rows) for _, rows in cells]
        spent = sum(c for c in costs if c is not None) if any(c is not None for c in costs) else None
        profile = ledger.book.profiles.get(runner)
        last = max(max(r["started_at"] for r in rows) for _, rows in cells)[:10]
        model_rows.append(
            f'<tr><td>{marker(runner)}<a href="{esc(model_href("", runner))}">{esc(ledger.runner_name(runner))}</a>'
            f'<br><span class="note">{esc(profile.vendor if profile else "")} {esc(profile.model if profile else "")}</span></td>'
            f'<td class="num">{len(cells)}</td><td>{badge(v)} <span class="note">{v.passes} 通过 · {v.fails} 未通过 · {v.unknown} 未评</span></td>'
            f'<td class="num">{fmt_cost(spent)}</td><td class="num">{esc(last)}</td></tr>'
        )
    sections.append(
        '<h2>模型 <span class="note">按最新一轮汇总，不同任务不合成总分</span></h2>'
        '<table><thead><tr><th>模型</th><th class="num">任务数</th><th>结果</th><th class="num">累计成本</th><th class="num">最近运行</th></tr></thead>'
        f'<tbody>{"".join(model_rows)}</tbody></table>'
    )
    total_runs = len(ledger.runs)
    body = (
        '<span class="eyebrow">Real tasks · Real outputs · Real cost</span>'
        '<h1>用自己的真实任务，看每个模型到底干成了没。</h1>'
        '<p class="lead">同一份任务交给不同模型，把交付的作品、通过与否、耗时和成本摆在一起。'
        '公开榜单回答"这个模型多强"，这里回答"它在我这类活上干成了没、花了多少钱、作品长什么样"。</p>'
        f'<p class="meta">{len(ledger.cases)} 个任务 · {len(runners)} 个模型 · {len(ledger.records)} 条运行记录 · {total_runs} 轮 · '
        f'<a href="data/index.json">原始数据</a> · <a href="{REPO_URL}#加一个用例">加入自己的任务 ↗</a></p>'
        + "".join(sections)
    )
    return page(title="DoneBench", body=body, prefix="", crumbs=[],
                description="真实个人任务的模型评测库：并排交付物、通过与否、耗时与成本。")


# ------------------------------------------------------------------ 用例页


def _dims_html(judge: dict[str, Any] | None) -> str:
    if not judge or not judge.get("dimensions"):
        return ""
    return '<div class="dims">' + "".join(
        f'<span>{esc(k)} <b>{v:g}</b></span>' for k, v in judge["dimensions"].items()
    ) + "</div>"


def _artifact_links(prefix: str, row: dict[str, Any]) -> str:
    if not row["artifacts"]:
        return '<p class="files">本轮无公开交付物</p>'
    links = "".join(
        f'<a href="{esc(prefix + a["path"])}" target="_blank" rel="noopener noreferrer">{esc(Path(a["path"]).name)}</a>'
        for a in row["artifacts"]
    )
    return f'<p class="files">交付物：{links}</p>'


def _work_card(ledger: Ledger, prefix: str, info: CaseInfo, runner: str, rows: list[dict[str, Any]]) -> str:
    first = min(rows, key=lambda r: r["repeat_index"])
    v = verdicts(rows)
    score = score_of(rows)
    output = first["output"]
    preview = ""
    if output and Path(output).suffix.lower() in PREVIEW_SUFFIXES:
        preview = render_html_preview(ledger.site / output, f"preview-{runner}-{first['run_id']}", ledger.runner_name(runner))
    elif first["is_error"]:
        preview = '<p class="note">运行失败：启动器报错或超时，下面的文件是工作目录快照，不是完成的交付。</p>'
    else:
        preview = ""
    open_link = (f'<a class="work-open" href="{esc(prefix + output)}" target="_blank" rel="noopener noreferrer">打开原始作品 ↗</a>'
                 if output else "")
    judge = first.get("judge")
    reasoning = ""
    if judge and judge.get("reasoning"):
        reasoning = f'<details><summary>裁判依据（{esc(judge.get("model") or "LLM")}{"，与被评模型同源" if judge.get("same_source") else ""}）</summary><pre>{esc(judge["reasoning"])}</pre></details>'
    score_html = f'<strong>{score[0]:g}</strong> / {score[1]:g}' if score else '<strong>—</strong>'
    return (
        f'<article class="work"><h4><span>{marker(runner)}<a href="{esc(model_href(prefix, runner))}">{esc(ledger.runner_name(runner))}</a></span>{badge(v)}</h4>'
        f'{preview}'
        f'<div class="work-measures"><span>参考分 {score_html}</span><span>耗时 <strong>{esc(fmt_duration(mean([r["duration_ms"] for r in rows])))}</strong></span>'
        f'<span>成本 <strong>{esc(fmt_cost(cell_cost(rows)))}</strong></span><span>token <strong>{esc(fmt_tokens(first.get("usage")))}</strong></span></div>'
        f'{_dims_html(judge)}{open_link}{_artifact_links(prefix, first)}{reasoning}'
        f'<p class="meta">运行 {esc(first["started_at"][:10])} · {esc(first["run_id"])}{" · " + str(len(rows)) + " 次重复取均值" if len(rows) > 1 else ""}</p>'
        '</article>'
    )


def _sparkline(ledger: Ledger, info: CaseInfo, runners: list[str]) -> str:
    dates = sorted({rows[0]["started_at"][:10] for runner in runners for rows in ledger.history.get((info.id, runner), [])})
    if len(dates) < 2:
        return ""
    width, height, left, top = 520, 120, 36, 10
    plot_w, plot_h = width - left - 12, height - top - 26
    x_of = {d: left + i * plot_w / (len(dates) - 1) for i, d in enumerate(dates)}
    parts = [f'<svg class="spark" viewBox="0 0 {width} {height}" role="img" aria-label="参考分随运行日期的变化">']
    for frac in (0, 0.5, 1):
        y = top + plot_h - frac * plot_h
        parts.append(f'<line class="axis" x1="{left}" y1="{y:.1f}" x2="{width - 12}" y2="{y:.1f}"/><text x="0" y="{y + 3:.1f}">{frac * 100:.0f}%</text>')
    for d in dates:
        parts.append(f'<text x="{x_of[d]:.1f}" y="{height - 6}" text-anchor="middle">{esc(d[5:])}</text>')
    for runner in runners:
        points = []
        for rows in ledger.history.get((info.id, runner), []):
            score = score_of(rows)
            if not score or not score[1]:
                continue
            x = x_of[rows[0]["started_at"][:10]]
            y = top + plot_h - (score[0] / score[1]) * plot_h
            points.append((x, y, verdicts(rows)))
        if not points:
            continue
        color = runner_color(runner)
        if len(points) > 1:
            parts.append(f'<polyline fill="none" stroke="{color}" stroke-width="2" points="{" ".join(f"{x:.1f},{y:.1f}" for x, y, _ in points)}"/>')
        for x, y, v in points:
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{color if v.klass != "bad" else "#fff"}" stroke="{color}" stroke-width="2"><title>{esc(ledger.runner_name(runner))} {esc(v.label)}</title></circle>')
    parts.append("</svg>")
    return "".join(parts)


def render_case(ledger: Ledger, info: CaseInfo) -> str:
    prefix = "../../"
    runners = sorted({label for (case, label) in ledger.latest if case == info.id})
    ranked = sorted(runners, key=lambda r: (
        -(verdicts(ledger.latest[(info.id, r)]).passes), -(score_of(ledger.latest[(info.id, r)]) or (0, 1))[0], r))
    cards = "".join(_work_card(ledger, prefix, info, runner, ledger.latest[(info.id, runner)]) for runner in ranked)
    columns = min(3, max(1, len(ranked)))

    rows_html = []
    for runner in ranked:
        rows = ledger.latest[(info.id, runner)]
        score = score_of(rows)
        rows_html.append(
            f'<tr><td>{marker(runner)}<a href="{esc(model_href(prefix, runner))}">{esc(ledger.runner_name(runner))}</a></td>'
            f'<td>{badge(verdicts(rows))}</td><td class="num">{f"{score[0]:g} / {score[1]:g}" if score else "—"}</td>'
            f'<td class="num">{esc(fmt_duration(mean([r["duration_ms"] for r in rows])))}</td>'
            f'<td class="num">{esc(fmt_tokens(rows[0].get("usage")))}</td><td class="num">{esc(fmt_cost(cell_cost(rows)))}</td>'
            f'<td class="num">{esc(rows[0]["started_at"][:10])}</td></tr>'
        )
    history_rows = []
    for runner in ranked:
        for rows in ledger.history.get((info.id, runner), []):
            score = score_of(rows)
            history_rows.append(
                f'<tr><td>{marker(runner)}{esc(ledger.runner_name(runner))}</td><td class="num">{esc(rows[0]["started_at"][:10])}</td>'
                f'<td>{badge(verdicts(rows))}</td><td class="num">{f"{score[0]:g} / {score[1]:g}" if score else "—"}</td>'
                f'<td class="num">{esc(fmt_duration(mean([r["duration_ms"] for r in rows])))}</td><td class="num">{esc(fmt_cost(cell_cost(rows)))}</td>'
                f'<td><span class="note">{esc(rows[0]["run_id"])}</span></td></tr>'
            )
    spark = _sparkline(ledger, info, ranked)
    repo_case = f"{REPO_URL}/tree/main/cases/{info.id}"
    tag = '<span class="tag core">核心集</span>' if info.core else ""
    klass = f'<span class="tag">{esc(CLASS_LABELS.get(info.class_, info.class_))}</span>' if info.class_ else ""
    body = (
        f'<span class="eyebrow">Case</span><h1>{esc(info.title)}{tag}{klass}</h1>'
        f'<p class="lead">{esc(info.brief)}</p>'
        f'<p class="meta">通过判据：{esc(info.criteria)} · <a href="{esc(repo_case)}">题目、rubric 与 check 脚本 ↗</a></p>'
        f'<h2>最新一轮 <span class="note">每个模型取最近一次运行，按通过与参考分排序</span></h2>'
        f'<section class="work-section"><div class="work-grid" style="grid-template-columns:repeat({columns},minmax(0,1fr))">{cards}</div></section>'
        '<h2>并排指标</h2><table><thead><tr><th>模型</th><th>结果</th><th class="num">参考分</th><th class="num">耗时</th>'
        f'<th class="num">token</th><th class="num">成本</th><th class="num">日期</th></tr></thead><tbody>{"".join(rows_html)}</tbody></table>'
        '<p class="note">成本按厂商官方牌价与运行时的 token 用量计算，缓存读写分开计价；没有价格表的模型显示 —。</p>'
        f'<h2>历史 <span class="note">同一任务跨运行、跨模型代际的记录</span></h2>{spark}'
        '<table><thead><tr><th>模型</th><th class="num">日期</th><th>结果</th><th class="num">参考分</th><th class="num">耗时</th><th class="num">成本</th><th>运行</th></tr></thead>'
        f'<tbody>{"".join(history_rows)}</tbody></table>'
    )
    return page(title=info.title, body=body, prefix=prefix,
                crumbs=[("首页", prefix + "index.html")], description=info.brief or info.title)


# ------------------------------------------------------------------ 模型页


def render_model(ledger: Ledger, runner: str) -> str:
    prefix = "../../"
    profile = ledger.book.profiles.get(runner)
    cells = sorted(((case, rows) for (case, label), rows in ledger.latest.items() if label == runner),
                   key=lambda item: (not ledger.cases[item[0]].core, item[0]))
    rows_html = []
    for case, rows in cells:
        info = ledger.cases[case]
        score = score_of(rows)
        first = min(rows, key=lambda r: r["repeat_index"])
        link = (f' · <a href="{esc(prefix + first["output"])}" target="_blank" rel="noopener noreferrer">作品 ↗</a>'
                if first["output"] else "")
        rows_html.append(
            f'<tr><td><a href="{esc(case_href(prefix, case))}">{esc(info.title)}</a>{"<span class=\"tag core\">核心集</span>" if info.core else ""}{link}</td>'
            f'<td>{badge(verdicts(rows))}</td><td class="num">{f"{score[0]:g} / {score[1]:g}" if score else "—"}</td>'
            f'<td class="num">{esc(fmt_duration(mean([r["duration_ms"] for r in rows])))}</td><td class="num">{esc(fmt_tokens(first.get("usage")))}</td>'
            f'<td class="num">{esc(fmt_cost(cell_cost(rows)))}</td><td class="num">{esc(first["started_at"][:10])}</td></tr>'
        )
    price = ledger.book.price_for(runner, datetime.now(timezone.utc).strftime("%Y-%m-%d")) if profile else None
    price_html = ""
    if price:
        price_html = (f'当前牌价 {price.currency} {price.input_per_m:g} / {price.output_per_m:g} 每百万 token（输入 / 输出）'
                      + (f'，缓存读 {price.cache_read_per_m:g}' if price.cache_read_per_m is not None else "")
                      + (f' · <a href="{esc(price.source)}">来源 ↗</a>' if price.source else ""))
    summary = Verdicts(
        passes=sum(verdicts(rows).evaluated > 0 and verdicts(rows).fails == 0 for _, rows in cells),
        fails=sum(verdicts(rows).evaluated > 0 and verdicts(rows).passes == 0 for _, rows in cells),
        unknown=sum(verdicts(rows).evaluated == 0 for _, rows in cells),
    )
    body = (
        f'<span class="eyebrow">Model</span><h1>{marker(runner)}{esc(ledger.runner_name(runner))}</h1>'
        f'<p class="lead">{esc(profile.vendor if profile else "")} {esc(profile.model if profile else runner)}'
        f'{" · 发布于 " + esc(profile.released) if profile and profile.released else ""}</p>'
        f'<p class="meta">{len(cells)} 个任务 · {summary.passes} 通过 · {summary.fails} 未通过 · {summary.unknown} 未评'
        f'{" · " + price_html if price_html else ""}</p>'
        f'{"<p class=note>" + esc(profile.note) + "</p>" if profile and profile.note else ""}'
        '<h2>各任务最新表现</h2><table><thead><tr><th>任务</th><th>结果</th><th class="num">参考分</th><th class="num">耗时</th>'
        f'<th class="num">token</th><th class="num">成本</th><th class="num">日期</th></tr></thead><tbody>{"".join(rows_html)}</tbody></table>'
        '<p class="note">不同任务的分数不相加，不合成总分。</p>'
    )
    return page(title=ledger.runner_name(runner), body=body, prefix=prefix,
                crumbs=[("首页", prefix + "index.html")])


# ------------------------------------------------------------------ 首图与索引


def featured_data(ledger: Ledger) -> dict[str, Any] | None:
    featured = ledger.featured
    if not featured:
        return None
    run_id = featured["run_id"]
    case_ids = [c["id"] for c in featured["cases"]]
    runners = list(featured["runners"])
    if len(case_ids) != 2 or len(runners) != 2:
        raise SiteError("featured.json 当前只支持 2 个任务 × 2 个模型的首图编排")
    records = []
    for row in ledger.records:
        if row["run_id"] == run_id and row["case"] in case_ids and row["runner_label"] in runners and row["repeat_index"] == 0:
            if not row["output"] or not row["output"].endswith(".svg") or not row.get("judge"):
                raise SiteError(f"首图格 {row['case']}/{row['runner_label']} 需要 SVG 交付物与裁判分")
            records.append({**row, "svg": row["output"]})
    if len(records) != 4:
        raise SiteError("featured.json 指定的四格在账本里不完整")
    return {
        "date": next(r for r in ledger.runs if r["run_id"] == run_id)["date"],
        "runners": runners,
        "names": {r: ledger.runner_name(r) for r in runners},
        "cases": [{"id": c["id"], "title": c.get("title") or ledger.cases[c["id"]].title, "note": c.get("note", "")}
                  for c in featured["cases"]],
        "records": records,
    }


def index_json(ledger: Ledger) -> dict[str, Any]:
    return {
        "schema_version": LEDGER_SCHEMA,
        "generated_from": [r["run_id"] for r in ledger.runs],
        "cases": [{"id": c.id, "title": c.title, "class": c.class_, "core": c.core} for c in ledger.cases.values()],
        "runners": sorted({r["runner_label"] for r in ledger.records}),
        "latest": [
            {"case": case, "runner": runner, "run_id": rows[0]["run_id"], "date": rows[0]["started_at"][:10],
             "verdict": verdicts(rows).label, "score": score_of(rows), "cost_usd": cell_cost(rows),
             "duration_ms": mean([r["duration_ms"] for r in rows]), "output": min(rows, key=lambda r: r["repeat_index"])["output"]}
            for (case, runner), rows in sorted(ledger.latest.items())
        ],
    }


def build(site: Path = SITE, *, check: bool = False, book: ModelBook | None = None) -> list[Path]:
    ledger = load_ledger(site, book)
    files: dict[Path, bytes] = {
        Path("index.html"): render_index(ledger).encode("utf-8"),
        Path("data/index.json"): (json.dumps(index_json(ledger), ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        Path(".nojekyll"): b"",
    }
    for info in ledger.cases.values():
        files[Path("cases") / info.id / "index.html"] = render_case(ledger, info).encode("utf-8")
    for runner in sorted({r["runner_label"] for r in ledger.records}):
        files[Path("models") / runner / "index.html"] = render_model(ledger, runner).encode("utf-8")
    featured = featured_data(ledger)
    if featured:
        files[Path("assets/report.svg")] = render_showcase(featured, site)
        files[Path("assets/report-mobile.svg")] = render_showcase(featured, site, mobile=True)
    approved = hashes(ledger.records)
    for relative, content in files.items():
        if relative.suffix in {".html", ".json"}:
            public_text(_strip_generated_stamp(content.decode("utf-8")), approved)
    for relative, content in files.items():
        destination = site / relative
        if check:
            if not destination.is_file() or _strip_generated_stamp(destination.read_bytes().decode("utf-8", "replace")) != _strip_generated_stamp(content.decode("utf-8", "replace")):
                raise SiteError(f"站点产物需要重建：{relative}")
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
    return list(files)


def _strip_generated_stamp(text: str) -> str:
    return re.sub(r"站点生成于 \d{4}-\d{2}-\d{2} UTC", "站点生成于 DATE UTC", text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--site", type=Path, default=SITE)
    parser.add_argument("--check", action="store_true", help="只校验产物与账本一致，不写入")
    args = parser.parse_args(argv)
    try:
        files = build(args.site, check=args.check)
    except (SiteError, ValueError, OSError, KeyError) as exc:
        parser.exit(1, f"site: {exc}\n")
    print(f"{'Verified' if args.check else 'Generated'} {len(files)} site files under {args.site}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
