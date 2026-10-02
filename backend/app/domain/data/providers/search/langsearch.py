"""LangSearch — last WEB_SEARCH failover (data-sources.md §2).

Ported from ``../dexter/src/tools/search/langsearch.ts``.
"""

from __future__ import annotations

from app.domain.data.base import Capability, DataRequest, DataResult, NoData, NotConfigured, Unsupported
from app.domain.data.http import make_client, request_with_retry
from app.domain.data.schemas import WebSearchHit
from app.domain.models.provenance import new_record
from app.domain.models.symbol import Symbol
from app.settings import get_settings

_URL = "https://api.langsearch.com/v1/web-search"


class LangSearchProvider:
    id = "langsearch"
    capabilities = frozenset({Capability.WEB_SEARCH})

    def is_configured(self) -> bool:
        return bool(get_settings().langsearch_api_key)

    def supports(self, cap: Capability, symbol: Symbol | None) -> bool:
        return cap in self.capabilities

    async def fetch(self, cap: Capability, req: DataRequest) -> DataResult:
        key = get_settings().langsearch_api_key
        if not key:
            raise NotConfigured("LANGSEARCH_API_KEY is not set")
        query = str(req.params.get("query", ""))
        if not query:
            raise Unsupported("WEB_SEARCH requires params.query")

        async with make_client() as client:
            resp = await request_with_retry(
                client,
                "POST",
                _URL,
                headers={
                    "Authorization": f"Bearer {key.get_secret_value()}",
                    "Content-Type": "application/json",
                },
                json={"query": query, "summary": True, "count": 10},
            )
        body = resp.json()
        pages = ((body.get("data") or {}).get("webPages") or {}).get("value", [])
        if not pages:
            raise NoData(f"no LangSearch results for {query!r}")

        hits = [
            WebSearchHit(
                title=p.get("name", ""),
                url=p.get("url", ""),
                snippet=p.get("summary") or p.get("snippet", "") or "",
                source="langsearch",
            )
            for p in pages
        ]
        record = new_record(
            "data", {"count": len(hits)}, source=self.id, tool="web_search", params={"query": query}
        )
        return DataResult(
            provider=self.id, capability=Capability.WEB_SEARCH, data=hits, records=[record], status="ok"
        )
