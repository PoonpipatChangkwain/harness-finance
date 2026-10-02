"""Jev (TypeSafe) social-post screening: off-topic filter + stance count.

Ported from ``../TradingAgents/tradingagents/agents/post_screen.py``: asks
two typed questions per post ("is this about the instrument?", "what
stance does the author take?"), drops posts that are clearly off-topic,
and counts stance only when Jev isn't genuinely uncertain about it. Skips
silently without ``TYPESAFE_API_KEY``; on request failure, keeps the
source's posts unscreened and returns a note instead of raising, since a
degraded SOCIAL fanout must not blow up over an optional enrichment step.
"""

from __future__ import annotations

import asyncio
import random
from typing import Any

import httpx

from app.domain.data.http import make_client
from app.domain.data.schemas import SocialPost
from app.settings import get_settings

_URL = "https://api.typesafe.ai/v1/systemone"
_DEFAULT_MODEL = "jev-latest"
_RETRY_STATUSES = (429, 529)
_ATTEMPTS = 3
_MAX_WAIT = 30.0
_TIMEOUT = 15.0
_WORKERS = 16
_OFF_TOPIC_BELOW = 0.3
_STANCE_CONFIDENCE = 0.5

_QUESTIONS: dict[str, dict[str, Any]] = {
    "about": {
        "type": "noul",
        "instructions": "Is `post` about `instrument`: the company, its stock, its products or its outlook?",
        "criteria": {
            "true": "`post` discusses `instrument` itself.",
            "false": "`post` names `instrument` only in passing or in a list of tickers, is spam or "
            "promotion, or is about a different company.",
        },
    },
    "stance": {
        "type": "choice",
        "instructions": "What does the author of `post` expect for the stock price of `instrument`?",
        "criteria": {
            "bullish": "The author expects `instrument`'s stock to rise, or is buying or holding it long.",
            "bearish": "The author expects `instrument`'s stock to fall, or is selling or shorting it.",
            "neutral": "The author gives no view of their own on `instrument`'s stock.",
        },
    },
}


class TypeSafeError(Exception):
    pass


class JevScreenResult:
    def __init__(self, kept: list[SocialPost], stance_counts: dict[str, int], note: str | None):
        self.kept = kept
        self.stance_counts = stance_counts
        self.note = note


def _retry_after(resp: httpx.Response) -> float | None:
    try:
        return min(max(0.0, float(resp.headers.get("retry-after", ""))), _MAX_WAIT)
    except (TypeError, ValueError):
        return None


async def _system_one(client: httpx.AsyncClient, api_key: str, state: str) -> dict[str, Any]:
    body = {"state": state, "model": _DEFAULT_MODEL, "questions": _QUESTIONS}
    headers = {"Authorization": f"Bearer {api_key}"}
    backoff = 1.0
    last_error = "unknown"
    for attempt in range(_ATTEMPTS):
        if attempt:
            await asyncio.sleep(backoff * random.uniform(0.8, 1.2))
            backoff *= 2
        try:
            resp = await client.post(_URL, json=body, headers=headers, timeout=_TIMEOUT)
        except httpx.RequestError as exc:
            last_error = type(exc).__name__
            continue
        if resp.status_code == 200:
            payload = resp.json()
            answers = payload.get("answers", {})
            if all(answers.get(q, {}).get("type") == spec["type"] for q, spec in _QUESTIONS.items()):
                return dict(answers)
            last_error = "malformed response"
            break
        last_error = f"HTTP {resp.status_code}"
        if resp.status_code not in _RETRY_STATUSES:
            break
        wait = _retry_after(resp)
        if wait is not None:
            await asyncio.sleep(wait)
    raise TypeSafeError(last_error)


async def jev_screen(instrument: str, posts: list[SocialPost]) -> JevScreenResult | None:
    """Screen ``posts`` for relevance to ``instrument``; None if not configured."""
    api_key = get_settings().typesafe_api_key
    if not api_key:
        return None
    if not posts:
        return JevScreenResult(kept=[], stance_counts={}, note=None)

    semaphore = asyncio.Semaphore(_WORKERS)

    async def _ask(client: httpx.AsyncClient, post: SocialPost) -> dict[str, Any] | None:
        async with semaphore:
            try:
                return await _system_one(
                    client, api_key.get_secret_value(), f"instrument: {instrument}\npost: {post.text}"
                )
            except TypeSafeError:
                return None

    async with make_client() as client:
        answers = await asyncio.gather(*(_ask(client, p) for p in posts))

    if all(a is None for a in answers):
        return JevScreenResult(kept=posts, stance_counts={}, note="screening unavailable")

    kept: list[SocialPost] = []
    stance_counts: dict[str, int] = {"bullish": 0, "bearish": 0, "neutral": 0}
    for post, answer in zip(posts, answers, strict=True):
        if answer is None:
            kept.append(post)
            continue
        about_score = answer.get("about", {}).get("noul", 1.0)
        if about_score < _OFF_TOPIC_BELOW:
            continue
        stance_answer = answer.get("stance", {})
        choice = stance_answer.get("choice")
        confidence = stance_answer.get("confidence", 0.0)
        stance = (
            choice
            if choice in ("bullish", "bearish", "neutral") and confidence >= _STANCE_CONFIDENCE
            else None
        )
        if stance:
            stance_counts[stance] += 1
            post = post.model_copy(update={"stance_label": stance if stance != "neutral" else None})
        kept.append(post)

    return JevScreenResult(kept=kept, stance_counts=stance_counts, note=None)
