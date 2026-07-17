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
    """token / 成本用量。

    `input_tokens` 是**未缓存的新增输入**；缓存命中的输入在 `cache_read_tokens`、新建缓存在
    `cache_creation_tokens`（Anthropic 兼容端点都分开报）。丢掉它们会严重低估真实处理量
    （实测 sonnet 一次 input 显示 6，实际含缓存 13 万）。

    **真实总输入 = input + cache_creation + cache_read**（`effective_input`）；`total_tokens` 含全部四项。
    未来计价要分字段（cache_read 通常折扣、cache_creation 溢价），故各自留存。
    `cost_usd` 仅对真实 Anthropic 计费的 claude 启动器可信（c 路由的第三方/本地端点不采，codex 无）。
    """

    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_creation_tokens: int | None = None
    cache_read_tokens: int | None = None
    total_tokens: int | None = None
    cost_usd: float | None = None

    @property
    def effective_input(self) -> int | None:
        """真实处理的总输入（含缓存读写）。三项全 None 时返回 None。"""
        parts = (self.input_tokens, self.cache_creation_tokens, self.cache_read_tokens)
        if all(p is None for p in parts):
            return None
        return (self.input_tokens or 0) + (self.cache_creation_tokens or 0) + (self.cache_read_tokens or 0)

    @staticmethod
    def from_tokens(
        input_tokens: int | None,
        output_tokens: int | None,
        cost_usd: float | None = None,
        *,
        cache_creation_tokens: int | None = None,
        cache_read_tokens: int | None = None,
    ) -> "Usage":
        parts = (input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens)
        total = None
        if any(p is not None for p in parts):
            total = (
                (input_tokens or 0)
                + (output_tokens or 0)
                + (cache_creation_tokens or 0)
                + (cache_read_tokens or 0)
            )
        return Usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_tokens=cache_creation_tokens,
            cache_read_tokens=cache_read_tokens,
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
    report: dict[str, Any] = field(default_factory=dict)


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
    """单次 (case × runner × prompt_variant × repeat_index) 运行记录。"""

    case: str
    runner_label: str
    launcher_type: str
    runner_model: str = ""
    run_id: str = "legacy"
    variant_label: str = "default"
    prompt_template_sha256: str = ""
    run_context_sha256: str = ""
    input_manifest_sha256: str = ""
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
    request_manifest_file: str = ""
    request_manifest_sha256: str = ""
    response_sha256: dict[str, str] = field(default_factory=dict)

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
            run_id=data.get("run_id", "legacy"),
            variant_label=data.get("variant_label", "default"),
            prompt_template_sha256=data.get("prompt_template_sha256", ""),
            run_context_sha256=data.get("run_context_sha256", ""),
            input_manifest_sha256=data.get("input_manifest_sha256", ""),
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
            request_manifest_file=data.get("request_manifest_file", ""),
            request_manifest_sha256=data.get("request_manifest_sha256", ""),
            response_sha256=data.get("response_sha256") or {},
        )

    @classmethod
    def from_json(cls, text: str) -> "RunRecord":
        return cls.from_dict(json.loads(text))
