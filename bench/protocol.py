"""Reusable, declarative case protocol profiles.

A protocol owns mechanics shared by multiple cases: runtime files, the trusted
checker, variant parameters, and run-contract defaults.  It is intentionally a
data profile, not a Python plugin system.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


_PROTOCOL_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_RESERVED_RUNTIME_TARGETS = {"PROMPT.txt", "RUN_CONTEXT.json", "input-manifest.json"}
_FORBIDDEN_SOURCE_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}


class ProtocolError(ValueError):
    """Protocol manifest is missing or violates its contract."""


@dataclass(frozen=True)
class RuntimeFile:
    source: Path
    target: str


@dataclass(frozen=True)
class ProtocolSpec:
    name: str
    directory: Path
    runtime_files: tuple[RuntimeFile, ...] = ()
    variant_parameters: dict[str, dict[str, Any]] = field(default_factory=dict)
    check_script: Path | None = None
    check_report_file: str | None = None
    protected_files: tuple[str, ...] = ()
    request_manifest_file: str | None = None
    request_manifest_required: bool = False
    default_class: str | None = None
    default_requires_engine: str | None = None


def _assert_mapping(value: object, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProtocolError(f"{context} 必须是映射。")
    return {str(key): item for key, item in value.items()}


def _assert_keys(data: dict[str, Any], allowed: set[str], context: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ProtocolError(f"{context} 含未知字段: {', '.join(unknown)}。")


def _safe_relative(raw: object, context: str) -> str:
    value = str(raw or "").strip()
    path = Path(value)
    if not value or path.is_absolute() or ".." in path.parts:
        raise ProtocolError(f"{context} 必须是安全的相对路径: {value!r}")
    return value


def _source_file(root: Path, raw: object, context: str) -> Path:
    relative = _safe_relative(raw, context)
    if any(part in _FORBIDDEN_SOURCE_DIRS for part in Path(relative).parts):
        raise ProtocolError(f"{context} 不能位于缓存目录: {relative}")
    path = root / relative
    if path.is_symlink() or not path.is_file():
        raise ProtocolError(f"{context} 不存在或为符号链接: {relative}")
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ProtocolError(f"{context} 越出 protocol 目录: {relative}") from exc
    return path


def _parse_runtime_files(root: Path, raw: object) -> tuple[RuntimeFile, ...]:
    if raw is None:
        return ()
    data = _assert_mapping(raw, "protocol.runtime_files")
    files: list[RuntimeFile] = []
    for raw_target, raw_source in data.items():
        target = _safe_relative(raw_target, "protocol.runtime_files target")
        if target in _RESERVED_RUNTIME_TARGETS:
            raise ProtocolError(f"protocol.runtime_files 不能覆盖框架文件: {target}")
        files.append(
            RuntimeFile(
                source=_source_file(
                    root,
                    raw_source,
                    f"protocol.runtime_files[{target!r}]",
                ),
                target=target,
            )
        )
    return tuple(files)


def _parse_variant_parameters(raw: object) -> dict[str, dict[str, Any]]:
    if raw is None:
        return {}
    data = _assert_mapping(raw, "protocol.variant_parameters")
    parsed: dict[str, dict[str, Any]] = {}
    for label, parameters in data.items():
        if not label or not _PROTOCOL_NAME_RE.fullmatch(label):
            raise ProtocolError(f"protocol variant label 非法: {label!r}")
        parsed[label] = _assert_mapping(
            parameters, f"protocol.variant_parameters.{label}"
        )
    return parsed


def resolve_protocol(case_dir: str | Path, name: object) -> ProtocolSpec:
    """Resolve ``<repo>/protocols/<name>/protocol.yaml`` for a case."""
    protocol_name = str(name or "").strip()
    if not _PROTOCOL_NAME_RE.fullmatch(protocol_name):
        raise ProtocolError(f"protocol 名称非法: {protocol_name!r}")

    case_path = Path(case_dir)
    protocol_root = case_path.parent.parent / "protocols"
    directory = protocol_root / protocol_name
    if directory.is_symlink():
        raise ProtocolError(f"protocol 目录不允许是符号链接: {directory}")
    try:
        directory.resolve().relative_to(protocol_root.resolve())
    except ValueError as exc:
        raise ProtocolError(f"protocol 目录越界: {protocol_name}") from exc
    manifest = directory / "protocol.yaml"
    if not manifest.is_file() or manifest.is_symlink():
        raise ProtocolError(f"protocol 不存在: {protocol_name} ({manifest})")

    raw_doc = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
    doc = _assert_mapping(raw_doc, f"protocol '{protocol_name}'")
    _assert_keys(
        doc,
        {
            "schema_version",
            "case_defaults",
            "runtime_files",
            "variant_parameters",
            "check",
            "run_contract",
        },
        f"protocol '{protocol_name}'",
    )
    if doc.get("schema_version") != 1:
        raise ProtocolError(f"protocol '{protocol_name}' schema_version 必须为 1。")

    defaults = _assert_mapping(doc.get("case_defaults") or {}, "protocol.case_defaults")
    _assert_keys(defaults, {"class", "requires_engine"}, "protocol.case_defaults")

    check = _assert_mapping(doc.get("check") or {}, "protocol.check")
    _assert_keys(check, {"script", "report_file", "protected_files"}, "protocol.check")
    check_script = None
    if check.get("script") is not None:
        check_script = _source_file(directory, check["script"], "protocol.check.script")
    report_file = None
    if check.get("report_file") is not None:
        report_file = _safe_relative(check["report_file"], "protocol.check.report_file")
    raw_protected = check.get("protected_files") or ()
    if not isinstance(raw_protected, (list, tuple)):
        raise ProtocolError("protocol.check.protected_files 必须是列表。")
    protected = tuple(
        _safe_relative(item, "protocol.check.protected_files") for item in raw_protected
    )

    contract = _assert_mapping(doc.get("run_contract") or {}, "protocol.run_contract")
    _assert_keys(
        contract,
        {"request_manifest_file", "request_manifest_required"},
        "protocol.run_contract",
    )
    request_file = None
    if contract.get("request_manifest_file") is not None:
        request_file = _safe_relative(
            contract["request_manifest_file"],
            "protocol.run_contract.request_manifest_file",
        )
    request_required = contract.get("request_manifest_required", False)
    if not isinstance(request_required, bool):
        raise ProtocolError("protocol.run_contract.request_manifest_required 必须是 boolean。")
    if request_required and not request_file:
        raise ProtocolError("protocol 要求 request manifest 时必须声明文件路径。")

    return ProtocolSpec(
        name=protocol_name,
        directory=directory,
        runtime_files=_parse_runtime_files(directory, doc.get("runtime_files")),
        variant_parameters=_parse_variant_parameters(doc.get("variant_parameters")),
        check_script=check_script,
        check_report_file=report_file,
        protected_files=protected,
        request_manifest_file=request_file,
        request_manifest_required=request_required,
        default_class=str(defaults["class"]) if defaults.get("class") else None,
        default_requires_engine=(
            str(defaults["requires_engine"])
            if defaults.get("requires_engine")
            else None
        ),
    )
