"""Exa — the first WEB_SEARCH provider in the failover chain (data-sources.md §2).

Ported concept from ``../dexter/src/tools/search/exa.ts`` (which uses the
``exa-js``/``@langchain/exa`` SDKs); called here directly over Exa's REST
API to avoid a JS-only SDK dependency.
"""

from __future__ import annotations

from datetime import datetime

from app.domain.data.base import Capability, DataRequest, DataResult, NoData, NotConfigured, Unsupported
from app.domain.data.http import make_client, request_with_retry
from app.domain.data.schemas import WebSearchHit
from app.domain.models.provenance import new_record
from app.domain.models.symbol import Symbol
from app.settings import get_settings

_BASE = "https://api.exa.ai/search"


class ExaProvider:
    id = "exa"
    capabilities = frozenset({Capability.WEB_SEARCH})

    def is_configured(self) -> bool:
        return bool(get_settings().exasearch_api_key)

    def supports(self, cap: Capability, symbol: Symbol | None) -> bool:
        return cap in self.capabilities

    async def fetch(self, cap: Capability, req: DataRequest) -> DataResult:
        key = get_settings().exasearch_api_key
        if not key:
            raise NotConfigured("EXASEARCH_API_KEY is not set")
        query = str(req.params.get("query", ""))
        if not query:
            raise Unsupported("WEB_SEARCH requires params.query")

        async with make_client() as client:
            resp = await request_with_retry(
                client,
                "POST",
                _BASE,
                headers={"x-api-key": key.get_secret_value(), "Content-Type": "application/json"},
                json={"query": query, "numResults": 5, "contents": {"highlights": True, "text": True}},
            )
        body = resp.json()
        results = body.get("results", [])
        if not results:
            raise NoData(f"no Exa results for {query!r}")

        hits = []
        for r in results:
            published_at = None
            if r.get("publishedDate"):
                try:
                    published_at = datetime.fromisoformat(r["publishedDate"].replace("Z", "+00:00"))
                except ValueError:
                    published_at = None
            snippet = " ".join(r.get("highlights", [])) or (r.get("text", "") or "")[:500]
            hits.append(
                WebSearchHit(
                    title=r.get("title", ""),
                    url=r.get("url", ""),
                    snippet=snippet,
                    published_at=published_at,
                    source="exa",
                )
            )

        record = new_record(
            "data", {"count": len(hits)}, source=self.id, tool="search", params={"query": query}
        )
        return DataResult(
            provider=self.id, capability=Capability.WEB_SEARCH, data=hits, records=[record], status="ok"
        )
