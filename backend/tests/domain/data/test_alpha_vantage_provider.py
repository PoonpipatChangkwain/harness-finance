from __future__ import annotations

import httpx
import pytest
import respx

from app.domain.data.base import Capability, DataRequest, RateLimited
from app.domain.data.providers import alpha_vantage_provider as mod
from app.domain.models.symbol import normalize_symbol
from tests.domain.data._fake_settings import make_settings


@pytest.fixture(autouse=True)
def _configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "get_settings", lambda: make_settings(alpha_vantage_api_key="test-key"))

    async def _no_rate_limit(_provider_id: str) -> None:
        return None

    monkeypatch.setattr(mod.ratelimit, "acquire", _no_rate_limit)


@pytest.mark.asyncio
@respx.mock
async def test_alpha_vantage_daily_prices() -> None:
    respx.get("https://www.alphavantage.co/query").mock(
        return_value=httpx.Response(
            200,
            json={
                "Time Series (Daily)": {
                    "2026-09-01": {
                        "1. open": "100",
                        "2. high": "105",
                        "3. low": "99",
                        "4. close": "104",
                        "6. volume": "1000",
                    },
                    "2026-09-02": {
                        "1. open": "104",
                        "2. high": "108",
                        "3. low": "103",
                        "4. close": "107",
                        "6. volume": "1200",
                    },
                }
            },
        )
    )
    provider = mod.AlphaVantageProvider()
    req = DataRequest(symbol=normalize_symbol("AAPL"))

    result = await provider.fetch(Capability.PRICES, req)

    assert result.status == "ok"
    assert len(result.data.bars) == 2
    assert result.data.bars[0].close == pytest.approx(104)


@pytest.mark.asyncio
@respx.mock
async def test_alpha_vantage_throttle_note_becomes_rate_limited() -> None:
    note = "Thank you for using Alpha Vantage! Our standard API rate limit is 25 requests per day."
    respx.get("https://www.alphavantage.co/query").mock(return_value=httpx.Response(200, json={"Note": note}))
    provider = mod.AlphaVantageProvider()
    req = DataRequest(symbol=normalize_symbol("AAPL"))

    with pytest.raises(RateLimited):
        await provider.fetch(Capability.PRICES, req)


@pytest.mark.asyncio
@respx.mock
async def test_alpha_vantage_fundamentals() -> None:
    respx.get("https://www.alphavantage.co/query").mock(
        return_value=httpx.Response(
            200, json={"Symbol": "AAPL", "PERatio": "28.5", "Beta": "1.2", "EPS": "6.1"}
        )
    )
    provider = mod.AlphaVantageProvider()
    req = DataRequest(symbol=normalize_symbol("AAPL"))

    result = await provider.fetch(Capability.FUNDAMENTALS, req)

    assert result.data.metrics["PERatio"] == pytest.approx(28.5)


@pytest.mark.asyncio
@respx.mock
async def test_alpha_vantage_statements() -> None:
    respx.get("https://www.alphavantage.co/query").mock(
        return_value=httpx.Response(
            200,
            json={
                "annualReports": [
                    {
                        "fiscalDateEnding": "2025-09-30",
                        "totalRevenue": "400000000000",
                        "netIncome": "95000000000",
                        "grossProfit": "180000000000",
                    }
                ]
            },
        )
    )
    provider = mod.AlphaVantageProvider()
    req = DataRequest(symbol=normalize_symbol("AAPL"))

    result = await provider.fetch(Capability.STATEMENTS, req)

    assert result.data.fiscal_period == "2025-09-30"
    assert result.data.income["revenue"] == pytest.approx(400_000_000_000)


@pytest.mark.asyncio
@respx.mock
async def test_alpha_vantage_news() -> None:
    respx.get("https://www.alphavantage.co/query").mock(
        return_value=httpx.Response(
            200,
            json={
                "feed": [
                    {
                        "title": "AAPL beats estimates",
                        "url": "https://example.com/a",
                        "time_published": "20260920T120000",
                        "source": "Reuters",
                        "overall_sentiment_score": "0.4",
                    }
                ]
            },
        )
    )
    provider = mod.AlphaVantageProvider()
    req = DataRequest(symbol=normalize_symbol("AAPL"))

    result = await provider.fetch(Capability.NEWS, req)

    assert result.data[0].title == "AAPL beats estimates"
    assert result.data[0].sentiment == pytest.approx(0.4)


@pytest.mark.asyncio
async def test_alpha_vantage_forex_prices_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {}

    async def _fake_get(self: mod.AlphaVantageProvider, params: dict[str, str]) -> dict[str, object]:
        calls["params"] = params
        return {
            "Time Series FX (Daily)": {
                "2026-09-01": {"1. open": "1.1", "2. high": "1.12", "3. low": "1.09", "4. close": "1.11"}
            }
        }

    monkeypatch.setattr(mod.AlphaVantageProvider, "_get", _fake_get)
    provider = mod.AlphaVantageProvider()
    req = DataRequest(symbol=normalize_symbol("EURUSD"))

    result = await provider.fetch(Capability.PRICES, req)

    assert calls["params"]["function"] == "FX_DAILY"
    assert len(result.data.bars) == 1
