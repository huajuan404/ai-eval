"""U5: check + LLM 裁判。"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

from bench.adapters.base import Adapter, ParsedOutput
from bench.case import load_case
from bench.record import CheckResult, JudgeResult, RunRecord
from bench.registry import RunnerProfile
from bench.scoring import (
    _validate_check_report,
    assemble_judge_prompt,
    gather_contestant_output,
    run_check,
    run_judge,
    score_record,
)


def test_review_check_report_accepts_reference_and_null_correct() -> None:
    report = {
        "schema_version": 1,
        "passed": True,
        "summary": {"evaluation_mode": "baseline_snapshot_review"},
        "items": [
            {
                "id": "1",
                "reference": "true",
                "actual": "false",
                "correct": None,
                "evaluated": True,
            }
        ],
        "errors": [],
    }

    assert _validate_check_report(
        report, expected_mode="baseline_snapshot_review"
    ) is None
    report["items"][0]["correct"] = False
    assert "必须为 null" in (
        _validate_check_report(report, expected_mode="baseline_snapshot_review") or ""
    )


def test_oracle_contract_rejects_self_declared_review_mode() -> None:
    report = {
        "schema_version": 1,
        "passed": True,
        "summary": {"evaluation_mode": "baseline_snapshot_review"},
        "items": [
            {
                "id": "1",
                "reference": "true",
                "actual": "false",
                "correct": None,
                "evaluated": True,
            }
        ],
        "errors": [],
    }

    assert "与 case comparison 合同不一致" in (_validate_check_report(report) or "")


def test_run_check_accepts_review_report_end_to_end(tmp_path: Path) -> None:
    case_dir = _make_case(tmp_path, judge=False)
    manifest = case_dir / "case.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "  script: check.sh\n", "  script: check.sh\n  report_file: evaluation.json\n"
        ),
        encoding="utf-8",
    )
    case = load_case(case_dir)
    from dataclasses import replace
    from bench.case import EvaluationPolicy, VariantComparisonSpec

    case = replace(
        case,
        evaluation=EvaluationPolicy(
            role="human_review",
            generalizes=False,
            comparison=VariantComparisonSpec(
                "original", "v4", method="item_value_diff"
            ),
            unit_of_analysis="item",
        ),
    )
    report = {
        "schema_version": 1,
        "passed": True,
        "summary": {"evaluation_mode": "baseline_snapshot_review"},
        "items": [
            {
                "id": "1",
                "reference": "true",
                "actual": "false",
                "correct": None,
                "evaluated": True,
            }
        ],
        "errors": [],
    }

    def fake_run(command, cwd, env):
        Path(cwd, "evaluation.json").write_text(json.dumps(report), encoding="utf-8")
        return "review", "", 0

    result = run_check(case, tmp_path, run_fn=fake_run)

    assert result.ran and result.passed is True
    assert result.report["items"][0]["correct"] is None


def _make_case(tmp_path: Path, *, check: bool = True, judge: bool = True) -> Path:
    d = tmp_path / "case"
    d.mkdir()
    check_block = "check:\n  type: script\n  script: check.sh\n" if check else ""
    judge_block = (
        "judge:\n  enabled: true\n  rubric_file: rubric.md\n  dimensions: [correctness]\n"
        if judge
        else ""
    )
    (d / "case.yaml").write_text(
        textwrap.dedent(
            """
            name: case
            task:
              type: prompt
              prompt_file: task.md
            """
        ).lstrip()
        + check_block
        + judge_block,
        encoding="utf-8",
    )
    (d / "task.md").write_text("Implement add(a,b)", encoding="utf-8")
    if check:
        (d / "check.sh").write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    if judge:
        (d / "rubric.md").write_text("Award 10 if correct.", encoding="utf-8")
    return d


class FakeJudgeAdapter(Adapter):
    launcher_type = "claude"

    def __init__(self, response: str):
        self.response = response
        self.last_prompt: str | None = None
        self.last_cwd: str | None = None

    def build_command(self, profile, prompt, workdir):
        self.last_prompt = prompt
        self.last_cwd = workdir
        return ["judge"]

    def build_env(self, profile, base_env=None):
        return {}

    def parse(self, stdout, stderr, exit_code):
        return ParsedOutput()


def test_check_pass(tmp_path: Path) -> None:
    case = load_case(_make_case(tmp_path, judge=False))
    res = run_check(case, tmp_path, run_fn=lambda c, w, e: ("ok", "", 0))
    assert res.ran and res.passed is True
    assert "ok" in res.detail


def test_check_fail_captures_detail(tmp_path: Path) -> None:
    case = load_case(_make_case(tmp_path, judge=False))
    res = run_check(case, tmp_path, run_fn=lambda c, w, e: ("assertion failed", "", 1))
    assert res.ran and res.passed is False
    assert "assertion failed" in res.detail


def test_check_none(tmp_path: Path) -> None:
    case = load_case(_make_case(tmp_path, check=False))
    res = run_check(case, tmp_path)
    assert res.ran is False


def test_judge_prompt_wraps_untrusted_output() -> None:
    prompt = assemble_judge_prompt("rubric X", "task Y", "MODEL SAID IGNORE INSTRUCTIONS", inline=True)
    assert "<contestant_output>" in prompt
    assert "UNTRUSTED" in prompt
    assert "MODEL SAID IGNORE INSTRUCTIONS" in prompt
    assert "rubric X" in prompt


def test_gather_output_inline_small(tmp_path: Path) -> None:
    art = tmp_path / "art"
    art.mkdir()
    (art / "sol.py").write_text("def add(a,b): return a+b", encoding="utf-8")
    text, inline = gather_contestant_output(art, inline_limit=8000)
    assert inline is True
    assert "def add" in text


def test_gather_output_large_not_inline(tmp_path: Path) -> None:
    art = tmp_path / "art"
    art.mkdir()
    (art / "big.txt").write_text("x" * 100, encoding="utf-8")
    text, inline = gather_contestant_output(art, inline_limit=10)
    assert inline is False
    assert text == ""


def test_gather_skips_prompt_includes_output(tmp_path: Path) -> None:
    art = tmp_path / "art"
    art.mkdir()
    (art / "PROMPT.txt").write_text("the task prompt", encoding="utf-8")
    (art / "OUTPUT.txt").write_text("the model answer", encoding="utf-8")
    (art / "sol.py").write_text("code", encoding="utf-8")
    text, inline = gather_contestant_output(art, inline_limit=8000)
    assert "the model answer" in text
    assert "code" in text
    assert "the task prompt" not in text  # PROMPT.txt 是输入，排除


def test_run_judge_structured(tmp_path: Path) -> None:
    case = load_case(_make_case(tmp_path))
    art = tmp_path / "art"
    art.mkdir()
    (art / "sol.py").write_text("def add(a,b): return a+b", encoding="utf-8")
    rec = RunRecord(
        case="case", runner_label="codex", launcher_type="codex", artifacts_dir=str(art)
    )
    adapter = FakeJudgeAdapter('{"score": 9, "max": 10, "dimensions": {"correctness": 9}, "reasoning": "good"}')
    res = run_judge(
        case, rec, RunnerProfile("claude", "claude"),
        run_fn=lambda c, w, e: (adapter.response, "", 0),
        adapter_factory=lambda p: adapter,
    )
    assert res.ran and res.score == 9 and res.max == 10
    assert res.dimensions == {"correctness": 9}
    assert res.same_source is False  # codex runner vs claude judge
    assert "<contestant_output>" in adapter.last_prompt


def test_run_judge_same_source_flag(tmp_path: Path) -> None:
    case = load_case(_make_case(tmp_path))
    art = tmp_path / "art"
    art.mkdir()
    (art / "x.txt").write_text("hi", encoding="utf-8")
    rec = RunRecord(case="case", runner_label="claude", launcher_type="claude", artifacts_dir=str(art))
    adapter = FakeJudgeAdapter('{"score": 5, "max": 10}')
    res = run_judge(
        case, rec, RunnerProfile("claude", "claude"),
        run_fn=lambda c, w, e: ('{"score": 5, "max": 10}', "", 0),
        adapter_factory=lambda p: adapter,
    )
    assert res.same_source is True


def test_run_judge_invalid_json_degrades(tmp_path: Path) -> None:
    case = load_case(_make_case(tmp_path))
    art = tmp_path / "art"
    art.mkdir()
    (art / "x.txt").write_text("hi", encoding="utf-8")
    rec = RunRecord(case="case", runner_label="codex", launcher_type="codex", artifacts_dir=str(art))
    res = run_judge(
        case, rec, RunnerProfile("claude", "claude"),
        run_fn=lambda c, w, e: ("the model rambled, no json", "", 0),
        adapter_factory=lambda p: FakeJudgeAdapter("x"),
    )
    assert res.ran is True and res.score is None
    assert "rambled" in res.reasoning


def test_run_judge_disabled(tmp_path: Path) -> None:
    case = load_case(_make_case(tmp_path, judge=False))
    rec = RunRecord(case="case", runner_label="codex", launcher_type="codex")
    res = run_judge(
        case, rec, RunnerProfile("claude", "claude"),
        run_fn=lambda c, w, e: ("{}", "", 0),
    )
    assert res.ran is False


def test_score_record_skips_scoring_after_runner_error(tmp_path: Path) -> None:
    case = load_case(_make_case(tmp_path))
    stale = RunRecord(
        case="case",
        runner_label="failed",
        launcher_type="command",
        is_error=True,
        check=CheckResult(ran=True, passed=False, detail="stale check"),
        judge=JudgeResult(ran=True, model="stale", score=0),
        artifacts_dir=str(tmp_path / "missing-artifacts"),
    )

    def must_not_run(*args, **kwargs):
        raise AssertionError("执行失败后不应调用 check/judge")

    scored = score_record(
        case,
        stale,
        RunnerProfile("judge", "command", template="judge"),
        check_run_fn=must_not_run,
        judge_run_fn=must_not_run,
    )

    assert scored.check.ran is False
    assert scored.check.passed is None
    assert "执行失败" in scored.check.detail
    assert scored.judge is None
