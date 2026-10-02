"""Economic calendar tool (ForexFactory): scheduled releases that can move an instrument.

For short-term trading a CPI, NFP or central-bank decision in the next hours matters more
than any indicator. ``event_currencies`` maps an instrument to the currencies whose
releases move it; ``upcoming`` filters the calendar to a window and minimum impact.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.domain.data.base import Capability
from app.domain.data.schemas import EconomicEvent
from app.domain.models.symbol import AssetType, Symbol
from app.domain.tools.base import ToolContext, ToolOutput, ToolSpec
from app.domain.tools.common import MarketHint, derived_record, fetch, to_symbol

MinImpact = Literal["high", "medium", "low"]
_RANK = {"high": 3, "medium": 2, "low": 1, "holiday": 0}


def event_currencies(sym: Symbol) -> list[str]:
    """Currencies whose releases move ``sym``: both legs of a pair, else its currency + USD."""
    if sym.asset_type == AssetType.FOREX:
        pair = sym.canonical.removesuffix("=X")
        if len(pair) == 6:
            return [pair[:3], pair[3:]]
    return list(dict.fromkeys([sym.currency.upper(), "USD"]))


def upcoming(
    events: list[EconomicEvent],
    currencies: list[str],
    *,
    now: datetime,
    hours_ahead: int,
    hours_back: int = 0,
    min_impact: str = "medium",
) -> list[EconomicEvent]:
    wanted = {c.upper() for c in currencies}
    start, end = now - timedelta(hours=hours_back), now + timedelta(hours=hours_ahead)
    floor = _RANK.get(min_impact, 2)
    return [
        e
        for e in events
        if (not wanted or e.currency in wanted or e.currency == "ALL")
        and start <= e.at <= end
        and (_RANK[e.impact] >= floor or e.impact == "holiday")
    ]


def event_row(e: EconomicEvent, now: datetime) -> dict[str, Any]:
    return {
        "at": e.at.astimezone(UTC).isoformat(),
        "hours_from_now": round((e.at - now).total_seconds() / 3600, 1),
        "currency": e.currency,
        "impact": e.impact,
        "title": e.title,
        "forecast": e.forecast,
        "previous": e.previous,
        "actual": e.actual,
    }


class CalendarArgs(BaseModel):
    symbol: str | None = Field(default=None, description="Instrument; its currencies are used when set")
    market: MarketHint | None = None
    currencies: list[str] = Field(default_factory=list, description='e.g. ["USD", "GBP"]; empty = all')
    hours_ahead: int = Field(default=48, ge=1, le=336)
    hours_back: int = Field(default=0, ge=0, le=72, description="Also show releases from the last N hours")
    min_impact: MinImpact = "medium"


async def calendar_window(args: CalendarArgs, now: datetime | None = None) -> ToolOutput:
    now = now or datetime.now(UTC)
    currencies = [c.upper() for c in args.currencies]
    if args.symbol:
        own = event_currencies(to_symbol(args.symbol, args.market))
        currencies = list(dict.fromkeys([*currencies, *own]))
    result = await fetch(Capability.ECONOMIC_CALENDAR)
    if result.status == "unavailable" or not isinstance(result.data, list):
        return ToolOutput(
            data=None,
            summary="economic calendar unavailable: " + "; ".join(result.notes),
            status="unavailable",
        )
    rows = [
        event_row(e, now)
        for e in upcoming(
            result.data,
            currencies,
            now=now,
            hours_ahead=args.hours_ahead,
            hours_back=args.hours_back,
            min_impact=args.min_impact,
        )
    ]
    value = {"now": now.isoformat(), "currencies": currencies, "events": rows}
    high = [r for r in rows if r["impact"] == "high" and r["hours_from_now"] >= 0]
    summary = f"{len(rows)} events ({'/'.join(currencies) or 'all'}, next {args.hours_ahead}h)"
    if high:
        nxt = high[0]
        summary += f" · next high-impact: {nxt['currency']} {nxt['title']} in {nxt['hours_from_now']}h"
    return ToolOutput(
        data=value,
        summary=summary,
        records=[*result.records, derived_record(result, value, tool="economic_calendar")],
        status=result.status,
    )


async def get_economic_calendar(args: CalendarArgs, ctx: ToolContext) -> ToolOutput:
    return await calendar_window(args)


CALENDAR_TOOLS: list[ToolSpec] = [
    ToolSpec(
        name="get_economic_calendar",
        description=(
            "Scheduled economic releases and central-bank events from the ForexFactory calendar (this and "
            "next week only; no history): time (UTC) and hours from now, currency, impact high/medium/low, "
            "forecast and previous. Pass `symbol` to get the currencies that move it (both legs of a forex "
            "pair; a stock's currency plus USD). Use it for event risk over a trade's holding period; not "
            "for past data (use get_macro)."
        ),
        args_model=CalendarArgs,
        category="data",
        requires=frozenset({Capability.ECONOMIC_CALENDAR}),
        fn=get_economic_calendar,
    ),
]
