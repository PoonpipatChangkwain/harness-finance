from __future__ import annotations

import httpx
import pytest
import respx

from app.domain.data.base import Capability, DataRequest
from app.domain.data.providers import yfinance_provider as yf_mod
from app.domain.data.registry import get_router
from app.domain.models.symbol import normalize_symbol


class _FakeTicker:
    def __init__(self, symbol: str) -> None:
        self.symbol = symbol

    def get_info(self) -> dict[str, object]:
        return {"longName": "Apple Inc.", "sector": "Technology"}


@pytest.mark.asyncio
@respx.mock(assert_all_called=False)
async def test_no_env_keys_means_no_http_calls_to_keyed_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    fred_route = respx.get("https://api.stlouisfed.org/fred/series/observations").mock(
        return_value=httpx.Response(200, json={"observations": []})
    )
    finnhub_route = respx.get(url__startswith="https://finnhub.io").mock(
        return_value=httpx.Response(200, json={})
    )
    fmp_route = respx.get(url__startswith="https://financialmodelingprep.com").mock(
        return_value=httpx.Response(200, json=[])
    )
    monkeypatch.setattr(yf_mod.yf, "Ticker", _FakeTicker)

    router = get_router()

    # MACRO's only provider (fred) is unconfigured -> optional capability
    # degrades to "unavailable" without ever touching the network.
    result = await router.get(Capability.MACRO, DataRequest())
    assert result.status == "unavailable"
    assert not fred_route.called

    # PROFILE resolves via yfinance (Tier A) alone; finnhub/fmp (Tier B, no
    # key) must never be dialed even though they're later in the order.
    profile = await router.get(Capability.PROFILE, DataRequest(symbol=normalize_symbol("AAPL")))
    assert profile.provider == "yfinance"
    assert not finnhub_route.called
    assert not fmp_route.called
