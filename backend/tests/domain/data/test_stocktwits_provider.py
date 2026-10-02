from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from app.domain.data import http as http_module
from app.domain.data.base import Capability, DataRequest
from app.domain.data.providers.stocktwits_provider import StocktwitsProvider
from app.domain.models.symbol import normalize_symbol


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _instant_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(http_module.asyncio, "sleep", _instant_sleep)


FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "providers" / "stocktwits" / "ptt_stream.json"


@pytest.mark.asyncio
@respx.mock
async def test_stocktwits_parses_messages_and_sentiment() -> None:
    respx.get("https://api.stocktwits.com/api/2/streams/symbol/PTT.json").mock(
        return_value=httpx.Response(200, json=json.loads(FIXTURE.read_text()))
    )
    provider = StocktwitsProvider()
    req = DataRequest(symbol=normalize_symbol("PTT"))

    result = await provider.fetch(Capability.SOCIAL, req)

    assert result.status == "ok"
    assert len(result.data) == 2
    assert result.data[0].stance_label == "bullish"
    assert result.data[0].text == "PTT looking strong today & breaking out"


@pytest.mark.asyncio
@respx.mock
async def test_stocktwits_crypto_symbol_mapping() -> None:
    route = respx.get("https://api.stocktwits.com/api/2/streams/symbol/BTC.X.json").mock(
        return_value=httpx.Response(200, json={"messages": []})
    )
    provider = StocktwitsProvider()
    req = DataRequest(symbol=normalize_symbol("BTCUSD"))

    await provider.fetch(Capability.SOCIAL, req)

    assert route.called


@pytest.mark.asyncio
@respx.mock
async def test_stocktwits_failure_is_unavailable_not_empty_ok() -> None:
    respx.get("https://api.stocktwits.com/api/2/streams/symbol/PTT.json").mock(
        return_value=httpx.Response(500)
    )
    provider = StocktwitsProvider()
    req = DataRequest(symbol=normalize_symbol("PTT"))

    result = await provider.fetch(Capability.SOCIAL, req)

    assert result.status == "unavailable"
    assert result.data == []
