"""models.yaml：按日期取价、缓存分开计价、汇率换算、无价格表时退回上报成本。"""

from __future__ import annotations

from pathlib import Path

import pytest

from bench.models import ModelBook, ModelBookError
from bench.record import Usage


def _book(tmp_path: Path, text: str) -> ModelBook:
    path = tmp_path / "models.yaml"
    path.write_text(text, encoding="utf-8")
    return ModelBook.load(path)


BOOK = """
runners:
  kimi-k3:
    display: Kimi K3
    vendor: Moonshot
    pricing:
      - since: 2026-01-01
        currency: CNY
        input_per_m: 4
        output_per_m: 16
        cache_read_per_m: 1
        source: https://example.test/pricing
      - since: 2026-09-01
        currency: CNY
        input_per_m: 2
        output_per_m: 8
        cache_read_per_m: 0.5
        source: https://example.test/pricing
  sonnet:
    display: Claude Sonnet
    pricing:
      - since: 2026-01-01
        currency: USD
        input_per_m: 3
        output_per_m: 15
  nameless:
    display: No Price
fx:
  CNY_USD: {rate: 0.14, as_of: 2026-09-01}
"""


def test_price_entry_follows_run_date(tmp_path):
    book = _book(tmp_path, BOOK)
    assert book.price_for("kimi-k3", "2026-08-15T10:00:00+00:00").input_per_m == 4
    assert book.price_for("kimi-k3", "2026-09-01T00:00:00+00:00").input_per_m == 2
    assert book.price_for("nameless", "2026-09-01") is None
    assert book.price_for("unknown", "2026-09-01") is None


def test_cost_splits_cache_and_converts_currency(tmp_path):
    book = _book(tmp_path, BOOK)
    usage = Usage.from_tokens(1_000_000, 100_000, cache_read_tokens=2_000_000, cache_creation_tokens=500_000)
    estimate = book.estimate("kimi-k3", "2026-09-10", usage)
    # 9 月价：input 2 + output 0.8 + cache_read 1.0 + cache_write（无牌价按 input）1.0 = 4.8 CNY → USD
    assert estimate.cost_usd == pytest.approx(4.8 * 0.14, rel=1e-6)
    assert estimate.source == "pricing"
    assert estimate.price_version and len(estimate.price_version) == 12
    earlier = book.estimate("kimi-k3", "2026-08-10", usage)
    assert earlier.cost_usd > estimate.cost_usd
    assert earlier.price_version != estimate.price_version


def test_usd_pricing_needs_no_fx_and_reported_cost_is_fallback(tmp_path):
    book = _book(tmp_path, BOOK)
    usage = Usage.from_tokens(1_000_000, 1_000_000, cost_usd=99.0)
    assert book.estimate("sonnet", "2026-05-01", usage).cost_usd == pytest.approx(18.0)
    fallback = book.estimate("nameless", "2026-05-01", usage)
    assert fallback.cost_usd == 99.0 and fallback.source == "reported" and fallback.price_version is None
    assert book.estimate("nameless", "2026-05-01", None).cost_usd is None
    assert book.estimate("nameless", "2026-05-01", Usage()).source is None


def test_missing_fx_is_an_error_not_a_guess(tmp_path):
    book = _book(tmp_path, BOOK.replace("fx:\n  CNY_USD: {rate: 0.14, as_of: 2026-09-01}\n", "fx: {}\n"))
    with pytest.raises(ModelBookError, match="汇率"):
        book.estimate("kimi-k3", "2026-09-10", Usage.from_tokens(10, 10))


def test_display_falls_back_to_label_and_repo_book_loads():
    book = ModelBook.load()
    assert book.display("kimi-k3") == "Kimi K3"
    assert book.display("never-registered") == "never-registered"
