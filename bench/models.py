"""模型档案与价格表（`models.yaml`）→ 每条运行记录的美元成本。

价格按生效日期版本化：只追加新条目，历史记录发布时用当时生效的价格算出并冻结，不重算。
计价公式（单位：每百万 token）：

    cost = input × input_per_m + output × output_per_m
         + cache_read × cache_read_per_m + cache_creation × cache_write_per_m

厂商没列缓存价时，缓存读写都按 input 价计（保守上限）。非美元报价按 `fx` 表换算。
`price_version` 是所用价格条目与汇率的哈希前缀，写进公开记录，方便日后追溯。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .record import Usage

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = ROOT / "models.yaml"


class ModelBookError(ValueError):
    pass


@dataclass(frozen=True)
class PriceEntry:
    since: str  # YYYY-MM-DD，含当天
    currency: str  # USD / CNY
    input_per_m: float
    output_per_m: float
    cache_read_per_m: float | None = None
    cache_write_per_m: float | None = None
    source: str = ""
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "since": self.since, "currency": self.currency,
            "input_per_m": self.input_per_m, "output_per_m": self.output_per_m,
            "cache_read_per_m": self.cache_read_per_m, "cache_write_per_m": self.cache_write_per_m,
            "source": self.source,
        }


@dataclass(frozen=True)
class ModelProfile:
    label: str  # runners.yaml 里的 runner 标签
    display: str
    vendor: str = ""
    model: str = ""
    released: str | None = None
    pricing: tuple[PriceEntry, ...] = ()
    note: str = ""


@dataclass(frozen=True)
class CostEstimate:
    cost_usd: float | None
    price_version: str | None
    source: str | None  # pricing | reported | None


@dataclass(frozen=True)
class ModelBook:
    profiles: dict[str, ModelProfile] = field(default_factory=dict)
    fx: dict[str, tuple[float, str]] = field(default_factory=dict)  # "CNY_USD" → (rate, as_of)

    @classmethod
    def load(cls, path: str | Path = DEFAULT_PATH) -> "ModelBook":
        file = Path(path)
        if not file.exists():
            return cls()
        raw = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ModelBookError(f"{file} 顶层必须是映射")
        profiles: dict[str, ModelProfile] = {}
        for label, data in (raw.get("runners") or {}).items():
            data = data or {}
            if not isinstance(data, dict):
                raise ModelBookError(f"runner '{label}' 的档案必须是映射")
            pricing = []
            for entry in data.get("pricing") or []:
                if not isinstance(entry, dict):
                    raise ModelBookError(f"runner '{label}' 的 pricing 条目必须是映射")
                try:
                    pricing.append(PriceEntry(
                        since=str(entry["since"]), currency=str(entry.get("currency", "USD")).upper(),
                        input_per_m=float(entry["input_per_m"]), output_per_m=float(entry["output_per_m"]),
                        cache_read_per_m=_optional_float(entry.get("cache_read_per_m")),
                        cache_write_per_m=_optional_float(entry.get("cache_write_per_m")),
                        source=str(entry.get("source", "")), note=str(entry.get("note", "")),
                    ))
                except KeyError as exc:
                    raise ModelBookError(f"runner '{label}' 的 pricing 条目缺少 {exc}") from exc
            pricing.sort(key=lambda e: e.since)
            profiles[str(label)] = ModelProfile(
                label=str(label), display=str(data.get("display") or label),
                vendor=str(data.get("vendor", "")), model=str(data.get("model", "")),
                released=(str(data["released"]) if data.get("released") else None),
                pricing=tuple(pricing), note=str(data.get("note", "")),
            )
        fx: dict[str, tuple[float, str]] = {}
        for pair, data in (raw.get("fx") or {}).items():
            if not isinstance(data, dict) or "rate" not in data:
                raise ModelBookError(f"fx '{pair}' 需要 rate 与 as_of")
            fx[str(pair).upper()] = (float(data["rate"]), str(data.get("as_of", "")))
        return cls(profiles=profiles, fx=fx)

    def display(self, label: str) -> str:
        profile = self.profiles.get(label)
        return profile.display if profile else label

    def price_for(self, label: str, started_at: str) -> PriceEntry | None:
        profile = self.profiles.get(label)
        if not profile or not profile.pricing:
            return None
        day = (started_at or "")[:10]
        chosen = None
        for entry in profile.pricing:
            if not day or entry.since <= day:
                chosen = entry
        return chosen

    def estimate(self, label: str, started_at: str, usage: Usage | None) -> CostEstimate:
        """优先按价格表统一口径计算；没有价格表时退回启动器上报的 cost_usd。"""
        entry = self.price_for(label, started_at)
        if entry is not None and usage is not None and usage.effective_input is not None:
            rate, fx_as_of = (1.0, "") if entry.currency == "USD" else self.fx.get(f"{entry.currency}_USD", (None, ""))
            if rate is None:
                raise ModelBookError(f"缺少 {entry.currency}_USD 汇率，无法为 {label} 计价")
            read_price = entry.cache_read_per_m if entry.cache_read_per_m is not None else entry.input_per_m
            write_price = entry.cache_write_per_m if entry.cache_write_per_m is not None else entry.input_per_m
            native = (
                (usage.input_tokens or 0) * entry.input_per_m
                + (usage.output_tokens or 0) * entry.output_per_m
                + (usage.cache_read_tokens or 0) * read_price
                + (usage.cache_creation_tokens or 0) * write_price
            ) / 1_000_000
            version = hashlib.sha256(json.dumps(
                {"label": label, "entry": entry.to_dict(), "fx": [rate, fx_as_of]},
                sort_keys=True, ensure_ascii=False,
            ).encode()).hexdigest()[:12]
            return CostEstimate(cost_usd=round(native * rate, 6), price_version=version, source="pricing")
        if usage is not None and usage.cost_usd is not None:
            return CostEstimate(cost_usd=round(float(usage.cost_usd), 6), price_version=None, source="reported")
        return CostEstimate(cost_usd=None, price_version=None, source=None)


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)
