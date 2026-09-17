"""Rebuild the README's animated showcase and the public, interactive report.

Only the two approved public SVG cases are accepted. Raw logs, final replies,
machine configuration and arbitrary run-record fields never enter the export.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import re
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import replace
from pathlib import Path

from .case import load_case
from .record import RunRecord
from .report import build_report_html
from .scrub import scrub_text
from .showcase_svg import render_showcase

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "docs"
REPO_URL = "https://github.com/huajuan404/donebench"
CASE_TITLES = {
    "2026-09-15-001-flamingo-bicycle": "火烈鸟骑自行车",
    "2026-09-15-002-capybara-bicycle": "卡皮巴拉骑自行车",
}
RUNNERS = ("kimi-k3", "glm-5.3")
FIELDS = (
    "case", "runner_label", "launcher_type", "runner_model", "run_id", "variant_label",
    "prompt_template_sha256", "run_context_sha256", "input_manifest_sha256",
    "repeat_index", "started_at", "duration_ms", "exit_code", "is_error", "usage", "agentic", "judge",
)
LOCAL_PATH = re.compile(r"/(?:Users|home|private|tmp|var/folders)/|file://|[A-Za-z]:\\")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def public_text(text: str, allowed: tuple[str, ...] = ()) -> None:
    candidate = text
    for value in allowed:
        candidate = candidate.replace(value, "APPROVED_PUBLIC_VALUE")
    if LOCAL_PATH.search(text) or scrub_text(candidate) != candidate:
        raise ValueError("公开展示包含本机路径或疑似凭证；请先核对来源，不自动删改证据")


def hashes(records: list[dict]) -> tuple[str, ...]:
    return tuple(value for row in records for key, value in row.items()
                 if key.endswith("_sha256") and isinstance(value, str)
                 and re.fullmatch(r"[0-9a-f]{64}", value))


def validate_svg(source: bytes) -> None:
    text = source.decode("utf-8")
    public_text(text)
    if "\\" in text or re.search(r"<!DOCTYPE|<!ENTITY|@import", text, re.IGNORECASE):
        raise ValueError("展示 SVG 不允许外部声明或转义 CSS")
    root = ET.fromstring(source)
    namespace = "{http://www.w3.org/2000/svg}"
    if root.tag != namespace + "svg" or "viewBox" not in root.attrib:
        raise ValueError("展示需要带 viewBox 的独立 SVG")
    viewport = [float(v) for v in root.attrib["viewBox"].replace(",", " ").split()]
    if len(viewport) != 4 or not all(math.isfinite(v) for v in viewport) or min(viewport[2:]) <= 0:
        raise ValueError("SVG viewBox 必须是有效的有限画布")
    for element in root.iter():
        if not element.tag.startswith(namespace) or element.tag.removeprefix(namespace) in {
            "script", "foreignObject", "iframe", "image", "style",
        }:
            raise ValueError("当前首图编排仅接受不含活动内容、嵌套图片或样式表的 SMIL 作品")
        for key, value in element.attrib.items():
            local = key.rsplit("}", 1)[-1].lower()
            if local.startswith("on") or (local in {"href", "src"} and not value.startswith("#")):
                raise ValueError("展示 SVG 不允许事件处理器或外部引用")
        for ref in re.findall(r"url\s*\((.*?)\)", " ".join(element.attrib.values()), re.IGNORECASE):
            if not ref.strip(" \t\r\n\"'").startswith("#"):
                raise ValueError("展示 SVG 的资源必须是内部片段")


def import_run(run: Path, site: Path = SITE) -> None:
    """Capture a reviewed public subset, after validating every selected cell."""
    manifest = json.loads((run / "run_manifest.json").read_text())
    if manifest.get("status") != "complete":
        raise ValueError("只导入已完成的 run")
    records = [RunRecord.from_json(p.read_text()) for p in sorted(run.glob("cells/*/*/*/*/run.json"))]
    expected = {(case, runner) for case in CASE_TITLES for runner in RUNNERS}
    if len(records) != 4 or {(r.case, r.runner_label) for r in records} != expected:
        raise ValueError("此展示只接收两组公开骑车任务 × kimi-k3/glm-5.3，各一格")
    data = {"schema_version": 1, "run_id": run.name, "date": records[0].started_at[:10],
            "judge": manifest["judge"], "runners": list(RUNNERS),
            "cases": [{"id": name, "title": title} for name, title in CASE_TITLES.items()], "records": []}
    files: dict[Path, bytes] = {}
    for record in records:
        if record.is_error or record.repeat_index != 0 or record.variant_label != "default":
            raise ValueError("展示要求每个组合一次完整的 default 运行")
        case = load_case(ROOT / "cases" / record.case)
        relative = Path("cells") / record.case / "default" / record.runner_label / "repeat-0" / "artifacts"
        artifacts = run / relative
        if not artifacts.resolve().is_relative_to(run.resolve()):
            raise ValueError("运行产物目录越出本次 run")
        prompt = (artifacts / "PROMPT.txt").read_bytes()
        if prompt.decode() != case.task.prompt or sha256(prompt) != record.prompt_template_sha256:
            raise ValueError("运行提示词与公开 case 不一致")
        if record.input_manifest_sha256 != sha256(b"[]"):
            raise ValueError("这组展示只接受空输入目录的运行")
        context = (artifacts / "RUN_CONTEXT.json").read_bytes()
        if sha256(context) != record.run_context_sha256:
            raise ValueError("RUN_CONTEXT 与运行记录不一致")
        context_data = json.loads(context)
        if set(context_data) != {"schema_version", "case", "protocol", "run_id", "variant",
                                "repeat_index", "prompt_template_sha256"}:
            raise ValueError("RUN_CONTEXT 含未审核字段")
        svg_path = relative / case.expected["output_file"]
        if (run / svg_path).is_symlink():
            raise ValueError("原始 SVG 不允许符号链接")
        svg = (run / svg_path).read_bytes()
        validate_svg(svg)
        # Build from schema fields; unknown fields and human_note are excluded.
        raw = record.to_dict()
        item = {field: raw[field] for field in FIELDS}
        item.update(svg=svg_path.as_posix(), svg_sha256=sha256(svg), context=context.decode())
        public_text(json.dumps(item, ensure_ascii=False), hashes([item]))
        data["records"].append(item)
        files[svg_path] = svg
    # No mutations before all four candidates pass the publication boundary.
    for relative, content in files.items():
        path = site / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    (site / "showcase.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def load_showcase(site: Path = SITE) -> dict:
    data = json.loads((site / "showcase.json").read_text())
    if data.get("schema_version") != 1 or data.get("runners") != list(RUNNERS):
        raise ValueError("未知展示数据格式")
    if data.get("cases") != [{"id": k, "title": v} for k, v in CASE_TITLES.items()]:
        raise ValueError("展示仅接受已审核的两个公开 case")
    expected = {(case, runner) for case in CASE_TITLES for runner in RUNNERS}
    if len(data["records"]) != 4 or {(r["case"], r["runner_label"]) for r in data["records"]} != expected:
        raise ValueError("展示数据必须包含完整四格")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", data["date"]):
        raise ValueError("展示日期格式非法")
    public_text(json.dumps(data, ensure_ascii=False), hashes(data["records"]))
    for item in data["records"]:
        if set(item) != set(FIELDS) | {"svg", "svg_sha256", "context"}:
            raise ValueError("展示记录含未审核字段")
        case = load_case(ROOT / "cases" / item["case"])
        relative = Path("cells") / item["case"] / "default" / item["runner_label"] / "repeat-0" / "artifacts" / case.expected["output_file"]
        if item["svg"] != relative.as_posix():
            raise ValueError("SVG 路径必须位于对应 cell 内")
        if (site / relative).is_symlink() or not (site / relative).resolve().is_relative_to(site.resolve()):
            raise ValueError("展示 SVG 不允许链接到其他位置")
        svg = (site / relative).read_bytes()
        if sha256(svg) != item["svg_sha256"]:
            raise ValueError("原始 SVG 已改变；不能修改作品来美化展示")
        validate_svg(svg)
        judge = item["judge"]
        if not judge or not judge["ran"] or judge["max"] != case.expected["max_score"]:
            raise ValueError("展示需要与 case 一致的参考评分")
        if set(judge["dimensions"]) != set(case.judge.dimensions) or sum(judge["dimensions"].values()) != judge["score"]:
            raise ValueError("裁判维度分不一致")
        if not all(isinstance(x, (int, float)) and 0 <= x <= 5 for x in judge["dimensions"].values()):
            raise ValueError("裁判维度分超出范围")
        if item["prompt_template_sha256"] != sha256(case.task.prompt.encode()):
            raise ValueError("case 提示词已改变；应使用对应的新运行更新展示")
        if item["input_manifest_sha256"] != sha256(b"[]"):
            raise ValueError("展示输入目录必须为空")
        if sha256(item["context"].encode()) != item["run_context_sha256"]:
            raise ValueError("展示运行上下文已改变")
    return data


def public_report(data: dict, site: Path) -> str:
    """Reuse the real report, with only public evidence staged for rendering."""
    cases = {name: load_case(ROOT / "cases" / name) for name in CASE_TITLES}
    with tempfile.TemporaryDirectory(prefix="donebench-showcase-") as temp:
        records = []
        for item in data["records"]:
            record = RunRecord.from_dict(item)
            artifacts = Path(temp) / Path(item["svg"]).parent
            artifacts.mkdir(parents=True)
            (artifacts / Path(item["svg"]).name).write_bytes((site / item["svg"]).read_bytes())
            (artifacts / "PROMPT.txt").write_text(cases[item["case"]].task.prompt)
            (artifacts / "RUN_CONTEXT.json").write_text(item["context"])
            (artifacts / "input-manifest.json").write_text("[]")
            records.append(replace(record, artifacts_dir=str(artifacts)))
        report = build_report_html(run_id=data["run_id"], records=records, cases=cases,
                                   comparisons={}, judge_label=data["judge"])
    report, removed = re.subn(r' · <a href="[^"]*/raw\.txt">脱敏 raw\.txt</a>', "", report)
    if removed != len(records):
        raise ValueError("报告日志入口发生变化；请复核公开导出规则")
    notice = ('<p class="note">公开演示：仅包含已审核的两个公开任务、四份原始 SVG、运行指标与裁判依据。'
              '每格运行一次，参考分不是通过判据，也不代表总体模型能力。'
              '<a href="showcase.json">查看公开数据与作品 SHA-256 ↗</a></p>')
    report, notices = re.subn(r'<p class="note">分享边界：.*?</p>', notice, report, flags=re.DOTALL)
    if notices != 1:
        raise ValueError("报告分享说明发生变化；请复核公开导出规则")
    report = re.sub(r"生成于 \d{4}-\d{2}-\d{2} UTC", f"运行于 {data['date']} UTC", report)
    # Titles are a presentation alias; hashes and actual task prompts stay intact.
    for case, title in CASE_TITLES.items():
        report = report.replace(f">{case}</h2>", f">{title}</h2>")
        report = report.replace(f">{case}</summary>", f">{title}</summary>")
        report = report.replace(f">{case}</a>", f">{title}</a>")
    report = report.replace('<a class="wordmark" href="#">', f'<a class="wordmark" href="{REPO_URL}">')
    report = report.replace("<title>评测报告 ", "<title>donebench · 公开评测报告 ")
    approved = hashes(data["records"]) + tuple(
        base64.b64encode((site / row["svg"]).read_bytes()).decode() for row in data["records"]
    )
    public_text(report, approved)
    return report


def build(site: Path = SITE, *, check: bool = False) -> list[Path]:
    data = load_showcase(site)
    files = {
        Path("index.html"): public_report(data, site).encode(),
        Path("assets/report.svg"): render_showcase(data, site),
        Path("assets/report-mobile.svg"): render_showcase(data, site, mobile=True),
        Path(".nojekyll"): b"",
    }
    for relative, content in files.items():
        destination = site / relative
        if check:
            if not destination.is_file() or destination.read_bytes() != content:
                raise ValueError(f"展示产物需要重建：{relative}")
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
    return list(files)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--import-run", type=Path, help="导入已完成的公开四格运行")
    parser.add_argument("--check", action="store_true", help="检查生成产物是否与公开数据一致")
    args = parser.parse_args()
    if args.import_run and args.check:
        parser.error("--import-run 与 --check 不能同时使用")
    try:
        if args.import_run:
            import_run(args.import_run.resolve())
        files = build(check=args.check)
    except (ValueError, OSError, KeyError, TypeError, ET.ParseError) as exc:
        parser.exit(1, f"showcase: {exc}\n")
    print(f"{'Verified' if args.check else 'Generated'} {len(files)} showcase files; original SVG animations preserved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
