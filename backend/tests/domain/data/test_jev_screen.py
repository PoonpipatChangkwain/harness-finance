from __future__ import annotations

import httpx
import pytest
import respx

from app.domain.data.postprocess import jev_screen as mod
from app.domain.data.schemas import SocialPost
from tests.domain.data._fake_settings import make_settings


def _post(i: int, text: str) -> SocialPost:
    return SocialPost(platform="stocktwits", id=str(i), text=text)


@pytest.mark.asyncio
async def test_jev_screen_skips_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "get_settings", lambda: make_settings())
    result = await mod.jev_screen("AAPL (Apple)", [_post(1, "AAPL to the moon")])
    assert result is None


@pytest.mark.asyncio
@respx.mock
async def test_jev_screen_drops_off_topic_and_counts_stance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "get_settings", lambda: make_settings(typesafe_api_key="k"))

    def _answer_for(request: httpx.Request) -> httpx.Response:
        body = request.content.decode()
        if "off topic post" in body:
            return httpx.Response(
                200,
                json={
                    "answers": {
                        "about": {"type": "noul", "noul": 0.05},
                        "stance": {"type": "choice", "choice": "neutral", "confidence": 0.9},
                    }
                },
            )
        return httpx.Response(
            200,
            json={
                "answers": {
                    "about": {"type": "noul", "noul": 0.9},
                    "stance": {"type": "choice", "choice": "bullish", "confidence": 0.8},
                }
            },
        )

    respx.post("https://api.typesafe.ai/v1/systemone").mock(side_effect=_answer_for)

    posts = [
        _post(1, "AAPL earnings looking great, staying long"),
        _post(2, "totally off topic post about weather"),
    ]
    result = await mod.jev_screen("AAPL (Apple)", posts)

    assert result is not None
    assert len(result.kept) == 1
    assert result.kept[0].id == "1"
    assert result.stance_counts["bullish"] == 1


@pytest.mark.asyncio
@respx.mock
async def test_jev_screen_all_requests_fail_keeps_posts_with_note(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "get_settings", lambda: make_settings(typesafe_api_key="k"))
    respx.post("https://api.typesafe.ai/v1/systemone").mock(return_value=httpx.Response(500))

    posts = [_post(1, "AAPL post")]
    result = await mod.jev_screen("AAPL (Apple)", posts)

    assert result is not None
    assert result.note == "screening unavailable"
    assert result.kept == posts
