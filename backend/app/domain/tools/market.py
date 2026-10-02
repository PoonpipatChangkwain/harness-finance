"""Market tools: prices, quote, verified snapshot, indicators, symbol search, sector lists (P05, P07).

Plain async functions over the data router and ``compute/``; they never
know which vendor answered (F4) and return provenance records instead of
bare numbers (F3). Descriptions say when to use a tool and when not to
(pattern from ``../dexter/src/tools/descriptions/``).
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, Field, field_validator

from app.domain.compute import indicators as ind
from app.domain.compute.indicators import verified_snapshot
from app.domain.data.base import Capability
from app.domain.data.providers.static_symbol_provider import _load_rows
from app.domain.data.schemas import SymbolMatch
from app.domain.models.horizon import INTERVAL_RANGE
from app.domain.models.market import OHLCVFrame, Quote
from app.domain.models.symbol import Symbol
from app.domain.tools.base import ToolContext, ToolOutput, ToolSpec
from app.domain.tools.common import (
    ListMarket,
    MarketHint,
    SymbolArgs,
    derived_record,
    fetch,
    record_ids,
    to_symbol,
)


def _lenient_range(value: Any) -> Any:
    """Sub-5-day ranges ("1d", "2d"…) are common LLM guesses; 5d is the smallest window served."""
    if isinstance(value, str) and value.strip().lower() in ("1d", "2d", "3d", "4d", "1w"):
        return "5d"
    return value


Range = Annotated[Literal["5d", "1mo", "3mo", "6mo", "1y", "2y", "5y"], BeforeValidator(_lenient_range)]
Interval = Literal["1d", "1wk", "1h", "15m"]
SnapshotInterval = Literal["1d", "1h", "15m"]
IndicatorName = Literal["sma", "ema", "rsi", "macd", "bollinger", "atr", "vwma", "stoch", "obv"]


class PricesArgs(SymbolArgs):
    range: Range = "1y"
    interval: Interval = Field(default="1d", description="15m allows range up to 1mo; 1h up to 2y")


class SnapshotArgs(SymbolArgs):
    as_of: date | None = Field(default=None, description="Snapshot date (default: latest bar)")
    interval: SnapshotInterval = Field(
        default="1d", description="Bar size: 1d (default), 1h or 15m for short-term trading"
    )


class IndicatorArgs(SnapshotArgs):
    indicators: list[IndicatorName] = Field(
        default_factory=list, description="Which indicators; empty = the standard snapshot set"
    )
    period: int = Field(default=20, ge=2, le=200, description="Window for sma/ema/vwma/bollinger")

    @field_validator("period", mode="before")
    @classmethod
    def _null_period(cls, value: Any) -> Any:
        return 20 if value is None else value


class SearchArgs(BaseModel):
    query: str = Field(min_length=1)
    market: MarketHint | None = None
    limit: int = Field(default=10, ge=1, le=25)


class SectorArgs(BaseModel):
    market: ListMarket = Field(description="TH = SET list, US = large-cap list")
    sector: str | None = Field(default=None, description="Sector name; omit to list available sectors")


async def _fetch_prices(sym: Symbol, range_: str, interval: str, as_of: date | None = None) -> Any:
    return await fetch(Capability.PRICES, sym, as_of=as_of, range=range_, interval=interval)


def _price_record(result: Any, frame: OHLCVFrame) -> Any:
    closes = [float(b.close) for b in frame.bars]
    value = {
        "symbol": frame.symbol,
        "last_close": closes[-1] if closes else None,
        "first_close": closes[0] if closes else None,
        "period_high": max((float(b.high) for b in frame.bars), default=None),
        "period_low": min((float(b.low) for b in frame.bars), default=None),
        "closes": closes,
    }
    as_of = frame.bars[-1].ts.date() if frame.bars else None
    return derived_record(result, value, tool="get_prices", as_of=as_of)


async def get_prices(args: PricesArgs, ctx: ToolContext) -> ToolOutput:
    sym = to_symbol(args.symbol, args.market)
    range_ = args.range
    if args.interval == "15m" and range_ not in ("5d", "1mo"):
        range_ = "1mo"  # Yahoo serves ~60 days of 15-minute bars
    elif args.interval == "1h" and range_ == "5y":
        range_ = "2y"
    result = await _fetch_prices(sym, range_, args.interval)
    frame: OHLCVFrame = result.data
    bars = frame.bars
    span = f"{bars[0].ts.date()}→{bars[-1].ts.date()}" if bars else "no bars"
    data: dict[str, Any] = {
        "symbol": sym.canonical,
        "currency": sym.currency,
        "interval": frame.interval,
        "provider": result.provider,
        "bars": [
            {
                "date": b.ts.date().isoformat() if frame.interval in ("1d", "1wk") else b.ts.isoformat(),
                "open": float(b.open),
                "high": float(b.high),
                "low": float(b.low),
                "close": float(b.close),
                "volume": b.volume,
            }
            for b in bars
        ],
    }
    return ToolOutput(
        data=data,
        summary=f"{sym.canonical} · {len(bars)} bars · {span}",
        records=[*result.records, _price_record(result, frame)],
        status=result.status,
    )


async def get_quote(args: SymbolArgs, ctx: ToolContext) -> ToolOutput:
    sym = to_symbol(args.symbol, args.market)
    result = await fetch(Capability.QUOTE, sym)
    quote: Quote = result.data
    value = quote.model_dump(mode="json")
    return ToolOutput(
        data={"provider": result.provider, **value},
        summary=f"{sym.canonical} {quote.price} {quote.currency}",
        records=[*result.records, derived_record(result, value, tool="get_quote", unit=quote.currency)],
        status=result.status,
    )


def _bar_labels(value: dict[str, Any], interval: str) -> dict[str, Any]:
    """On intraday bars the snapshot's day-named fields count bars: name them so."""
    if interval == "1d":
        return {**value, "interval": interval}
    changes = value.get("change_pct") or {}
    renamed = dict(zip(("1 bar", "5 bars", "21 bars", "63 bars"), changes.values(), strict=False))
    out = {k: v for k, v in value.items() if k not in ("high_52w", "low_52w", "change_pct")}
    return {
        **out,
        "interval": interval,
        "change_pct": renamed,
        "range_high_252_bars": value.get("high_52w"),
        "range_low_252_bars": value.get("low_52w"),
    }


async def _snapshot(
    sym: Symbol, as_of: date | None, interval: str = "1d"
) -> tuple[dict[str, Any], list[Any], Any]:
    result = await _fetch_prices(sym, INTERVAL_RANGE[interval], interval, as_of)
    frame: OHLCVFrame = result.data
    if not frame.bars:
        raise ValueError(f"no price bars for {sym.canonical}")
    cutoff = as_of or frame.bars[-1].ts.date()
    snap = verified_snapshot(frame, cutoff, inputs=record_ids(result.records))
    value = _bar_labels(snap.value, interval)
    records = list(result.records)
    for rec in snap.records:
        records.append(rec.model_copy(update={"value": value, "as_of": cutoff, "unit": sym.currency}))
    return value, records, frame


async def get_market_snapshot(args: SnapshotArgs, ctx: ToolContext) -> ToolOutput:
    sym = to_symbol(args.symbol, args.market)
    value, records, _ = await _snapshot(sym, args.as_of, args.interval)
    latest = value.get("latest_bar") or {}
    ind_values = value.get("indicators") or {}
    rsi = ind_values.get("rsi14") if isinstance(ind_values, dict) else None
    close = latest.get("close") if isinstance(latest, dict) else None
    summary = f"{sym.canonical} as of {value.get('as_of')} · close {close}"
    if isinstance(rsi, float):
        summary += f" · RSI14 {rsi:.1f}"
    return ToolOutput(data={"currency": sym.currency, **value}, summary=summary, records=records)


def _last(series: list[float | None], n: int = 5) -> list[float | None]:
    return series[-n:]


async def compute_indicators(args: IndicatorArgs, ctx: ToolContext) -> ToolOutput:
    sym = to_symbol(args.symbol, args.market)
    if not args.indicators:
        value, records, _ = await _snapshot(sym, args.as_of, args.interval)
        ind_values = value["indicators"]
        rsi = ind_values.get("rsi14") if isinstance(ind_values, dict) else None
        summary = f"{sym.canonical} as of {value['as_of']}"
        if isinstance(rsi, float):
            summary += f" · RSI14 {rsi:.1f}"
        return ToolOutput(data=value, summary=summary, records=records)

    result = await _fetch_prices(sym, INTERVAL_RANGE[args.interval], args.interval, args.as_of)
    frame: OHLCVFrame = result.data
    cutoff = args.as_of or (frame.bars[-1].ts.date() if frame.bars else date.today())
    bars = frame.as_of(cutoff).bars
    if not bars:
        return ToolOutput(data=None, summary=f"no price bars for {sym.canonical}", status="unavailable")
    closes = [float(b.close) for b in bars]
    highs = [float(b.high) for b in bars]
    lows = [float(b.low) for b in bars]
    volumes = [float(b.volume) for b in bars]
    inputs = record_ids(result.records)
    n = args.period
    out: dict[str, Any] = {}
    records = list(result.records)
    for name in dict.fromkeys(args.indicators):
        if name == "sma":
            res: Any = ind.sma(closes, n, inputs=inputs)
            out[f"sma{n}"] = _last(res.value)
        elif name == "ema":
            res = ind.ema(closes, n, inputs=inputs)
            out[f"ema{n}"] = _last(res.value)
        elif name == "rsi":
            res = ind.rsi(closes, 14, inputs=inputs)
            out["rsi14"] = _last(res.value)
        elif name == "macd":
            res = ind.macd(closes, inputs=inputs)
            out["macd"] = {k: _last(v) for k, v in res.value.items()}
        elif name == "bollinger":
            res = ind.bollinger(closes, n, inputs=inputs)
            out[f"bollinger{n}"] = {k: _last(v) for k, v in res.value.items()}
        elif name == "atr":
            res = ind.atr(highs, lows, closes, 14, inputs=inputs)
            out["atr14"] = _last(res.value)
        elif name == "vwma":
            res = ind.vwma(closes, volumes, n, inputs=inputs)
            out[f"vwma{n}"] = _last(res.value)
        elif name == "stoch":
            res = ind.stoch(highs, lows, closes, inputs=inputs)
            out["stoch"] = {k: _last(v) for k, v in res.value.items()}
        else:
            res = ind.obv(closes, volumes, inputs=inputs)
            out["obv"] = _last(res.value)
        key = next(reversed(out))
        records += [r.model_copy(update={"value": out[key], "as_of": cutoff}) for r in res.records]
    data = {
        "symbol": sym.canonical,
        "interval": args.interval,
        "as_of": cutoff.isoformat(),
        "last_close": closes[-1],
        "values": out,
    }
    records.append(
        derived_record(result, {"last_close": closes[-1]}, tool="compute_indicators", as_of=cutoff)
    )
    return ToolOutput(
        data=data,
        summary=f"{sym.canonical} · {', '.join(out)} as of {cutoff}",
        records=records,
    )


async def search_symbols(args: SearchArgs, ctx: ToolContext) -> ToolOutput:
    result = await fetch(Capability.SYMBOL_SEARCH, None, q=args.query, market=args.market, limit=args.limit)
    matches: list[SymbolMatch] = (result.data or [])[: args.limit]
    return ToolOutput(
        data=[
            {"symbol": m.symbol.canonical, "name": m.name, "exchange": m.exchange, "score": round(m.score, 3)}
            for m in matches
        ],
        summary=f"{len(matches)} matches for '{args.query}'",
        records=result.records,
        status=result.status,
    )


def _sector_rows(market: str) -> list[dict[str, str]]:
    if market == "TH":
        return [
            {"symbol": f"{r['symbol']}.BK", "name": r["name_en"], "sector": r["sector"]}
            for r in _load_rows("set_symbols.csv")
        ]
    return [
        {"symbol": r["symbol"], "name": r["name"], "sector": r["sector"]} for r in _load_rows("us_stocks.csv")
    ]


async def list_sector_symbols(args: SectorArgs, ctx: ToolContext) -> ToolOutput:
    rows = _sector_rows(args.market)
    sectors = sorted({r["sector"] for r in rows})
    if not args.sector:
        return ToolOutput(data={"market": args.market, "sectors": sectors}, summary=f"{len(sectors)} sectors")
    wanted = args.sector.strip().lower()
    picked = [r for r in rows if r["sector"].lower() == wanted or wanted in r["sector"].lower()]
    if not picked:
        return ToolOutput(
            data={"market": args.market, "sector": args.sector, "symbols": [], "available_sectors": sectors},
            summary=f"no symbols for sector '{args.sector}'",
            status="partial",
        )
    return ToolOutput(
        data={"market": args.market, "sector": args.sector, "symbols": picked},
        summary=f"{len(picked)} symbols · {args.sector} ({args.market})",
    )


MARKET_TOOLS: list[ToolSpec] = [
    ToolSpec(
        name="get_prices",
        description=(
            "OHLCV price history (daily/weekly/hourly) for a stock, SET stock (.BK), index, forex pair, "
            "commodity or crypto. Use for charts, trends and when you need the raw bars. Do NOT use it to "
            "get the latest price alone (use get_quote) or indicator values (use get_market_snapshot)."
        ),
        args_model=PricesArgs,
        limit_by="symbol",
        category="data",
        requires=frozenset({Capability.PRICES}),
        fn=get_prices,
    ),
    ToolSpec(
        name="get_quote",
        description=(
            "Latest price quote (price, change, change %) for one symbol. "
            "Cheapest way to get 'the price now'."
        ),
        args_model=SymbolArgs,
        limit_by="symbol",
        category="data",
        requires=frozenset({Capability.QUOTE}),
        fn=get_quote,
    ),
    ToolSpec(
        name="get_market_snapshot",
        description=(
            "Ground-truth technical snapshot computed from real prices: latest bar, SMA20/50/200, RSI14, "
            "MACD, Bollinger, ATR14, % change 1d/5d/1m/3m, 52-week high/low. `interval` 1h or 15m gives "
            "the same set on intraday bars (changes and range then count bars). Use this for any price or "
            "indicator number you will quote; never estimate these yourself."
        ),
        args_model=SnapshotArgs,
        limit_by="symbol",
        category="compute",
        requires=frozenset({Capability.PRICES}),
        fn=get_market_snapshot,
    ),
    ToolSpec(
        name="compute_indicators",
        description=(
            "Compute chosen indicators (sma, ema, rsi, macd, bollinger, atr, vwma, stoch, obv) from real "
            "prices; returns the last 5 values of each. With no indicators it returns the snapshot set. "
            "Use when you need a specific indicator or window the snapshot does not include."
        ),
        args_model=IndicatorArgs,
        limit_by="symbol",
        category="compute",
        requires=frozenset({Capability.PRICES}),
        fn=compute_indicators,
    ),
    ToolSpec(
        name="search_symbols",
        description=(
            "Find ticker symbols by company name or partial ticker (US and SET). Use when the user names a "
            "company instead of a ticker, or a ticker looks ambiguous. Not needed for well-known tickers."
        ),
        args_model=SearchArgs,
        category="search",
        requires=frozenset({Capability.SYMBOL_SEARCH}),
        fn=search_symbols,
    ),
    ToolSpec(
        name="list_sector_symbols",
        description=(
            "List the symbols of one sector from the built-in SET (TH) or US large-cap lists, or the "
            "available sectors when no sector is given. Use for sector scans and peer lists."
        ),
        args_model=SectorArgs,
        category="data",
        fn=list_sector_symbols,
    ),
]
