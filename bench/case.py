"""用例加载与隔离工作目录（R5/R6/R7/R19，KTD6）。

用例与模型解耦：case.yaml 只声明任务 + 校验 + 裁判 rubric + 输入资产 + 引擎兼容性。
每个 (case × runner) 在隔离临时目录中运行，不污染用例源目录。
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .protocol import ProtocolError, ProtocolSpec, resolve_protocol

VALID_TASK_TYPES = ("prompt", "skill", "slash", "custom")
# Case 一级类目：4 轴覆盖全栈开发者用 LLM 的主要工作类型。
VALID_CLASSES = ("reasoning", "coding", "tool-using", "writing")
_VARIANT_LABEL_RE = re.compile(r"^[A-Za-z0-9._-]+$")

# files_changed 快照忽略列表：launcher 自建/工具产物，避免污染计数与跨 launcher 可比性。
_IGNORE_DIRS = {
    ".git",
    ".omx",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    "node_modules",
    ".venv",
    ".idea",
    ".vscode",
}
_IGNORE_FILES = {".DS_Store"}
_IGNORE_SUFFIXES = (".pyc", ".pyo", ".log", ".tmp", ".lock")


class CaseError(ValueError):
    """用例加载或校验错误。"""


@dataclass(frozen=True)
class PromptVariant:
    label: str
    prompt: str
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Task:
    type: str
    prompt: str
    skill: str | None = None
    args: str = ""
    variants: tuple[PromptVariant, ...] = ()
    default_variant: str = "default"

    @property
    def variant_labels(self) -> tuple[str, ...]:
        return tuple(v.label for v in self.variants)

    def prompt_for(self, label: str) -> str:
        return self.variant_for(label).prompt

    def variant_for(self, label: str) -> PromptVariant:
        for variant in self.variants:
            if variant.label == label:
                return variant
        raise CaseError(f"prompt variant 不存在: {label}")


@dataclass(frozen=True)
class CheckSpec:
    type: str = "none"  # script | none
    script: str | None = None  # 相对用例目录
    report_file: str | None = None  # 相对 workdir
    protected_files: tuple[str, ...] = ()  # 相对用例目录，不复制给 runner
    script_root: Path | None = None  # protocol checker 时指向 protocol 目录


@dataclass(frozen=True)
class RunContract:
    request_manifest_file: str | None = None  # 相对 workdir
    request_manifest_required: bool = False
    integrity_manifest_file: str | None = None  # 相对 case 目录，运行前复算


@dataclass(frozen=True)
class JudgeSpec:
    enabled: bool = False
    rubric: str = ""  # rubric 文本
    dimensions: tuple[str, ...] = ()


@dataclass(frozen=True)
class VariantComparisonSpec:
    baseline: str
    candidate: str
    method: str = "item_exact_match"


@dataclass(frozen=True)
class EvaluationPolicy:
    role: str | None = None
    scope: str | None = None
    generalizes: bool | None = None
    intervention: str | None = None
    comparison: VariantComparisonSpec | None = None
    unit_of_analysis: str = "run"
    independent_unit: str = "run"


@dataclass(frozen=True)
class Case:
    name: str
    directory: Path
    task: Task
    check: CheckSpec = field(default_factory=CheckSpec)
    judge: JudgeSpec = field(default_factory=JudgeSpec)
    requires_engine: str | None = None
    repeat: int | None = None  # 覆盖全局 repeat
    class_: str = "coding"  # reasoning / coding / tool-using / writing
    expected: dict = field(default_factory=dict)  # ground-truth，check.sh / judge 用
    run_contract: RunContract = field(default_factory=RunContract)
    evaluation: EvaluationPolicy = field(default_factory=EvaluationPolicy)
    schema_version: int = 1
    protocol: ProtocolSpec | None = None

    def __post_init__(self) -> None:
        # Preserve the small programmatic Case constructor used by integrations
        # while keeping the normalized runtime contract strongly typed.
        if isinstance(self.evaluation, dict):
            object.__setattr__(
                self,
                "evaluation",
                _parse_evaluation(
                    self.evaluation,
                    self.expected,
                    self.task,
                    self.name,
                    self.schema_version,
                ),
            )

    @property
    def input_dir(self) -> Path:
        return self.directory / "input"

    @property
    def verify_dir(self) -> Path:
        """只读基准目录：check 前覆盖到产物目录，选手改不了（防篡改）。"""
        return self.directory / "verify"

    def output_dir(self, runner_label: str) -> Path:
        """旧输出路径，仅供读取历史产物；新运行的产物统一落在
        `<report_root>/runs/<run_id>/cells/...`（见 bench/layout.py）。"""
        if not _VARIANT_LABEL_RE.fullmatch(runner_label):
            raise CaseError(f"runner label 不能用于输出路径: {runner_label!r}")
        return self.directory / "output" / runner_label

    def supports_launcher(self, launcher_type: str) -> bool:
        """requires_engine 兼容性判断（R19）。"""
        return self.requires_engine is None or self.requires_engine == launcher_type

    @property
    def check_script_path(self) -> Path | None:
        if not self.check.script:
            return None
        return (self.check.script_root or self.directory) / self.check.script


def _safe_relative_path(raw: object, *, field_name: str) -> str:
    value = str(raw or "").strip()
    path = Path(value)
    if not value or path.is_absolute() or ".." in path.parts:
        raise CaseError(f"{field_name} 必须是安全的相对路径: {value!r}")
    return value


def _case_file(case_dir: Path, raw: object, *, field_name: str) -> Path:
    relative = _safe_relative_path(raw, field_name=field_name)
    path = case_dir / relative
    if path.is_symlink():
        raise CaseError(f"{field_name} 不允许使用符号链接: {relative}")
    try:
        path.resolve().relative_to(case_dir.resolve())
    except ValueError as exc:
        raise CaseError(f"{field_name} 越出用例目录: {relative}") from exc
    return path


def _read_prompt_file(case_dir: Path, name: str, raw: object, field_name: str) -> str:
    path = _case_file(case_dir, raw, field_name=field_name)
    if not path.is_file():
        raise CaseError(f"用例 '{name}' 的 {field_name} 不存在: {raw}")
    return path.read_text(encoding="utf-8").strip()


def _resolve_prompt(
    case_dir: Path,
    name: str,
    task_data: dict[str, Any],
    protocol: ProtocolSpec | None = None,
) -> Task:
    ttype = task_data.get("type")
    if ttype not in VALID_TASK_TYPES:
        raise CaseError(
            f"用例 '{name}' 的 task.type='{ttype}' 非法；必须是 {', '.join(VALID_TASK_TYPES)}。"
        )
    skill = task_data.get("skill")
    args = str(task_data.get("args", "") or "")

    variants: tuple[PromptVariant, ...]
    default_variant = "default"

    if ttype in ("prompt", "custom"):
        raw_variants = task_data.get("variants")
        if raw_variants is not None:
            if task_data.get("prompt_file"):
                raise CaseError(
                    f"用例 '{name}' 的 task.variants 与 task.prompt_file 不能同时配置。"
                )
            if not isinstance(raw_variants, dict) or not raw_variants:
                raise CaseError(f"用例 '{name}' 的 task.variants 必须是非空映射。")
            resolved: list[PromptVariant] = []
            for raw_label, spec in raw_variants.items():
                label = str(raw_label)
                if not _VARIANT_LABEL_RE.fullmatch(label):
                    raise CaseError(f"用例 '{name}' 的 variant label 非法: {label!r}")
                if not isinstance(spec, dict) or not spec.get("prompt_file"):
                    raise CaseError(
                        f"用例 '{name}' 的 variant '{label}' 必须提供 prompt_file。"
                    )
                prompt_text = _read_prompt_file(
                    case_dir,
                    name,
                    spec["prompt_file"],
                    f"variant '{label}' prompt_file",
                )
                resolved.append(
                    PromptVariant(
                        label=label,
                        prompt=prompt_text,
                        parameters=dict(
                            (protocol.variant_parameters if protocol else {}).get(label, {})
                        ),
                    )
                )
            default_variant = str(task_data.get("default_variant") or "")
            labels = {v.label for v in resolved}
            if default_variant not in labels:
                raise CaseError(
                    f"用例 '{name}' 的 default_variant='{default_variant}' 未在 variants 中声明。"
                )
            variants = tuple(resolved)
            prompt = next(v.prompt for v in variants if v.label == default_variant)
        else:
            pf = task_data.get("prompt_file")
            if not pf:
                raise CaseError(f"用例 '{name}' 的 {ttype} 任务必须提供 prompt_file。")
            prompt = _read_prompt_file(case_dir, name, pf, "prompt_file")
            variants = (
                PromptVariant(
                    label="default",
                    prompt=prompt,
                    parameters=dict(
                        (protocol.variant_parameters if protocol else {}).get("default", {})
                    ),
                ),
            )
    elif ttype == "skill":
        if task_data.get("variants") is not None:
            raise CaseError(f"用例 '{name}' 的 skill 任务不支持 task.variants。")
        if not skill:
            raise CaseError(f"用例 '{name}' 的 skill 任务必须提供 skill 名。")
        prompt = f"/{skill} {args}".strip()
        variants = (PromptVariant(label="default", prompt=prompt),)
    else:  # slash
        if task_data.get("variants") is not None:
            raise CaseError(f"用例 '{name}' 的 slash 任务不支持 task.variants。")
        cmd = task_data.get("command") or skill
        if not cmd:
            raise CaseError(f"用例 '{name}' 的 slash 任务必须提供 command。")
        prompt = f"{cmd} {args}".strip()
        variants = (PromptVariant(label="default", prompt=prompt),)

    return Task(
        type=ttype,
        prompt=prompt,
        skill=skill,
        args=args,
        variants=variants,
        default_variant=default_variant,
    )


def _assert_mapping(raw: object, context: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise CaseError(f"{context} 必须是映射。")
    return {str(key): value for key, value in raw.items()}


def _assert_keys(data: dict[str, Any], allowed: set[str], context: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise CaseError(f"{context} 含未知字段: {', '.join(unknown)}。")


def _normalize_v2_task(raw: object, name: str) -> dict[str, Any]:
    if isinstance(raw, str):
        return {"type": "prompt", "prompt_file": raw}
    task = _assert_mapping(raw, f"用例 '{name}' 的 task")
    _assert_keys(
        task,
        {"type", "prompt", "variants", "default", "skill", "args", "command"},
        f"用例 '{name}' 的 task",
    )
    task_type = str(task.get("type") or ("custom" if "variants" in task else "prompt"))
    normalized: dict[str, Any] = {"type": task_type}
    for key in ("skill", "args", "command"):
        if key in task:
            normalized[key] = task[key]

    if "variants" in task:
        if "prompt" in task:
            raise CaseError(f"用例 '{name}' 的 task.prompt 与 task.variants 不能同时配置。")
        variants = _assert_mapping(task["variants"], f"用例 '{name}' 的 task.variants")
        if not variants:
            raise CaseError(f"用例 '{name}' 的 task.variants 不能为空。")
        normalized_variants: dict[str, dict[str, str]] = {}
        for label, spec in variants.items():
            if isinstance(spec, str):
                prompt_file = spec
            else:
                variant = _assert_mapping(
                    spec, f"用例 '{name}' 的 task.variants.{label}"
                )
                _assert_keys(
                    variant,
                    {"prompt"},
                    f"用例 '{name}' 的 task.variants.{label}",
                )
                prompt_file = variant.get("prompt")
            if not prompt_file:
                raise CaseError(f"用例 '{name}' 的 variant '{label}' 缺少 prompt。")
            normalized_variants[label] = {"prompt_file": str(prompt_file)}
        default = task.get("default")
        if not isinstance(default, str) or not default:
            raise CaseError(f"用例 '{name}' 的多 variant task 必须显式声明 default。")
        normalized["variants"] = normalized_variants
        normalized["default_variant"] = default
    else:
        prompt_file = task.get("prompt")
        if task_type in ("prompt", "custom"):
            if not isinstance(prompt_file, str) or not prompt_file:
                raise CaseError(f"用例 '{name}' 的 task.prompt 必须是非空路径。")
            normalized["prompt_file"] = prompt_file
    return normalized


def _normalize_v2_case(data: dict[str, Any], name: str) -> dict[str, Any]:
    _assert_keys(
        data,
        {
            "schema_version",
            "name",
            "class",
            "requires_engine",
            "repeat",
            "protocol",
            "task",
            "check",
            "judge",
            "expected",
            "evaluation",
        },
        f"用例 '{name}'",
    )
    normalized = dict(data)
    normalized["task"] = _normalize_v2_task(data.get("task"), name)

    raw_check = data.get("check")
    if isinstance(raw_check, str):
        normalized["check"] = {"type": "script", "script": raw_check}
    elif raw_check is not None:
        check = _assert_mapping(raw_check, f"用例 '{name}' 的 check")
        _assert_keys(
            check,
            {"script", "report_file", "protected_files"},
            f"用例 '{name}' 的 check",
        )
        normalized["check"] = {
            **check,
            "type": "script" if check.get("script") else "none",
        }

    raw_judge = data.get("judge")
    if isinstance(raw_judge, str):
        normalized["judge"] = {"enabled": True, "rubric_file": raw_judge}
    elif raw_judge is not None:
        judge = _assert_mapping(raw_judge, f"用例 '{name}' 的 judge")
        _assert_keys(
            judge,
            {"rubric", "dimensions"},
            f"用例 '{name}' 的 judge",
        )
        rubric = judge.get("rubric")
        normalized["judge"] = {
            "enabled": bool(rubric),
            "rubric_file": rubric,
            "dimensions": judge.get("dimensions") or [],
        }
    return normalized


def _positive_repeat(raw: object, name: str, schema_version: int) -> int | None:
    if raw is None:
        return None
    if schema_version == 2 and (isinstance(raw, bool) or not isinstance(raw, int)):
        raise CaseError(f"用例 '{name}' 的 repeat 必须是正整数。")
    try:
        repeat = int(raw)
    except (TypeError, ValueError) as exc:
        raise CaseError(f"用例 '{name}' 的 repeat 必须是正整数。") from exc
    if repeat < 1:
        raise CaseError(f"用例 '{name}' 的 repeat 必须是正整数。")
    return repeat


def _parse_evaluation(
    raw: object,
    expected: dict[str, Any],
    task: Task,
    name: str,
    schema_version: int,
) -> EvaluationPolicy:
    data = _assert_mapping(raw or {}, f"用例 '{name}' 的 evaluation")
    if schema_version == 2:
        _assert_keys(
            data,
            {
                "role",
                "scope",
                "generalizes",
                "intervention",
                "comparison",
                "unit_of_analysis",
                "independent_unit",
            },
            f"用例 '{name}' 的 evaluation",
        )
        if "comparison" in expected:
            raise CaseError(
                f"用例 '{name}' 的 schema v2 必须把 comparison 放到 evaluation。"
            )
        comparison_data = data.get("comparison")
        generalizes = data.get("generalizes")
    else:
        comparison_data = data.get("comparison") or expected.get("comparison")
        generalizes = data.get("generalization_evidence")

    if generalizes is not None and not isinstance(generalizes, bool):
        raise CaseError(f"用例 '{name}' 的 generalizes 必须是 boolean。")

    comparison = None
    if comparison_data is not None:
        comparison_map = _assert_mapping(
            comparison_data, f"用例 '{name}' 的 evaluation.comparison"
        )
        if schema_version == 2:
            _assert_keys(
                comparison_map,
                {"method", "baseline", "candidate"},
                f"用例 '{name}' 的 evaluation.comparison",
            )
            method = comparison_map.get("method")
            baseline = comparison_map.get("baseline")
            candidate = comparison_map.get("candidate")
        else:
            method = "item_exact_match"
            baseline = comparison_map.get("baseline_variant")
            candidate = comparison_map.get("candidate_variant")
        if method not in {"item_exact_match", "item_value_diff"}:
            raise CaseError(
                f"用例 '{name}' 的 comparison.method 当前仅支持 "
                "item_exact_match 或 item_value_diff。"
            )
        if not isinstance(baseline, str) or not isinstance(candidate, str):
            raise CaseError(f"用例 '{name}' 的 comparison 必须声明 baseline/candidate。")
        if baseline == candidate:
            raise CaseError(f"用例 '{name}' 的 comparison 两个 variant 不能相同。")
        labels = set(task.variant_labels)
        missing = [label for label in (baseline, candidate) if label not in labels]
        if missing:
            raise CaseError(
                f"用例 '{name}' 的 comparison 引用了不存在的 variant: {', '.join(missing)}。"
            )
        comparison = VariantComparisonSpec(
            baseline=baseline,
            candidate=candidate,
            method=method,
        )

    def optional_text(key: str) -> str | None:
        value = data.get(key)
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise CaseError(f"用例 '{name}' 的 evaluation.{key} 必须是非空字符串。")
        return value.strip()

    unit = data.get("unit_of_analysis", "run")
    independent = data.get("independent_unit", "run")
    if not isinstance(unit, str) or not unit.strip():
        raise CaseError(f"用例 '{name}' 的 unit_of_analysis 必须是非空字符串。")
    if not isinstance(independent, str) or not independent.strip():
        raise CaseError(f"用例 '{name}' 的 independent_unit 必须是非空字符串。")
    role = optional_text("role")
    if schema_version == 2 and comparison is not None:
        if role is None or generalizes is None:
            raise CaseError(
                f"用例 '{name}' 声明 comparison 时必须显式声明 role 和 generalizes。"
            )
        if unit.strip() != "item":
            raise CaseError(
                f"用例 '{name}' 的 item comparison 要求 unit_of_analysis=item。"
            )
        if role in {"calibration", "synthetic_diagnostic"} and generalizes:
            raise CaseError(
                f"用例 '{name}' 的 role={role} 不能声明 generalizes=true。"
            )
    return EvaluationPolicy(
        role=role,
        scope=optional_text("scope"),
        generalizes=generalizes,
        intervention=optional_text("intervention"),
        comparison=comparison,
        unit_of_analysis=unit.strip(),
        independent_unit=independent.strip(),
    )


def load_case(case_dir: str | Path) -> Case:
    """加载并校验一个用例目录。"""
    directory = Path(case_dir)
    manifest = directory / "case.yaml"
    if not manifest.exists():
        raise CaseError(f"用例缺少 case.yaml: {directory}")

    raw_data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
    if not isinstance(raw_data, dict):
        raise CaseError(f"用例 manifest 顶层必须是映射: {manifest}")
    data = {str(key): value for key, value in raw_data.items()}
    name = str(data.get("name") or directory.name)

    schema_version = data.get("schema_version", 1)
    if isinstance(schema_version, bool) or not isinstance(schema_version, int):
        raise CaseError(f"用例 '{name}' 的 schema_version 必须是整数。")
    if schema_version not in (1, 2):
        raise CaseError(f"用例 '{name}' 不支持 schema_version={schema_version}。")

    protocol = None
    if schema_version == 2:
        data = _normalize_v2_case(data, name)
        if data.get("protocol") is not None:
            try:
                protocol = resolve_protocol(directory, data["protocol"])
            except ProtocolError as exc:
                raise CaseError(f"用例 '{name}' 的 protocol 非法: {exc}") from exc

    task_data = data.get("task")
    if not isinstance(task_data, dict):
        raise CaseError(f"用例 '{name}' 缺少 task: 映射。")
    task = _resolve_prompt(directory, name, task_data, protocol)

    check_data = _assert_mapping(data.get("check") or {}, f"用例 '{name}' 的 check")
    if protocol and check_data and protocol.check_script:
        raise CaseError(f"用例 '{name}' 不能覆盖 protocol 提供的 check。")
    if protocol and not check_data:
        check_data = {
            "type": "script" if protocol.check_script else "none",
            "script": (
                protocol.check_script.relative_to(protocol.directory).as_posix()
                if protocol.check_script
                else None
            ),
            "report_file": protocol.check_report_file,
            "protected_files": list(protocol.protected_files),
        }
    check_type = check_data.get("type", "none")
    if check_type not in ("script", "none"):
        raise CaseError(f"用例 '{name}' 的 check.type='{check_type}' 非法（script|none）。")
    if check_type == "script" and not check_data.get("script"):
        raise CaseError(f"用例 '{name}' 的 script 校验必须提供 script 路径。")
    report_file = check_data.get("report_file")
    if report_file is not None:
        report_file = _safe_relative_path(report_file, field_name="check.report_file")
    raw_protected = check_data.get("protected_files") or ()
    if not isinstance(raw_protected, (list, tuple)):
        raise CaseError(f"用例 '{name}' 的 check.protected_files 必须是列表。")
    protected_files: list[str] = []
    input_root = (directory / "input").resolve()
    for raw in raw_protected:
        relative = _safe_relative_path(raw, field_name="check.protected_files")
        protected = _case_file(directory, relative, field_name="check.protected_files")
        if not protected.is_file():
            raise CaseError(f"用例 '{name}' 的 protected file 不存在: {relative}")
        try:
            protected.resolve().relative_to(input_root)
        except ValueError:
            pass
        else:
            raise CaseError(f"用例 '{name}' 的 protected file 不能位于 input/: {relative}")
        protected_files.append(relative)
    check_script = check_data.get("script")
    check_script_root = protocol.directory if protocol and protocol.check_script else None
    if check_script is not None:
        check_script = _safe_relative_path(check_script, field_name="check.script")
        if check_script_root is None:
            script_path = _case_file(directory, check_script, field_name="check.script")
            if schema_version == 2 and not script_path.is_file():
                raise CaseError(f"用例 '{name}' 的 check.script 不存在: {check_script}")
    check = CheckSpec(
        type=check_type,
        script=check_script,
        script_root=check_script_root,
        report_file=report_file,
        protected_files=tuple(protected_files),
    )

    contract_data = data.get("run_contract") or {}
    if protocol:
        contract_data = {
            "request_manifest_file": protocol.request_manifest_file,
            "request_manifest_required": protocol.request_manifest_required,
        }
    contract_data = _assert_mapping(contract_data, f"用例 '{name}' 的 run_contract")
    request_manifest_file = contract_data.get("request_manifest_file")
    if request_manifest_file is not None:
        request_manifest_file = _safe_relative_path(
            request_manifest_file, field_name="run_contract.request_manifest_file"
        )
    request_manifest_required = bool(contract_data.get("request_manifest_required", False))
    if request_manifest_required and not request_manifest_file:
        raise CaseError(
            f"用例 '{name}' 启用 request_manifest_required 时必须配置 request_manifest_file。"
        )
    integrity_manifest_file = contract_data.get("integrity_manifest_file")
    if integrity_manifest_file is not None:
        integrity_manifest_file = _safe_relative_path(
            integrity_manifest_file, field_name="run_contract.integrity_manifest_file"
        )
        integrity_path = _case_file(
            directory,
            integrity_manifest_file,
            field_name="run_contract.integrity_manifest_file",
        )
        if not integrity_path.is_file():
            raise CaseError(
                f"用例 '{name}' 的 integrity manifest 不存在: {integrity_manifest_file}"
            )
    run_contract = RunContract(
        request_manifest_file=request_manifest_file,
        request_manifest_required=request_manifest_required,
        integrity_manifest_file=integrity_manifest_file,
    )

    judge_data = _assert_mapping(data.get("judge") or {}, f"用例 '{name}' 的 judge")
    rubric = ""
    if judge_data.get("enabled"):
        rf = judge_data.get("rubric_file")
        if rf:
            rf = _safe_relative_path(rf, field_name="judge.rubric_file")
            rpath = _case_file(directory, rf, field_name="judge.rubric_file")
            if not rpath.exists():
                raise CaseError(f"用例 '{name}' 的 rubric_file 不存在: {rf}")
            rubric = rpath.read_text(encoding="utf-8").strip()
    judge = JudgeSpec(
        enabled=bool(judge_data.get("enabled")),
        rubric=rubric,
        dimensions=tuple(judge_data.get("dimensions", []) or []),
    )

    repeat = _positive_repeat(data.get("repeat"), name, schema_version)
    class_ = str(
        data.get("class")
        or (protocol.default_class if protocol else None)
        or "coding"
    )
    if class_ not in VALID_CLASSES:
        raise CaseError(
            f"用例 '{name}' 的 class='{class_}' 非法；必须是 {', '.join(VALID_CLASSES)}。"
        )
    expected = data.get("expected") or {}
    if not isinstance(expected, dict):
        raise CaseError(f"用例 '{name}' 的 expected 必须是映射。")
    expected = {str(key): value for key, value in expected.items()}
    evaluation = _parse_evaluation(
        data.get("evaluation"), expected, task, name, schema_version
    )
    if evaluation.comparison is not None and (
        check.type != "script" or not check.report_file
    ):
        raise CaseError(
            f"用例 '{name}' 的 item comparison 必须使用结构化 script check。"
        )
    if schema_version == 2 and check.type == "none" and not (
        judge.enabled and judge.rubric
    ):
        raise CaseError(
            f"用例 '{name}' 的 schema v2 必须至少声明一个可用的 check 或 judge。"
        )
    return Case(
        name=name,
        directory=directory,
        task=task,
        check=check,
        judge=judge,
        requires_engine=(
            data.get("requires_engine")
            or (protocol.default_requires_engine if protocol else None)
        ),
        repeat=repeat,
        class_=class_,
        expected=expected,
        run_contract=run_contract,
        evaluation=evaluation,
        schema_version=schema_version,
        protocol=protocol,
    )


def list_cases(cases_root: str | Path) -> list[Case]:
    """加载单个 cases 根下所有含 case.yaml 的用例。"""
    root = Path(cases_root)
    out: list[Case] = []
    if not root.exists():
        return out
    for child in sorted(root.iterdir()):
        if child.is_dir() and (child / "case.yaml").exists():
            out.append(load_case(child))
    return out


def resolve_case_roots(repo_root: str | Path) -> list[Path]:
    """公开 `cases/` + 环境变量 `AI_EVAL_PRIVATE_CASES` 指定的私有根（os.pathsep 分隔）。

    私有根**放仓库外**（物理隔离，杜绝误提交进公开仓）；不存在的路径安静跳过。
    顺序：公开在前 → 私有按声明顺序在后（用于同名冲突时公开优先）。
    """
    roots: list[Path] = [Path(repo_root) / "cases"]
    for raw in os.environ.get("AI_EVAL_PRIVATE_CASES", "").split(os.pathsep):
        raw = raw.strip()
        if raw:
            p = Path(raw).expanduser()
            if p not in roots:
                roots.append(p)
    return roots


def discover_cases(repo_root: str | Path) -> list[Case]:
    """扫描公开 + 私有所有 case 根，合并；同名冲突保留先出现者（公开优先）。"""
    seen: set[str] = set()
    out: list[Case] = []
    for root in resolve_case_roots(repo_root):
        for case in list_cases(root):
            if case.name in seen:
                continue
            seen.add(case.name)
            out.append(case)
    return out


def is_private_case(case: Case, repo_root: str | Path) -> bool:
    """用例是否来自仓库外的私有根（不在公开 `cases/` 下）。"""
    public = (Path(repo_root) / "cases").resolve()
    try:
        case.directory.resolve().relative_to(public)
        return False
    except ValueError:
        return True


# ── 隔离工作目录 ─────────────────────────────────────
def _should_ignore(rel_parts: tuple[str, ...], name: str) -> bool:
    if any(part in _IGNORE_DIRS for part in rel_parts):
        return True
    if name in _IGNORE_FILES:
        return True
    return name.endswith(_IGNORE_SUFFIXES)


def snapshot_dir(path: str | Path) -> dict[str, tuple[int, int]]:
    """对目录做文件签名快照（相对路径 → (size, mtime_ns)），应用忽略列表。"""
    base = Path(path)
    snap: dict[str, tuple[int, int]] = {}
    for f in base.rglob("*"):
        rel = f.relative_to(base)
        if _should_ignore(rel.parts[:-1], f.name) or any(
            part in _IGNORE_DIRS for part in rel.parts
        ):
            continue
        if f.is_symlink():
            raise CaseError(f"目录中不允许符号链接: {f}")
        if not f.is_file():
            continue
        try:
            st = f.stat()
        except OSError:
            continue
        snap[str(rel)] = (st.st_size, st.st_mtime_ns)
    return snap


def count_changed(before: dict[str, tuple], after: dict[str, tuple]) -> int:
    """统计 created + modified + deleted 文件数（无方向诊断量，KTD5）。"""
    created = set(after) - set(before)
    deleted = set(before) - set(after)
    modified = {k for k in set(before) & set(after) if before[k] != after[k]}
    return len(created) + len(deleted) + len(modified)


def copy_artifacts(workdir: str | Path, dest: str | Path) -> None:
    """把 workdir 内容（排除忽略项）复制到 output/<runner>/。

    复制前清空 dest，避免上次运行的残留产物污染本次 check/judge（重跑误判）。
    """
    src = Path(workdir)
    dst = Path(dest)
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    dst.mkdir(parents=True, exist_ok=True)
    for f in src.rglob("*"):
        rel = f.relative_to(src)
        if _should_ignore(rel.parts[:-1], f.name) or any(
            part in _IGNORE_DIRS for part in rel.parts
        ):
            continue
        if f.is_symlink():
            raise CaseError(f"产物中不允许符号链接: {f}")
        if not f.is_file():
            continue
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, target)


def restore_verify_assets(case: Case, target_dir: str | Path) -> None:
    """把 case verify/ 的只读基准文件覆盖到 target_dir（check 前调用，防选手篡改测试）。

    verify/ 不进入选手 workdir（只 input/ 进），所以选手看不到也改不了基准测试；
    check 时还原基准，确保确定性质量锚可信。
    """
    if not case.verify_dir.exists():
        return
    dst = Path(target_dir)
    for f in case.verify_dir.rglob("*"):
        if f.is_symlink():
            raise CaseError(f"verify/ 中不允许符号链接: {f}")
        if not f.is_file():
            continue
        rel = f.relative_to(case.verify_dir)
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, target)


@contextlib.contextmanager
def isolated_workdir(case: Case) -> Iterator[Path]:
    """创建隔离目录，拷入 case input 与 protocol 共享 runtime 文件。"""
    tmp = Path(tempfile.mkdtemp(prefix=f"bench-{case.name}-"))
    try:
        if case.input_dir.exists():
            for f in case.input_dir.rglob("*"):
                if f.is_symlink():
                    raise CaseError(f"input/ 中不允许符号链接: {f}")
                if f.is_file():
                    try:
                        f.resolve().relative_to(case.input_dir.resolve())
                    except ValueError as exc:
                        raise CaseError(f"input/ 文件越界: {f}") from exc
                    rel = f.relative_to(case.input_dir)
                    target = tmp / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, target)
        if case.protocol:
            for runtime_file in case.protocol.runtime_files:
                target = tmp / runtime_file.target
                if target.exists():
                    raise CaseError(
                        f"protocol runtime 与 case input 路径冲突: {runtime_file.target}"
                    )
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(runtime_file.source, target)
        yield tmp
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
