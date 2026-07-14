"""一次运行配置（R15）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_DIMENSIONS = {
    "quality": True,
    "latency": True,
    "cost": True,
    "agentic": True,
}


class ConfigError(ValueError):
    """配置加载错误。"""


@dataclass(frozen=True)
class RunConfig:
    """一次运行的选择与参数。runners/cases 为空表示全部。"""

    runners: tuple[str, ...] = ()
    cases: tuple[str, ...] = ()
    variants: tuple[str, ...] = ()
    judge: str = "claude"
    repeat: int = 1
    workers: int = 0  # 0 = 自动 = min(选中 runners, 6)；显式传 N 走 N（测试用 1 串行）
    dimensions: dict[str, bool] = field(default_factory=lambda: dict(DEFAULT_DIMENSIONS))

    def __post_init__(self) -> None:
        if isinstance(self.repeat, bool) or not isinstance(self.repeat, int) or self.repeat < 1:
            raise ConfigError("repeat 必须是正整数。")
        if isinstance(self.workers, bool) or not isinstance(self.workers, int) or self.workers < 0:
            raise ConfigError("workers 必须是非负整数。")


def load_config(path: str | Path) -> RunConfig:
    """从 config.yaml 加载一次运行配置。"""
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"配置文件不存在: {p}（参考仓库根的 config.yaml 模板）")
    doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    dims = dict(DEFAULT_DIMENSIONS)
    dims.update(doc.get("dimensions") or {})
    return RunConfig(
        runners=tuple(doc.get("runners") or ()),
        cases=tuple(doc.get("cases") or ()),
        variants=tuple(doc.get("variants") or ()),
        judge=str(doc.get("judge") or "claude"),
        repeat=int(doc["repeat"]) if "repeat" in doc else 1,
        workers=int(doc["workers"]) if "workers" in doc else 0,
        dimensions=dims,
    )
