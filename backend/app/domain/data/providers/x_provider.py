"""X (Twitter) recent search — Tier-C paid, requires a bearer token.

Ported from ``../dexter/src/tools/search/x-search.ts``: waits on a 429
using ``x-rate-limit-reset`` (capped at 60s, up to 3 retries) instead of
failing over immediately, since X's rate window resets predictably.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from app.domain.data.base import Capability, DataRequest, DataResult, NoData, NotConfigured, RateLimited
from app.domain.data.http import make_client
from app.domain.data.schemas import SocialPost
from app.domain.models.provenance import new_record
from app.domain.models.symbol import Symbol
from app.settings import get_settings

_BASE = "https://api.x.com/2"
_TWEET_FIELDS = "tweet.fields=created_at,public_metrics,author_id&expansions=author_id&user.fields=username"
_MAX_RATE_LIMIT_RETRIES = 3
_MAX_RATE_LIMIT_WAIT_S = 60.0


class XProvider:
    id = "x_api"
    capabilities = frozenset({Capability.SOCIAL})

    def is_configured(self) -> bool:
        return bool(get_settings().x_bearer_token)

    def supports(self, cap: Capability, symbol: Symbol | None) -> bool:
        return cap in self.capabilities

    def _token(self) -> str:
        token = get_settings().x_bearer_token
        if not token:
            raise NotConfigured("X_BEARER_TOKEN is not set")
        return token.get_secret_value()

    async def _search(self, query: str, attempt: int = 0) -> dict[str, Any]:
        async with make_client() as client:
            resp = await client.get(
                f"{_BASE}/tweets/search/recent?{_TWEET_FIELDS}",
                params={"query": query, "max_results": 25},
                headers={"Authorization": f"Bearer {self._token()}"},
            )
        if resp.status_code == 429:
            reset_header = resp.headers.get("x-rate-limit-reset")
            wait_s = 60.0
            if reset_header:
                wait_s = max(float(reset_header) - time.time(), 1.0)
            if attempt < _MAX_RATE_LIMIT_RETRIES and wait_s <= _MAX_RATE_LIMIT_WAIT_S:
                await asyncio.sleep(wait_s)
                return await self._search(query, attempt + 1)
            raise RateLimited(f"X API rate limited, resets in {wait_s:.0f}s", retry_after=wait_s)
        resp.raise_for_status()
        return dict(resp.json())

    async def fetch(self, cap: Capability, req: DataRequest) -> DataResult:
        if req.symbol is None:
            raise NotConfigured("SOCIAL requires a symbol")
        ticker = req.symbol.canonical.split(".")[0].rstrip("=X")
        query = f"(${ticker} OR #{ticker}) -is:retweet (lang:en OR lang:th)"

        body = await self._search(query)
        raw_tweets: list[dict[str, Any]] = body.get("data") or []
        includes: dict[str, Any] = body.get("includes") or {}
        users = {u["id"]: u for u in includes.get("users", [])}

        posts = []
        for t in raw_tweets:
            author = users.get(t.get("author_id"), {})
            metrics = t.get("public_metrics", {})
            posts.append(
                SocialPost(
                    platform="x",
                    id=t["id"],
                    text=t.get("text", ""),
                    author=author.get("username"),
                    created_at=t.get("created_at"),
                    url=f"https://x.com/{author.get('username', 'i')}/status/{t['id']}",
                    metrics={
                        "likes": metrics.get("like_count", 0),
                        "retweets": metrics.get("retweet_count", 0),
                        "replies": metrics.get("reply_count", 0),
                    },
                )
            )
        if not posts:
            raise NoData(f"no X posts matching {ticker}")
        record = new_record(
            "data",
            {"count": len(posts)},
            source=self.id,
            tool="tweets_search_recent",
            params={"query": query},
        )
        return DataResult(
            provider=self.id, capability=Capability.SOCIAL, data=posts, records=[record], status="ok"
        )
