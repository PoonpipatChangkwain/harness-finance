"""Stance counts over social posts (F1): the tally is code, the interpretation is the LLM's.

Concept from the stance summary in ``../TradingAgents/tradingagents/agents/
analysts/sentiment_analyst.py``; aggregate rows (``kind="aggregate"``) are
vendor-side summaries and are not counted as posts.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.domain.compute.base import ComputeResult, compute_record
from app.domain.data.schemas import SocialPost

_VERSION = "v1"


def stance_counts(
    posts: Iterable[SocialPost], *, inputs: list[str] | None = None
) -> ComputeResult[dict[str, object]]:
    counts = {"bullish": 0, "bearish": 0, "neutral": 0, "unlabeled": 0}
    by_platform: dict[str, int] = {}
    for post in posts:
        if post.kind != "post":
            continue
        counts[post.stance_label or "unlabeled"] += 1
        by_platform[post.platform] = by_platform.get(post.platform, 0) + 1
    labeled = counts["bullish"] + counts["bearish"]
    value: dict[str, object] = {
        **counts,
        "total": sum(counts.values()),
        "bull_ratio": counts["bullish"] / labeled if labeled else None,
        "by_platform": by_platform,
    }
    return ComputeResult(
        value=value,
        records=[compute_record(f"compute.sentiment.stance_counts@{_VERSION}", value, inputs=inputs)],
    )
