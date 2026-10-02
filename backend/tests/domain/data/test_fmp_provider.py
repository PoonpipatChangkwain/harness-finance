from __future__ import annotations

import httpx
import pytest
import respx

from app.domain.data.base import Capability, DataRequest, NotConfigured
from app.domain.data.providers import fmp_provider as mod
from app.domain.models.symbol import normalize_symbol
from tests.domain.data._fake_settings import make_settings


@pytest.fixture(autouse=True)
def _configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "get_settings", lambda: make_settings(fmp_api_key="test-key"))


@pytest.mark.asyncio
@respx.mock
async def test_fmp_analyst_consensus_and_peers() -> None:
    respx.get("https://financialmodelingprep.com/api/v4/price-target-consensus").mock(
        return_value=httpx.Response(
            200, json=[{"targetHigh": 300, "targetLow": 200, "targetConsensus": 250, "targetMedian": 245}]
        )
    )
    respx.get("https://financialmodelingprep.com/api/v4/stock_peers").mock(
        return_value=httpx.Response(200, json=[{"symbol": "AAPL", "peersList": ["MSFT", "GOOGL"]}])
    )
    provider = mod.FmpProvider()
    req = DataRequest(symbol=normalize_symbol("AAPL"))

    result = await provider.fetch(Capability.ANALYST, req)

    assert result.data["target_consensus"] == 250
    assert result.data["peers"] == ["MSFT", "GOOGL"]


@pytest.mark.asyncio
@respx.mock
async def test_fmp_invalid_key_body_maps_to_not_configured() -> None:
    respx.get("https://financialmodelingprep.com/api/v4/price-target-consensus").mock(
        return_value=httpx.Response(200, json={"Error Message": "Invalid API KEY."})
    )
    provider = mod.FmpProvider()
    req = DataRequest(symbol=normalize_symbol("AAPL"))

    with pytest.raises(NotConfigured):
        await provider.fetch(Capability.ANALYST, req)


@pytest.mark.asyncio
@respx.mock
async def test_fmp_fundamentals_merges_metrics_and_ratios() -> None:
    respx.get("https://financialmodelingprep.com/api/v3/key-metrics-ttm/AAPL").mock(
        return_value=httpx.Response(200, json=[{"peRatioTTM": 28.5}])
    )
    respx.get("https://financialmodelingprep.com/api/v3/ratios-ttm/AAPL").mock(
        return_value=httpx.Response(200, json=[{"currentRatioTTM": 1.5}])
    )
    provider = mod.FmpProvider()
    req = DataRequest(symbol=normalize_symbol("AAPL"))

    result = await provider.fetch(Capability.FUNDAMENTALS, req)

    assert result.data.metrics["peRatioTTM"] == pytest.approx(28.5)
    assert result.data.metrics["currentRatioTTM"] == pytest.approx(1.5)
