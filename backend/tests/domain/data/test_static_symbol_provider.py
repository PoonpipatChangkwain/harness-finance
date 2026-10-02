from __future__ import annotations

import pytest

from app.domain.data.base import Capability, DataRequest
from app.domain.data.providers.static_symbol_provider import StaticSymbolProvider


@pytest.mark.asyncio
async def test_static_search_finds_thai_stock() -> None:
    provider = StaticSymbolProvider()
    result = await provider.fetch(Capability.SYMBOL_SEARCH, DataRequest(params={"q": "ptt"}))

    symbols = {m.symbol.canonical for m in result.data}
    assert "PTT.BK" in symbols
    assert "PTTEP.BK" in symbols


@pytest.mark.asyncio
async def test_static_search_finds_us_stock_and_forex() -> None:
    provider = StaticSymbolProvider()
    aapl = await provider.fetch(Capability.SYMBOL_SEARCH, DataRequest(params={"q": "aapl"}))
    assert aapl.data[0].symbol.canonical == "AAPL"

    eur = await provider.fetch(Capability.SYMBOL_SEARCH, DataRequest(params={"q": "eurusd"}))
    assert eur.data[0].symbol.canonical == "EURUSD=X"


@pytest.mark.asyncio
async def test_static_search_no_match_is_unavailable() -> None:
    provider = StaticSymbolProvider()
    result = await provider.fetch(Capability.SYMBOL_SEARCH, DataRequest(params={"q": "zzzznotasymbol"}))
    assert result.status == "unavailable"
    assert result.data == []
