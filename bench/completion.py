"""任务完成度 —— 把 check / judge 折成「这个模型到底把任务干成了没」这一维。

判分分数（如 16/20）偏「过程」，不直观。完成度只拎「对错」一维，给出可横排的结果信号。

**单 repeat 判据**（优先级，第一个适用的生效）：
  1. is_error → 失败（崩了 / 超时 = 没完成）
  2. 核心判据 `expected.completion.core_dimensions`（judge 各核心维 ≥ 阈值）——仅当 judge 返回了结构化维度分
  3. `check` 存在且 ran → `check.passed`（确定性，最硬）
  4. judge ran 且 `expected.passing_threshold` 存在 → `judge.score ≥ threshold`
  5. 都无判据 → None（未评，不计入完成率分母；judge 没跑成也走这里，绝不冤判成 0）

**cell**（一个 case×runner 的若干 repeat）→ 通过率 ∈ [0,1]，全未评则 None。
**runner 跨 case** → 各 case 等权的平均完成率（case 是关心单位，不被 repeat 多的 case 带偏）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .case import Case
from .record import RunRecord


def _core_dimensions_verdict(record: RunRecord, completion_spec: dict) -> bool | None:
    """核心维度判据：judge 各核心维 ≥ 阈值。维度分缺失 → None（不可用，回退）。"""
    reqs = (completion_spec or {}).get("core_dimensions") or {}
    if not reqs:
        return None
    dims = (record.judge.dimensions if record.judge else None) or {}
    for name, minimum in reqs.items():
        val = dims.get(name)
        if not isinstance(val, (int, float)):
            return None  # 结构化维度分缺失 → 此判据不可用
    return all(dims[name] >= minimum for name, minimum in reqs.items())


def repeat_pass(record: RunRecord, case: Case) -> bool | None:
    """单次运行是否「完成」。True/False = 有判据的通过/失败；None = 无判据/未评。"""
    if record.is_error:
        return False
    expected = case.expected or {}
    completion_spec = expected.get("completion") or {}

    # 2. 核心判据优先（更贴「完成任务」，绕开次要维度噪声）
    cv = _core_dimensions_verdict(record, completion_spec)
    if cv is not None:
        return cv

    # 3. check（确定性）
    if case.check.type == "script" and record.check.ran:
        return bool(record.check.passed)

    # 4. judge 阈值
    threshold = expected.get("passing_threshold")
    if (
        case.judge.enabled
        and record.judge is not None
        and record.judge.ran
        and record.judge.score is not None
        and threshold is not None
    ):
        return record.judge.score >= threshold

    # 5. 无判据
    return None


@dataclass(frozen=True)
class CellCompletion:
    """一个 case×runner 单元（若干 repeat）的完成情况。"""

    passes: int      # 通过的 repeat 数
    evaluated: int   # 有明确判据的 repeat 数
    total: int       # 总 repeat 数

    @property
    def unevaluated(self) -> int:
        return self.total - self.evaluated

    @property
    def rate(self) -> float | None:
        return self.passes / self.evaluated if self.evaluated else None

    @property
    def display(self) -> str:
        if self.evaluated == 0:
            return "—(未评)"
        if self.evaluated == 1:
            return "✅ 完成" if self.passes == 1 else "❌ 未完成"
        tail = "" if self.unevaluated == 0 else f"(+{self.unevaluated}未评)"
        return f"{self.passes}/{self.evaluated}{tail}"


def cell_completion(records: Sequence[RunRecord], case: Case) -> CellCompletion:
    verdicts = [repeat_pass(r, case) for r in records]
    evaluated = [v for v in verdicts if v is not None]
    passes = sum(1 for v in evaluated if v)
    return CellCompletion(passes=passes, evaluated=len(evaluated), total=len(records))


@dataclass(frozen=True)
class RunnerCompletion:
    """一个 runner 跨多个 case 的汇总完成率。"""

    runner_label: str
    completed: float   # Σ 各 case 完成率（repeat 部分通过算小数）
    applicable: int    # 有判据的 case 数（分母）
    na_cases: int      # 无判据 / 未评的 case 数

    @property
    def rate(self) -> float | None:
        return self.completed / self.applicable if self.applicable else None

    @property
    def display(self) -> str:
        if self.applicable == 0:
            return "—(无判据)"
        comp = (
            f"{self.completed:.0f}"
            if abs(self.completed - round(self.completed)) < 1e-9
            else f"{self.completed:.1f}"
        )
        tail = "" if self.na_cases == 0 else f" +{self.na_cases}未评"
        return f"{comp}/{self.applicable} ({round(100 * self.rate)}%){tail}"


def runner_completion(label: str, cells: Sequence[CellCompletion]) -> RunnerCompletion:
    """跨 case 汇总：各 case 等权，部分通过（repeat）算小数。"""
    rates = [c.rate for c in cells]
    applicable = [r for r in rates if r is not None]
    return RunnerCompletion(
        runner_label=label,
        completed=sum(applicable),
        applicable=len(applicable),
        na_cases=len(rates) - len(applicable),
    )
