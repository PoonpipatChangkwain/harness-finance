"""A backtest cell must not see past its analysis date, whichever tool asks (Phase 15.1)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from app.domain.data import registry
from app.domain.data.backtest import PointInTimeRouter, PriceStore, preload_prices, range_covering
from app.domain.data.base import Capability, DataRequest, DataResult, NoData
from app.domain.data.router import DataRouter
from app.domain.models.market import OHLCVBar, OHLCVFrame
from app.domain.models.symbol import normalize_symbol
from app.domain.tools import market

AS_OF = date(2025, 12, 31)
FIRST = date(2025, 1, 1)


def _bars(n: int = 420) -> list[OHLCVBar]:
    out = []
    for i in range(n):
        px = Decimal(100 + i)  # strictly rising: the close identifies the day
        out.append(
            OHLCVBar(
                ts=datetime.combine(FIRST + timedelta(days=i), datetime.min.time(), tzinfo=UTC),
                open=px, high=px + 1, low=px - 1, close=px, volume=1000,
            )
        )  # fmt: skip
    return out


class _Provider:
    """PRICES returns every bar it has (incl. the "future"); NEWS records the as_of it was asked."""

    id = "fake"
    capabilities = frozenset({Capability.PRICES, Capability.NEWS})

    def __init__(self) -> None:
        self.prices_calls: list[DataRequest] = []
        self.news_as_of: list[date | None] = []

    def is_configured(self) -> bool:
        return True

    def supports(self, cap: Capability, symbol: Any) -> bool:
        return True

    async def fetch(self, cap: Capability, req: DataRequest) -> DataResult:
        if cap is Capability.NEWS:
            self.news_as_of.append(req.as_of)
            return DataResult(provider=self.id, capability=cap, data=[], status="ok")
        self.prices_calls.append(req)
        frame = OHLCVFrame(symbol=req.symbol.canonical, interval="1d", bars=_bars())  # type: ignore[union-attr]
        return DataResult(provider=self.id, capability=cap, data=frame, status="ok")


@pytest.fixture
async def setup(monkeypatch: pytest.MonkeyPatch) -> tuple[_Provider, PointInTimeRouter]:
    provider = _Provider()
    base = DataRouter({provider.id: provider})  # type: ignore[dict-item]
    monkeypatch.setattr(base, "_order_for", lambda cap, mode: ("failover", [provider.id]))
    store = PriceStore()
    sym = normalize_symbol("PTT")
    failures = await preload_prices(base, store, [sym], ["1d"], date(2025, 6, 1), date(2026, 2, 1))
    assert failures == {}
    return provider, PointInTimeRouter(base, store, AS_OF)


def test_range_covering() -> None:
    assert range_covering(30) == "1mo"
    assert range_covering(400) == "2y"
    assert range_covering(99_999) == "10y"


async def test_preload_downloads_once_with_warmup(setup: tuple[_Provider, PointInTimeRouter]) -> None:
    provider, _ = setup
    [req] = provider.prices_calls
    # 2025-06-01 minus 400 days of warm-up, explicit; `range` for providers that ignore `start`
    assert req.as_of == date(2026, 2, 1)
    assert req.params == {"interval": "1d", "start": "2024-04-27", "range": "2y"}


async def test_preload_clamps_intraday_to_what_yahoo_keeps() -> None:
    provider = _Provider()
    base = DataRouter({provider.id: provider})  # type: ignore[dict-item]
    base._order_for = lambda cap, mode: ("failover", [provider.id])  # type: ignore[method-assign]
    today = date.today()
    sym = normalize_symbol("PTT")
    failures = await preload_prices(base, PriceStore(), [sym], ["15m"], today - timedelta(days=10), today)
    assert failures == {}
    assert provider.prices_calls[0].params["start"] == (today - timedelta(days=58)).isoformat()
    # a backtest a year ago cannot get 15m bars at all: reported, not fetched
    old = await preload_prices(base, PriceStore(), [sym], ["15m"], date(2025, 1, 1), date(2025, 3, 1))
    assert "Yahoo keeps only" in old[("PTT.BK", "15m")]


async def test_a_tool_without_as_of_still_cannot_see_the_future(
    setup: tuple[_Provider, PointInTimeRouter],
) -> None:
    _, pit = setup
    with registry.use_router(pit):
        # get_prices takes no as_of argument at all — the router is the guard
        out = await market.get_prices(market.PricesArgs(symbol="PTT", range="3mo"), None)  # type: ignore[arg-type]
        snap = await market.get_market_snapshot(market.SnapshotArgs(symbol="PTT"), None)  # type: ignore[arg-type]
        quote = await market.get_quote(market.SymbolArgs(symbol="PTT"), None)  # type: ignore[arg-type]
    dates = [b["date"] for b in out.data["bars"]]
    assert max(dates) == AS_OF.isoformat()
    assert min(dates) > (AS_OF - timedelta(days=93)).isoformat()  # trimmed to the requested range
    assert snap.data["as_of"] == AS_OF.isoformat()
    assert snap.data["latest_bar"]["close"] == float(100 + (AS_OF - FIRST).days)
    assert Decimal(quote.data["price"]) == 100 + (AS_OF - FIRST).days
    # outside the block the real (here: fake) router is back
    assert registry.get_router() is not pit


async def test_other_capabilities_get_the_analysis_date(setup: tuple[_Provider, PointInTimeRouter]) -> None:
    provider, pit = setup
    sym = normalize_symbol("PTT")
    await pit.get(Capability.NEWS, DataRequest(symbol=sym))
    await pit.get(Capability.NEWS, DataRequest(symbol=sym, as_of=date(2026, 1, 15)))  # later: clamped
    await pit.get(Capability.NEWS, DataRequest(symbol=sym, as_of=date(2025, 11, 1)))  # earlier: kept
    assert provider.news_as_of == [AS_OF, AS_OF, date(2025, 11, 1)]


async def test_a_symbol_that_was_not_preloaded_is_refused(setup: tuple[_Provider, PointInTimeRouter]) -> None:
    _, pit = setup
    with pytest.raises(NoData, match="not preloaded"):
        await pit.get(
            Capability.PRICES, DataRequest(symbol=normalize_symbol("AAPL"), params={"interval": "1d"})
        )
    with pytest.raises(NoData, match="not preloaded"):
        await pit.get(
            Capability.PRICES, DataRequest(symbol=normalize_symbol("PTT"), params={"interval": "1h"})
        )
