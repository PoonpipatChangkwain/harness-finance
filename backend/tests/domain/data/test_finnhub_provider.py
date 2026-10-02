from __future__ import annotations

import httpx
import pytest
import respx

from app.domain.data.base import Capability, DataRequest, NoData
from app.domain.data.providers import finnhub_provider as mod
from app.domain.models.symbol import normalize_symbol
from tests.domain.data._fake_settings import make_settings


@pytest.fixture(autouse=True)
def _configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "get_settings", lambda: make_settings(finnhub_api_key="test-key"))


@pytest.mark.asyncio
@respx.mock
async def test_finnhub_profile() -> None:
    respx.get("https://finnhub.io/api/v1/stock/profile2").mock(
        return_value=httpx.Response(
            200,
            json={
                "name": "Apple Inc",
                "finnhubIndustry": "Technology",
                "exchange": "NASDAQ",
                "marketCapitalization": 3000000,
            },
        )
    )
    provider = mod.FinnhubProvider()
    req = DataRequest(symbol=normalize_symbol("AAPL"))

    result = await provider.fetch(Capability.PROFILE, req)

    assert result.data.name == "Apple Inc"
    assert result.data.industry == "Technology"


@pytest.mark.asyncio
@respx.mock
async def test_finnhub_quote_no_data_raises() -> None:
    respx.get("https://finnhub.io/api/v1/quote").mock(return_value=httpx.Response(200, json={"c": 0}))
    provider = mod.FinnhubProvider()
    req = DataRequest(symbol=normalize_symbol("ZZZZ"))

    with pytest.raises(NoData):
        await provider.fetch(Capability.QUOTE, req)


@pytest.mark.asyncio
@respx.mock
async def test_finnhub_quote_ok() -> None:
    respx.get("https://finnhub.io/api/v1/quote").mock(
        return_value=httpx.Response(200, json={"c": 231.5, "d": 1.2, "dp": 0.52, "t": 1758700000})
    )
    provider = mod.FinnhubProvider()
    req = DataRequest(symbol=normalize_symbol("AAPL"))

    result = await provider.fetch(Capability.QUOTE, req)

    assert float(result.data.price) == pytest.approx(231.5)


@pytest.mark.asyncio
@respx.mock
async def test_finnhub_news() -> None:
    respx.get("https://finnhub.io/api/v1/company-news").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": 1,
                    "headline": "AAPL launches new product",
                    "url": "https://x.test",
                    "datetime": 1758700000,
                    "source": "Reuters",
                }
            ],
        )
    )
    provider = mod.FinnhubProvider()
    req = DataRequest(symbol=normalize_symbol("AAPL"))

    result = await provider.fetch(Capability.NEWS, req)

    assert result.data[0].title == "AAPL launches new product"


@pytest.mark.asyncio
@respx.mock
async def test_finnhub_fundamentals() -> None:
    respx.get("https://finnhub.io/api/v1/stock/metric").mock(
        return_value=httpx.Response(200, json={"metric": {"peBasicExclExtraTTM": 28.5, "roeTTM": 0.35}})
    )
    provider = mod.FinnhubProvider()
    req = DataRequest(symbol=normalize_symbol("AAPL"))

    result = await provider.fetch(Capability.FUNDAMENTALS, req)

    assert result.data.metrics["peBasicExclExtraTTM"] == pytest.approx(28.5)


@pytest.mark.asyncio
async def test_finnhub_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.domain.data.base import NotConfigured
    from tests.domain.data._fake_settings import make_settings

    monkeypatch.setattr(mod, "get_settings", lambda: make_settings())
    provider = mod.FinnhubProvider()
    req = DataRequest(symbol=normalize_symbol("AAPL"))

    with pytest.raises(NotConfigured):
        await provider.fetch(Capability.QUOTE, req)
