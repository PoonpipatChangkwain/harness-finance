from __future__ import annotations

import httpx
import pytest
import respx

from app.domain.data.base import Capability, DataRequest
from app.domain.data.providers import adanos_provider as mod
from app.domain.models.symbol import normalize_symbol
from tests.domain.data._fake_settings import make_settings


@pytest.fixture(autouse=True)
def _configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "get_settings", lambda: make_settings(adanos_api_key="test-key"))


@pytest.mark.asyncio
@respx.mock
async def test_adanos_partial_platform_outage_still_yields_snapshot() -> None:
    respx.get("https://api.adanos.org/reddit/stocks/v1/compare").mock(
        return_value=httpx.Response(
            200, json={"stocks": [{"ticker": "AAPL", "buzz_score": 10, "bullish_pct": 60, "mentions": 50}]}
        )
    )
    respx.get("https://api.adanos.org/x/stocks/v1/compare").mock(return_value=httpx.Response(500))
    respx.get("https://api.adanos.org/polymarket/stocks/v1/compare").mock(
        return_value=httpx.Response(200, json={"stocks": []})
    )
    provider = mod.AdanosProvider()
    req = DataRequest(symbol=normalize_symbol("AAPL"))

    result = await provider.fetch(Capability.SOCIAL, req)

    assert result.status == "ok"
    post = result.data[0]
    assert post.kind == "aggregate"
    assert post.stance_label == "bullish"
