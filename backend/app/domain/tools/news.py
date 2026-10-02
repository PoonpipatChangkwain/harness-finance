"""News, social, macro, prediction-market and web tools (P07).

``unavailable`` (fetch failed / not configured) and ``ok`` with an empty
list (nothing was said) stay distinct all the way to the LLM (contracts §3).
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.domain.compute.sentiment import stance_counts
from app.domain.data.base import Capability
from app.domain.data.schemas import MacroSeries, NewsItem, SocialPost, WebPage, WebSearchHit
from app.domain.models.symbol import normalize_symbol
from app.domain.tools.base import ToolContext, ToolOutput, ToolSpec
from app.domain.tools.common import (
    MarketHint,
    SymbolArgs,
    derived_record,
    fetch,
    jsonable,
    record_ids,
    to_symbol,
)

MACRO_PRESETS: dict[str, list[str]] = {
    "rates": ["fed_funds_rate", "2y_treasury", "10y_treasury", "10y_2y_spread"],
    "inflation": ["cpi", "core_cpi", "core_pce"],
    "growth": ["real_gdp", "unemployment_rate", "nonfarm_payrolls"],
    "risk": ["vix", "dollar_index"],
}


class NewsArgs(SymbolArgs):
    limit: int = Field(default=15, ge=1, le=40)


class GlobalNewsArgs(BaseModel):
    topic: str = Field(default="stock market", description="What the news should be about")
    market: MarketHint | None = Field(default=None, description="TH = Thai market news, otherwise US/global")
    limit: int = Field(default=15, ge=1, le=40)


class SocialArgs(SymbolArgs):
    lookback_days: int = Field(default=7, ge=1, le=30)
    max_posts: int = Field(default=30, ge=1, le=100, description="Posts to show (all are counted)")


class MacroArgs(BaseModel):
    preset: Literal["rates", "inflation", "growth", "risk"] | None = Field(
        default=None, description="Named bundle of US FRED series"
    )
    series: list[str] = Field(
        default_factory=list, max_length=6, description="FRED aliases (cpi, 10y_treasury, vix…) or raw ids"
    )
    observations: int = Field(default=12, ge=1, le=120, description="Latest N observations per series")


class QueryArgs(BaseModel):
    query: str = Field(min_length=2)
    limit: int = Field(default=8, ge=1, le=20)


class FetchArgs(BaseModel):
    url: str = Field(pattern=r"^https?://", description="Absolute http(s) URL")


def _news_rows(items: list[NewsItem], limit: int) -> list[dict[str, Any]]:
    return [
        {
            "title": n.title,
            "source": n.source,
            "published_at": n.published_at.isoformat() if n.published_at else None,
            "url": n.url,
            "summary": (n.summary or "")[:400] or None,
        }
        for n in items[:limit]
    ]


async def get_news(args: NewsArgs, ctx: ToolContext) -> ToolOutput:
    sym = to_symbol(args.symbol, args.market)
    result = await fetch(Capability.NEWS, sym, limit=args.limit)
    items: list[NewsItem] = sorted(
        list(result.data or []),
        key=lambda n: n.published_at.timestamp() if n.published_at else 0,
        reverse=True,
    )
    rows = _news_rows(items, args.limit)
    return ToolOutput(
        data={"symbol": sym.canonical, "provider": result.provider, "items": rows, "notes": result.notes},
        summary=f"{sym.canonical} · {len(rows)} news items",
        records=result.records,
        status=result.status,
    )


async def get_global_news(args: GlobalNewsArgs, ctx: ToolContext) -> ToolOutput:
    anchor = normalize_symbol("^SET.BK" if args.market == "TH" else "^GSPC")
    result = await fetch(Capability.GLOBAL_NEWS, anchor, query=args.topic, limit=args.limit)
    data = list(result.data or [])
    if data and isinstance(data[0], WebSearchHit):
        rows = [h.model_dump(mode="json") for h in data[: args.limit]]
    else:
        rows = _news_rows(data, args.limit)
    return ToolOutput(
        data={"topic": args.topic, "provider": result.provider, "items": rows},
        summary=f"{len(rows)} market news items · {args.topic}",
        records=result.records,
        status=result.status,
    )


async def get_social_sentiment(args: SocialArgs, ctx: ToolContext) -> ToolOutput:
    sym = to_symbol(args.symbol, args.market)
    result = await fetch(Capability.SOCIAL, sym, lookback_days=args.lookback_days)
    if result.status == "unavailable" and not result.data:
        return ToolOutput(
            data={"symbol": sym.canonical, "status": "unavailable", "notes": result.notes},
            summary=f"{sym.canonical} social data unavailable",
            records=result.records,
            status="unavailable",
        )
    posts: list[SocialPost] = list(result.data or [])
    counts = stance_counts(posts, inputs=record_ids(result.records))
    shown = [
        {
            "platform": p.platform,
            "text": p.text[:280],
            "stance": p.stance_label,
            "created_at": p.created_at.isoformat() if p.created_at else None,
            "url": p.url,
        }
        for p in posts
        if p.kind == "post"
    ][: args.max_posts]
    aggregates = [p.model_dump(mode="json") for p in posts if p.kind == "aggregate"]
    return ToolOutput(
        data={
            "symbol": sym.canonical,
            "provider": result.provider,
            "stance_counts": counts.value,
            "posts": shown,
            "aggregates": aggregates,
            "notes": result.notes,
        },
        summary=f"{sym.canonical} · {counts.value['total']} posts · "
        f"bull {counts.value['bullish']} / bear {counts.value['bearish']}",
        records=[*result.records, *counts.records],
        status=result.status,
    )


async def _one_series(series_id: str, n: int) -> tuple[str, Any]:
    try:
        return series_id, await fetch(Capability.MACRO, None, series_id=series_id)
    except Exception as exc:
        return series_id, exc


async def get_macro(args: MacroArgs, ctx: ToolContext) -> ToolOutput:
    wanted = list(dict.fromkeys([*(MACRO_PRESETS.get(args.preset or "", [])), *args.series]))
    if not wanted:
        wanted = MACRO_PRESETS["rates"]
    results = await asyncio.gather(*(_one_series(s, args.observations) for s in wanted))
    out: dict[str, Any] = {}
    records = []
    failed: list[str] = []
    for series_id, res in results:
        if isinstance(res, Exception) or res.status == "unavailable" or res.data is None:
            failed.append(series_id if isinstance(res, Exception) else f"{series_id} (unavailable)")
            continue
        series: MacroSeries = res.data
        obs = [
            {"date": o.date.isoformat(), "value": o.value} for o in series.observations[-args.observations :]
        ]
        value = {"series_id": series.series_id, "observations": obs, "pct_change": series.pct_change}
        out[series_id] = value
        records += [*res.records, derived_record(res, value, tool="get_macro")]
    status: Literal["ok", "partial", "unavailable"] = (
        "unavailable" if not out else ("partial" if failed else "ok")
    )
    return ToolOutput(
        data={"series": out, "failed": failed},
        summary=f"{len(out)}/{len(wanted)} macro series"
        + (f" · failed: {', '.join(failed)}" if failed else ""),
        records=records,
        status=status,
    )


async def get_prediction_markets(args: QueryArgs, ctx: ToolContext) -> ToolOutput:
    result = await fetch(Capability.PREDICTION_MARKETS, None, query=args.query, limit=args.limit)
    rows = jsonable(list(result.data or [])[: args.limit])
    records = list(result.records)
    if rows:
        records.append(derived_record(result, rows, tool="get_prediction_markets"))
    return ToolOutput(
        data={"query": args.query, "provider": result.provider, "markets": rows, "notes": result.notes},
        summary=f"{len(rows)} prediction markets · '{args.query}'",
        records=records,
        status=result.status,
    )


async def web_search(args: QueryArgs, ctx: ToolContext) -> ToolOutput:
    result = await fetch(Capability.WEB_SEARCH, None, query=args.query, limit=args.limit)
    hits: list[WebSearchHit] = list(result.data or [])[: args.limit]
    return ToolOutput(
        data={
            "query": args.query,
            "provider": result.provider,
            "results": [h.model_dump(mode="json") for h in hits],
        },
        summary=f"{len(hits)} results · '{args.query}'",
        records=result.records,
        status=result.status,
    )


async def web_fetch(args: FetchArgs, ctx: ToolContext) -> ToolOutput:
    result = await fetch(Capability.WEB_FETCH, None, url=args.url)
    page: WebPage = result.data
    return ToolOutput(
        data=page.model_dump(mode="json"),
        summary=f"{page.title or args.url} · {len(page.markdown)} chars",
        records=result.records,
        status=result.status,
    )


NEWS_TOOLS: list[ToolSpec] = [
    ToolSpec(
        name="get_news",
        description=(
            "Recent news for one symbol from every configured news provider (merged, newest first). Use for "
            "events, earnings reactions, catalysts. Use web_fetch only if a headline is not enough."
        ),
        args_model=NewsArgs,
        limit_by="symbol",
        category="data",
        requires=frozenset({Capability.NEWS}),
        fn=get_news,
    ),
    ToolSpec(
        name="get_global_news",
        description=(
            "Broad market / macro news (US or Thai market). Use for market-wide context, not one company."
        ),
        args_model=GlobalNewsArgs,
        category="data",
        requires=frozenset({Capability.GLOBAL_NEWS}),
        fn=get_global_news,
    ),
    ToolSpec(
        name="get_social_sentiment",
        description=(
            "Social posts (StockTwits, Reddit, X when configured) with stance counts computed in code. "
            "Use for retail sentiment; quote the computed counts, never your own tally."
        ),
        args_model=SocialArgs,
        limit_by="symbol",
        category="data",
        timeout_s=45,
        requires=frozenset({Capability.SOCIAL}),
        fn=get_social_sentiment,
    ),
    ToolSpec(
        name="get_macro",
        description=(
            "US macro series from FRED: preset rates/inflation/growth/risk or specific aliases (cpi, "
            "10y_treasury, fed_funds_rate, vix, dollar_index…). Use for rate/inflation/growth context."
        ),
        args_model=MacroArgs,
        category="data",
        requires=frozenset({Capability.MACRO}),
        fn=get_macro,
    ),
    ToolSpec(
        name="get_prediction_markets",
        description=(
            "Polymarket odds for a topic (e.g. 'Fed rate cut'). "
            "Use for market-implied probabilities of events."
        ),
        args_model=QueryArgs,
        category="data",
        requires=frozenset({Capability.PREDICTION_MARKETS}),
        fn=get_prediction_markets,
    ),
    ToolSpec(
        name="web_search",
        description=(
            "General web search. Use when no dedicated tool covers the question (Thai-specific news, "
            "company announcements, policy). Prefer dedicated data tools for numbers."
        ),
        args_model=QueryArgs,
        category="search",
        requires=frozenset({Capability.WEB_SEARCH}),
        fn=web_search,
    ),
    ToolSpec(
        name="web_fetch",
        description="Read one web page as markdown. Use only for a specific URL whose details you need.",
        args_model=FetchArgs,
        category="search",
        timeout_s=45,
        requires=frozenset({Capability.WEB_FETCH}),
        fn=web_fetch,
    ),
]
