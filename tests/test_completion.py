"""任务完成度：单 repeat 判据、cell 通过率、跨 case 汇总、scorecard 集成。"""

from __future__ import annotations

from pathlib import Path

from bench.case import Case, CheckSpec, JudgeSpec, Task
from bench.completion import (
    cell_completion,
    repeat_pass,
    runner_completion,
)
from bench.record import CheckResult, JudgeResult, RunRecord


def _case(*, check="none", judge=True, expected=None) -> Case:
    return Case(
        name="c",
        directory=Path("."),
        task=Task(type="prompt", prompt="x"),
        check=CheckSpec(type=check, script="check.sh" if check == "script" else None),
        judge=JudgeSpec(enabled=judge, rubric="r"),
        class_="reasoning",
        expected=expected or {},
    )


def _rec(*, is_error=False, check=None, judge=None) -> RunRecord:
    return RunRecord(
        case="c", runner_label="r", launcher_type="c",
        is_error=is_error,
        check=check or CheckResult(),
        judge=judge,
    )


# ── 单 repeat 判据 ──────────────────────────────────
def test_is_error_is_fail():
    assert repeat_pass(_rec(is_error=True), _case(expected={"passing_threshold": 14})) is False


def test_check_pass_and_fail():
    case = _case(check="script", judge=False)
    assert repeat_pass(_rec(check=CheckResult(ran=True, passed=True)), case) is True
    assert repeat_pass(_rec(check=CheckResult(ran=True, passed=False)), case) is False


def test_judge_threshold():
    case = _case(expected={"passing_threshold": 14})
    assert repeat_pass(_rec(judge=JudgeResult(ran=True, score=16, max=20)), case) is True
    assert repeat_pass(_rec(judge=JudgeResult(ran=True, score=4, max=20)), case) is False


def test_no_criterion_is_none():
    # judge enabled 但没阈值、没 check → 无判据
    assert repeat_pass(_rec(judge=JudgeResult(ran=True, score=16)), _case(expected={})) is None
    # judge 没跑成（没登录）→ 无判据，不冤判为 0
    assert repeat_pass(_rec(judge=JudgeResult(ran=False)), _case(expected={"passing_threshold": 14})) is None


def test_check_takes_priority_over_judge():
    case = _case(check="script", expected={"passing_threshold": 14})
    # check 通过但 judge 低于阈值 → 以 check 为准（完成）
    rec = _rec(check=CheckResult(ran=True, passed=True), judge=JudgeResult(ran=True, score=2, max=20))
    assert repeat_pass(rec, case) is True


def test_core_dimensions_priority_and_fallback():
    case = _case(expected={"passing_threshold": 14, "completion": {"core_dimensions": {"correctness": 4}}})
    # 有结构化维度分且达标 → 完成（即使总分低于阈值）
    rec_ok = _rec(judge=JudgeResult(ran=True, score=10, max=20, dimensions={"correctness": 5}))
    assert repeat_pass(rec_ok, case) is True
    # 维度分不达标 → 未完成（即使总分够）
    rec_bad = _rec(judge=JudgeResult(ran=True, score=18, max=20, dimensions={"correctness": 2}))
    assert repeat_pass(rec_bad, case) is False
    # 维度分缺失 → 回退到阈值
    rec_nodims = _rec(judge=JudgeResult(ran=True, score=16, max=20, dimensions={}))
    assert repeat_pass(rec_nodims, case) is True


# ── cell 聚合（repeat）──────────────────────────────
def test_cell_completion_rates():
    case = _case(expected={"passing_threshold": 14})
    recs = [
        _rec(judge=JudgeResult(ran=True, score=16)),
        _rec(judge=JudgeResult(ran=True, score=4)),
        _rec(judge=JudgeResult(ran=True, score=18)),
    ]
    cc = cell_completion(recs, case)
    assert (cc.passes, cc.evaluated, cc.total) == (2, 3, 0 + 3)
    assert cc.rate == 2 / 3
    assert cc.display == "2/3"


def test_cell_single_displays_binary():
    case = _case(expected={"passing_threshold": 14})
    assert cell_completion([_rec(judge=JudgeResult(ran=True, score=16))], case).display == "✅ 完成"
    assert cell_completion([_rec(judge=JudgeResult(ran=True, score=4))], case).display == "❌ 未完成"


def test_cell_unevaluated_display():
    case = _case(expected={})  # 无判据
    cc = cell_completion([_rec(judge=JudgeResult(ran=True, score=16))], case)
    assert cc.evaluated == 0 and cc.display == "—(未评)"


# ── 跨 case 汇总 ────────────────────────────────────
def test_runner_completion_across_cases():
    from bench.completion import CellCompletion
    cells = [
        CellCompletion(passes=1, evaluated=1, total=1),   # 完成
        CellCompletion(passes=0, evaluated=1, total=1),   # 未完成
        CellCompletion(passes=2, evaluated=3, total=3),   # 2/3
    ]
    rc = runner_completion("minimax", cells)
    assert rc.applicable == 3
    assert abs(rc.completed - (1 + 0 + 2 / 3)) < 1e-9
    assert "5/3" not in rc.display  # 不是把 repeat 加总
    assert rc.display.startswith("1.7/3") and "%" in rc.display


def test_runner_completion_skips_na():
    from bench.completion import CellCompletion
    cells = [
        CellCompletion(passes=1, evaluated=1, total=1),
        CellCompletion(passes=0, evaluated=0, total=1),   # 未评 → 不计入分母
    ]
    rc = runner_completion("x", cells)
    assert rc.applicable == 1 and rc.na_cases == 1
    assert rc.display == "1/1 (100%) +1未评"
