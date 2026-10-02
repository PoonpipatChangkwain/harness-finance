"""Look-ahead-safe date-window filtering for dated content.

Ported from ``../TradingAgents/tradingagents/dataflows/date_window.py``.
News, StockTwits and Reddit items must be trimmed to the analysis window so
a historical/backtest run never sees content published after its as-of
date. Every timestamp is normalized to UTC; the window is half-open
``[start, end + 1 day)`` so an item stamped exactly at midnight after
``end`` cannot leak in.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta


def to_utc(dt: datetime) -> datetime:
    """Normalize a datetime to UTC-aware; a naive value is assumed to be UTC."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def in_window(pub_dt: datetime | None, start_dt: datetime, end_dt: datetime) -> bool:
    """Whether an item belongs in the half-open window ``[start, end + 1 day)``.

    ``pub_dt`` of ``None`` (undated item) is kept only when the window
    reaches the present — a live run — since in a backtest we cannot prove
    an undated item isn't from the future.
    """
    end = to_utc(end_dt)
    if pub_dt is not None:
        return to_utc(start_dt) <= to_utc(pub_dt) < end + timedelta(days=1)
    return end >= datetime.now(UTC) - timedelta(days=1)


def coverage_gap(
    dates: Iterable[datetime | None], start_date: date, end_date: date, source: str, subject: str
) -> str | None:
    """A note explaining an empty result window a feed never fully observed, else None.

    Some feeds (Yahoo news, Reddit RSS, StockTwits) return only their
    latest items regardless of the window requested, so "nothing found"
    over a window they never observed would misreport an absence nobody
    actually saw.
    """
    now = datetime.now(UTC)
    oldest = min((to_utc(d) for d in dates if d is not None), default=now)
    if end_date > now.date():
        reason = "the window extends past today"
    elif oldest.date() > start_date:
        reason = "it only serves recent items"
    else:
        return None
    return (
        f"<{source} unavailable for {start_date}..{end_date}: {reason}, "
        f"so this is not an absence of {subject}>"
    )
