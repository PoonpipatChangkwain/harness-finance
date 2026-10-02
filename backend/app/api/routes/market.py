"""Market data for the UI: OHLCV, quote and indicator series (contracts.md §11).

``/indicators`` computes every series with ``domain/compute/indicators`` (F1: the
frontend never computes an indicator) and returns one ``kind=compute`` provenance id
per series, each with the price record as its input. Long-window indicators need
warm-up bars, so the endpoint fetches a longer history than the requested range and
trims the series to the range afterwards. Intraday intervals (1m/5m/15m/1h) back the
live chart, which polls these endpoints.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, get_args

from fastapi import APIRouter, HTTPException, Query

from app.domain.compute import indicators as ind
from app.domain.data.base import Capability, DataError, DataRequest
from app.domain.data.registry import get_router
from app.domain.models.market import Interval, OHLCVBar, OHLCVFrame, Quote
from app.domain.models.provenance import ProvenanceRecord
from app.domain.models.symbol import normalize_symbol
from app.harness.runtime.services import get_services

router = APIRouter(tags=["market"])

INDICATOR_SETS = ("sma20", "sma50", "sma200", "bb20", "rsi14", "macd")
INTERVALS = get_args(Interval)
_RANGE_DAYS = {
    "1d": 1,
    "5d": 5,
    "1mo": 31,
    "3mo": 92,
    "6mo": 183,
    "1y": 366,
    "2y": 731,
    "5y": 1827,
    "10y": 3653,
    "max": 36500,
}
# fetch enough history for SMA200 / MACD to be defined over the whole requested range
_WARMUP_RANGE = {"1mo": "2y", "3mo": "2y", "6mo": "2y", "1y": "2y", "2y": "5y", "5y": "10y"}
# intraday warm-up is capped by how far back Yahoo serves each interval (1m ≈ 7d, 5m/15m ≈ 60d)
_INTRADAY_WARMUP = {"1m": "5d", "5m": "1mo", "15m": "1mo", "1h": "3mo"}


def _symbol(raw: str) -> Any:
    try:
        return normalize_symbol(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _interval(raw: str) -> str:
    if raw not in INTERVALS:
        raise HTTPException(status_code=400, detail=f"unknown interval {raw!r}; use {INTERVALS}")
    return raw


def _warmup_range(interval: str, range_: str) -> str:
    return _INTRADAY_WARMUP.get(interval) or _WARMUP_RANGE.get(range_, range_)


def _range_start(bars: list[OHLCVBar], interval: str, range_: str, as_of: date | None) -> int:
    """Index of the first bar inside ``range_`` counted back from the newest bar."""
    if interval not in _INTRADAY_WARMUP:
        end_day = bars[-1].ts.date() if bars else (as_of or date.today())
        cutoff_day = end_day - timedelta(days=_RANGE_DAYS[range_])
        return next((i for i, b in enumerate(bars) if b.ts.date() >= cutoff_day), len(bars))
    # intraday: trim by timestamp, a date cut would keep a whole extra session
    if not bars:
        return 0
    cutoff_ts = bars[-1].ts - timedelta(days=_RANGE_DAYS[range_])
    return next((i for i, b in enumerate(bars) if b.ts > cutoff_ts), len(bars))


async def _bars(
    symbol: str, interval: str, range_: str, as_of: date | None
) -> tuple[Any, list[OHLCVBar], Any]:
    sym = _symbol(symbol)
    req = DataRequest(symbol=sym, params={"interval": interval, "range": range_}, as_of=as_of)
    try:
        result = await get_router().get(Capability.PRICES, req)
    except DataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    frame: OHLCVFrame = result.data
    bars = [b for b in frame.bars if as_of is None or b.ts.date() <= as_of]  # backtest: cut at as_of
    return sym, bars, result


async def _price_record(result: Any) -> str | None:
    if not result.records:
        return None
    [provenance_id] = await get_services().provenance.add_many(result.records[:1])
    return str(provenance_id)


@router.get("/market/{symbol}/ohlcv")
async def get_ohlcv(
    symbol: str,
    interval: str = Query(default="1d"),
    range: str = Query(default="1y"),
    as_of: date | None = None,
) -> dict[str, object]:
    interval = _interval(interval)
    if interval in _INTRADAY_WARMUP:
        # same fetch as /indicators (one cache entry, one Yahoo call per chart poll), then the
        # same timestamp trim, so candles and indicator lines cover the same window
        if range not in _RANGE_DAYS:
            raise HTTPException(status_code=400, detail=f"unknown range {range!r}")
        sym, bars, result = await _bars(symbol, interval, _warmup_range(interval, range), as_of)
        bars = bars[_range_start(bars, interval, range, as_of) :]
    else:
        sym, bars, result = await _bars(symbol, interval, range, as_of)
    return {
        "symbol": sym.canonical,
        "currency": sym.currency,
        "asset_type": sym.asset_type.value,
        "bars": [b.model_dump(mode="json") for b in bars],
        "provider": result.provider,
        "provenance_id": await _price_record(result),
    }


def _series(set_name: str, closes: list[float], inputs: list[str]) -> tuple[dict[str, Any], ProvenanceRecord]:
    """One indicator set → its named lines, plus the compute record of the formula."""
    if set_name.startswith("sma"):
        r = ind.sma(closes, int(set_name[3:]), inputs=inputs)
        return {set_name: r.value}, r.records[0]
    if set_name == "bb20":
        rb = ind.bollinger(closes, 20, 2.0, inputs=inputs)
        return {
            "bb_upper": rb.value["upper"],
            "bb_mid": rb.value["mid"],
            "bb_lower": rb.value["lower"],
        }, rb.records[0]
    if set_name == "rsi14":
        r = ind.rsi(closes, 14, inputs=inputs)
        return {"rsi14": r.value}, r.records[0]
    rm = ind.macd(closes, inputs=inputs)
    return {
        "macd": rm.value["macd"],
        "macd_signal": rm.value["signal"],
        "macd_hist": rm.value["hist"],
    }, rm.records[0]


@router.get("/market/{symbol}/indicators")
async def get_indicators(
    symbol: str,
    set: str = Query(default="sma20,sma50,bb20,rsi14,macd"),
    interval: str = Query(default="1d"),
    range: str = Query(default="1y"),
    as_of: date | None = None,
) -> dict[str, Any]:
    wanted = [s.strip() for s in set.split(",") if s.strip()]
    unknown = [s for s in wanted if s not in INDICATOR_SETS]
    if unknown or not wanted:
        raise HTTPException(status_code=400, detail=f"unknown indicator set {unknown}; use {INDICATOR_SETS}")
    if range not in _RANGE_DAYS:
        raise HTTPException(status_code=400, detail=f"unknown range {range!r}")

    interval = _interval(interval)
    sym, bars, result = await _bars(symbol, interval, _warmup_range(interval, range), as_of)
    price_id = await _price_record(result)
    inputs = [price_id] if price_id else []
    closes = [float(b.close) for b in bars]
    start = _range_start(bars, interval, range, as_of)

    series: dict[str, list[float | None]] = {}
    records: list[ProvenanceRecord] = []
    for name in dict.fromkeys(wanted):
        lines, record = _series(name, closes, inputs)
        series.update({k: v[start:] for k, v in lines.items()})
        records.append(record)
    ids = await get_services().provenance.add_many(records) if records else []
    return {
        "symbol": sym.canonical,
        "ts": [b.ts.isoformat() for b in bars[start:]],
        "series": series,
        "provenance": {name: str(pid) for name, pid in zip(dict.fromkeys(wanted), ids, strict=True)},
        "price_provenance_id": price_id,
        "provider": result.provider,
    }


@router.get("/market/{symbol}/quote")
async def get_quote(symbol: str) -> dict[str, object]:
    sym = _symbol(symbol)
    req = DataRequest(symbol=sym)
    try:
        result = await get_router().get(Capability.QUOTE, req)
    except DataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    quote: Quote = result.data
    return {
        "quote": quote.model_dump(mode="json"),
        "provider": result.provider,
        "provenance_id": await _price_record(result),
    }
