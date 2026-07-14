from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from bench.adapters.base import Adapter, ParsedOutput
from bench.case import CheckSpec, CaseError, isolated_workdir, load_case
from bench.config import ConfigError, RunConfig, load_config
from bench.orchestrator import run_matrix
from bench.registry import RunnerProfile
from bench.run_manifest import snapshot_case_integrity
from bench.scoring import run_check


class _Adapter(Adapter):
    launcher_type = "command"

    def build_command(self, profile, prompt, workdir):
        return ["fake", prompt]

    def parse(self, stdout, stderr, exit_code):
        return ParsedOutput(is_error=exit_code not in (0, None))


def _case_root(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "repo"
    case_dir = root / "cases" / "v2-case"
    (case_dir / "prompts").mkdir(parents=True)
    (case_dir / "input").mkdir()
    (case_dir / "oracle").mkdir()
    (case_dir / "prompts" / "a.md").write_text("prompt A", encoding="utf-8")
    (case_dir / "prompts" / "b.md").write_text("prompt B", encoding="utf-8")
    (case_dir / "input" / "dataset.jsonl").write_text('{"id":"1"}\n', encoding="utf-8")
    (case_dir / "oracle" / "labels.jsonl").write_text('{"id":"1"}\n', encoding="utf-8")
    return root, case_dir


def _write_protocol(root: Path) -> None:
    protocol = root / "protocols" / "structured-batch-v1"
    protocol.mkdir(parents=True)
    (protocol / "runtime.py").write_text("print('runtime')\n", encoding="utf-8")
    (protocol / "check.sh").write_text(
        textwrap.dedent(
            """
            #!/bin/sh
            set -eu
            test -f "$AI_EVAL_CASE_DIR/oracle/labels.jsonl"
            printf '%s' '{"schema_version":1,"passed":true,"summary":{},"items":[],"errors":[]}' > evaluation.json
            """
        ).lstrip(),
        encoding="utf-8",
    )
    (protocol / "protocol.yaml").write_text(
        textwrap.dedent(
            """
            schema_version: 1
            case_defaults:
              class: reasoning
              requires_engine: command
            runtime_files:
              run_provider.py: runtime.py
            variant_parameters:
              original:
                renderer: legacy
              candidate:
                renderer: bounded
            check:
              script: check.sh
              report_file: evaluation.json
              protected_files: [oracle/labels.jsonl]
            run_contract:
              request_manifest_file: request_manifest.json
              request_manifest_required: true
            """
        ),
        encoding="utf-8",
    )


def _write_v2_case(case_dir: Path) -> None:
    (case_dir / "case.yaml").write_text(
        textwrap.dedent(
            """
            schema_version: 2
            protocol: structured-batch-v1
            repeat: 2
            task:
              variants:
                original: prompts/a.md
                candidate: prompts/b.md
              default: candidate
            evaluation:
              role: calibration
              scope: dev_regression
              generalizes: false
              intervention: prompt_construction
              comparison: {method: item_exact_match, baseline: original, candidate: candidate}
              unit_of_analysis: item
              independent_unit: batch
            """
        ),
        encoding="utf-8",
    )


def test_v2_protocol_normalizes_minimal_case_and_runtime(tmp_path: Path) -> None:
    root, case_dir = _case_root(tmp_path)
    _write_protocol(root)
    _write_v2_case(case_dir)

    case = load_case(case_dir)
    assert case.name == "v2-case"
    assert case.schema_version == 2
    assert case.class_ == "reasoning"
    assert case.requires_engine == "command"
    assert case.task.default_variant == "candidate"
    assert case.task.variant_for("original").parameters == {"renderer": "legacy"}
    assert case.evaluation.comparison is not None
    assert case.evaluation.comparison.baseline == "original"
    assert case.check.script_root == root / "protocols" / "structured-batch-v1"
    assert case.run_contract.request_manifest_required is True

    with isolated_workdir(case) as workdir:
        assert (workdir / "run_provider.py").read_text(encoding="utf-8") == "print('runtime')\n"


def test_protocol_change_updates_framework_case_lock(tmp_path: Path) -> None:
    root, case_dir = _case_root(tmp_path)
    _write_protocol(root)
    _write_v2_case(case_dir)
    case = load_case(case_dir)
    before = snapshot_case_integrity(case)

    cache = root / "protocols" / "structured-batch-v1" / "__pycache__"
    cache.mkdir()
    (cache / "runtime.cpython-311.pyc").write_bytes(b"cache")
    with_cache = snapshot_case_integrity(case)
    assert with_cache.protocol_sha256 == before.protocol_sha256
    assert with_cache.lock_sha256 == before.lock_sha256

    protocol_output = root / "protocols" / "structured-batch-v1" / "output"
    protocol_output.mkdir()
    (protocol_output / "runtime.py").write_text("print('tracked')\n", encoding="utf-8")
    with_output = snapshot_case_integrity(case)
    assert with_output.protocol_sha256 != before.protocol_sha256
    assert with_output.lock_sha256 != before.lock_sha256

    (root / "protocols" / "structured-batch-v1" / "runtime.py").write_text(
        "print('changed')\n", encoding="utf-8"
    )

    after = snapshot_case_integrity(case)
    assert before.protocol_sha256 != after.protocol_sha256
    assert before.lock_sha256 != after.lock_sha256


def test_check_spec_keeps_v1_positional_constructor_order() -> None:
    check = CheckSpec("script", "check.sh", "report.json", ("oracle.json",))

    assert check.report_file == "report.json"
    assert check.protected_files == ("oracle.json",)
    assert check.script_root is None


def test_v2_requires_at_least_one_evaluator(tmp_path: Path) -> None:
    _, case_dir = _case_root(tmp_path)
    (case_dir / "case.yaml").write_text(
        "schema_version: 2\ntask: prompts/a.md\n",
        encoding="utf-8",
    )

    with pytest.raises(CaseError, match="至少声明一个可用的 check 或 judge"):
        load_case(case_dir)


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        (
            "comparison: {method: item_exact_match, baseline: original, candidate: candidate}",
            "comparison: {baseline: original, candidate: candidate}",
            "comparison.method",
        ),
        ("unit_of_analysis: item", "unit_of_analysis: batch", "要求 unit_of_analysis=item"),
        ("generalizes: false", "generalizes: true", "不能声明 generalizes=true"),
        ("  role: calibration\n", "", "必须显式声明 role 和 generalizes"),
    ],
)
def test_v2_comparison_semantics_fail_closed(
    tmp_path: Path, old: str, new: str, message: str
) -> None:
    root, case_dir = _case_root(tmp_path)
    _write_protocol(root)
    _write_v2_case(case_dir)
    manifest = case_dir / "case.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(old, new),
        encoding="utf-8",
    )

    with pytest.raises(CaseError, match=message):
        load_case(case_dir)


def test_v2_role_is_open_metadata(tmp_path: Path) -> None:
    root, case_dir = _case_root(tmp_path)
    _write_protocol(root)
    _write_v2_case(case_dir)
    manifest = case_dir / "case.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "role: calibration", "role: safety_regression"
        ),
        encoding="utf-8",
    )

    assert load_case(case_dir).evaluation.role == "safety_regression"


def test_v2_writes_generic_run_context_and_uses_protocol_check(tmp_path: Path) -> None:
    root, case_dir = _case_root(tmp_path)
    _write_protocol(root)
    _write_v2_case(case_dir)
    case = load_case(case_dir)
    seen: list[dict] = []

    def run_fn(cmd, cwd, env):
        workdir = Path(cwd)
        assert (workdir / "run_provider.py").is_file()
        seen.append(json.loads((workdir / "RUN_CONTEXT.json").read_text(encoding="utf-8")))
        return "", "", 0

    result = run_matrix(
        RunConfig(runners=("direct",), variants=("original",), workers=1),
        {"direct": RunnerProfile("direct", "command", template="fake")},
        [case],
        report_root=tmp_path, run_fn=run_fn,
        adapter_factory=lambda profile: _Adapter(),
        run_id="v2-run",
    )
    assert seen[0]["variant"] == {
        "label": "original",
        "parameters": {"renderer": "legacy"},
    }
    check = run_check(case, result.records[0].artifacts_dir)
    assert check.passed is True


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        ("synthetic: true\n", "未知字段: synthetic"),
        ("evaluation:\n  generalization_evidence: false\n", "未知字段: generalization_evidence"),
    ],
)
def test_v2_rejects_unknown_fields(tmp_path: Path, extra: str, message: str) -> None:
    _, case_dir = _case_root(tmp_path)
    (case_dir / "case.yaml").write_text(
        "schema_version: 2\ntask: prompts/a.md\n" + extra,
        encoding="utf-8",
    )
    with pytest.raises(CaseError, match=message):
        load_case(case_dir)


@pytest.mark.parametrize("repeat", [0, -1])
def test_repeat_must_be_positive_in_case_and_run_config(tmp_path: Path, repeat: int) -> None:
    _, case_dir = _case_root(tmp_path)
    (case_dir / "case.yaml").write_text(
        f"schema_version: 2\nrepeat: {repeat}\ntask: prompts/a.md\n",
        encoding="utf-8",
    )
    with pytest.raises(CaseError, match="正整数"):
        load_case(case_dir)
    with pytest.raises(ConfigError, match="正整数"):
        RunConfig(repeat=repeat)

    config = tmp_path / "config.yaml"
    config.write_text(f"repeat: {repeat}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="正整数"):
        load_config(config)
