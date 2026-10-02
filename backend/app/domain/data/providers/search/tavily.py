"""Tavily — WEB_SEARCH failover after Exa/Perplexity.

Ported concept from ``../dexter/src/tools/search/tavily.ts`` (which uses
``@langchain/tavily``); called here over Tavily's REST API directly.
"""

from __future__ import annotations

from app.domain.data.base import Capability, DataRequest, DataResult, NoData, NotConfigured, Unsupported
from app.domain.data.http import make_client, request_with_retry
from app.domain.data.schemas import WebSearchHit
from app.domain.models.provenance import new_record
from app.domain.models.symbol import Symbol
from app.settings import get_settings

_URL = "https://api.tavily.com/search"


class TavilyProvider:
    id = "tavily"
    capabilities = frozenset({Capability.WEB_SEARCH})

    def is_configured(self) -> bool:
        return bool(get_settings().tavily_api_key)

    def supports(self, cap: Capability, symbol: Symbol | None) -> bool:
        return cap in self.capabilities

    async def fetch(self, cap: Capability, req: DataRequest) -> DataResult:
        key = get_settings().tavily_api_key
        if not key:
            raise NotConfigured("TAVILY_API_KEY is not set")
        query = str(req.params.get("query", ""))
        if not query:
            raise Unsupported("WEB_SEARCH requires params.query")

        async with make_client() as client:
            resp = await request_with_retry(
                client,
                "POST",
                _URL,
                json={"api_key": key.get_secret_value(), "query": query, "max_results": 5},
            )
        body = resp.json()
        results = body.get("results", [])
        if not results:
            raise NoData(f"no Tavily results for {query!r}")

        hits = [
            WebSearchHit(
                title=r.get("title", ""),
                url=r.get("url", ""),
                snippet=(r.get("content", "") or "")[:500],
                source="tavily",
            )
            for r in results
        ]
        record = new_record(
            "data", {"count": len(hits)}, source=self.id, tool="search", params={"query": query}
        )
        return DataResult(
            provider=self.id, capability=Capability.WEB_SEARCH, data=hits, records=[record], status="ok"
        )
