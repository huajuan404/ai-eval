"""Persisted report-only case selection; source runs and measurements stay immutable."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .case import Case
from .comparison import compare_run_variants
from .layout import RunLayout
from .report import ReportError, build_report_html, load_run_records
from .run_manifest import snapshot_case_integrity


def _json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReportError(f"无法读取报告来源文件：{path}") from exc
    if not isinstance(value, dict):
        raise ReportError(f"报告来源文件必须为对象：{path}")
    return value


def build_report_view(layout: RunLayout, cases: dict[str, Case]) -> str:
    """Render explicitly selected cases from completed runs under the same report root."""
    view = _json(layout.run_dir / "report_view.json")
    if type(view.get("schema_version")) is not int or view.get("schema_version") != 1 or set(view) != {"schema_version", "cases"}:
        raise ReportError("report_view.json 必须使用 schema_version: 1 与 cases")
    selections = view.get("cases")
    if not isinstance(selections, list) or not selections:
        raise ReportError("report_view.json 的 cases 必须为非空列表")
    host = _json(layout.manifest_path)
    records = []
    origins = {}
    judges = set()
    for selection in selections:
        if not isinstance(selection, dict) or set(selection) != {"case", "run_id"}:
            raise ReportError("每个报告选项只能包含 case 和 run_id")
        name, run_id = selection["case"], selection["run_id"]
        if any(not isinstance(x, str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9._-]*", x)
               for x in (name, run_id)):
            raise ReportError("报告来源包含非法 case 或 run_id")
        if name in origins:
            raise ReportError(f"报告不可重复选择同一 case：{name}")
        if name not in cases:
            raise ReportError(f"找不到报告所需的 case 定义：{name}")
        source = RunLayout(layout.report_root, run_id)
        if source.run_dir.is_symlink() or not source.run_dir.resolve().is_relative_to(layout.run_dir.parent.resolve()):
            raise ReportError("报告来源不得通过软链跳出运行目录")
        manifest = _json(source.manifest_path)
        if manifest.get("status") != "complete" or manifest.get("run_id") != run_id:
            raise ReportError(f"报告来源不是已完成的匹配运行：{run_id}")
        if manifest.get("private") and not host.get("private"):
            raise ReportError("不能将私有运行并入公开报告")
        locks = _json(source.case_lock_path).get("cases")
        locked = locks.get(name) if isinstance(locks, dict) else None
        if not isinstance(locked, dict) or locked.get("lock_sha256") != snapshot_case_integrity(cases[name]).lock_sha256:
            raise ReportError(f"case 定义与来源运行的完整性锁不匹配：{name}")
        selected = [r for r in load_run_records(source) if r.case == name]
        if not selected or any(r.run_id != run_id for r in selected):
            raise ReportError(f"来源运行缺少匹配的 case 记录：{name}")
        origins[name] = run_id
        judges.add(str(manifest.get("judge") or "—"))
        records.extend(selected)
    selected_cases = {name: cases[name] for name in origins}
    return build_report_html(
        run_id=layout.run_id, records=records, cases=selected_cases,
        comparisons=compare_run_variants(records, selected_cases),
        judge_label=" / ".join(sorted(judges)), report_sources=origins,
    )
