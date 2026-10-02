from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import pytest
import respx

from app.domain.data.base import Capability, DataRequest, NoData, Unsupported
from app.domain.data.providers.polymarket_provider import PolymarketProvider

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "providers" / "polymarket" / "markets.json"


@pytest.mark.asyncio
@respx.mock
async def test_polymarket_filters_closed_and_ranks_by_volume() -> None:
    respx.get("https://gamma-api.polymarket.com/markets").mock(
        return_value=httpx.Response(200, json=json.loads(FIXTURE.read_text()))
    )
    provider = PolymarketProvider()

    result = await provider.fetch(Capability.PREDICTION_MARKETS, DataRequest(params={"query": "fed"}))

    assert result.status == "ok"
    questions = [m.question for m in result.data]
    assert "Already resolved market" not in questions
    assert questions[0] == "Will the Fed cut rates in December?"
    assert result.data[0].outcomes[0].prob == pytest.approx(0.62)


@pytest.mark.asyncio
@respx.mock
async def test_polymarket_no_matches_raises_no_data() -> None:
    respx.get("https://gamma-api.polymarket.com/markets").mock(return_value=httpx.Response(200, json=[]))
    provider = PolymarketProvider()

    with pytest.raises(NoData):
        await provider.fetch(Capability.PREDICTION_MARKETS, DataRequest())


@pytest.mark.asyncio
async def test_polymarket_withholds_historical_dates() -> None:
    provider = PolymarketProvider()
    with pytest.raises(Unsupported):
        await provider.fetch(Capability.PREDICTION_MARKETS, DataRequest(as_of=date(2020, 1, 1)))
