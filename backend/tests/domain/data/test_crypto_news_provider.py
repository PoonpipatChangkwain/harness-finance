from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from app.domain.data.base import Capability, DataRequest
from app.domain.data.providers.crypto_news_provider import FEEDS, CryptoNewsProvider
from app.domain.models.symbol import normalize_symbol

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "providers" / "crypto_news" / "feed.xml"


def test_only_supports_crypto_news() -> None:
    p = CryptoNewsProvider()
    assert p.supports(Capability.NEWS, normalize_symbol("BTC-USD"))
    assert not p.supports(Capability.NEWS, normalize_symbol("AAPL"))
    assert not p.supports(Capability.NEWS, normalize_symbol("GBPJPY"))
    assert not p.supports(Capability.PRICES, normalize_symbol("BTC-USD"))


@pytest.mark.asyncio
@respx.mock
async def test_symbol_mentions_come_first() -> None:
    for url in FEEDS.values():
        respx.get(url).mock(return_value=httpx.Response(200, text=FIXTURE.read_text(encoding="utf-8")))
    result = await CryptoNewsProvider().fetch(
        Capability.NEWS, DataRequest(symbol=normalize_symbol("BTC-USD"), params={"limit": 3})
    )
    assert result.status == "ok"
    assert result.data[0].title.startswith("Bitcoin ETF")
    assert result.data[0].tickers == ["BTC-USD"]
    assert result.data[0].summary == "BTC rallies"
    assert len(result.data) == 3


@pytest.mark.asyncio
@respx.mock
async def test_all_feeds_failing_is_unavailable() -> None:
    for url in FEEDS.values():
        respx.get(url).mock(return_value=httpx.Response(503))
    result = await CryptoNewsProvider().fetch(
        Capability.NEWS, DataRequest(symbol=normalize_symbol("ETH-USD"))
    )
    assert result.status == "unavailable" and result.data == []
