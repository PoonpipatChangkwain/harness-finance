from __future__ import annotations

import httpx
import pytest
import respx

from app.domain.data.base import Capability, DataRequest, NoData
from app.domain.data.providers.search import exa as exa_mod
from app.domain.data.providers.search import langsearch as langsearch_mod
from app.domain.data.providers.search import perplexity as perplexity_mod
from app.domain.data.providers.search import tavily as tavily_mod
from tests.domain.data._fake_settings import make_settings


@pytest.mark.asyncio
@respx.mock
async def test_exa_search(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(exa_mod, "get_settings", lambda: make_settings(exasearch_api_key="k"))
    respx.post("https://api.exa.ai/search").mock(
        return_value=httpx.Response(
            200, json={"results": [{"title": "T", "url": "https://x.test", "highlights": ["snippet"]}]}
        )
    )
    provider = exa_mod.ExaProvider()
    result = await provider.fetch(Capability.WEB_SEARCH, DataRequest(params={"query": "PTT outlook"}))
    assert result.data[0].url == "https://x.test"


@pytest.mark.asyncio
@respx.mock
async def test_tavily_search(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tavily_mod, "get_settings", lambda: make_settings(tavily_api_key="k"))
    respx.post("https://api.tavily.com/search").mock(
        return_value=httpx.Response(
            200, json={"results": [{"title": "T", "url": "https://y.test", "content": "body"}]}
        )
    )
    provider = tavily_mod.TavilyProvider()
    result = await provider.fetch(Capability.WEB_SEARCH, DataRequest(params={"query": "AAPL news"}))
    assert result.data[0].snippet == "body"


@pytest.mark.asyncio
@respx.mock
async def test_perplexity_search_returns_answer_and_citations(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(perplexity_mod, "get_settings", lambda: make_settings(perplexity_api_key="k"))
    respx.post("https://api.perplexity.ai/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "The Fed is expected to cut rates."}}],
                "search_results": [{"title": "Fed news", "url": "https://z.test", "snippet": "..."}],
            },
        )
    )
    provider = perplexity_mod.PerplexityProvider()
    result = await provider.fetch(Capability.WEB_SEARCH, DataRequest(params={"query": "fed rate cut"}))
    assert "Fed" in result.data[0].snippet
    assert result.data[1].url == "https://z.test"


@pytest.mark.asyncio
@respx.mock
async def test_langsearch_no_results_raises_no_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(langsearch_mod, "get_settings", lambda: make_settings(langsearch_api_key="k"))
    respx.post("https://api.langsearch.com/v1/web-search").mock(
        return_value=httpx.Response(200, json={"data": {"webPages": {"value": []}}})
    )
    provider = langsearch_mod.LangSearchProvider()
    with pytest.raises(NoData):
        await provider.fetch(Capability.WEB_SEARCH, DataRequest(params={"query": "obscure query"}))
