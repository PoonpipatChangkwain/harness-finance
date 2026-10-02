"""Reddit API (PRAW) — richer than the RSS feed: score, comments, subreddit.

Ported concept from ``../FinRobot/finrobot/data_source/reddit_utils.py``.
The sync PRAW client runs under ``asyncio.to_thread``. Supersedes
``reddit_rss`` in the router once ``REDDIT_CLIENT_ID``/``_SECRET`` are set
(``router.py`` ``_SUPERSEDED_BY``).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import praw

from app.domain.data.base import Capability, DataRequest, DataResult, NoData, NotConfigured
from app.domain.data.http import to_thread_retry
from app.domain.data.schemas import SocialPost
from app.domain.models.provenance import new_record
from app.domain.models.symbol import Symbol
from app.settings import get_settings

_SUBREDDITS = ("stocks", "investing", "wallstreetbets", "SecurityAnalysis", "Forex")


class RedditApiProvider:
    id = "reddit_api"
    capabilities = frozenset({Capability.SOCIAL})

    def is_configured(self) -> bool:
        settings = get_settings()
        return bool(settings.reddit_client_id and settings.reddit_client_secret)

    def supports(self, cap: Capability, symbol: Symbol | None) -> bool:
        return cap in self.capabilities and symbol is not None

    def _client(self) -> praw.Reddit:
        settings = get_settings()
        if not (settings.reddit_client_id and settings.reddit_client_secret):
            raise NotConfigured("REDDIT_CLIENT_ID/REDDIT_CLIENT_SECRET are not set")
        return praw.Reddit(
            client_id=settings.reddit_client_id.get_secret_value(),
            client_secret=settings.reddit_client_secret.get_secret_value(),
            user_agent="python:finance-harness:v0.1 (by /u/finance_harness)",
        )

    async def fetch(self, cap: Capability, req: DataRequest) -> DataResult:
        if req.symbol is None:
            raise NotConfigured("SOCIAL requires a symbol")
        query = req.symbol.canonical.split(".")[0]
        limit = int(req.params.get("limit", 10))

        def _search() -> list[dict[str, Any]]:
            client = self._client()
            found = []
            for subreddit_name in _SUBREDDITS:
                subreddit = client.subreddit(subreddit_name)
                for post in subreddit.search(query, limit=limit):
                    found.append(
                        {
                            "id": post.id,
                            "title": post.title,
                            "selftext": post.selftext,
                            "score": post.score,
                            "num_comments": post.num_comments,
                            "created_utc": post.created_utc,
                            "permalink": post.permalink,
                            "subreddit": subreddit_name,
                        }
                    )
            return found

        raw = await to_thread_retry(_search)
        assert isinstance(raw, list)
        if not raw:
            raise NoData(f"no Reddit posts for {query}")

        posts = [
            SocialPost(
                platform="reddit",
                id=str(p["id"]),
                text=str(p["title"]),
                created_at=datetime.fromtimestamp(float(p["created_utc"]), tz=UTC),
                url=f"https://reddit.com{p['permalink']}",
                metrics={"score": int(p["score"]), "num_comments": int(p["num_comments"])},
            )
            for p in raw
        ]
        record = new_record(
            "data", {"count": len(posts)}, source=self.id, tool="reddit_search", params={"q": query}
        )
        return DataResult(
            provider=self.id, capability=Capability.SOCIAL, data=posts, records=[record], status="ok"
        )
