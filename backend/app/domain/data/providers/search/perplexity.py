"""Perplexity (Sonar) — grounded, citation-backed web search answer.

Ported from ``../dexter/src/tools/search/perplexity.ts``. Unlike Exa/Tavily/
LangSearch, Perplexity returns a synthesized answer plus citations rather
than raw hits; the answer is kept as the first hit's snippet and every
citation URL becomes its own ``WebSearchHit`` so downstream code has a
uniform shape regardless of provider.
"""

from __future__ import annotations

from app.domain.data.base import Capability, DataRequest, DataResult, NoData, NotConfigured, Unsupported
from app.domain.data.http import make_client, request_with_retry
from app.domain.data.schemas import WebSearchHit
from app.domain.models.provenance import new_record
from app.domain.models.symbol import Symbol
from app.settings import get_settings

_URL = "https://api.perplexity.ai/chat/completions"
_MODEL = "sonar"


class PerplexityProvider:
    id = "perplexity"
    capabilities = frozenset({Capability.WEB_SEARCH})

    def is_configured(self) -> bool:
        return bool(get_settings().perplexity_api_key)

    def supports(self, cap: Capability, symbol: Symbol | None) -> bool:
        return cap in self.capabilities

    async def fetch(self, cap: Capability, req: DataRequest) -> DataResult:
        key = get_settings().perplexity_api_key
        if not key:
            raise NotConfigured("PERPLEXITY_API_KEY is not set")
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
                json={"model": _MODEL, "messages": [{"role": "user", "content": query}], "max_tokens": 2048},
            )
        body = resp.json()
        answer = ((body.get("choices") or [{}])[0].get("message") or {}).get("content", "")
        search_results = body.get("search_results") or []
        if not answer and not search_results:
            raise NoData(f"no Perplexity answer for {query!r}")

        hits = [
            WebSearchHit(
                title="Perplexity answer",
                url=search_results[0]["url"] if search_results else "",
                snippet=answer[:1000],
                source="perplexity",
            )
        ]
        for r in search_results:
            hits.append(
                WebSearchHit(
                    title=r.get("title", ""),
                    url=r.get("url", ""),
                    snippet=r.get("snippet", "") or "",
                    source="perplexity",
                )
            )

        record = new_record(
            "data", {"count": len(hits)}, source=self.id, tool="chat_completions", params={"query": query}
        )
        return DataResult(
            provider=self.id, capability=Capability.WEB_SEARCH, data=hits, records=[record], status="ok"
        )
