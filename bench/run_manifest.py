"""运行溯源：稳定 hash、request manifest 校验与跨 cell 不变量。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .adapters.base import normalize_model_label
from .case import Case
from .record import RunRecord


class RunManifestError(ValueError):
    """运行溯源合同不满足。"""


def _same_model_identity(requested: object, returned: object) -> bool:
    """请求/返回模型是否同一身份。

    `[1m]` 等方括号后缀是请求侧的路由指令（如 1M 上下文别名），provider 回包
    通常回规范名；这里比较的是模型**身份**（复用 same_source 同一套归一化），
    防的是路由到错误模型，而不是禁止上下文窗口指令。
    """
    return normalize_model_label(str(requested or "")) == normalize_model_label(
        str(returned or "")
    )


@dataclass(frozen=True)
class CaseIntegritySnapshot:
    lock_sha256: str
    case_source_sha256: str
    input_sha256: str
    protocol_sha256: str | None
    protected_files: dict[str, str]
    declared_files: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


def tree_manifest(root: str | Path) -> list[dict[str, Any]]:
    base = Path(root)
    if not base.exists():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(base.rglob("*")):
        if path.is_symlink():
            raise RunManifestError(f"hash 输入不允许符号链接: {path}")
        if not path.is_file():
            continue
        try:
            path.resolve().relative_to(base.resolve())
        except ValueError as exc:
            raise RunManifestError(f"hash 输入越界: {path}") from exc
        data = path.read_bytes()
        items.append(
            {
                "path": path.relative_to(base).as_posix(),
                "size": len(data),
                "file_sha256": sha256_bytes(data),
            }
        )
    return items


def tree_manifest_sha256(root: str | Path) -> tuple[str, list[dict[str, Any]]]:
    items = tree_manifest(root)
    canonical = json.dumps(
        items, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256_bytes(canonical), items


def _source_manifest(root: Path, ignored_dirs: set[str]) -> list[dict[str, Any]]:
    """Hash owned source while excluding generated output and local caches."""
    ignored_files = {".DS_Store"}
    items: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in ignored_dirs for part in relative.parts):
            continue
        if path.name in ignored_files:
            continue
        if path.is_symlink():
            raise RunManifestError(f"case source 不允许符号链接: {path}")
        if not path.is_file():
            continue
        data = path.read_bytes()
        items.append(
            {
                "path": relative.as_posix(),
                "size": len(data),
                "file_sha256": sha256_bytes(data),
            }
        )
    return items


def _case_source_manifest(case: Case) -> list[dict[str, Any]]:
    return _source_manifest(
        case.directory,
        {"output", "__pycache__", ".pytest_cache", ".git", ".omx"},
    )


def _manifest_sha256(items: object) -> str:
    canonical = json.dumps(
        items, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256_bytes(canonical)


def snapshot_case_integrity(case: Case) -> CaseIntegritySnapshot:
    """Create one framework-owned lock for all execution-relevant case assets."""
    input_sha256, _ = tree_manifest_sha256(case.input_dir)
    case_source_sha256 = _manifest_sha256(_case_source_manifest(case))
    protocol_sha256 = None
    if case.protocol:
        protocol_sha256 = _manifest_sha256(
            _source_manifest(
                case.protocol.directory,
                {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"},
            )
        )
    protected = protected_hashes(case)
    declared = validate_case_integrity(case)
    lock_payload = {
        "case_source_sha256": case_source_sha256,
        "input_sha256": input_sha256,
        "protocol_sha256": protocol_sha256,
        "protected_files": protected,
        "declared_files": declared,
    }
    return CaseIntegritySnapshot(
        lock_sha256=_manifest_sha256(lock_payload),
        case_source_sha256=case_source_sha256,
        input_sha256=input_sha256,
        protocol_sha256=protocol_sha256,
        protected_files=protected,
        declared_files=declared,
    )


def protected_hashes(case: Case) -> dict[str, str]:
    return {
        relative: sha256_file(case.directory / relative)
        for relative in case.check.protected_files
    }


def validate_case_integrity(case: Case) -> dict[str, str]:
    relative_manifest = case.run_contract.integrity_manifest_file
    if not relative_manifest:
        return {}
    manifest_path = case.directory / relative_manifest
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RunManifestError(f"case integrity manifest 无法解析: {manifest_path}: {exc}") from exc
    declared = data.get("file_sha256") if isinstance(data, dict) else None
    if (
        not isinstance(data, dict)
        or data.get("schema_version") != 1
        or not isinstance(declared, dict)
        or not declared
    ):
        raise RunManifestError(
            f"case integrity manifest 必须为 schema_version=1 且含非空 file_sha256: {manifest_path}"
        )
    verified: dict[str, str] = {}
    case_root = case.directory.resolve()
    for raw_relative, expected in declared.items():
        relative = str(raw_relative)
        path = Path(relative)
        if (
            not relative
            or path.is_absolute()
            or ".." in path.parts
            or not isinstance(expected, str)
            or len(expected) != 64
        ):
            raise RunManifestError(f"case integrity manifest 条目非法: {relative!r}")
        candidate = case.directory / path
        if candidate.is_symlink() or not candidate.is_file():
            raise RunManifestError(f"case integrity file 不存在或为符号链接: {relative}")
        try:
            candidate.resolve().relative_to(case_root)
        except ValueError as exc:
            raise RunManifestError(f"case integrity file 越界: {relative}") from exc
        actual = sha256_file(candidate)
        if actual != expected:
            raise RunManifestError(
                f"case integrity hash 不一致: case={case.name} file={relative}"
            )
        verified[relative] = actual
    return verified


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _load_request_manifest_v2(
    data: dict[str, Any],
    manifest_path: Path,
    *,
    allow_failed_requests: bool = False,
) -> dict[str, Any]:
    allowed_top = {
        "schema_version",
        "provider",
        "runtime_sha256",
        "variant",
        "requests",
    }
    unknown_top = sorted(set(data) - allowed_top)
    if unknown_top:
        raise RunManifestError(
            f"request manifest v2 含未知顶层字段 {unknown_top}: {manifest_path}"
        )
    provider = data.get("provider")
    variant = data.get("variant")
    requests = data.get("requests")
    if not isinstance(provider, dict) or not isinstance(variant, dict):
        raise RunManifestError(f"request manifest v2 缺少 provider/variant: {manifest_path}")
    if not isinstance(requests, list) or (not requests and not allow_failed_requests):
        raise RunManifestError(f"request manifest v2 requests 必须是非空列表: {manifest_path}")
    if not _is_sha256(data.get("runtime_sha256")):
        raise RunManifestError(f"request manifest v2 runtime_sha256 非法: {manifest_path}")

    required_provider = {
        "endpoint",
        "requested_model",
        "request_config_sha256",
        "credentials_present",
    }
    if set(provider) != required_provider:
        raise RunManifestError(
            f"request manifest v2 provider 字段必须为 {sorted(required_provider)}: {manifest_path}"
        )
    if not all(
        isinstance(provider.get(key), str) and provider[key]
        for key in ("endpoint", "requested_model")
    ):
        raise RunManifestError(f"request manifest v2 provider 字符串字段非法: {manifest_path}")
    if not _is_sha256(provider.get("request_config_sha256")):
        raise RunManifestError(f"request manifest v2 request_config_sha256 非法: {manifest_path}")
    if provider.get("credentials_present") is not True:
        raise RunManifestError(f"provider 凭证不存在: {manifest_path}")

    if set(variant) != {"label", "parameters_sha256"}:
        raise RunManifestError(f"request manifest v2 variant 字段合同不满足: {manifest_path}")
    if not isinstance(variant.get("label"), str) or not variant["label"]:
        raise RunManifestError(f"request manifest v2 variant.label 非法: {manifest_path}")
    if not _is_sha256(variant.get("parameters_sha256")):
        raise RunManifestError(f"request manifest v2 parameters_sha256 非法: {manifest_path}")

    required_request = {
        "unit_id",
        "input_sha256",
        "request_sha256",
        "status_code",
        "auth_succeeded",
        "returned_model",
    }
    allowed_request = required_request | {"provider_request_id", "failure_stage"}
    allowed_failure_stages = {
        "response_decode",
        "response_envelope",
        "response_truncated",
    }
    seen_units: set[str] = set()
    for index, request in enumerate(requests):
        if not isinstance(request, dict):
            raise RunManifestError(f"requests[{index}] 必须是映射: {manifest_path}")
        status = request.get("status_code")
        auth_succeeded = request.get("auth_succeeded")
        if type(status) is not int:
            raise RunManifestError(f"provider status_code 非整数: unit={request.get('unit_id')} status={status}")
        if not isinstance(auth_succeeded, bool):
            raise RunManifestError(
                f"provider auth_succeeded 非布尔值: unit={request.get('unit_id')}"
            )
        failure_stage = request.get("failure_stage")
        if failure_stage is not None and failure_stage not in allowed_failure_stages:
            raise RunManifestError(
                f"provider failure_stage 非法: unit={request.get('unit_id')} "
                f"stage={failure_stage!r}"
            )
        if failure_stage is not None and not allow_failed_requests:
            raise RunManifestError(
                f"成功 cell 不得包含 failure_stage: unit={request.get('unit_id')}"
            )
        may_omit_returned_model = allow_failed_requests and (
            not 200 <= status < 300 or auth_succeeded is False
            or failure_stage in {"response_decode", "response_envelope"}
        )
        missing = sorted(required_request - set(request))
        if may_omit_returned_model:
            missing = [field for field in missing if field != "returned_model"]
        unknown = sorted(set(request) - allowed_request)
        if missing or unknown:
            raise RunManifestError(
                f"requests[{index}] 字段非法 missing={missing} unknown={unknown}: {manifest_path}"
            )
        unit_id = request.get("unit_id")
        if not isinstance(unit_id, str) or not unit_id or unit_id in seen_units:
            raise RunManifestError(f"request manifest v2 unit_id 为空或重复: {unit_id!r}")
        seen_units.add(unit_id)
        if not _is_sha256(request.get("input_sha256")) or not _is_sha256(
            request.get("request_sha256")
        ):
            raise RunManifestError(f"request manifest v2 request hash 非法: unit={unit_id}")
        if not allow_failed_requests and not 200 <= status < 300:
            raise RunManifestError(f"provider 请求失败: unit={unit_id} status={status}")
        if not allow_failed_requests and auth_succeeded is not True:
            raise RunManifestError(f"provider 认证失败: unit={unit_id}")
        returned_model = request.get("returned_model")
        if returned_model is None:
            if not may_omit_returned_model:
                raise RunManifestError(f"provider returned_model 缺失: unit={unit_id}")
        elif not isinstance(returned_model, str) or not returned_model:
            raise RunManifestError(f"provider returned_model 非空字符串: unit={unit_id}")
        elif not _same_model_identity(provider["requested_model"], returned_model):
            raise RunManifestError(
                f"provider 返回模型与请求不一致: unit={unit_id} "
                f"requested={provider['requested_model']!r} returned={returned_model!r}"
            )
    return data


def load_request_manifest(
    path: str | Path, *, allow_failed_requests: bool = False
) -> dict[str, Any]:
    manifest_path = Path(path)
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RunManifestError(f"request manifest 无法解析: {manifest_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise RunManifestError(f"request manifest 顶层必须是映射: {manifest_path}")
    if data.get("schema_version") == 2:
        return _load_request_manifest_v2(
            data,
            manifest_path,
            allow_failed_requests=allow_failed_requests,
        )
    if data.get("schema_version") != 1:
        raise RunManifestError(f"request manifest schema_version 必须为 1 或 2: {manifest_path}")
    allowed_top = {
        "schema_version",
        "provider",
        "wrapper_sha256",
        "prompt_construction",
        "requests",
    }
    unknown_top = sorted(set(data) - allowed_top)
    if unknown_top:
        raise RunManifestError(
            f"request manifest 含未允许顶层字段 {unknown_top}: {manifest_path}"
        )
    provider = data.get("provider")
    requests = data.get("requests")
    wrapper_sha256 = data.get("wrapper_sha256")
    if not isinstance(provider, dict) or not isinstance(requests, list) or not wrapper_sha256:
        raise RunManifestError(
            f"request manifest 缺少 provider/requests/wrapper_sha256: {manifest_path}"
        )
    required_provider = (
        "endpoint",
        "requested_model",
        "api_path",
        "temperature",
        "max_tokens",
        "credentials_present",
    )
    missing_provider = [key for key in required_provider if key not in provider]
    if missing_provider:
        raise RunManifestError(
            f"request manifest.provider 缺字段 {missing_provider}: {manifest_path}"
        )
    if provider["credentials_present"] is not True:
        raise RunManifestError(f"provider 凭证不存在: {manifest_path}")
    allowed_provider = set(required_provider) | {"api_version"}
    unknown_provider = sorted(set(provider) - allowed_provider)
    if unknown_provider:
        raise RunManifestError(
            f"request manifest.provider 含未允许字段 {unknown_provider}: {manifest_path}"
        )
    construction = data.get("prompt_construction")
    if construction is not None:
        if not isinstance(construction, dict) or set(construction) != {
            "label",
            "records_renderer",
        }:
            raise RunManifestError(
                f"prompt_construction 必须仅含 label/records_renderer: {manifest_path}"
            )
        if not all(isinstance(value, str) and value for value in construction.values()):
            raise RunManifestError(f"prompt_construction 字段必须是非空字符串: {manifest_path}")
    seen_batches: set[str] = set()
    required_request = (
        "batch_id",
        "input_sha256",
        "request_prompt_sha256",
        "status_code",
        "auth_succeeded",
        "returned_model",
    )
    allowed_request = set(required_request) | {"request_id"}
    for index, request in enumerate(requests):
        if not isinstance(request, dict):
            raise RunManifestError(f"requests[{index}] 必须是映射: {manifest_path}")
        status = request.get("status_code")
        auth_succeeded = request.get("auth_succeeded")
        if type(status) is not int:
            raise RunManifestError(
                f"provider status_code 非整数: batch={request.get('batch_id')} status={status}"
            )
        if not isinstance(auth_succeeded, bool):
            raise RunManifestError(
                f"provider auth_succeeded 非布尔值: batch={request.get('batch_id')}"
            )
        may_omit_returned_model = allow_failed_requests and (
            not 200 <= status < 300 or auth_succeeded is False
        )
        missing = [key for key in required_request if key not in request]
        if may_omit_returned_model:
            missing = [field for field in missing if field != "returned_model"]
        if missing:
            raise RunManifestError(
                f"requests[{index}] 缺字段 {missing}: {manifest_path}"
            )
        unknown_request = sorted(set(request) - allowed_request)
        if unknown_request:
            raise RunManifestError(
                f"requests[{index}] 含未允许字段 {unknown_request}: {manifest_path}"
            )
        batch_id = str(request["batch_id"])
        if batch_id in seen_batches:
            raise RunManifestError(f"request manifest batch_id 重复: {batch_id}")
        seen_batches.add(batch_id)
        if not allow_failed_requests and not 200 <= status < 300:
            raise RunManifestError(f"provider 请求失败: batch={batch_id} status={status}")
        if not allow_failed_requests and auth_succeeded is not True:
            raise RunManifestError(f"provider 认证失败: batch={batch_id}")
        returned_model = request.get("returned_model")
        if returned_model is None:
            if not may_omit_returned_model:
                raise RunManifestError(f"provider returned_model 缺失: batch={batch_id}")
        elif not isinstance(returned_model, str) or not returned_model:
            raise RunManifestError(f"provider returned_model 非空字符串: batch={batch_id}")
        elif not _same_model_identity(provider["requested_model"], returned_model):
            raise RunManifestError(
                f"provider 返回模型与请求不一致: batch={batch_id} "
                f"requested={provider['requested_model']!r} returned={returned_model!r}"
            )
    if not requests and not allow_failed_requests:
        raise RunManifestError(f"request manifest.requests 不能为空: {manifest_path}")
    return data


def validate_provider_invariants(
    records: list[RunRecord],
    cases: dict[str, Case],
    input_hashes: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """校验显式声明 request manifest 的 cells，返回可写入 run manifest 的脱敏摘要。"""
    provider_identity_by_runner: dict[str, tuple[str, str]] = {}
    request_config_by_runner_case: dict[tuple[str, str], tuple[Any, ...]] = {}
    wrapper_by_runner_case: dict[tuple[str, str], str] = {}
    prompt_by_cell_batch: dict[tuple[str, str, str, str], str] = {}
    batches_by_runner_case: dict[tuple[str, str], tuple[tuple[str, str], ...]] = {}
    summaries: list[dict[str, Any]] = []

    for record in records:
        case = cases[record.case]
        if input_hashes is not None:
            expected_input = input_hashes[record.case]
            if record.input_manifest_sha256 != expected_input:
                raise RunManifestError(
                    f"cell input hash 与 run 起始基线不一致: case={record.case} "
                    f"runner={record.runner_label} variant={record.variant_label}"
                )
        relative = case.run_contract.request_manifest_file
        if not relative:
            continue
        path = Path(record.artifacts_dir) / relative
        if not path.is_file():
            if case.run_contract.request_manifest_required:
                raise RunManifestError(f"request manifest 缺失: {path}")
            continue
        data = load_request_manifest(path, allow_failed_requests=record.is_error)
        provider = data["provider"]
        is_v2 = data["schema_version"] == 2
        provider_identity = (provider["endpoint"], provider["requested_model"])
        if is_v2:
            request_config = (provider["request_config_sha256"],)
            runtime_hash = str(data["runtime_sha256"])
            variant_summary = dict(data["variant"])
            expected_parameters_hash = _manifest_sha256(
                case.task.variant_for(record.variant_label).parameters
            )
            if variant_summary != {
                "label": record.variant_label,
                "parameters_sha256": expected_parameters_hash,
            }:
                raise RunManifestError(
                    "request manifest variant 与框架 RUN_CONTEXT 不一致: "
                    f"case={record.case} runner={record.runner_label}"
                )
            provider_summary = dict(provider)
        else:
            request_config = (
                provider["api_path"],
                provider.get("api_version"),
                provider["temperature"],
                provider["max_tokens"],
            )
            runtime_hash = str(data["wrapper_sha256"])
            variant_summary = data.get("prompt_construction")
            provider_summary = {
                key: provider[key]
                for key in (
                    "endpoint",
                    "requested_model",
                    "api_path",
                    "api_version",
                    "temperature",
                    "max_tokens",
                    "credentials_present",
                )
                if key in provider
            }
        existing_provider = provider_identity_by_runner.setdefault(
            record.runner_label, provider_identity
        )
        if existing_provider != provider_identity:
            raise RunManifestError(
                f"provider 身份跨 case/cell 漂移: runner={record.runner_label}"
            )
        config_scope = (record.runner_label, record.case)
        existing_config = request_config_by_runner_case.setdefault(
            config_scope, request_config
        )
        if existing_config != request_config:
            raise RunManifestError(
                "request 配置跨 variant/repeat 漂移: "
                f"runner={record.runner_label} case={record.case}"
            )
        wrapper_scope = (record.runner_label, record.case)
        existing_wrapper = wrapper_by_runner_case.setdefault(wrapper_scope, runtime_hash)
        if existing_wrapper != runtime_hash:
            raise RunManifestError(
                f"runtime hash 跨 variant/repeat 漂移: runner={record.runner_label} case={record.case}"
            )
        request_summaries: list[dict[str, Any]] = []
        batch_signature: list[tuple[str, str]] = []
        for request in data["requests"]:
            unit_id = str(request["unit_id"] if is_v2 else request["batch_id"])
            prompt_scope = (
                record.case,
                record.runner_label,
                record.variant_label,
                unit_id,
            )
            request_hash = str(
                request["request_sha256"]
                if is_v2
                else request["request_prompt_sha256"]
            )
            existing_prompt = prompt_by_cell_batch.setdefault(prompt_scope, request_hash)
            if existing_prompt != request_hash:
                raise RunManifestError(
                    "最终 request 跨 repeat 漂移: "
                    f"case={record.case} runner={record.runner_label} "
                    f"variant={record.variant_label} unit={unit_id}"
                )
            batch_signature.append((unit_id, str(request["input_sha256"])))
            request_summaries.append(
                {
                    "unit_id": unit_id,
                    "input_sha256": request["input_sha256"],
                    "request_sha256": request_hash,
                    "status_code": request["status_code"],
                    "returned_model": request.get("returned_model"),
                    "provider_request_id": (
                        request.get("provider_request_id")
                        if is_v2
                        else request.get("request_id")
                    ),
                }
            )
        # 运行失败的 cell（如 provider 中途报错）manifest 天然只覆盖已发出的请求：
        # 失败本身已如实记录在 run record（is_error + check），这里不让它参与
        # 完整批次签名比较，否则一个 variant 的模型失败会把整个 run 的报告一票否决。
        # 已发出请求的 per-unit hash 一致性（上方 prompt 漂移检查）仍然照常校验。
        if not record.is_error:
            batch_scope = (record.runner_label, record.case)
            signature = tuple(batch_signature)
            existing_signature = batches_by_runner_case.setdefault(batch_scope, signature)
            if existing_signature != signature:
                raise RunManifestError(
                    "batch 顺序或输入 hash 跨 variant/repeat 漂移: "
                    f"runner={record.runner_label} case={record.case}"
                )
        summaries.append(
            {
                "case": record.case,
                "runner": record.runner_label,
                "variant": record.variant_label,
                "repeat_index": record.repeat_index,
                "cell_is_error": record.is_error,
                "provider": provider_summary,
                "runtime_sha256": runtime_hash,
                "variant_context": variant_summary,
                "requests": request_summaries,
            }
        )
    return summaries
