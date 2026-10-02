from __future__ import annotations

import httpx
import pytest
import respx

from app.domain.data.base import Capability, DataRequest, RateLimited
from app.domain.data.providers import x_provider as mod
from app.domain.models.symbol import normalize_symbol
from tests.domain.data._fake_settings import make_settings


@pytest.fixture(autouse=True)
def _configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "get_settings", lambda: make_settings(x_bearer_token="test-token"))

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(mod.asyncio, "sleep", _no_sleep)


@pytest.mark.asyncio
@respx.mock
async def test_x_search_parses_tweets_with_authors() -> None:
    respx.get(url__startswith="https://api.x.com/2/tweets/search/recent").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "1",
                        "text": "$AAPL breaking out",
                        "author_id": "u1",
                        "public_metrics": {"like_count": 5},
                    }
                ],
                "includes": {"users": [{"id": "u1", "username": "trader1"}]},
            },
        )
    )
    provider = mod.XProvider()
    req = DataRequest(symbol=normalize_symbol("AAPL"))

    result = await provider.fetch(Capability.SOCIAL, req)

    assert result.data[0].author == "trader1"
    assert result.data[0].metrics["likes"] == 5


@pytest.mark.asyncio
@respx.mock
async def test_x_rate_limited_after_max_retries() -> None:
    respx.get(url__startswith="https://api.x.com/2/tweets/search/recent").mock(
        return_value=httpx.Response(429, headers={"x-rate-limit-reset": "999999999999"})
    )
    provider = mod.XProvider()
    req = DataRequest(symbol=normalize_symbol("AAPL"))

    with pytest.raises(RateLimited):
        await provider.fetch(Capability.SOCIAL, req)
