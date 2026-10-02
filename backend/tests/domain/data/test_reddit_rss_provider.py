from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from app.domain.data.base import Capability, DataRequest
from app.domain.data.providers.reddit_rss_provider import RedditRssProvider
from app.domain.models.symbol import normalize_symbol

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "providers" / "reddit_rss" / "search.xml"


@pytest.mark.asyncio
@respx.mock
async def test_reddit_rss_parses_atom_entries() -> None:
    respx.get("https://www.reddit.com/search.rss").mock(
        return_value=httpx.Response(200, text=FIXTURE.read_text(encoding="utf-8"))
    )
    provider = RedditRssProvider()
    req = DataRequest(symbol=normalize_symbol("AAPL"))

    result = await provider.fetch(Capability.SOCIAL, req)

    assert result.status == "ok"
    assert len(result.data) == 2
    assert result.data[0].author == "investor42"
    assert "AAPL" in result.data[0].text


@pytest.mark.asyncio
@respx.mock
async def test_reddit_rss_rate_limited_is_unavailable() -> None:
    respx.get("https://www.reddit.com/search.rss").mock(return_value=httpx.Response(429))
    provider = RedditRssProvider()
    req = DataRequest(symbol=normalize_symbol("AAPL"))

    result = await provider.fetch(Capability.SOCIAL, req)

    assert result.status == "unavailable"
    assert result.data == []
