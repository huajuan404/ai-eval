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
import hashlib
import json
from collections import defaultdict
from dataclasses import replace
from datetime import date
from difflib import unified_diff
from pathlib import Path
from typing import Any

from .case import Case
from .comparison import RunnerComparison
from .completion import CellCompletion, cell_completion, repeat_pass
from .layout import RunLayout
from .record import RunRecord
from .run_manifest import RunManifestError, load_request_manifest
from .scorecard import (
    CellAgg,
    _aggregate,
    _cell_label,
    _fmt,
    _judging_summary,
    _task_brief,
    _winners,
)
from .scrub import scrub_text

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
--ok:#176a51;--ok-bg:#ddf2e9;--ok-line:#9fd2bf;--bad:#a63f50;--bad-bg:#f8e1e5;
--bad-line:#e5aeb7;--warn:#885c16;--warn-bg:#f8edcf;--warn-line:#dfc681;
--na:#6f7d91;--na-bg:#f0f3f7;--na-line:#d5dce6;--accent:#175cd3;
--diff-add:#116329;--diff-add-bg:#dafbe1;--diff-del:#82071e;--diff-del-bg:#ffebe9;
--diff-hunk:#0550ae;--diff-hunk-bg:#ddf4ff;}
@media (prefers-color-scheme: dark){:root{--bg:#101418;--fg:#e6e9ee;--muted:#98a2b3;
--line:#2b3440;--card:#171d24;--ok:#72c7aa;--ok-bg:#173128;--ok-line:#285c4b;
--bad:#e99aa3;--bad-bg:#381f24;--bad-line:#69404a;--warn:#dfb469;--warn-bg:#332a19;
--warn-line:#65512d;--na:#9aa8ba;--na-bg:#202833;--na-line:#364252;--accent:#7ab3ff;
--diff-add:#7ee787;--diff-add-bg:#12261e;--diff-del:#ffa198;--diff-del-bg:#31171b;
--diff-hunk:#79c0ff;--diff-hunk-bg:#121d2f;}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",
"Hiragino Sans GB","Microsoft YaHei",sans-serif;padding:0 24px 64px;}
main{max-width:1100px;margin:0 auto}
h1{font-size:24px;margin:32px 0 4px}
h2{font-size:19px;margin:40px 0 12px;padding-top:16px;border-top:1px solid var(--line)}
h3{font-size:16px;margin:24px 0 8px}
h4{font-size:14px;margin:18px 0 6px}
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
tbody tr{transition:background-color .16s ease}
tbody tr:hover{background:color-mix(in srgb,var(--accent) 3%,transparent)}
th{background:var(--card);font-weight:600;position:sticky;top:0}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.badge{display:inline-flex;align-items:center;gap:6px;border:1px solid transparent;
border-radius:999px;padding:2px 9px;font-size:12.5px;font-weight:650;line-height:1.45;
letter-spacing:.01em;white-space:nowrap}
.badge::before,.result-cell .signal::before{content:"";width:6px;height:6px;border-radius:50%;
background:currentColor;box-shadow:0 0 0 3px color-mix(in srgb,currentColor 13%,transparent);
flex:0 0 auto}
.badge.ok{color:var(--ok);background:var(--ok-bg);border-color:var(--ok-line)}
.badge.bad{color:var(--bad);background:var(--bad-bg);border-color:var(--bad-line)}
.badge.warn{color:var(--warn);background:var(--warn-bg);border-color:var(--warn-line)}
.badge.na{color:var(--na);background:var(--na-bg);border-color:var(--na-line)}
.result-grid td.result-cell{padding:0;border-left:1px solid var(--bg);border-right:1px solid var(--bg);
font-variant-numeric:tabular-nums;transition:filter .16s ease,box-shadow .16s ease}
.result-cell .signal{display:flex;align-items:center;justify-content:center;gap:7px;min-height:36px;
padding:7px 12px;color:inherit;font-weight:700;letter-spacing:.015em;white-space:nowrap}
.result-cell.ok{color:var(--ok);background:var(--ok-bg);box-shadow:inset 0 1px var(--ok-line)}
.result-cell.bad{color:var(--bad);background:var(--bad-bg);box-shadow:inset 0 1px var(--bad-line)}
.result-cell.warn{color:var(--warn);background:var(--warn-bg);box-shadow:inset 0 1px var(--warn-line)}
.result-cell.na{color:var(--na);background:var(--na-bg);box-shadow:inset 0 1px var(--na-line)}
.result-grid tbody tr:hover .result-cell{filter:saturate(1.08);box-shadow:inset 0 0 0 1px currentColor}
.case-matrix th[scope=row]{min-width:240px;max-width:420px;white-space:normal}
.case-matrix td.result-cell{min-width:176px}
.case-matrix .signal{min-height:52px;flex-direction:column;gap:6px;text-transform:uppercase;
font:750 12px/1.2 ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace;
letter-spacing:.065em}
.case-matrix .signal::before{display:none}
.repeat-strip{display:grid;grid-auto-flow:column;grid-auto-columns:minmax(12px,1fr);gap:4px;
width:min(150px,100%)}
.repeat-tile{height:8px;border-radius:2px;border:1px solid color-mix(in srgb,currentColor 28%,transparent);
box-shadow:inset 0 1px rgba(255,255,255,.36)}
.repeat-tile.ok{color:var(--ok);background:var(--ok)}
.repeat-tile.bad{color:var(--bad);background:var(--bad)}
.repeat-tile.na{color:var(--na);background:var(--na)}
.case-matrix .summary-row th,.case-matrix .summary-row td{border-bottom:2px solid var(--line)}
.note{color:var(--muted);font-size:13px;margin:6px 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;
padding:12px 16px;margin:10px 0}
details{border:1px solid var(--line);border-radius:8px;margin:8px 0;background:var(--card)}
details>summary{cursor:pointer;padding:8px 14px;font-weight:600;font-size:13.5px}
details>.body{padding:0 14px 12px}
pre{background:var(--bg);border:1px solid var(--line);border-radius:6px;
padding:10px;font-size:12.5px;overflow-x:auto;white-space:pre-wrap;word-break:break-word}
.diff{padding:0;white-space:pre-wrap;word-break:break-word}
.diff-line{display:block;padding:0 10px;min-height:1.65em}
.diff-line.add{color:var(--diff-add);background:var(--diff-add-bg)}
.diff-line.del{color:var(--diff-del);background:var(--diff-del-bg)}
.diff-line.hunk{color:var(--diff-hunk);background:var(--diff-hunk-bg)}
.diff-line.meta{color:var(--muted);font-style:italic}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
code{background:var(--na-bg);border-radius:4px;padding:0 5px;font-size:12.5px}
.winner{font-weight:600}
"""


def _chip(label: str, value: object) -> str:
    return f'<span class="chip">{_e(label)} <b>{_e(value)}</b></span>'


def _badge(text: str, klass: str) -> str:
    return f'<span class="badge {klass}">{_e(text)}</span>'


def _result_cell(text: str, klass: str) -> str:
    """W3C implementation-report 式结果格：整格传达状态，文字保留可访问性。"""
    return f'<td class="result-cell {klass}"><span class="signal">{_e(text)}</span></td>'


def _completion_result_cell(records: list[RunRecord], case: Case) -> str:
    """渲染逐 repeat 结果色块；汇总文字不再掩盖具体失败轮次。"""
    ordered = sorted(records, key=lambda record: record.repeat_index)
    verdicts = [repeat_pass(record, case) for record in ordered]
    comp = cell_completion(ordered, case)
    failed = comp.evaluated - comp.passes
    if comp.evaluated == 0:
        label = "NO DATA"
    elif comp.evaluated == 1:
        label = "PASS" if comp.passes else "FAIL"
    elif failed == 0:
        label = f"{comp.passes}/{comp.evaluated} PASS"
    elif comp.passes == 0:
        label = f"0/{comp.evaluated} PASS"
    else:
        label = f"{comp.passes} PASS · {failed} FAIL"
    tiles = "".join(
        f'<span class="repeat-tile {"ok" if verdict is True else "bad" if verdict is False else "na"}" '
        f'title="repeat-{record.repeat_index}: '
        f'{"passed" if verdict is True else "failed" if verdict is False else "not evaluated"}"></span>'
        for record, verdict in zip(ordered, verdicts, strict=True)
    )
    return (
        f'<td class="result-cell {_completion_class(comp)}" aria-label="{_e(label)}">'
        f'<span class="signal"><span>{_e(label)}</span>'
        f'<span class="repeat-strip" aria-hidden="true">{tiles}</span></span></td>'
    )


def _completion_badge(comp: CellCompletion) -> str:
    return _badge(comp.display, _completion_class(comp))


def _text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical_json_sha256(value: object) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _decoded_request_input(payload: object) -> str:
    """把请求体中的模型输入字段无损解码成人可读文本，不混入模型参数。"""
    if not isinstance(payload, dict):
        raise ReportError("request payload 顶层不是对象")
    parts: list[str] = []
    if "system" in payload:
        system = payload["system"]
        rendered = system if isinstance(system, str) else json.dumps(system, ensure_ascii=False, indent=2)
        parts.append(f"[system]\n{rendered}")
    messages = payload.get("messages")
    if messages is not None:
        if not isinstance(messages, list):
            raise ReportError("request payload.messages 不是列表")
        for index, message in enumerate(messages):
            if not isinstance(message, dict):
                raise ReportError(f"request payload.messages[{index}] 不是对象")
            role = message.get("role", "unknown")
            content = message.get("content")
            rendered = (
                content
                if isinstance(content, str)
                else json.dumps(content, ensure_ascii=False, indent=2)
            )
            parts.append(f"[message {index} role={role}]\n{rendered}")
    for field in ("prompt", "input"):
        if field in payload:
            value = payload[field]
            rendered = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
            parts.append(f"[{field}]\n{rendered}")
    if not parts:
        raise ReportError("request payload 不含 system/messages/prompt/input")
    return "\n\n".join(parts)


def _add_evidence_version(
    versions: dict[str, dict[str, Any]], text: str, origin: str
) -> None:
    digest = _text_sha256(text)
    entry = versions.setdefault(digest, {"text": text, "origins": []})
    entry["origins"].append(origin)


def _declared_request_units(artifacts: Path) -> set[str] | None:
    """读取 protocol 可选的 batch 声明；没有声明时返回 None。"""
    manifest_path = artifacts / "batch_manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        batches = manifest["batches"]
        if not isinstance(batches, list):
            raise TypeError("batches 不是列表")
        units = {
            str(batch["id"])
            for batch in batches
            if isinstance(batch, dict) and batch.get("id") is not None
        }
        if len(units) != len(batches):
            raise TypeError("batch id 缺失或重复")
        return units
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ReportError(f"无法读取 batch_manifest.json: {exc}") from exc


def _collect_prompt_evidence(
    case_obj: Case,
    case_records: dict[tuple[str, str], list[RunRecord]],
) -> dict[tuple[str, str], dict[str, Any]]:
    evidence: dict[tuple[str, str], dict[str, Any]] = {}
    for (variant, runner), records in sorted(case_records.items()):
        group_data = evidence.setdefault(
            (runner, variant),
            {
                "templates": {},
                "parameters": {},
                "inputs": defaultdict(dict),
                "errors": [],
                "actual_units": {},
                "declared_units": {},
                "missing_manifests": [],
                "request_surfaces": {},
            },
        )
        for record in records:
            origin = f"{record.runner_label}/repeat-{record.repeat_index}"
            artifacts = Path(record.artifacts_dir)

            prompt_path = artifacts / "PROMPT.txt"
            try:
                prompt_text = prompt_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                group_data["errors"].append(f"{origin}: 无法读取 PROMPT.txt: {exc}")
            else:
                if not record.prompt_template_sha256:
                    group_data["errors"].append(
                        f"{origin}: run record 缺少 prompt hash，拒绝展示未锚定模板"
                    )
                elif _text_sha256(prompt_text) != record.prompt_template_sha256:
                    group_data["errors"].append(
                        f"{origin}: PROMPT.txt 与 run record hash 不一致"
                    )
                else:
                    _add_evidence_version(group_data["templates"], prompt_text, origin)

            context_path = artifacts / "RUN_CONTEXT.json"
            try:
                context_bytes = context_path.read_bytes()
                if not record.run_context_sha256:
                    raise ReportError("run record 缺少 RUN_CONTEXT hash")
                if hashlib.sha256(context_bytes).hexdigest() != record.run_context_sha256:
                    raise ReportError("RUN_CONTEXT.json 与 run record hash 不一致")
                context = json.loads(context_bytes.decode("utf-8"))
                parameters = context["variant"]["parameters"]
                if not isinstance(parameters, dict):
                    raise TypeError("variant.parameters 不是对象")
                parameters_text = json.dumps(
                    parameters, ensure_ascii=False, indent=2, sort_keys=True
                )
            except (
                OSError,
                UnicodeDecodeError,
                json.JSONDecodeError,
                KeyError,
                TypeError,
                ReportError,
            ) as exc:
                group_data["errors"].append(f"{origin}: 无法读取 variant 参数: {exc}")
            else:
                _add_evidence_version(
                    group_data["parameters"], parameters_text, origin
                )

            try:
                declared_units = _declared_request_units(artifacts)
            except ReportError as exc:
                group_data["errors"].append(f"{origin}: {exc}")
                declared_units = None
            if declared_units is None and not (artifacts / "batch_manifest.json").exists():
                group_data["missing_manifests"].append(origin)
            group_data["declared_units"][origin] = declared_units

            request_dir = artifacts / "request_payloads"
            request_paths = sorted(request_dir.glob("*.json")) if request_dir.is_dir() else []
            request_hashes: dict[str, str] = {}
            request_manifest_file = record.request_manifest_file
            request_manifest_verified = False
            if request_paths and not request_manifest_file:
                group_data["errors"].append(
                    f"{origin}: run record 未记录 request manifest，拒绝展示未锚定的实际输入"
                )
            if request_manifest_file:
                request_manifest_path = artifacts / request_manifest_file
                if not request_manifest_path.is_file():
                    group_data["errors"].append(
                        f"{origin}: {request_manifest_file} 缺失，无法校验请求体"
                    )
                else:
                    try:
                        if not record.request_manifest_sha256:
                            raise RunManifestError(
                                "run record 缺少 request manifest hash"
                            )
                        if (
                            hashlib.sha256(request_manifest_path.read_bytes()).hexdigest()
                            != record.request_manifest_sha256
                        ):
                            raise RunManifestError(
                                "request manifest 与 run record hash 不一致"
                            )
                        request_manifest = load_request_manifest(
                            request_manifest_path,
                            allow_failed_requests=record.is_error,
                        )
                    except (OSError, RunManifestError) as exc:
                        group_data["errors"].append(
                            f"{origin}: request manifest 非法: {exc}"
                        )
                    else:
                        if request_manifest["schema_version"] == 2:
                            request_hashes = {
                                str(request["unit_id"]): str(request["request_sha256"])
                                for request in request_manifest["requests"]
                            }
                            request_manifest_verified = True
                        else:
                            group_data["errors"].append(
                                f"{origin}: request manifest v1 不含请求体 hash，拒绝展示实际输入"
                            )
            group_data["request_surfaces"][origin] = (
                (artifacts / "batch_manifest.json").is_file() or bool(request_paths)
            )
            if request_dir.is_dir() and not request_paths and declared_units is None:
                group_data["errors"].append(
                    f"{origin}: request_payloads/ 目录为空"
                )
            actual_units = {path.stem for path in request_paths}
            group_data["actual_units"][origin] = actual_units
            for request_path in request_paths:
                try:
                    payload = json.loads(request_path.read_text(encoding="utf-8"))
                    actual_input = _decoded_request_input(payload)
                except (
                    OSError,
                    UnicodeDecodeError,
                    json.JSONDecodeError,
                    ReportError,
                ) as exc:
                    group_data["errors"].append(
                        f"{origin}/{request_path.name}: 无法读取实际输入: {exc}"
                    )
                    continue
                expected_request_hash = request_hashes.get(request_path.stem)
                if not request_manifest_verified:
                    continue
                if request_manifest_file and expected_request_hash is None:
                    group_data["errors"].append(
                        f"{origin}/{request_path.name}: request manifest 未声明该请求体"
                    )
                    continue
                elif (
                    expected_request_hash is not None
                    and _canonical_json_sha256(payload) != expected_request_hash
                ):
                    group_data["errors"].append(
                        f"{origin}/{request_path.name}: 请求体与 request manifest hash 不一致"
                    )
                    continue
                _add_evidence_version(
                    group_data["inputs"][request_path.stem], actual_input, origin
                )
        declared_sets = {
            frozenset(units)
            for units in group_data["declared_units"].values()
            if units is not None
        }
        if len(declared_sets) > 1:
            group_data["errors"].append(
                "各 cell 的 batch_manifest 声明不一致"
            )
        if declared_sets:
            for origin in group_data["missing_manifests"]:
                group_data["errors"].append(
                    f"{origin}: batch_manifest.json 缺失，无法核对声明 batch"
                )
        for origin, actual_units in group_data["actual_units"].items():
            declared_units = group_data["declared_units"].get(origin)
            if declared_units is None:
                continue
            missing = sorted(declared_units - actual_units)
            unexpected = sorted(actual_units - declared_units)
            if missing:
                group_data["errors"].append(
                    f"{origin}: 缺少声明 batch: {', '.join(missing)}"
                )
            if unexpected:
                group_data["errors"].append(
                    f"{origin}: 出现未声明 batch: {', '.join(unexpected)}"
                )
        actual_sets = {
            frozenset(units) for units in group_data["actual_units"].values()
        }
        if len(actual_sets) > 1:
            group_data["errors"].append("各 cell 的实际输入 batch 集合不一致")

    runners_with_request_evidence = {
        runner
        for (runner, _variant), group_data in evidence.items()
        if any(group_data["request_surfaces"].values())
    }
    for (runner, _variant), group_data in evidence.items():
        group_data["request_evidence_available"] = any(
            group_data["request_surfaces"].values()
        )
        if runner not in runners_with_request_evidence:
            continue
        for origin, present in group_data["request_surfaces"].items():
            if not present:
                group_data["errors"].append(
                    f"{origin}: 未发现 request_payloads/ 或 batch_manifest.json"
                )
    return evidence


def _render_evidence_versions(
    *, label: str, versions: dict[str, dict[str, Any]], drift_message: str
) -> str:
    if not versions:
        return ""
    parts: list[str] = []
    if len(versions) > 1:
        parts.append(f'<p class="note">⚠️ {_e(drift_message)}：共 {len(versions)} 个版本。</p>')
    for digest, entry in sorted(versions.items()):
        origins = sorted(set(entry["origins"]))
        summary = f"{label} · sha256={digest[:12]} · {len(origins)} cells"
        parts.append(
            f"<details><summary>{_e(summary)}</summary>"
            f'<div class="body"><p class="note">来源：{_e(", ".join(origins))}</p>'
            f"<pre>{_e(scrub_text(entry['text']))}</pre></div></details>"
        )
    return "".join(parts)


def _render_unified_diff(
    *, title: str, baseline: str, candidate: str, baseline_text: str, candidate_text: str
) -> str:
    baseline_text = scrub_text(baseline_text)
    candidate_text = scrub_text(candidate_text)
    diff_lines = list(
        unified_diff(
            baseline_text.splitlines(),
            candidate_text.splitlines(),
            fromfile=baseline,
            tofile=candidate,
            lineterm="",
        )
    )
    if baseline_text.endswith("\n") != candidate_text.endswith("\n"):
        if not diff_lines:
            diff_lines.extend(
                [
                    f"--- {baseline}",
                    f"+++ {candidate}",
                    "@@ 文件末尾换行 @@",
                ]
            )
        missing_newline_side = (
            baseline if not baseline_text.endswith("\n") else candidate
        )
        diff_lines.append(f"\\ No newline at end of file: {missing_newline_side}")
    if not diff_lines:
        rendered_diff = '<span class="diff-line">（无差异）</span>'
    else:
        rendered_lines: list[str] = []
        for line in diff_lines:
            if line.startswith("+++"):
                klass = "add"
            elif line.startswith("---"):
                klass = "del"
            elif line.startswith("+"):
                klass = "add"
            elif line.startswith("-"):
                klass = "del"
            elif line.startswith("@@"):
                klass = "hunk"
            elif line.startswith("\\"):
                klass = "meta"
            else:
                klass = ""
            class_attr = f" diff-line {klass}".rstrip()
            rendered_lines.append(
                f'<span class="{class_attr.strip()}">{_e(line)}</span>'
            )
        rendered_diff = "".join(rendered_lines)
    return (
        f"<details><summary>{_e(title)}</summary>"
        f'<div class="body"><pre class="diff">{rendered_diff}</pre></div></details>'
    )


def _render_prompt_comparison(
    case_obj: Case,
    case_records: dict[tuple[str, str], list[RunRecord]],
) -> str:
    variants_in_run = {variant for variant, _runner in case_records}
    if len(variants_in_run) < 2:
        return ""
    evidence = _collect_prompt_evidence(case_obj, case_records)
    variant_order = [
        label for label in case_obj.task.variant_labels if label in variants_in_run
    ]
    variant_order.extend(sorted(variants_in_run - set(variant_order)))
    runners = sorted({runner for _variant, runner in case_records})
    comparison = case_obj.evaluation.comparison
    if (
        comparison is not None
        and comparison.baseline in variants_in_run
        and comparison.candidate in variants_in_run
    ):
        baseline, candidate = comparison.baseline, comparison.candidate
    else:
        baseline, candidate = variant_order[:2]

    rows: list[str] = []
    detail_parts: list[str] = []
    diff_parts: list[str] = []
    for runner in runners:
        runner_variants = [
            variant for variant in variant_order if (runner, variant) in evidence
        ]
        if baseline in runner_variants and candidate in runner_variants:
            baseline_data = evidence[(runner, baseline)]
            candidate_data = evidence[(runner, candidate)]
            baseline_has_manifest = any(
                units is not None
                for units in baseline_data["declared_units"].values()
            )
            candidate_has_manifest = any(
                units is not None
                for units in candidate_data["declared_units"].values()
            )
            neither_has_manifest = not (
                baseline_has_manifest or candidate_has_manifest
            )
            baseline_actual_sets = {
                frozenset(units)
                for units in baseline_data["actual_units"].values()
            }
            candidate_actual_sets = {
                frozenset(units)
                for units in candidate_data["actual_units"].values()
            }
            if baseline_has_manifest != candidate_has_manifest:
                message = (
                    f"{baseline} 与 {candidate} 的 batch_manifest 覆盖不一致"
                )
                baseline_data["errors"].append(message)
                candidate_data["errors"].append(message)
            elif (
                neither_has_manifest
                and baseline_data["request_evidence_available"]
                and candidate_data["request_evidence_available"]
                and len(baseline_actual_sets) == 1
                and len(candidate_actual_sets) == 1
                and baseline_actual_sets != candidate_actual_sets
            ):
                message = (
                    f"{baseline} 与 {candidate} 的实际输入 batch 集合不一致"
                )
                baseline_data["errors"].append(message)
                candidate_data["errors"].append(message)
        for variant in runner_variants:
            data = evidence[(runner, variant)]
            template_count = len(data["templates"])
            parameter_count = len(data["parameters"])
            input_units = (
                str(len(data["inputs"]))
                if data["request_evidence_available"]
                else _DASH
            )
            errors = sorted(set(data["errors"]))
            if errors:
                status, status_class = "证据不完整", "bad"
            elif not data["request_evidence_available"]:
                status, status_class = "模板完整 · 请求未采集", "na"
            else:
                status, status_class = "完整", "ok"
            rows.append(
                f"<tr><td><code>{_e(f'{runner}@{variant}')}</code></td>"
                f'<td class="num">{template_count}</td>'
                f'<td class="num">{parameter_count}</td>'
                f'<td class="num">{_e(input_units)}</td>'
                f"<td>{_badge(status, status_class)}</td></tr>"
            )
            detail_parts.append(
                f"<h4>Runner@Variant：<code>{_e(f'{runner}@{variant}')}</code></h4>"
            )
            detail_parts.append(
                _render_evidence_versions(
                    label="Prompt 模板",
                    versions=data["templates"],
                    drift_message="检测到模板漂移",
                )
            )
            detail_parts.append(
                _render_evidence_versions(
                    label="Variant 参数",
                    versions=data["parameters"],
                    drift_message="检测到 variant 参数漂移",
                )
            )
            for unit_id, versions in sorted(data["inputs"].items()):
                detail_parts.append(
                    _render_evidence_versions(
                        label=f"实际输入：{unit_id}",
                        versions=versions,
                        drift_message=f"检测到实际输入漂移（{unit_id}）",
                    )
                )
            if not data["request_evidence_available"]:
                detail_parts.append(
                    '<p class="note">实际请求体未采集：该 case/runner 未产出 '
                    '<code>request_payloads/*.json</code>；Prompt 模板与 variant 参数仍可比较。</p>'
                )
            if errors:
                error_items = "".join(f"<li>{_e(error)}</li>" for error in errors)
                detail_parts.append(
                    f'<div class="card"><b>证据读取失败</b><ul>{error_items}</ul></div>'
                )

        if baseline not in runner_variants or candidate not in runner_variants:
            continue
        baseline_data = evidence[(runner, baseline)]
        candidate_data = evidence[(runner, candidate)]
        if len(baseline_data["templates"]) == len(candidate_data["templates"]) == 1:
            baseline_template = next(iter(baseline_data["templates"].values()))["text"]
            candidate_template = next(iter(candidate_data["templates"].values()))["text"]
            diff_parts.append(
                _render_unified_diff(
                    title=(
                        f"模板差异：{baseline} → {candidate} · runner={runner}"
                    ),
                    baseline=f"{runner}/{baseline}/PROMPT.txt",
                    candidate=f"{runner}/{candidate}/PROMPT.txt",
                    baseline_text=baseline_template,
                    candidate_text=candidate_template,
                )
            )
        elif baseline_data["templates"] or candidate_data["templates"]:
            diff_parts.append(
                f'<p class="note">Runner <code>{_e(runner)}</code> 的模板存在漂移或缺失，'
                "无法生成唯一的 variant diff。</p>"
            )
        units = sorted(set(baseline_data["inputs"]) | set(candidate_data["inputs"]))
        for unit_id in units:
            baseline_versions = baseline_data["inputs"].get(unit_id, {})
            candidate_versions = candidate_data["inputs"].get(unit_id, {})
            if len(baseline_versions) == len(candidate_versions) == 1:
                diff_parts.append(
                    _render_unified_diff(
                        title=(
                            f"实际输入差异：{unit_id} · {baseline} → {candidate} "
                            f"· runner={runner}"
                        ),
                        baseline=f"{runner}/{baseline}/{unit_id}",
                        candidate=f"{runner}/{candidate}/{unit_id}",
                        baseline_text=next(iter(baseline_versions.values()))["text"],
                        candidate_text=next(iter(candidate_versions.values()))["text"],
                    )
                )
            elif baseline_versions or candidate_versions:
                diff_parts.append(
                    f'<p class="note">Runner <code>{_e(runner)}</code> 的实际输入 '
                    f'<code>{_e(unit_id)}</code> 存在漂移或缺失，'
                    "无法生成唯一的 variant diff。</p>"
                )

    return (
        "<h3>Prompt / 实际输入对比</h3>"
        '<p class="note">证据来自本次运行持久化的 cell artifacts：<code>PROMPT.txt</code>、'
        '<code>RUN_CONTEXT.json</code> 与 <code>request_payloads/*.json</code>；'
        "展示的是当时真正比较的输入，不读取当前 case 文件替代历史证据；"
        "可用时会与 run record / request manifest hash 对账。</p>"
        '<div class="tablewrap"><table><thead><tr><th>Runner@Variant</th>'
        '<th class="num">模板版本</th><th class="num">参数版本</th>'
        '<th class="num">实际输入 batch</th><th>证据状态</th></tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table></div>"
        + "".join(diff_parts)
        + "".join(detail_parts)
    )


def _render_summary(
    by_case: dict[str, dict[tuple[str, str], list[RunRecord]]],
    cases: dict[str, Case],
    case_names: list[str],
) -> str:
    """W3C 式结果墙：行 = case，列 = runner@variant，每格直接展示 pass/fail。"""
    labels = sorted(
        {
            f"{runner}@{variant}"
            for case_records in by_case.values()
            for variant, runner in case_records
        }
    )
    if not labels:
        return ""
    outcomes: dict[str, list[CellCompletion]] = defaultdict(list)
    rows: list[str] = []
    for case_name in case_names:
        case_obj = cases.get(case_name)
        if case_obj is None:
            continue
        records_by_label = {
            f"{runner}@{variant}": records
            for (variant, runner), records in by_case.get(case_name, {}).items()
        }
        cells: list[str] = []
        for label in labels:
            records = records_by_label.get(label)
            if records:
                outcomes[label].append(cell_completion(records, case_obj))
                cells.append(_completion_result_cell(records, case_obj))
            else:
                cells.append(_result_cell("NO DATA", "na"))
        rows.append(
            f'<tr><th scope="row"><code>{_e(case_name)}</code></th>{"".join(cells)}</tr>'
        )
    summary_cells: list[str] = []
    for label in labels:
        comps = outcomes[label]
        passed = sum(comp.rate == 1 for comp in comps)
        failed = sum(comp.rate == 0 for comp in comps)
        partial = sum(comp.rate not in (None, 0, 1) for comp in comps)
        no_data = sum(comp.rate is None for comp in comps)
        parts = [f"{passed} pass", f"{failed} fail"]
        if partial:
            parts.append(f"{partial} partial")
        if no_data:
            parts.append(f"{no_data} no data")
        klass = "na" if not comps or no_data == len(comps) else (
            "ok" if passed == len(comps) else "bad" if failed == len(comps) else "warn"
        )
        summary_cells.append(_result_cell(" · ".join(parts), klass))
    heads = "".join(f"<th>{_e(label)}</th>" for label in labels)
    return (
        "<h2>用例通过矩阵</h2>"
        '<p class="note">完成 = 通过用例权威判据（check 通过 / judge ≥ 阈值 / 核心维达标）；'
        "每个小条对应一次 repeat，绿色通过、红色失败、灰色未评。</p>"
        '<div class="tablewrap"><table class="result-grid case-matrix"><thead><tr><th>Case</th>'
        f'{heads}</tr></thead><tbody><tr class="summary-row"><th scope="row">总计</th>'
        f'{"".join(summary_cells)}</tr>{"".join(rows)}</tbody></table></div>'
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
            f"{int(round(c.check_pass_rate * c.check_samples))}/{c.check_samples}"
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
        model = (
            f' <span class="note">({_e(scrub_text(c.runner_model))})</span>'
            if c.runner_model
            else ""
        )
        rows.append(
            f'<tr><td><code>{_e(c.runner_label)}</code>{model}</td>'
            f"<td><code>{_e(c.variant_label)}</code>{mark}</td>"
            f'<td class="num">{c.samples}</td>'
            f'<td class="num">{c.execution_successes}/{c.samples}</td><td>{comp_html}</td>'
            f'<td class="num">{_e(pass_str)}</td>'
            f'<td class="num">{_e(_fmt(c.judge_scores))}</td>'
            f'<td class="num">{_e(_fmt(c.durations, as_int=True))}</td>'
            f'<td class="num">{_e(tok)}</td>'
            f'<td class="num">{_e(_fmt(c.costs))}</td>'
            f'<td class="num">{_e(_fmt(c.files_changed, as_int=True))}</td></tr>'
        )
    return (
        '<div class="tablewrap"><table><thead><tr>'
        "<th>Runner</th><th>Variant</th><th class='num'>轮次</th>"
        "<th class='num'>执行成功</th><th>任务完成</th>"
        "<th class='num'>check</th><th class='num'>judge</th><th class='num'>耗时(ms)</th>"
        "<th class='num'>tokens in/out</th><th class='num'>cost($)</th>"
        "<th class='num'>files±</th>"
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
    )


def _jsonl_by_id(path: Path) -> tuple[dict[str, dict[str, Any]], str | None]:
    rows: dict[str, dict[str, Any]] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        return {}, f"无法读取 {path.name}: {exc}"
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            return {}, f"{path.name}:{line_number} JSON 无法解析: {exc}"
        if not isinstance(value, dict) or "id" not in value:
            return {}, f"{path.name}:{line_number} 必须是含 id 的 JSON 对象"
        item_id = str(value["id"])
        if item_id in rows:
            return {}, f"{path.name} 存在重复 id={item_id}"
        rows[item_id] = value
    return rows, None


def _verified_dataset_by_id(
    record: RunRecord,
) -> tuple[dict[str, dict[str, Any]], str | None]:
    artifacts = Path(record.artifacts_dir)
    manifest_path = artifacts / "input-manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {}, f"无法校验 dataset.jsonl: input-manifest.json 非法: {exc}"
    if not isinstance(manifest, list):
        return {}, "无法校验 dataset.jsonl: input-manifest.json 不是列表"
    if not record.input_manifest_sha256:
        return {}, "无法校验 dataset.jsonl: run record 缺少 input manifest hash"
    if _canonical_json_sha256(manifest) != record.input_manifest_sha256:
        return {}, "input-manifest.json 与 run record hash 不一致"
    entries = [
        entry
        for entry in manifest
        if isinstance(entry, dict) and entry.get("path") == "dataset.jsonl"
    ]
    if len(entries) != 1:
        return {}, "input-manifest.json 未唯一声明 dataset.jsonl"
    dataset_path = artifacts / "dataset.jsonl"
    try:
        actual_hash = hashlib.sha256(dataset_path.read_bytes()).hexdigest()
    except OSError as exc:
        return {}, f"无法读取 dataset.jsonl: {exc}"
    if actual_hash != entries[0].get("file_sha256"):
        return {}, "dataset.jsonl 与 input manifest hash 不一致"
    return _jsonl_by_id(dataset_path)


def _response_predictions_by_id(
    artifacts: Path, expected_hashes: dict[str, str]
) -> tuple[dict[str, dict[str, Any]], str | None]:
    recovered: dict[str, dict[str, Any]] = {}
    response_dir = artifacts / "responses"
    if not response_dir.is_dir():
        return {}, f"无法读取 responses/: {response_dir} 不存在"
    response_paths = sorted(response_dir.glob("*.json"))
    if not expected_hashes:
        return {}, "responses/ 缺少运行时 hash，拒绝恢复未锚定的原始响应"
    actual_names = {path.name for path in response_paths}
    if actual_names != set(expected_hashes):
        return {}, "responses/ 文件集合与 run record hash 清单不一致"
    for path in response_paths:
        try:
            actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            return {}, f"无法读取 {path.name}: {exc}"
        if actual_hash != expected_hashes[path.name]:
            return {}, f"{path.name} 与 run record response hash 不一致"
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            return {}, f"无法解析 {path.name}: {exc}"
        content = envelope.get("content") if isinstance(envelope, dict) else None
        if not isinstance(content, list):
            continue
        text = "\n".join(
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("text") is not None
        ).strip()
        candidates = [text]
        if text.startswith("```") and text.endswith("```"):
            lines = text.splitlines()
            if len(lines) >= 3:
                candidates.append("\n".join(lines[1:-1]).strip())
        values: Any = None
        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, list):
                values = parsed
                break
        if not isinstance(values, list):
            continue
        for value in values:
            if not isinstance(value, dict) or "id" not in value:
                continue
            item_id = str(value["id"])
            if item_id in recovered:
                return {}, f"responses/ 恢复出重复 id={item_id}"
            recovered[item_id] = value
    return recovered, None


def _check_actual(record: RunRecord, item_id: str) -> str | None:
    for item in record.check.report.get("items") or []:
        if str(item.get("id")) == item_id and item.get("actual") is not None:
            return str(item["actual"])
    return None


def _prediction_matches_actual(prediction: dict[str, Any], actual: str) -> bool:
    """完整输出必须与该 repeat 的 check 投影一致，避免展示被篡改的响应。"""
    projected = prediction.get("actual", prediction.get("is_online_issue"))
    return projected is not None and str(projected) == actual


def _render_value_diff_details(
    comparison: RunnerComparison,
    case_records: dict[tuple[str, str], list[RunRecord]],
) -> str:
    changed_rows = [row for row in comparison.rows if row.status == "changed"]
    if not changed_rows:
        return '<p class="note">本次 original 与 candidate 没有 item 级标签分歧。</p>'
    baseline_records = sorted(
        case_records.get((comparison.baseline, comparison.runner), []),
        key=lambda record: record.repeat_index,
    )
    candidate_records = sorted(
        case_records.get((comparison.candidate, comparison.runner), []),
        key=lambda record: record.repeat_index,
    )
    parts: list[str] = []
    for baseline_record, candidate_record in zip(baseline_records, candidate_records):
        dataset, dataset_error = _verified_dataset_by_id(baseline_record)
        baseline_recovered, baseline_recovery_error = _response_predictions_by_id(
            Path(baseline_record.artifacts_dir), baseline_record.response_sha256
        )
        candidate_recovered, candidate_recovery_error = _response_predictions_by_id(
            Path(candidate_record.artifacts_dir), candidate_record.response_sha256
        )
        errors = [
            error
            for error in (
                dataset_error,
                baseline_recovery_error,
                candidate_recovery_error,
            )
            if error
        ]
        if errors:
            parts.append(
                '<div class="card"><b>分歧详情证据读取失败</b><ul>'
                + "".join(f"<li>{_e(error)}</li>" for error in errors)
                + "</ul></div>"
            )
            continue
        for row in changed_rows:
            item = dataset.get(row.item_id)
            baseline_prediction = baseline_recovered.get(row.item_id)
            candidate_prediction = candidate_recovered.get(row.item_id)
            if item is None or baseline_prediction is None or candidate_prediction is None:
                missing = [
                    name
                    for name, value in (
                        ("dataset", item),
                        (comparison.baseline, baseline_prediction),
                        (comparison.candidate, candidate_prediction),
                    )
                    if value is None
                ]
                parts.append(
                    f'<div class="card"><b>id={_e(row.item_id)}</b>'
                    f'<p class="note">缺少证据：{_e(", ".join(missing))}</p></div>'
                )
                continue
            baseline_actual = _check_actual(baseline_record, row.item_id)
            candidate_actual = _check_actual(candidate_record, row.item_id)
            evidence_mismatch = []
            for label, prediction, actual in (
                (comparison.baseline, baseline_prediction, baseline_actual),
                (comparison.candidate, candidate_prediction, candidate_actual),
            ):
                if actual is None:
                    evidence_mismatch.append(f"{label} 缺少 check actual")
                elif not _prediction_matches_actual(prediction, actual):
                    evidence_mismatch.append(f"{label} 完整输出与 check actual 不一致")
            if evidence_mismatch:
                parts.append(
                    f'<div class="card"><b>id={_e(row.item_id)} 证据完整性失败</b><ul>'
                    + "".join(f"<li>{_e(error)}</li>" for error in evidence_mismatch)
                    + "</ul></div>"
                )
                continue
            team = str(item.get("team", ""))
            product = str(item.get("product_display_name", ""))
            baseline_text = scrub_text(
                json.dumps(baseline_prediction, ensure_ascii=False, indent=2)
            ) + "\n"
            candidate_text = scrub_text(
                json.dumps(candidate_prediction, ensure_ascii=False, indent=2)
            ) + "\n"
            recovery_note = (
                '<p class="note">双方完整对象均从本次原始 response 恢复，并已校验运行时 hash；'
                "仅用于展示，不会改变严格结构判定。</p>"
            )
            parts.append(
                f'<div class="card"><h4>工单 <code>{_e(scrub_text(row.item_id))}</code> · '
                f'repeat-{baseline_record.repeat_index}</h4>'
                f'<p>团队：<code>{_e(scrub_text(team) or "—")}</code> · '
                f'产品：<code>{_e(scrub_text(product) or "—")}</code> · '
                f'数据库 baseline 快照：{_badge(scrub_text(row.reference_value) or "—", "na")} · '
                f'{_e(comparison.baseline)}：{_badge(scrub_text(row.baseline_value), "bad")} · '
                f'{_e(comparison.candidate)}：{_badge(scrub_text(row.candidate_value), "ok")}</p>'
                '<p class="note">红/绿仅表示删除/新增，不代表错误/正确。</p>'
                + recovery_note
                +
                f'<details><summary>查看完整工单输入</summary><div class="body"><pre>'
                f'{_e(scrub_text(json.dumps(item, ensure_ascii=False, indent=2)))}</pre></div></details>'
                + _render_unified_diff(
                    title=f"完整输出 diff：{comparison.baseline} → {comparison.candidate}",
                    baseline=f"{comparison.baseline}/predictions.jsonl#{row.item_id}",
                    candidate=f"{comparison.candidate}/predictions.jsonl#{row.item_id}",
                    baseline_text=baseline_text,
                    candidate_text=candidate_text,
                )
                + "</div>"
            )
    return "".join(parts)


def _render_comparisons(
    comparisons: list[RunnerComparison],
    case_records: dict[tuple[str, str], list[RunRecord]],
) -> str:
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
        if comparison.method == "item_value_diff":
            changed_rows = [row for row in comparison.rows if row.status == "changed"]
            unavailable_rows = [row for row in comparison.rows if row.status == "unavailable"]
            parts.append(
                f'<p>Runner <code>{_e(comparison.runner)}</code>：'
                f'baseline=<code>{_e(comparison.baseline)}</code> → '
                f'candidate=<code>{_e(comparison.candidate)}</code>，'
                f'{_badge(f"有分歧 {comparison.changed}", "warn" if comparison.changed else "ok")} '
                f'{_badge(f"相同 {len(comparison.rows) - comparison.changed - comparison.unavailable_items}", "na")} '
                f'{_badge(f"不可比较 {comparison.unavailable_items}", "bad" if comparison.unavailable_items else "na")}</p>'
                '<p class="note">数据库 baseline 快照仅用于抽样分层和参照，不是真值；'
                '红/绿 diff 只表示双方完整输出的删除/新增，哪个更优由人工复核。</p>'
            )
            rows = [
                f'<tr><td><code>{_e(scrub_text(row.item_id))}</code></td>'
                f'<td>{_badge(scrub_text(row.reference_value) or "—", "na")}</td>'
                f'<td>{_badge(scrub_text(row.baseline_value), "bad")}</td>'
                f'<td>{_badge(scrub_text(row.candidate_value), "ok")}</td>'
                f'<td>{_badge("有分歧", "warn")}</td></tr>'
                for row in changed_rows
            ]
            if rows:
                parts.append(
                    '<div class="tablewrap"><table><thead><tr><th>工单</th>'
                    '<th>数据库 baseline 快照</th><th>Original 输出</th>'
                    '<th>Candidate 输出</th><th>状态</th></tr></thead>'
                    f'<tbody>{"".join(rows)}</tbody></table></div>'
                )
            if unavailable_rows:
                unavailable = "".join(
                    f'<li><code>{_e(scrub_text(row.item_id))}</code>：'
                    f'{_e(comparison.baseline)}={_e(scrub_text(row.baseline_value))}，'
                    f'{_e(comparison.candidate)}={_e(scrub_text(row.candidate_value))}</li>'
                    for row in unavailable_rows
                )
                parts.append(
                    '<div class="card"><b>不可比较工单</b><ul>'
                    f'{unavailable}</ul></div>'
                )
            parts.append(_render_value_diff_details(comparison, case_records))
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
    columns = sorted(case_records)
    per_col: dict[tuple[str, str], dict[str, tuple[int, int]]] = {}
    for key in columns:
        stats: dict[str, list[bool]] = defaultdict(list)
        for record in case_records[key]:
            if record.is_error or not record.check.ran:
                continue
            for item in record.check.report.get("items") or []:
                if item.get("evaluated") is False:
                    continue
                if not isinstance(item.get("correct"), bool):
                    continue
                stats[str(item["id"])].append(item["correct"])
        per_col[key] = {i: (sum(v), len(v)) for i, v in stats.items()}
    if not any(per_col.values()):
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
                tds.append(_result_cell(_DASH, "na"))
                continue
            passed, total = got
            klass = "ok" if passed == total else ("bad" if passed == 0 else "warn")
            tds.append(_result_cell(f"{passed}/{total}", klass))
        rows.append(f"<tr><td><code>{_e(item_id)}</code></td>{''.join(tds)}</tr>")
    return (
        "<h3>数据轴：item 级明细</h3>"
        '<p class="note">每格 = 该条数据在此 runner@variant 下通过的已评测 repeat 数；'
        "runner 执行失败的 repeat 不进入分母；"
        "哪类输入拖垮了哪个组合一目了然。</p>"
        '<div class="tablewrap"><table class="result-grid"><thead><tr><th>Item</th>'
        f"{heads}</tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
    )


def _render_check_axes(case_records: dict[tuple[str, str], list[RunRecord]]) -> str:
    """可选双轴 check 摘要：结构合规与语义结论分别统计。"""
    rows: list[str] = []
    for (variant, runner), records in sorted(case_records.items()):
        structure_passed = 0
        structure_total = 0
        semantic_evaluated = 0
        semantic_expected = 0
        semantic_correct = 0
        reference_mode = False
        for record in records:
            if record.is_error or not record.check.ran:
                continue
            summary = record.check.report.get("summary") or {}
            structure = summary.get("structure")
            semantic = summary.get("semantic")
            if not isinstance(structure, dict) or not isinstance(semantic, dict):
                continue
            record_reference_mode = "reference_agreement_count" in semantic
            reference_mode = reference_mode or record_reference_mode
            if isinstance(structure.get("compliant"), bool):
                structure_total += 1
                structure_passed += int(structure["compliant"])
            items = record.check.report.get("items") or []
            if not all(isinstance(item, dict) for item in items):
                continue
            evaluated_items = [
                item for item in items if item.get("evaluated", True) is not False
            ]
            semantic_expected += len(items)
            semantic_evaluated += len(evaluated_items)
            if record_reference_mode:
                semantic_correct += sum(
                    str(item.get("actual")) == str(item.get("reference"))
                    for item in evaluated_items
                )
            else:
                semantic_correct += sum(item.get("correct") is True for item in evaluated_items)
        if not structure_total and not semantic_expected:
            continue
        structure_text = (
            f"{structure_passed}/{structure_total}" if structure_total else _DASH
        )
        coverage_text = (
            f"{semantic_evaluated}/{semantic_expected}" if semantic_expected else _DASH
        )
        accuracy_text = (
            f"{semantic_correct}/{semantic_evaluated}" if semantic_evaluated else _DASH
        )
        structure_class = (
            "ok"
            if structure_total and structure_passed == structure_total
            else ("bad" if structure_passed == 0 else "warn")
        )
        coverage_class = (
            "ok"
            if semantic_expected and semantic_evaluated == semantic_expected
            else ("bad" if semantic_evaluated == 0 else "warn")
        )
        accuracy_class = (
            "ok"
            if semantic_evaluated and semantic_correct == semantic_evaluated
            else ("bad" if semantic_correct == 0 else "warn")
        )
        rows.append(
            f"<tr><td><code>{_e(f'{runner}@{variant}')}</code></td>"
            f"{_result_cell(structure_text, structure_class)}"
            f"{_result_cell(coverage_text, coverage_class)}"
            f"{_result_cell(accuracy_text, accuracy_class)}</tr>"
        )
    if not rows:
        return ""
    reference_note = "数据库 baseline 快照仅作参照，不是真值。" if reference_mode else ""
    return (
        "<h3>Check 轴：结构与结论双轴</h3>"
        f'<p class="note">结构合规按 repeat 统计；结论覆盖按可审计的语义投影 item 统计。'
        f"{reference_note}格式恢复不改变结构判定。</p>"
        '<div class="tablewrap"><table class="result-grid"><thead><tr><th>Runner@Variant</th>'
        "<th class='num'>结构合规</th><th class='num'>结论覆盖</th>"
        f"<th class='num'>{'Baseline 快照一致' if reference_mode else '结论正确'}</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def _render_cell_details(records: list[RunRecord]) -> str:
    parts = ["<h3>逐格明细</h3>"]
    for record in sorted(
        records, key=lambda r: (r.runner_label, r.variant_label, r.repeat_index)
    ):
        status = _badge("错误", "bad") if record.is_error else _badge("正常", "ok")
        if record.is_error:
            check = _badge("check 跳过", "na")
        else:
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
            if not record.is_error
            and record.judge
            and record.judge.ran
            and record.judge.score is not None
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
            + f' · <a href="{_e(href)}raw.txt">脱敏 raw.txt</a></p>'
        ]
        if record.is_error:
            body.append('<p class="note">runner 执行失败，未进入 check / judge。</p>')
        elif record.check.detail:
            body.append(
                f"<p>check 详情：</p><pre>{_e(scrub_text(record.check.detail))}</pre>"
            )
        if not record.is_error and record.judge and record.judge.reasoning:
            dims = (
                f'<p class="note">维度分：<code>{_e(scrub_text(json.dumps(record.judge.dimensions, ensure_ascii=False)))}</code></p>'
                if record.judge.dimensions
                else ""
            )
            body.append(
                f"{dims}<p>裁判理由（advisory，已脱敏）：</p>"
                f"<pre>{_e(scrub_text(record.judge.reasoning))}</pre>"
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
            sections.append(_render_prompt_comparison(case_obj, case_records))
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
        sections.append(_render_comparisons(comparisons.get(case_name, []), case_records))
        sections.append(_render_check_axes(case_records))
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
        + _chip("裁判", scrub_text(judge_label))
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
	<p class="note">分享边界：本 report.html 可能包含任务输入与模型输出正文，
	必须按 case 的保密级别保存；仅 scorecard.md 是默认可分享摘要。
	cells/、run.json 与 artifacts/ 同样不可直接外发。</p>
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
    return [
        replace(
            RunRecord.from_json(path.read_text(encoding="utf-8")),
            artifacts_dir=str(path.parent / "artifacts"),
        )
        for path in paths
    ]
