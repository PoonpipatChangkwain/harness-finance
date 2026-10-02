from __future__ import annotations

import pytest

from app.domain.data.base import Capability, DataRequest
from app.domain.data.providers import reddit_api_provider as mod
from app.domain.models.symbol import normalize_symbol
from tests.domain.data._fake_settings import make_settings


class _FakePost:
    def __init__(self, i: int) -> None:
        self.id = f"p{i}"
        self.title = f"Post about AAPL #{i}"
        self.selftext = ""
        self.score = 10 * i
        self.num_comments = i
        self.created_utc = 1758700000 + i
        self.permalink = f"/r/stocks/comments/p{i}/"


class _FakeSubreddit:
    def search(self, query: str, limit: int):
        return [_FakePost(1), _FakePost(2)]


class _FakeReddit:
    def __init__(self, **kwargs: object) -> None:
        pass

    def subreddit(self, name: str) -> _FakeSubreddit:
        return _FakeSubreddit()


@pytest.fixture(autouse=True)
def _configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        mod, "get_settings", lambda: make_settings(reddit_client_id="id", reddit_client_secret="secret")
    )
    monkeypatch.setattr(mod, "praw", type("praw", (), {"Reddit": _FakeReddit}))


@pytest.mark.asyncio
async def test_reddit_api_aggregates_across_subreddits() -> None:
    provider = mod.RedditApiProvider()
    req = DataRequest(symbol=normalize_symbol("AAPL"), params={"limit": 2})

    result = await provider.fetch(Capability.SOCIAL, req)

    assert result.status == "ok"
    assert len(result.data) == 2 * len(mod._SUBREDDITS)
    assert result.data[0].metrics["score"] == 10
