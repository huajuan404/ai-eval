"""把一次本地运行（`runs/<run_id>/`）发布进公开结果账本（`docs/data/`）。

账本是站点的唯一数据源，按运行追加：

    docs/data/runs/<run_id>.json                       该运行的公开记录
    docs/data/cells/<case>/<runner>/<run_id>/<variant>/repeat-N/<file>   白名单交付物

发布门槛（任一不满足整次拒绝，不写任何文件）：
- 只接受公开 `cases/` 下的用例；提示词与输入目录哈希必须等于当前仓库版本。
- 只发布白名单字段与白名单交付物（`expected.output_file` + `publish.artifacts`），单文件 ≤ 2 MB。
- 所有文本过 `public_text`：本机路径、疑似凭证直接拒绝，不静默删改证据。raw.txt、OUTPUT.txt 永不发布。
- SVG 交付物必须是不含活动内容与外链的独立 SVG。

用法：
    python3 -m bench.publish <run_id 或 run 目录> [--cases a,b] [--runners x,y] [--site docs]
"""

from __future__ import annotations

import argparse
import json
import math
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .case import Case, CaseError, load_case
from .completion import repeat_pass
from .models import ModelBook
from .record import RunRecord
from .run_manifest import sha256_bytes, tree_manifest_sha256
from .scrub import scrub_text

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "docs"
RUNS = ROOT / "runs"
LEDGER_SCHEMA = 1
MAX_ARTIFACT_BYTES = 2 * 1024 * 1024
CONTEXT_KEYS = {"schema_version", "case", "protocol", "run_id", "variant", "repeat_index", "prompt_template_sha256"}
NEVER_PUBLISH = {"raw.txt", "OUTPUT.txt", "PROMPT.txt", "RUN_CONTEXT.json", "input-manifest.json"}
# 只拦会暴露身份或本机布局的路径：用户主目录、macOS 私有临时目录、Windows 盘符。
# `/tmp/`、`file://` 这类字面量可能只是裁判或模型在叙述，不构成泄露。
LOCAL_PATH = re.compile(r"/(?:Users|home)/[^/\s\"']+|/private/var/folders/|/var/folders/|[A-Za-z]:\\Users\\")
TEXT_SUFFIXES = {".svg", ".html", ".htm", ".md", ".txt", ".py", ".js", ".ts", ".css", ".json", ".csv", ".yaml", ".yml", ".sh", ".xml"}


class PublishError(ValueError):
    pass


def sha256(data: bytes) -> str:
    return sha256_bytes(data)


def public_text(text: str, allowed: tuple[str, ...] = ()) -> None:
    """公开文本不得含本机路径或疑似凭证；不自动删改，交给人核对来源。"""
    candidate = text
    for value in allowed:
        candidate = candidate.replace(value, "APPROVED_PUBLIC_VALUE")
    if LOCAL_PATH.search(text) or scrub_text(candidate) != candidate:
        raise PublishError("公开内容包含本机路径或疑似凭证；请先核对来源，不自动删改证据")


def hashes(records: list[dict]) -> tuple[str, ...]:
    """记录里所有 sha256 字段的值（含嵌套的交付物清单），公开文本检查时视为已批准。"""
    found: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, inner in value.items():
                if key == "sha256" or key.endswith("_sha256"):
                    if isinstance(inner, str) and re.fullmatch(r"[0-9a-f]{64}", inner):
                        found.append(inner)
                else:
                    walk(inner)
        elif isinstance(value, list):
            for inner in value:
                walk(inner)

    for row in records:
        walk(row)
    return tuple(found)


def validate_svg(source: bytes) -> None:
    text = source.decode("utf-8")
    public_text(text)
    if "\\" in text or re.search(r"<!DOCTYPE|<!ENTITY|@import", text, re.IGNORECASE):
        raise PublishError("公开 SVG 不允许外部声明或转义 CSS")
    root = ET.fromstring(source)
    namespace = "{http://www.w3.org/2000/svg}"
    if root.tag != namespace + "svg" or "viewBox" not in root.attrib:
        raise PublishError("公开 SVG 必须是带 viewBox 的独立 SVG")
    viewport = [float(v) for v in root.attrib["viewBox"].replace(",", " ").split()]
    if len(viewport) != 4 or not all(math.isfinite(v) for v in viewport) or min(viewport[2:]) <= 0:
        raise PublishError("SVG viewBox 必须是有效的有限画布")
    for element in root.iter():
        if not element.tag.startswith(namespace) or element.tag.removeprefix(namespace) in {
            "script", "foreignObject", "iframe", "image", "style",
        }:
            raise PublishError("公开 SVG 不接受活动内容、嵌套图片或样式表")
        for key, value in element.attrib.items():
            local = key.rsplit("}", 1)[-1].lower()
            if local.startswith("on") or (local in {"href", "src"} and not value.startswith("#")):
                raise PublishError("公开 SVG 不允许事件处理器或外部引用")
        for ref in re.findall(r"url\s*\((.*?)\)", " ".join(element.attrib.values()), re.IGNORECASE):
            if not ref.strip(" \t\r\n\"'").startswith("#"):
                raise PublishError("公开 SVG 的资源必须是内部片段")


def criteria_sha256(case: Case) -> str:
    """通过判据的指纹：判据变了，旧记录的 verdict 就不再可比。"""
    expected = case.expected or {}
    spec = {
        "check": case.check.type,
        "completion": expected.get("completion"),
        "passing_threshold": expected.get("passing_threshold"),
        "max_score": expected.get("max_score"),
    }
    return sha256(json.dumps(spec, sort_keys=True, ensure_ascii=False).encode())


def cell_relative(record: RunRecord) -> Path:
    return (Path("data") / "cells" / record.case / record.runner_label / record.run_id
            / record.variant_label / f"repeat-{record.repeat_index}")


def _load_public_case(name: str) -> Case:
    directory = (ROOT / "cases" / name)
    if not directory.resolve().is_relative_to((ROOT / "cases").resolve()) or not (directory / "case.yaml").is_file():
        raise PublishError(f"只发布公开 cases/ 下的用例：{name}")
    try:
        return load_case(directory)
    except CaseError as exc:
        raise PublishError(f"用例 {name} 无法加载：{exc}") from exc


def _select_artifacts(case: Case, artifacts: Path) -> tuple[str | None, list[Path]]:
    declared = (case.expected or {}).get("output_file")
    chosen: dict[Path, None] = {}
    output = None
    if isinstance(declared, str) and declared.strip():
        candidate = artifacts / declared
        if candidate.exists():
            chosen[candidate] = None
            output = declared
    for pattern in case.publish_artifacts:
        for match in sorted(artifacts.glob(pattern)):
            if match.is_file():
                chosen[match] = None
    return output, list(chosen)


def _check_artifact(path: Path, artifacts: Path) -> bytes:
    if path.is_symlink():
        raise PublishError(f"交付物不允许符号链接: {path.name}")
    if not path.resolve().is_relative_to(artifacts.resolve()):
        raise PublishError(f"交付物越出本格 artifacts 目录: {path.name}")
    if path.name in NEVER_PUBLISH:
        raise PublishError(f"{path.name} 属于运行日志，不进入公开账本")
    data = path.read_bytes()
    if len(data) > MAX_ARTIFACT_BYTES:
        raise PublishError(f"交付物超过 {MAX_ARTIFACT_BYTES // 1024 // 1024} MB 上限: {path.name}")
    suffix = path.suffix.lower()
    if suffix == ".svg":
        validate_svg(data)
    elif suffix in TEXT_SUFFIXES:
        try:
            public_text(data.decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise PublishError(f"文本交付物不是 UTF-8: {path.name}") from exc
    return data


def _public_record(record: RunRecord, case: Case, artifacts: Path, book: ModelBook) -> tuple[dict[str, Any], dict[Path, bytes]]:
    variant = case.task.variant_for(record.variant_label)
    if record.prompt_template_sha256 != sha256(variant.prompt.encode("utf-8")):
        raise PublishError(f"{case.name}/{record.runner_label}: 运行提示词与当前公开 case 不一致，应用新运行更新账本")
    if record.input_manifest_sha256 != tree_manifest_sha256(case.input_dir)[0]:
        raise PublishError(f"{case.name}/{record.runner_label}: 运行输入目录与当前公开 case 不一致")
    context_path = artifacts / "RUN_CONTEXT.json"
    if not context_path.is_file():
        raise PublishError(f"{case.name}/{record.runner_label}: 缺少 RUN_CONTEXT.json")
    context = context_path.read_bytes()
    if sha256(context) != record.run_context_sha256:
        raise PublishError(f"{case.name}/{record.runner_label}: RUN_CONTEXT 与运行记录不一致")
    if not set(json.loads(context)) <= CONTEXT_KEYS:
        raise PublishError(f"{case.name}/{record.runner_label}: RUN_CONTEXT 含未审核字段")

    output, paths = _select_artifacts(case, artifacts)
    files: dict[Path, bytes] = {}
    listed = []
    base = cell_relative(record)
    for path in paths:
        data = _check_artifact(path, artifacts)
        relative = base / path.relative_to(artifacts)
        files[relative] = data
        listed.append({"path": relative.as_posix(), "sha256": sha256(data), "bytes": len(data)})

    usage = record.usage
    if record.is_error and not (usage and usage.total_tokens):
        # 启动器报错/超时且没有用量：上报的 0 成本没有信息量，不当作真实成本发布
        estimate = book.estimate(record.runner_label, record.started_at, None)
    else:
        estimate = book.estimate(record.runner_label, record.started_at, usage)
    judge = None
    if record.judge is not None:
        reasoning = scrub_text(record.judge.reasoning or "")
        if reasoning != (record.judge.reasoning or ""):
            raise PublishError(f"{case.name}/{record.runner_label}: 裁判原文含疑似凭证，请先核对来源")
        dimensions = record.judge.dimensions or {}
        if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in dimensions.values()):
            raise PublishError(f"{case.name}/{record.runner_label}: 裁判维度分必须是有限数值")
        judge = {
            "ran": bool(record.judge.ran), "model": record.judge.model, "same_source": bool(record.judge.same_source),
            "score": record.judge.score, "max": record.judge.max,
            "dimensions": dict(dimensions), "reasoning": reasoning,
        }
    item: dict[str, Any] = {
        "case": record.case, "runner_label": record.runner_label, "launcher_type": record.launcher_type,
        "runner_model": record.runner_model, "run_id": record.run_id, "variant_label": record.variant_label,
        "repeat_index": record.repeat_index, "started_at": record.started_at, "duration_ms": record.duration_ms,
        "exit_code": record.exit_code, "is_error": bool(record.is_error),
        "prompt_template_sha256": record.prompt_template_sha256,
        "run_context_sha256": record.run_context_sha256,
        "input_manifest_sha256": record.input_manifest_sha256,
        "usage": None if usage is None else {
            "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
            "cache_creation_tokens": usage.cache_creation_tokens, "cache_read_tokens": usage.cache_read_tokens,
            "total_tokens": usage.total_tokens,
        },
        "agentic": {"num_turns": record.agentic.num_turns, "files_changed": record.agentic.files_changed,
                    "commands_run": record.agentic.commands_run},
        "check": {"ran": bool(record.check.ran), "passed": record.check.passed},
        "judge": judge,
        "verdict": repeat_pass(record, case),
        "criteria_sha256": criteria_sha256(case),
        "cost_usd": estimate.cost_usd, "price_version": estimate.price_version, "cost_source": estimate.source,
        "output": (base / output).as_posix() if output else None,
        "artifacts": listed,
        "context": context.decode("utf-8"),
    }
    public_text(json.dumps(item, ensure_ascii=False), hashes([item]))
    return item, files


def load_run_records(run: Path) -> list[RunRecord]:
    return [RunRecord.from_json(p.read_text(encoding="utf-8")) for p in sorted(run.glob("cells/*/*/*/*/run.json"))]


def publish_run(run: Path, site: Path = SITE, *, cases: set[str] | None = None,
                runners: set[str] | None = None, book: ModelBook | None = None,
                now: datetime | None = None) -> dict[str, Any]:
    """校验全部通过后才落盘；返回写入的账本文件内容。"""
    run = Path(run)
    manifest_path = run / "run_manifest.json"
    if not manifest_path.is_file():
        raise PublishError(f"不是运行目录：{run}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "complete":
        raise PublishError("只发布 status=complete 的运行")
    book = book or ModelBook.load()
    records = load_run_records(run)
    if cases:
        records = [r for r in records if r.case in cases]
    if runners:
        records = [r for r in records if r.runner_label in runners]
    if not records:
        raise PublishError("没有符合筛选条件的运行记录")

    items: list[dict[str, Any]] = []
    files: dict[Path, bytes] = {}
    loaded: dict[str, Case] = {}
    for record in records:
        case = loaded.get(record.case) or _load_public_case(record.case)
        loaded[record.case] = case
        artifacts = run / "cells" / record.case / record.variant_label / record.runner_label / f"repeat-{record.repeat_index}" / "artifacts"
        if not artifacts.is_dir():
            raise PublishError(f"{record.case}/{record.runner_label}: 缺少 artifacts 目录")
        item, cell_files = _public_record(record, case, artifacts, book)
        items.append(item)
        files.update(cell_files)

    run_id = run.name
    ledger_path = site / "data" / "runs" / f"{run_id}.json"
    existing = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.exists() else None
    if existing and existing.get("run_id") != run_id:
        raise PublishError(f"账本文件 {ledger_path.name} 的 run_id 与目录名不一致")
    merged: dict[tuple, dict] = {}
    for row in (existing or {}).get("records", []):
        merged[(row["case"], row["runner_label"], row["variant_label"], row["repeat_index"])] = row
    for row in items:
        merged[(row["case"], row["runner_label"], row["variant_label"], row["repeat_index"])] = row
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    document = {
        "schema_version": LEDGER_SCHEMA,
        "run_id": run_id,
        "date": min(r.started_at for r in records)[:10],
        "judge": manifest.get("judge"),
        "published_at": stamp,
        "records": sorted(merged.values(), key=lambda r: (r["case"], r["runner_label"], r["variant_label"], r["repeat_index"])),
    }
    public_text(json.dumps(document, ensure_ascii=False), hashes(document["records"]))

    # 校验全部通过，才开始写。
    for relative, data in files.items():
        target = site / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return document


def resolve_run(arg: str) -> Path:
    candidate = Path(arg)
    if candidate.is_dir():
        return candidate.resolve()
    if (RUNS / arg).is_dir():
        return (RUNS / arg).resolve()
    raise PublishError(f"找不到运行：{arg}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run", help="run_id 或 runs/<run_id> 目录")
    parser.add_argument("--cases", help="只发布这些用例，逗号分隔")
    parser.add_argument("--runners", help="只发布这些 runner，逗号分隔")
    parser.add_argument("--site", type=Path, default=SITE, help="站点目录（默认 docs/）")
    args = parser.parse_args(argv)
    try:
        document = publish_run(
            resolve_run(args.run), args.site,
            cases={c.strip() for c in args.cases.split(",")} if args.cases else None,
            runners={r.strip() for r in args.runners.split(",")} if args.runners else None,
        )
    except (PublishError, CaseError, OSError, ValueError, ET.ParseError) as exc:
        parser.exit(1, f"publish: {exc}\n")
    print(f"已发布 {document['run_id']}：{len(document['records'])} 条公开记录 → {args.site / 'data' / 'runs' / (document['run_id'] + '.json')}")
    print("下一步：python3 -m bench.site 重建站点")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
