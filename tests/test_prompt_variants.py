from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from bench.adapters.base import Adapter, ParsedOutput
from bench.case import CaseError, isolated_workdir, load_case
from bench.config import RunConfig
from bench.layout import LayoutError, RunLayout
from bench.orchestrator import OrchestratorError, plan_matrix, run_matrix
from bench.registry import RunnerProfile


class _Adapter(Adapter):
    launcher_type = "command"

    def build_command(self, profile, prompt, workdir):
        return ["fake", prompt]

    def parse(self, stdout, stderr, exit_code):
        return ParsedOutput(is_error=exit_code not in (0, None))


def _variant_case(tmp_path: Path) -> Path:
    case_dir = tmp_path / "variants"
    (case_dir / "prompts").mkdir(parents=True)
    (case_dir / "input").mkdir()
    (case_dir / "prompts" / "a.md").write_text("prompt A", encoding="utf-8")
    (case_dir / "prompts" / "b.md").write_text("prompt B", encoding="utf-8")
    (case_dir / "input" / "data.json").write_text("{}", encoding="utf-8")
    (case_dir / "case.yaml").write_text(
        textwrap.dedent(
            """
            name: variants
            requires_engine: command
            task:
              type: custom
              variants:
                original:
                  prompt_file: prompts/a.md
                candidate:
                  prompt_file: prompts/b.md
              default_variant: candidate
            """
        ),
        encoding="utf-8",
    )
    return case_dir


def test_variant_case_loads_and_legacy_maps_to_default(tmp_path: Path) -> None:
    case = load_case(_variant_case(tmp_path))
    assert case.task.variant_labels == ("original", "candidate")
    assert case.task.default_variant == "candidate"
    assert case.task.prompt == "prompt B"
    assert case.task.prompt_for("original") == "prompt A"

    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "p.md").write_text("legacy prompt", encoding="utf-8")
    (legacy / "case.yaml").write_text(
        "name: legacy\ntask:\n  type: prompt\n  prompt_file: p.md\n",
        encoding="utf-8",
    )
    loaded = load_case(legacy)
    assert loaded.task.variant_labels == ("default",)
    assert loaded.task.prompt_for("default") == "legacy prompt"


def test_variant_schema_rejects_ambiguous_prompt(tmp_path: Path) -> None:
    case_dir = _variant_case(tmp_path)
    manifest = (case_dir / "case.yaml").read_text(encoding="utf-8")
    manifest = manifest.replace("variants:\n", "prompt_file: prompts/a.md\n  variants:\n")
    (case_dir / "case.yaml").write_text(manifest, encoding="utf-8")
    with pytest.raises(CaseError, match="不能同时配置"):
        load_case(case_dir)


def test_variant_matrix_isolated_and_ab_ba(tmp_path: Path) -> None:
    case = load_case(_variant_case(tmp_path))
    registry = {"direct": RunnerProfile("direct", "command", template="fake")}
    seen: list[str] = []

    def run_fn(cmd, cwd, env):
        seen.append(cmd[1])
        return "", "", 0

    result = run_matrix(
        RunConfig(
            runners=("direct",),
            variants=("original", "candidate"),
            repeat=2,
            workers=1,
        ),
        registry,
        [case],
        report_root=tmp_path,
        run_fn=run_fn,
        adapter_factory=lambda profile: _Adapter(),
        run_id="variant-run",
    )
    assert len(result.records) == 4
    assert result.schedule[1]["variants"] == list(reversed(result.schedule[0]["variants"]))
    assert {record.variant_label for record in result.records} == {"original", "candidate"}
    assert set(seen) == {"prompt A", "prompt B"}
    layout = RunLayout(tmp_path, "variant-run")
    for record in result.records:
        cell = layout.cell_dir(
            case.name, record.variant_label, record.runner_label, record.repeat_index
        )
        assert (cell / "run.json").is_file()
        assert (cell / "artifacts" / "PROMPT.txt").read_text(encoding="utf-8") in {
            "prompt A",
            "prompt B",
        }
        assert record.prompt_template_sha256
        assert record.input_manifest_sha256


def test_unknown_variant_fails_before_execution(tmp_path: Path) -> None:
    case = load_case(_variant_case(tmp_path))
    registry = {"direct": RunnerProfile("direct", "command", template="fake")}
    with pytest.raises(OrchestratorError, match="不存在 prompt variant"):
        run_matrix(
            RunConfig(runners=("direct",), variants=("missing",)),
            registry,
            [case],
            report_root=tmp_path,
            adapter_factory=lambda profile: _Adapter(),
        )


def test_plan_validates_variants_before_launcher_skip(tmp_path: Path) -> None:
    case = load_case(_variant_case(tmp_path))
    registry = {"agent": RunnerProfile("agent", "claude")}
    with pytest.raises(OrchestratorError, match="不存在 prompt variant"):
        plan_matrix(
            RunConfig(runners=("agent",), variants=("missing",)),
            registry,
            [case],
        )

    with pytest.raises(
        OrchestratorError,
        match="没有可执行组合.*requires_engine=command",
    ):
        plan_matrix(
            RunConfig(runners=("agent",), variants=("original", "candidate")),
            registry,
            [case],
        )


@pytest.mark.parametrize(
    ("run_id", "case_name", "variant_label", "runner_label"),
    [
        ("../run", "variants", "original", "direct"),
        ("run", "../case", "original", "direct"),
        ("run", "variants", "../variant", "direct"),
        ("run", "variants", "original", "../runner"),
    ],
)
def test_cell_dir_rejects_unsafe_path_labels(
    tmp_path: Path, run_id: str, case_name: str, variant_label: str, runner_label: str
) -> None:
    with pytest.raises(LayoutError, match="不能用于产物路径"):
        RunLayout(tmp_path, run_id).cell_dir(case_name, variant_label, runner_label, 0)


def test_runner_symlink_is_cell_error_and_ignored_tool_symlink_is_skipped(
    tmp_path: Path,
) -> None:
    case = load_case(_variant_case(tmp_path))
    registry = {"direct": RunnerProfile("direct", "command", template="fake")}

    def run_fn(cmd, cwd, env):
        workdir = Path(cwd)
        (workdir / "node_modules").mkdir()
        (workdir / "node_modules" / "ignored-link").symlink_to(workdir / "missing")
        (workdir / "unsafe-link").symlink_to(workdir / "missing")
        return "", "", 0

    result = run_matrix(
        RunConfig(runners=("direct",), variants=("original",), workers=1),
        registry,
        [case],
        report_root=tmp_path,
        run_fn=run_fn,
        adapter_factory=lambda profile: _Adapter(),
        run_id="symlink-run",
    )
    assert len(result.records) == 1
    assert result.records[0].is_error is True
    assert "符号链接" in (
        RunLayout(tmp_path, "symlink-run").cell_dir(case.name, "original", "direct", 0)
        / "raw.txt"
    ).read_text(encoding="utf-8")


def test_protected_file_in_input_and_input_symlink_are_rejected(tmp_path: Path) -> None:
    case_dir = tmp_path / "protected"
    (case_dir / "input").mkdir(parents=True)
    (case_dir / "input" / "labels.jsonl").write_text("{}", encoding="utf-8")
    (case_dir / "p.md").write_text("p", encoding="utf-8")
    (case_dir / "case.yaml").write_text(
        textwrap.dedent(
            """
            name: protected
            task:
              type: prompt
              prompt_file: p.md
            check:
              protected_files: [input/labels.jsonl]
            """
        ),
        encoding="utf-8",
    )
    with pytest.raises(CaseError, match="不能位于 input"):
        load_case(case_dir)

    (case_dir / "case.yaml").write_text(
        "name: protected\ntask:\n  type: prompt\n  prompt_file: p.md\n",
        encoding="utf-8",
    )
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    (case_dir / "input" / "leak.txt").symlink_to(outside)
    case = load_case(case_dir)
    with pytest.raises(CaseError, match="符号链接"):
        with isolated_workdir(case):
            pass


def test_check_script_and_rubric_reject_path_traversal(tmp_path: Path) -> None:
    outside = tmp_path / "outside.sh"
    outside.write_text("#!/bin/sh\n", encoding="utf-8")
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "prompt.md").write_text("prompt", encoding="utf-8")
    (case_dir / "case.yaml").write_text(
        textwrap.dedent(
            """
            name: unsafe-script
            task:
              type: prompt
              prompt_file: prompt.md
            check:
              type: script
              script: ../outside.sh
            """
        ),
        encoding="utf-8",
    )
    with pytest.raises(CaseError, match="check.script 必须是安全的相对路径"):
        load_case(case_dir)

    (case_dir / "case.yaml").write_text(
        textwrap.dedent(
            """
            name: unsafe-rubric
            task:
              type: prompt
              prompt_file: prompt.md
            judge:
              enabled: true
              rubric_file: ../rubric.md
            """
        ),
        encoding="utf-8",
    )
    with pytest.raises(CaseError, match="judge.rubric_file 必须是安全的相对路径"):
        load_case(case_dir)


def test_required_request_manifest_missing_marks_cell_error(tmp_path: Path) -> None:
    case_dir = _variant_case(tmp_path)
    manifest = (case_dir / "case.yaml").read_text(encoding="utf-8")
    manifest += textwrap.dedent(
        """
        run_contract:
          request_manifest_file: request_manifest.json
          request_manifest_required: true
        """
    )
    (case_dir / "case.yaml").write_text(manifest, encoding="utf-8")
    case = load_case(case_dir)
    registry = {"direct": RunnerProfile("direct", "command", template="fake")}
    result = run_matrix(
        RunConfig(runners=("direct",)),
        registry,
        [case],
        report_root=tmp_path,
        run_fn=lambda cmd, cwd, env: (json.dumps({}), "", 0),
        adapter_factory=lambda profile: _Adapter(),
        run_id="missing-manifest",
    )
    assert result.records[0].is_error is True
    raw = Path(result.records[0].artifacts_dir).parent / "raw.txt"
    assert "request manifest 缺失" in raw.read_text(encoding="utf-8")
