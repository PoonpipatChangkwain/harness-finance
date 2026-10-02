from __future__ import annotations

from datetime import date
from typing import ClassVar

import pandas as pd
import pytest

from app.domain.data.base import Capability, DataRequest, NoData
from app.domain.data.providers import yfinance_provider as mod
from app.domain.models.symbol import normalize_symbol


def _fake_history_df() -> pd.DataFrame:
    idx = pd.date_range("2026-08-01", periods=5, freq="D", tz="UTC")
    return pd.DataFrame(
        {
            "Open": [30.0, 30.5, 31.0, 30.8, 31.2],
            "High": [30.5, 31.0, 31.5, 31.2, 31.6],
            "Low": [29.8, 30.2, 30.7, 30.5, 30.9],
            "Close": [30.4, 30.9, 31.2, 30.9, 31.4],
            "Volume": [1_000_000, 1_100_000, 950_000, 1_050_000, 1_200_000],
        },
        index=idx,
    )


class _FakeTicker:
    calls: ClassVar[list[dict[str, object]]] = []

    def __init__(self, symbol: str):
        self.symbol = symbol

    def history(self, interval: str, auto_adjust: bool, **window: object) -> pd.DataFrame:
        _FakeTicker.calls.append(window)
        return _fake_history_df()

    @property
    def fast_info(self) -> dict[str, object]:
        return {"lastPrice": 31.4, "previousClose": 30.9, "currency": "THB"}

    def get_info(self) -> dict[str, object]:
        return {
            "longName": "PTT Public Company Limited",
            "sector": "Energy",
            "marketCap": 500_000_000_000,
            "trailingPE": 12.5,
            "priceToBook": 1.8,
            "beta": 0.9,
        }

    @property
    def income_stmt(self) -> pd.DataFrame:
        col = pd.Timestamp("2025-09-30")
        return pd.DataFrame({col: {"TotalRevenue": 400_000_000_000, "NetIncome": 95_000_000_000}})

    @property
    def balance_sheet(self) -> pd.DataFrame:
        col = pd.Timestamp("2025-09-30")
        return pd.DataFrame({col: {"TotalAssets": 350_000_000_000}})

    @property
    def cashflow(self) -> pd.DataFrame:
        col = pd.Timestamp("2025-09-30")
        return pd.DataFrame({col: {"OperatingCashFlow": 100_000_000_000}})

    @property
    def analyst_price_targets(self) -> dict[str, object]:
        return {"low": 25.0, "high": 45.0, "mean": 35.0}

    def get_news(self, count: int) -> list[dict[str, object]]:
        return [
            {
                "id": "n1",
                "content": {
                    "title": "PTT reports record profit",
                    "summary": "Strong quarter",
                    "pubDate": "2026-09-20T10:00:00Z",
                    "provider": {"displayName": "Reuters"},
                    "canonicalUrl": {"url": "https://example.com/n1"},
                },
            }
        ]


class _EmptyTicker:
    def __init__(self, symbol: str):
        self.symbol = symbol

    def history(self, period: str, interval: str, auto_adjust: bool) -> pd.DataFrame:
        return pd.DataFrame()


@pytest.mark.asyncio
async def test_yfinance_prices_maps_bars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod.yf, "Ticker", _FakeTicker)
    provider = mod.YFinanceProvider()
    req = DataRequest(symbol=normalize_symbol("PTT"))

    result = await provider.fetch(Capability.PRICES, req)

    assert result.status == "ok"
    assert result.data.symbol == "PTT.BK"
    assert len(result.data.bars) == 5
    assert result.records[0].source == "yfinance"


@pytest.mark.asyncio
async def test_yfinance_empty_frame_raises_no_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod.yf, "Ticker", _EmptyTicker)
    provider = mod.YFinanceProvider()
    req = DataRequest(symbol=normalize_symbol("AAPL"))

    with pytest.raises(NoData):
        await provider.fetch(Capability.PRICES, req)


@pytest.mark.asyncio
async def test_yfinance_as_of_cutoff_filters_bars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod.yf, "Ticker", _FakeTicker)
    provider = mod.YFinanceProvider()
    req = DataRequest(symbol=normalize_symbol("PTT"), as_of=date(2026, 8, 2))

    result = await provider.fetch(Capability.PRICES, req)

    assert len(result.data.bars) == 2


@pytest.mark.asyncio
async def test_daily_bars_are_dated_by_their_local_session(monkeypatch: pytest.MonkeyPatch) -> None:
    # Yahoo stamps a SET daily bar at Bangkok midnight = 17:00 UTC the day before
    class _BangkokTicker(_FakeTicker):
        def history(self, interval: str, auto_adjust: bool, **window: object) -> pd.DataFrame:
            df = _fake_history_df()
            df.index = pd.date_range("2026-09-29", periods=5, freq="D", tz="Asia/Bangkok")
            if interval != "1d":  # intraday: a 10:00 Bangkok bar is 03:00 UTC
                df.index = pd.date_range("2026-09-29 10:00", periods=5, freq="15min", tz="Asia/Bangkok")
            return df

    monkeypatch.setattr(mod.yf, "Ticker", _BangkokTicker)
    provider = mod.YFinanceProvider()
    sym = normalize_symbol("PTT")
    daily = await provider.fetch(Capability.PRICES, DataRequest(symbol=sym, as_of=date(2026, 9, 30)))
    # the 30 Sep session is dated the 30th, and an as_of of the 30th does not include 1 Oct
    assert [b.ts.isoformat() for b in daily.data.bars] == [
        "2026-09-29T00:00:00+00:00",
        "2026-09-30T00:00:00+00:00",
    ]
    intraday = await provider.fetch(Capability.PRICES, DataRequest(symbol=sym, params={"interval": "15m"}))
    assert intraday.data.bars[0].ts.isoformat() == "2026-09-29T03:00:00+00:00"


@pytest.mark.asyncio
async def test_yfinance_past_as_of_fetches_the_window_ending_then(monkeypatch: pytest.MonkeyPatch) -> None:
    # period= always ends today: an old backtest date would get no warm-up history
    monkeypatch.setattr(mod.yf, "Ticker", _FakeTicker)
    _FakeTicker.calls.clear()
    req = DataRequest(symbol=normalize_symbol("PTT"), params={"range": "1y"}, as_of=date(2026, 8, 3))

    await mod.YFinanceProvider().fetch(Capability.PRICES, req)
    # without as_of: period= as before
    await mod.YFinanceProvider().fetch(Capability.PRICES, DataRequest(symbol=normalize_symbol("PTT")))

    assert _FakeTicker.calls == [{"start": "2025-08-02", "end": "2026-08-04"}, {"period": "1y"}]


@pytest.mark.asyncio
async def test_yfinance_quote(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod.yf, "Ticker", _FakeTicker)
    provider = mod.YFinanceProvider()
    req = DataRequest(symbol=normalize_symbol("PTT"))

    result = await provider.fetch(Capability.QUOTE, req)

    assert result.data.currency == "THB"
    assert float(result.data.price) == pytest.approx(31.4)


@pytest.mark.asyncio
async def test_yfinance_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod.yf, "Ticker", _FakeTicker)
    provider = mod.YFinanceProvider()
    req = DataRequest(symbol=normalize_symbol("PTT"))

    result = await provider.fetch(Capability.PROFILE, req)

    assert result.data.name == "PTT Public Company Limited"
    assert result.data.sector == "Energy"


@pytest.mark.asyncio
async def test_yfinance_fundamentals(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod.yf, "Ticker", _FakeTicker)
    provider = mod.YFinanceProvider()
    req = DataRequest(symbol=normalize_symbol("PTT"))

    result = await provider.fetch(Capability.FUNDAMENTALS, req)

    assert result.data.metrics["trailingPE"] == pytest.approx(12.5)


@pytest.mark.asyncio
async def test_yfinance_statements(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod.yf, "Ticker", _FakeTicker)
    provider = mod.YFinanceProvider()
    req = DataRequest(symbol=normalize_symbol("PTT"))

    result = await provider.fetch(Capability.STATEMENTS, req)

    assert result.data.income["TotalRevenue"] == pytest.approx(400_000_000_000)
    assert result.data.balance["TotalAssets"] == pytest.approx(350_000_000_000)


@pytest.mark.asyncio
async def test_yfinance_analyst(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod.yf, "Ticker", _FakeTicker)
    provider = mod.YFinanceProvider()
    req = DataRequest(symbol=normalize_symbol("PTT"))

    result = await provider.fetch(Capability.ANALYST, req)

    assert result.data["mean"] == pytest.approx(35.0)


@pytest.mark.asyncio
async def test_yfinance_news(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod.yf, "Ticker", _FakeTicker)
    provider = mod.YFinanceProvider()
    req = DataRequest(symbol=normalize_symbol("PTT"))

    result = await provider.fetch(Capability.NEWS, req)

    assert result.data[0].title == "PTT reports record profit"
    assert result.data[0].source == "Reuters"


@pytest.mark.asyncio
async def test_yfinance_symbol_search(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeSearch:
        def __init__(self, query: str, max_results: int) -> None:
            self.quotes = [{"symbol": "AAPL", "longname": "Apple Inc.", "exchange": "NASDAQ", "score": 99.0}]

    monkeypatch.setattr(mod.yf, "Search", _FakeSearch)
    provider = mod.YFinanceProvider()
    req = DataRequest(params={"q": "apple"})

    result = await provider.fetch(Capability.SYMBOL_SEARCH, req)

    assert result.data[0].symbol.canonical == "AAPL"


@pytest.mark.asyncio
async def test_yfinance_insider_unsupported() -> None:
    provider = mod.YFinanceProvider()
    from app.domain.data.base import Unsupported

    with pytest.raises(Unsupported):
        await provider.fetch(Capability.INSIDER, DataRequest(symbol=normalize_symbol("AAPL")))
