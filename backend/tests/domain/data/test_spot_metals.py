"""Spot XAUUSD: Swissquote mid, futures bars shifted by the basis, routing ahead of yfinance."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from app.domain.data.base import Capability, DataRequest, DataResult
from app.domain.data.providers.spot_metals_provider import SpotMetalsProvider, shift_frame, swissquote_mid
from app.domain.models.market import OHLCVBar, OHLCVFrame, Quote
from app.domain.models.symbol import normalize_symbol

SWISSQUOTE = [  # shape of the public bboquotes feed
    {"topo": {"platform": "SwissquoteLtd", "server": "Live5"}, "ts": 1790569536888,
     "spreadProfilePrices": [
         {"spreadProfile": "premium", "bid": 4196.839, "ask": 4197.501},
         {"spreadProfile": "prime", "bid": 4196.853, "ask": 4197.487},
     ]},
]  # fmt: skip


def test_swissquote_mid_uses_the_prime_profile() -> None:
    mid, ts = swissquote_mid(SWISSQUOTE)
    assert mid == Decimal("4197.170")
    assert ts == datetime.fromtimestamp(1790569536.888, UTC)


def _frame(symbol: str, closes: list[float]) -> OHLCVFrame:
    start = datetime(2026, 9, 28, tzinfo=UTC)
    bars = [
        OHLCVBar(ts=start + timedelta(minutes=15 * i), open=Decimal(str(c)), high=Decimal(str(c + 2)),
                 low=Decimal(str(c - 2)), close=Decimal(str(c)), volume=10)
        for i, c in enumerate(closes)
    ]  # fmt: skip
    return OHLCVFrame(symbol=symbol, interval="15m", bars=bars)


def test_shift_keeps_ranges_and_moves_levels() -> None:
    shifted = shift_frame(_frame("GC=F", [4230.0, 4232.0]), Decimal("34.3"), "XAUUSD")
    assert shifted.symbol == "XAUUSD"
    assert [b.close for b in shifted.bars] == [Decimal("4195.7"), Decimal("4197.7")]
    assert shifted.bars[0].high - shifted.bars[0].low == Decimal("4")  # ATR-like ranges unchanged


class _FakeFutures:
    async def fetch(self, cap: Capability, req: DataRequest) -> DataResult:
        assert req.symbol is not None and req.symbol.canonical == "GC=F"
        if cap == Capability.QUOTE:
            quote = Quote(symbol="GC=F", price=Decimal("4231.5"), ts=datetime.now(UTC), currency="USD")
            return DataResult(provider="yfinance", capability=cap, data=quote, status="ok")
        return DataResult(
            provider="yfinance", capability=cap, data=_frame("GC=F", [4229.0, 4231.0]), status="ok"
        )


async def test_spot_prices_are_futures_minus_basis(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = SpotMetalsProvider(futures=_FakeFutures())  # type: ignore[arg-type]

    async def spot(sym: Any) -> tuple[Decimal, datetime, str]:
        return Decimal("4197.2"), datetime.now(UTC), "swissquote"

    monkeypatch.setattr(provider, "spot", spot)
    gold = normalize_symbol("XAUUSD")
    assert provider.supports(Capability.PRICES, gold)
    assert not provider.supports(Capability.PRICES, normalize_symbol("GC=F"))
    assert not provider.supports(Capability.PRICES, normalize_symbol("EURUSD"))

    result = await provider.fetch(Capability.PRICES, DataRequest(symbol=gold, params={"interval": "15m"}))
    assert result.data.symbol == "XAUUSD"
    assert [b.close for b in result.data.bars] == [Decimal("4194.700"), Decimal("4196.700")]  # basis 34.3
    assert "basis 34.300" in result.notes[0]
    quote = await provider.fetch(Capability.QUOTE, DataRequest(symbol=gold))
    assert quote.data.price == Decimal("4197.2")
