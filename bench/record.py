"""run record schema —— 执行、判分、计分卡之间的唯一数据契约（KTD4）。

每个 (case × runner × repeat_index) 产出一条 RunRecord。
`usage` / `cost_usd` / 部分 `agentic` 字段允许为 None（R9 优雅降级），
下游计分卡对 None 显示「—」。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, replace
from typing import Any


@dataclass(frozen=True)
class Usage:
    """token / 成本用量。cost_usd 可为 None（codex 无成本字段）。"""

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cost_usd: float | None = None

    @staticmethod
    def from_tokens(
        input_tokens: int | None,
        output_tokens: int | None,
        cost_usd: float | None = None,
    ) -> "Usage":
        total = None
        if input_tokens is not None or output_tokens is not None:
            total = (input_tokens or 0) + (output_tokens or 0)
        return Usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total,
            cost_usd=cost_usd,
        )


@dataclass(frozen=True)
class Agentic:
    """agentic 行为指标。files_changed 是无方向诊断量（KTD5）。"""

    num_turns: int | None = None
    files_changed: int = 0
    commands_run: int | None = None


@dataclass(frozen=True)
class CheckResult:
    """确定性校验结果。"""

    ran: bool = False
    passed: bool | None = None
    detail: str = ""


@dataclass(frozen=True)
class JudgeResult:
    """LLM 裁判结果（advisory）。score 可为 None（解析失败时）。"""

    ran: bool = False
    model: str = ""
    same_source: bool = False
    score: float | None = None
    max: float | None = None
    dimensions: dict[str, Any] = field(default_factory=dict)
    reasoning: str = ""


@dataclass(frozen=True)
class RunRecord:
    """单次 (case × runner × repeat_index) 运行记录。"""

    case: str
    runner_label: str
    launcher_type: str
    runner_model: str = ""
    repeat_index: int = 0
    started_at: str = ""
    duration_ms: int = 0
    exit_code: int | None = None
    is_error: bool = False
    usage: Usage | None = None
    agentic: Agentic = field(default_factory=Agentic)
    check: CheckResult = field(default_factory=CheckResult)
    judge: JudgeResult | None = None
    human_note: str = ""
    artifacts_dir: str = ""

    def with_usage(self, usage: Usage | None) -> "RunRecord":
        return replace(self, usage=usage)

    def with_agentic(self, agentic: Agentic) -> "RunRecord":
        return replace(self, agentic=agentic)

    def with_check(self, check: CheckResult) -> "RunRecord":
        return replace(self, check=check)

    def with_judge(self, judge: JudgeResult | None) -> "RunRecord":
        return replace(self, judge=judge)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RunRecord":
        usage = data.get("usage")
        judge = data.get("judge")
        agentic = data.get("agentic") or {}
        check = data.get("check") or {}
        return cls(
            case=data["case"],
            runner_label=data["runner_label"],
            launcher_type=data["launcher_type"],
            runner_model=data.get("runner_model", ""),
            repeat_index=data.get("repeat_index", 0),
            started_at=data.get("started_at", ""),
            duration_ms=data.get("duration_ms", 0),
            exit_code=data.get("exit_code"),
            is_error=data.get("is_error", False),
            usage=Usage(**usage) if usage is not None else None,
            agentic=Agentic(**agentic) if agentic else Agentic(),
            check=CheckResult(**check) if check else CheckResult(),
            judge=JudgeResult(**judge) if judge is not None else None,
            human_note=data.get("human_note", ""),
            artifacts_dir=data.get("artifacts_dir", ""),
        )

    @classmethod
    def from_json(cls, text: str) -> "RunRecord":
        return cls.from_dict(json.loads(text))
