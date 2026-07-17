from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from bench.case import (
    Case,
    CheckSpec,
    EvaluationPolicy,
    PromptVariant,
    Task,
    VariantComparisonSpec,
)
from bench.config import RunConfig
from bench.orchestrator import MatrixResult, OrchestratorError, plan_matrix
from bench.record import CheckResult, RunRecord
from bench.scorecard import build_scorecard
from bench.registry import RunnerProfile


def _case(tmp_path: Path) -> Case:
    return Case(
        name="variant-case",
        directory=tmp_path,
        task=Task(
            type="custom",
            prompt="candidate",
            variants=(
                PromptVariant("original", "original"),
                PromptVariant("v4", "candidate"),
            ),
            default_variant="v4",
        ),
        check=CheckSpec(type="script", script="check.sh", report_file="evaluation.json"),
        expected={
            "comparison": {
                "baseline_variant": "original",
                "candidate_variant": "v4",
            }
        },
        evaluation={
            "role": "calibration",
            "scope": "dev_calibration_regression",
            "generalization_evidence": False,
            "unit_of_analysis": "item",
            "independent_unit": "item",
        },
    )


def _record(case: Case, variant: str, repeat: int, correct: bool) -> RunRecord:
    actual = "false" if correct else "true"
    report = {
        "schema_version": 1,
        "passed": correct,
        "summary": {},
        "items": [
            {
                "id": "1",
                "expected": "false",
                "actual": actual,
                "correct": correct,
            }
        ],
        "errors": [],
    }
    return RunRecord(
        case=case.name,
        runner_label="r",
        launcher_type="command",
        variant_label=variant,
        repeat_index=repeat,
        check=CheckResult(ran=True, passed=correct, report=report),
    )


def test_scorecard_keeps_variants_separate_and_reports_fixed(tmp_path: Path) -> None:
    case = _case(tmp_path)
    records = [
        _record(case, "original", 0, False),
        _record(case, "original", 1, False),
        _record(case, "v4", 0, True),
        _record(case, "v4", 1, True),
    ]
    markdown = build_scorecard(
        MatrixResult(records=records, run_id="run-1"), cases={case.name: case}
    )
    assert "`original`" in markdown and "`v4`" in markdown
    assert "strict fixed=1，regressed=0" in markdown
    assert "| `1` | 0/2 | 2/2 |" in markdown
    assert "本用例不是泛化证据" in markdown
    assert "**评测角色**：`calibration`" in markdown
    assert "结果仅作描述性比较" in markdown
    assert "**每维赢家**" not in markdown


def test_scorecard_rejects_unpaired_repeats(tmp_path: Path) -> None:
    case = _case(tmp_path)
    records = [
        _record(case, "original", 0, False),
        _record(case, "v4", 1, True),
    ]
    with pytest.raises(ValueError, match="repeat 不完整"):
        build_scorecard(MatrixResult(records=records), cases={case.name: case})


def test_comparison_entry_rejects_programmatic_case_with_wrong_unit(
    tmp_path: Path,
) -> None:
    case = _case(tmp_path)
    unsafe = replace(
        case,
        evaluation=EvaluationPolicy(
            role="calibration",
            generalizes=False,
            comparison=VariantComparisonSpec("original", "v4"),
        ),
    )
    records = [
        _record(unsafe, "original", 0, False),
        _record(unsafe, "v4", 0, True),
    ]

    with pytest.raises(ValueError, match="unit_of_analysis=item"):
        build_scorecard(MatrixResult(records=records), cases={unsafe.name: unsafe})


def test_run_plan_rejects_comparison_without_structured_check(tmp_path: Path) -> None:
    case = _case(tmp_path)
    unsafe = replace(
        case,
        check=CheckSpec(type="script", script="check.sh"),
    )

    with pytest.raises(OrchestratorError, match="要求结构化 script check"):
        plan_matrix(
            RunConfig(runners=("r",)),
            {"r": RunnerProfile("r", "command", template="fake")},
            [unsafe],
        )


def test_comparison_rejects_empty_items_and_duplicate_repeats(tmp_path: Path) -> None:
    case = _case(tmp_path)
    original = _record(case, "original", 0, False)
    candidate = _record(case, "v4", 0, True)
    empty_original = replace(
        original,
        check=replace(original.check, report={**original.check.report, "items": []}),
    )
    empty_candidate = replace(
        candidate,
        check=replace(candidate.check, report={**candidate.check.report, "items": []}),
    )
    with pytest.raises(ValueError, match="没有 item 证据"):
        build_scorecard(
            MatrixResult(records=[empty_original, empty_candidate]),
            cases={case.name: case},
        )

    with pytest.raises(ValueError, match="repeat_index 重复"):
        build_scorecard(
            MatrixResult(records=[original, original, candidate, candidate]),
            cases={case.name: case},
        )


def test_scorecard_rejects_missing_paired_report(tmp_path: Path) -> None:
    case = _case(tmp_path)
    original = _record(case, "original", 0, False)
    candidate = _record(case, "v4", 0, True)
    candidate = RunRecord(
        case=candidate.case,
        runner_label=candidate.runner_label,
        launcher_type=candidate.launcher_type,
        variant_label=candidate.variant_label,
        repeat_index=candidate.repeat_index,
        check=CheckResult(ran=True, passed=True),
    )
    with pytest.raises(ValueError, match="缺少结构化 check report"):
        build_scorecard(
            MatrixResult(records=[original, candidate]), cases={case.name: case}
        )


def test_comparison_unavailable_when_one_variant_runs_fail(tmp_path: Path) -> None:
    """一侧 variant 运行失败（如产物不是严格 JSON）→ 配对比较显式不可用，不否决整个 run。"""
    case = _case(tmp_path)
    failed_original = replace(
        _record(case, "original", 0, False),
        is_error=True,
        check=CheckResult(ran=True, passed=False, detail="parse_ok=false"),
    )
    markdown = build_scorecard(
        MatrixResult(records=[failed_original, _record(case, "v4", 0, True)]),
        cases={case.name: case},
    )
    assert "paired comparison unavailable" in markdown
    assert "original 有 1/1 次运行失败" in markdown


def test_comparison_unavailable_when_semantic_projection_is_incomplete(
    tmp_path: Path,
) -> None:
    case = _case(tmp_path)
    original = _record(case, "original", 0, False)
    incomplete_report = {
        **original.check.report,
        "items": [
            {
                "id": "1",
                "expected": "false",
                "actual": "unavailable",
                "correct": False,
                "evaluated": False,
            }
        ],
    }
    original = replace(
        original,
        check=replace(original.check, report=incomplete_report),
    )

    markdown = build_scorecard(
        MatrixResult(records=[original, _record(case, "v4", 0, True)]),
        cases={case.name: case},
    )

    assert "paired comparison unavailable" in markdown
    assert "语义投影未覆盖" in markdown


def test_scorecard_marks_single_variant_comparison_unavailable(tmp_path: Path) -> None:
    case = _case(tmp_path)
    markdown = build_scorecard(
        MatrixResult(records=[_record(case, "v4", 0, True)]),
        cases={case.name: case},
    )
    assert "paired comparison unavailable" in markdown


def test_value_diff_comparison_does_not_claim_correctness(tmp_path: Path) -> None:
    case = _case(tmp_path)
    review_case = replace(
        case,
        evaluation=EvaluationPolicy(
            role="human_review",
            generalizes=False,
            comparison=VariantComparisonSpec(
                "original", "v4", method="item_value_diff"
            ),
            unit_of_analysis="item",
            independent_unit="batch",
        ),
    )

    markdown = build_scorecard(
        MatrixResult(
            records=[
                _record(review_case, "original", 0, False),
                _record(review_case, "v4", 0, True),
            ]
        ),
        cases={review_case.name: review_case},
    )

    assert "分歧=1/1" in markdown
    assert "数据库 baseline 快照仅作分层与参照，不是真值" in markdown
    assert "fixed=" not in markdown and "regressed=" not in markdown


def test_value_diff_pairs_values_by_repeat_index_not_record_order(tmp_path: Path) -> None:
    case = _case(tmp_path)
    review_case = replace(
        case,
        evaluation=EvaluationPolicy(
            role="human_review",
            generalizes=False,
            comparison=VariantComparisonSpec(
                "original", "v4", method="item_value_diff"
            ),
            unit_of_analysis="item",
            independent_unit="batch",
        ),
    )
    records = [
        _record(review_case, "original", 0, True),
        _record(review_case, "original", 1, False),
        _record(review_case, "v4", 1, False),
        _record(review_case, "v4", 0, True),
    ]

    markdown = build_scorecard(
        MatrixResult(records=records), cases={review_case.name: review_case}
    )

    assert "分歧=0/1" in markdown


def test_value_diff_keeps_comparable_items_when_one_item_is_unavailable(
    tmp_path: Path,
) -> None:
    case = _case(tmp_path)
    review_case = replace(
        case,
        evaluation=EvaluationPolicy(
            role="human_review",
            generalizes=False,
            comparison=VariantComparisonSpec(
                "original", "v4", method="item_value_diff"
            ),
            unit_of_analysis="item",
            independent_unit="batch",
        ),
    )
    original = _record(review_case, "original", 0, True)
    candidate = _record(review_case, "v4", 0, False)
    original_items = [
        {"id": "1", "reference": "false", "actual": "false", "correct": None, "evaluated": True},
        {"id": "2", "reference": "true", "actual": "true", "correct": None, "evaluated": True},
    ]
    candidate_items = [
        {"id": "1", "reference": "false", "actual": "true", "correct": None, "evaluated": True},
        {"id": "2", "reference": "true", "actual": "unavailable", "correct": None, "evaluated": False},
    ]
    original = replace(
        original, check=replace(original.check, report={**original.check.report, "items": original_items})
    )
    candidate = replace(
        candidate, check=replace(candidate.check, report={**candidate.check.report, "items": candidate_items})
    )

    markdown = build_scorecard(
        MatrixResult(records=[original, candidate]), cases={review_case.name: review_case}
    )

    assert "分歧=1/1；不可比较=1" in markdown


def test_value_diff_uses_evaluated_flag_and_scrubs_markdown_cells(
    tmp_path: Path,
) -> None:
    case = _case(tmp_path)
    review_case = replace(
        case,
        evaluation=EvaluationPolicy(
            role="human_review",
            generalizes=False,
            comparison=VariantComparisonSpec(
                "original", "v4", method="item_value_diff"
            ),
            unit_of_analysis="item",
            independent_unit="batch",
        ),
    )
    original = _record(review_case, "original", 0, True)
    candidate = _record(review_case, "v4", 0, False)
    original_items = [
        {
            "id": "1",
            "reference": "false",
            "actual": "false",
            "correct": None,
            "evaluated": True,
        },
        {
            "id": "2",
            "reference": "api_key=super-secret-value|line\nnext",
            "actual": "unknown",
            "correct": None,
            "evaluated": False,
        },
    ]
    candidate_items = [
        {
            "id": "1",
            "reference": "false",
            "actual": "</code><img src=x onerror=alert(1)>|true\nnext",
            "correct": None,
            "evaluated": True,
        },
        {
            "id": "2",
            "reference": "api_key=super-secret-value|line\nnext",
            "actual": "different",
            "correct": None,
            "evaluated": False,
        },
    ]
    original = replace(
        original,
        check=replace(original.check, report={**original.check.report, "items": original_items}),
    )
    candidate = replace(
        candidate,
        check=replace(candidate.check, report={**candidate.check.report, "items": candidate_items}),
    )

    markdown = build_scorecard(
        MatrixResult(records=[original, candidate]), cases={review_case.name: review_case}
    )

    assert "分歧=1/1；不可比较=1" in markdown
    assert "super-secret-value" not in markdown
    assert "<img" not in markdown
    assert "Baseline 快照" not in markdown
    assert "逐条业务值仅在" in markdown
