"""Fixture market data for portfolio tests: quotes, deterministic 1-year histories, no network."""

from __future__ import annotations

import random
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from app.domain.data.base import Capability, DataRequest, DataResult, NoData
from app.domain.models.market import OHLCVBar, OHLCVFrame, Quote
from app.domain.models.portfolio import Portfolio, Position
from app.domain.models.provenance import new_record
from app.domain.models.symbol import normalize_symbol

AS_OF = date(2026, 9, 25)

# canonical → (price, previous close)
QUOTES: dict[str, tuple[str, str]] = {
    "PTT.BK": ("34.5", "34.0"),
    "AAPL": ("200", "198"),
    "MSFT": ("400", "404"),
    "KBANK.BK": ("150", "149"),
    "USDTHB=X": ("36", "36"),
}


def business_days(n: int, end: date = AS_OF) -> list[date]:
    days: list[date] = []
    d = end
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    return list(reversed(days))


def walk(last: float, n: int, seed: int, vol: float = 0.012) -> list[float]:
    """A seeded random walk that ends exactly at ``last``."""
    rng = random.Random(seed)
    rets = [rng.gauss(0.0003, vol) for _ in range(n - 1)]
    closes = [1.0]
    for r in rets:
        closes.append(closes[-1] * (1 + r))
    scale = last / closes[-1]
    return [c * scale for c in closes]


class FakeMarket:
    def __init__(
        self,
        quotes: dict[str, tuple[str, str]] | None = None,
        *,
        missing: set[str] | None = None,
        history_days: int = 260,
    ) -> None:
        self.quotes = dict(QUOTES if quotes is None else quotes)
        self.missing = missing or set()
        self.history_days = history_days
        self.calls: list[tuple[str, str]] = []

    async def get(self, cap: Capability, req: DataRequest) -> DataResult:
        assert req.symbol is not None
        sym = req.symbol.canonical
        self.calls.append((cap.value, sym))
        if sym in self.missing:
            raise NoData(f"{sym}: no data (fixture)")
        if cap == Capability.QUOTE:
            if sym not in self.quotes:
                raise NoData(f"{sym}: no quote (fixture)")
            price, prev = (Decimal(x) for x in self.quotes[sym])
            quote = Quote(
                symbol=sym,
                price=price,
                change=price - prev,
                ts=datetime(2026, 9, 25, 9, tzinfo=UTC),
                currency=req.symbol.currency,
            )
            record = new_record("data", str(price), source="fake", tool="quote", unit=req.symbol.currency)
            return DataResult(provider="fake", capability=cap, data=quote, records=[record], status="ok")
        if cap == Capability.PRICES:
            last = float(self.quotes.get(sym, ("100", "100"))[0])
            days = business_days(self.history_days)
            closes = walk(last, len(days), seed=sum(map(ord, sym)))
            bars = [
                OHLCVBar(
                    ts=datetime(d.year, d.month, d.day, tzinfo=UTC),
                    open=Decimal(str(c)),
                    high=Decimal(str(c * 1.01)),
                    low=Decimal(str(c * 0.99)),
                    close=Decimal(str(c)),
                    volume=1000,
                )
                for d, c in zip(days, closes, strict=True)
            ]
            frame = OHLCVFrame(symbol=sym, interval="1d", bars=bars)
            record = new_record("data", {"bars": len(bars)}, source="fake", tool="prices")
            return DataResult(provider="fake", capability=cap, data=frame, records=[record], status="ok")
        raise NoData(f"{cap.value} not in fixture")


def install(monkeypatch: pytest.MonkeyPatch, market: FakeMarket | None = None) -> FakeMarket:
    """Every ``data_source()`` call (analytics, entry, plan tool) now hits the fixture."""
    fake = market or FakeMarket()
    monkeypatch.setattr("app.domain.data.registry.get_router", lambda: fake)
    return fake


def position(symbol: str, qty: str, avg: str | None = None, **kw: Any) -> Position:
    return Position(
        symbol=normalize_symbol(symbol), quantity=Decimal(qty), avg_cost=Decimal(avg) if avg else None, **kw
    )


def book(positions: list[Position], cash: str | None = "50000", **kw: Any) -> Portfolio:
    return Portfolio(
        id=kw.pop("id", "pf_test"),
        name=kw.pop("name", "Test"),
        base_currency=kw.pop("base_currency", "THB"),
        cash=Decimal(cash) if cash is not None else None,
        positions=positions,
        updated_at=datetime(2026, 9, 25, tzinfo=UTC),
        **kw,
    )
