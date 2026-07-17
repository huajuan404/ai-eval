"""Pure variant comparison calculation, independent from scorecard rendering."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .case import Case
from .record import RunRecord


class ComparisonError(ValueError):
    """Paired comparison inputs are incomplete or inconsistent."""


@dataclass(frozen=True)
class ItemComparison:
    item_id: str
    baseline_correct: int
    candidate_correct: int
    baseline_disagreement: float
    candidate_disagreement: float
    status: str
    baseline_value: str = ""
    candidate_value: str = ""
    reference_value: str = ""


@dataclass(frozen=True)
class RunnerComparison:
    runner: str
    baseline: str
    candidate: str
    method: str = "item_exact_match"
    unit_of_analysis: str = "item"
    independent_unit: str = "run"
    repeats: int = 0
    fixed: int = 0
    regressed: int = 0
    changed: int = 0
    unavailable_items: int = 0
    rows: tuple[ItemComparison, ...] = ()
    unavailable_reason: str | None = None


def validate_comparison_contract(case: Case) -> None:
    """Validate every requirement of the currently supported comparator."""
    spec = case.evaluation.comparison
    if spec is None:
        return
    if spec.method not in {"item_exact_match", "item_value_diff"}:
        raise ComparisonError(f"不支持的 variant comparison method: {spec.method}")
    if case.check.type != "script" or not case.check.report_file:
        raise ComparisonError(
            f"item comparison 要求结构化 script check: case={case.name}"
        )
    if case.evaluation.role is None or case.evaluation.generalizes is None:
        raise ComparisonError(
            f"variant comparison 必须显式声明 role/generalizes: case={case.name}"
        )
    if case.evaluation.unit_of_analysis != "item":
        raise ComparisonError(
            f"item comparison 要求 unit_of_analysis=item: case={case.name}"
        )
    if (
        case.evaluation.role in {"calibration", "synthetic_diagnostic"}
        and case.evaluation.generalizes
    ):
        raise ComparisonError(
            f"role={case.evaluation.role} 不能声明 generalizes=true: case={case.name}"
        )


def compare_case_variants(
    case: Case,
    case_records: dict[tuple[str, str], list[RunRecord]],
) -> list[RunnerComparison]:
    spec = case.evaluation.comparison
    if spec is None:
        return []
    validate_comparison_contract(case)

    results: list[RunnerComparison] = []
    runners = sorted({runner for _, runner in case_records})
    for runner in runners:
        baseline_records = sorted(
            case_records.get((spec.baseline, runner), []),
            key=lambda record: record.repeat_index,
        )
        candidate_records = sorted(
            case_records.get((spec.candidate, runner), []),
            key=lambda record: record.repeat_index,
        )
        if not baseline_records or not candidate_records:
            results.append(
                RunnerComparison(
                    runner=runner,
                    baseline=spec.baseline,
                    candidate=spec.candidate,
                    method=spec.method,
                    unit_of_analysis=case.evaluation.unit_of_analysis,
                    independent_unit=case.evaluation.independent_unit,
                    unavailable_reason=(
                        f"需同时运行 {spec.baseline} 与 {spec.candidate}"
                    ),
                )
            )
            continue
        # 运行失败的 repeat 天然没有完整 item 证据；失败已如实记录在对应 cell
        # （is_error + check + 完成度 ❌），配对比较对该 runner 显式标注不可用，
        # 而不是让整个 run 的报告消失。健康 record 缺证据仍按合同硬报错（见下）。
        error_notes = [
            f"{label} 有 {failed}/{len(records)} 次运行失败"
            for label, records in (
                (spec.baseline, baseline_records),
                (spec.candidate, candidate_records),
            )
            if (failed := sum(1 for record in records if record.is_error))
        ]
        if error_notes:
            results.append(
                RunnerComparison(
                    runner=runner,
                    baseline=spec.baseline,
                    candidate=spec.candidate,
                    method=spec.method,
                    unit_of_analysis=case.evaluation.unit_of_analysis,
                    independent_unit=case.evaluation.independent_unit,
                    unavailable_reason=(
                        "；".join(error_notes)
                        + "（失败详情见对应 cell；配对比较要求双方全部 repeat 都有 item 证据）"
                    ),
                )
            )
            continue
        all_records = baseline_records + candidate_records
        if not all(record.check.report for record in all_records):
            raise ComparisonError(
                f"paired delta 缺少结构化 check report: case={case.name} runner={runner}"
            )
        unevaluated_notes = []
        for label, records in (
            (spec.baseline, baseline_records),
            (spec.candidate, candidate_records),
        ):
            missing = sum(
                1
                for record in records
                for item in (record.check.report.get("items") or [])
                if item.get("evaluated") is False
            )
            if missing:
                unevaluated_notes.append(f"{label} 语义投影未覆盖 {missing} 个 item/repeat")
        if unevaluated_notes and spec.method != "item_value_diff":
            results.append(
                RunnerComparison(
                    runner=runner,
                    baseline=spec.baseline,
                    candidate=spec.candidate,
                    method=spec.method,
                    unit_of_analysis=case.evaluation.unit_of_analysis,
                    independent_unit=case.evaluation.independent_unit,
                    unavailable_reason="；".join(unevaluated_notes),
                )
            )
            continue
        baseline_repeats = {record.repeat_index for record in baseline_records}
        candidate_repeats = {record.repeat_index for record in candidate_records}
        if len(baseline_repeats) != len(baseline_records) or len(
            candidate_repeats
        ) != len(candidate_records):
            raise ComparisonError(
                f"paired delta repeat_index 重复: case={case.name} runner={runner}"
            )
        if baseline_repeats != candidate_repeats:
            raise ComparisonError(
                f"paired delta repeat 不完整: case={case.name} runner={runner}"
            )

        by_variant: dict[str, dict[str, list[dict]]] = {}
        for label, records in (
            (spec.baseline, baseline_records),
            (spec.candidate, candidate_records),
        ):
            by_item: dict[str, list[dict]] = defaultdict(list)
            for record in records:
                items = record.check.report.get("items") or []
                ids = {str(item["id"]) for item in items}
                if len(ids) != len(items):
                    raise ComparisonError(
                        f"paired delta item id 重复: case={case.name} "
                        f"runner={runner} variant={label}"
                    )
                for item in items:
                    by_item[str(item["id"])].append(item)
            by_variant[label] = by_item

        baseline_ids = set(by_variant[spec.baseline])
        candidate_ids = set(by_variant[spec.candidate])
        if not baseline_ids or not candidate_ids:
            raise ComparisonError(
                f"paired delta 没有 item 证据: case={case.name} runner={runner}"
            )
        if baseline_ids != candidate_ids:
            raise ComparisonError(
                f"paired delta item id 不一致: case={case.name} runner={runner}"
            )

        rows: list[ItemComparison] = []
        fixed = 0
        regressed = 0
        changed = 0
        unavailable_items = 0
        repeats = len(baseline_records)
        for item_id in sorted(baseline_ids):
            baseline_items = by_variant[spec.baseline][item_id]
            candidate_items = by_variant[spec.candidate][item_id]
            if len(baseline_items) != repeats or len(candidate_items) != repeats:
                raise ComparisonError(
                    f"paired delta item repeat 缺失: case={case.name} "
                    f"runner={runner} id={item_id}"
                )
            baseline_values = [str(item["actual"]) for item in baseline_items]
            candidate_values = [str(item["actual"]) for item in candidate_items]
            baseline_disagreement = 1 - max(
                baseline_values.count(value) for value in set(baseline_values)
            ) / repeats
            candidate_disagreement = 1 - max(
                candidate_values.count(value) for value in set(candidate_values)
            ) / repeats
            baseline_value = baseline_values[0] if len(set(baseline_values)) == 1 else "mixed"
            candidate_value = candidate_values[0] if len(set(candidate_values)) == 1 else "mixed"
            references = {
                str(item.get("reference", item.get("expected", "")))
                for item in baseline_items + candidate_items
                if item.get("reference", item.get("expected")) is not None
            }
            reference_value = next(iter(references)) if len(references) == 1 else ""
            if spec.method == "item_value_diff":
                baseline_correct = candidate_correct = 0
                if any(
                    item.get("evaluated") is False
                    for item in baseline_items + candidate_items
                ):
                    status = "unavailable"
                    unavailable_items += 1
                else:
                    status = "changed" if baseline_values != candidate_values else "same"
                    changed += int(status == "changed")
            else:
                baseline_correct = sum(1 for item in baseline_items if item["correct"])
                candidate_correct = sum(1 for item in candidate_items if item["correct"])
                status = "unchanged"
                if baseline_correct != repeats and candidate_correct == repeats:
                    status = "fixed"
                    fixed += 1
                elif baseline_correct == repeats and candidate_correct != repeats:
                    status = "regressed"
                    regressed += 1
            rows.append(
                ItemComparison(
                    item_id=item_id,
                    baseline_correct=baseline_correct,
                    candidate_correct=candidate_correct,
                    baseline_disagreement=baseline_disagreement,
                    candidate_disagreement=candidate_disagreement,
                    status=status,
                    baseline_value=baseline_value,
                    candidate_value=candidate_value,
                    reference_value=reference_value,
                )
            )
        results.append(
            RunnerComparison(
                runner=runner,
                baseline=spec.baseline,
                candidate=spec.candidate,
                method=spec.method,
                unit_of_analysis=case.evaluation.unit_of_analysis,
                independent_unit=case.evaluation.independent_unit,
                repeats=repeats,
                fixed=fixed,
                regressed=regressed,
                changed=changed,
                unavailable_items=unavailable_items,
                rows=tuple(rows),
            )
        )
    return results


def compare_run_variants(
    records: list[RunRecord], cases: dict[str, Case]
) -> dict[str, list[RunnerComparison]]:
    grouped: dict[str, dict[tuple[str, str], list[RunRecord]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for record in records:
        grouped[record.case][(record.variant_label, record.runner_label)].append(record)
    return {
        case_name: compare_case_variants(cases[case_name], case_records)
        for case_name, case_records in grouped.items()
        if case_name in cases and cases[case_name].evaluation.comparison is not None
    }
