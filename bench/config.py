"""一次运行配置（R15）。数据结构定义；文件加载与 CLI 合并在 __main__（U7）。"""

from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_DIMENSIONS = {
    "quality": True,
    "latency": True,
    "cost": True,
    "agentic": True,
}


@dataclass(frozen=True)
class RunConfig:
    """一次运行的选择与参数。runners/cases 为空表示全部。"""

    runners: tuple[str, ...] = ()
    cases: tuple[str, ...] = ()
    judge: str = "claude"
    repeat: int = 1
    dimensions: dict[str, bool] = field(default_factory=lambda: dict(DEFAULT_DIMENSIONS))
