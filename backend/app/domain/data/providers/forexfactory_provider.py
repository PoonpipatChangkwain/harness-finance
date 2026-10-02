"""ForexFactory economic calendar (ECONOMIC_CALENDAR), no key.

Reads the public weekly JSON export that ForexFactory publishes through
``nfs.faireconomy.media`` (this week, and next week when it is available):
``{title, country (currency), date (ISO with offset), impact, forecast, previous}``.
The feed is rate limited and only changes when an event is added or released, so
each feed is kept in memory for ``_TTL_S``. It covers the current and next week
only — a request dated in the past is withheld (``Unsupported``) rather than
answered with this week's calendar.
"""

from __future__ import annotations

import time
from datetime import date, datetime
from typing import Any

from app.domain.data.base import (
    Capability,
    DataError,
    DataRequest,
    DataResult,
    NoData,
    Transient,
    Unsupported,
)
from app.domain.data.http import make_client, request_with_retry
from app.domain.data.schemas import EconomicEvent, EventImpact
from app.domain.models.provenance import new_record
from app.domain.models.symbol import Symbol

_FEEDS = (
    "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
    "https://nfs.faireconomy.media/ff_calendar_nextweek.json",
)
_TTL_S = 30 * 60
_IMPACTS: dict[str, EventImpact] = {"high": "high", "medium": "medium", "low": "low", "holiday": "holiday"}
_cache: dict[str, tuple[float, list[EconomicEvent]]] = {}


def parse_feed(rows: Any) -> list[EconomicEvent]:
    """Vendor rows → events; rows with no parseable time or impact are skipped."""
    if not isinstance(rows, list):
        raise Transient("calendar feed is not a JSON list (rate limited?)")
    events = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        impact = _IMPACTS.get(str(row.get("impact", "")).strip().lower())
        try:
            at = datetime.fromisoformat(str(row.get("date", "")))
        except ValueError:
            continue
        if impact is None or at.tzinfo is None:
            continue
        events.append(
            EconomicEvent(
                title=str(row.get("title", "")).strip(),
                currency=str(row.get("country", "")).strip().upper() or "ALL",
                at=at,
                impact=impact,
                forecast=str(row.get("forecast") or "").strip() or None,
                previous=str(row.get("previous") or "").strip() or None,
                actual=str(row.get("actual") or "").strip() or None,
            )
        )
    return events


class ForexFactoryProvider:
    id = "forexfactory"
    capabilities = frozenset({Capability.ECONOMIC_CALENDAR})

    def is_configured(self) -> bool:
        return True

    def supports(self, cap: Capability, symbol: Symbol | None) -> bool:
        return cap in self.capabilities

    async def _feed(self, url: str) -> list[EconomicEvent]:
        hit = _cache.get(url)
        if hit is not None and time.monotonic() - hit[0] < _TTL_S:
            return hit[1]
        async with make_client() as client:
            resp = await request_with_retry(client, "GET", url)
            try:
                rows = resp.json()
            except ValueError as exc:
                raise Transient("calendar feed did not return JSON (rate limited?)") from exc
        events = parse_feed(rows)
        _cache[url] = (time.monotonic(), events)
        return events

    async def fetch(self, cap: Capability, req: DataRequest) -> DataResult:
        if req.as_of is not None and req.as_of < date.today():
            raise Unsupported("the ForexFactory feed covers this and next week only; no history")
        this_week = await self._feed(_FEEDS[0])
        notes: list[str] = []
        try:
            next_week = await self._feed(_FEEDS[1])
        except DataError as exc:  # published late in the week; its absence is not a failure
            next_week = []
            notes.append(f"next week's calendar not available: {exc}")
        unique = {(e.at, e.currency, e.title): e for e in [*this_week, *next_week]}
        events = sorted(unique.values(), key=lambda e: e.at)
        if not events:
            raise NoData("empty economic calendar")
        record = new_record(
            "data",
            {"count": len(events)},
            source=self.id,
            tool="economic_calendar",
            url="https://www.forexfactory.com/calendar",
        )
        return DataResult(
            provider=self.id,
            capability=Capability.ECONOMIC_CALENDAR,
            data=events,
            records=[record],
            status="ok",
            notes=notes,
        )
