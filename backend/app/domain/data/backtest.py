"""Point-in-time data for a backtest: preload bars once, serve every request cut at ``as_of``.

A backtest re-runs the trading workflow on past dates. Passing ``as_of`` to each tool is
not enough: a tool that forgets it (``get_prices`` has no such argument) would read bars
from after the analysis date. ``PointInTimeRouter`` closes that at the data layer:

- ``PRICES`` is served from a ``PriceStore`` preloaded for the whole backtest, cut at
  ``as_of`` and trimmed to the requested range (so one Yahoo download serves every cell);
- ``QUOTE`` is the last daily close on or before ``as_of`` (a live quote would be the future);
- every other capability goes to the real router with ``as_of`` clamped to the analysis
  date (providers filter dated items by it, see ``window.py``).

Activate it for the current task with ``use_router`` (``registry.py``); concurrent cells
each run in their own task, so each sees its own date.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

from app.domain.data.base import Capability, DataRequest, DataResult, NoData
from app.domain.data.router import DataRouter, RouterMode
from app.domain.models.market import OHLCVFrame, Quote
from app.domain.models.provenance import new_record

# calendar days a ``range`` spans; mirrors the yfinance provider
RANGE_DAYS: dict[str, int] = {
    "1d": 4, "5d": 8, "1mo": 31, "3mo": 93, "6mo": 186, "1y": 366, "2y": 731, "5y": 1827, "10y": 3653,
}  # fmt: skip


# how far back Yahoo serves intraday bars (calendar days, with a margin)
YAHOO_MAX_DAYS: dict[str, int] = {"1m": 6, "5m": 58, "15m": 58, "1h": 725}


def range_covering(days: int) -> str:
    """The smallest ``range`` that spans ``days`` calendar days (capped at 10y)."""
    return next((r for r, d in RANGE_DAYS.items() if d >= days), "10y")


class PriceStore:
    """Full bar history per (symbol, interval) — including bars after any analysis date.

    Only the simulator may read past ``as_of``; the router below never does.
    """

    def __init__(self) -> None:
        self._frames: dict[tuple[str, str], OHLCVFrame] = {}
        self._sources: dict[tuple[str, str], DataResult] = {}

    def put(self, symbol: str, interval: str, result: DataResult) -> None:
        frame: OHLCVFrame = result.data
        self._frames[(symbol, interval)] = frame
        self._sources[(symbol, interval)] = result

    def frame(self, symbol: str, interval: str) -> OHLCVFrame | None:
        return self._frames.get((symbol, interval))

    def source(self, symbol: str, interval: str) -> DataResult | None:
        return self._sources.get((symbol, interval))

    def keys(self) -> list[tuple[str, str]]:
        return list(self._frames)


async def preload_prices(
    router: DataRouter,
    store: PriceStore,
    symbols: list[Any],
    intervals: list[str],
    first_day: date,
    last_day: date,
    *,
    warmup_days: int = 400,
) -> dict[tuple[str, str], str]:
    """Download ``[first_day - warmup, last_day]`` once per (symbol, interval).

    Returns the failures as ``{(symbol, interval): reason}``; the caller decides whether a
    missing interval is fatal (Yahoo serves ~60 days of 15m bars, ~2 years of 1h).
    """
    failures: dict[tuple[str, str], str] = {}
    wanted = first_day - timedelta(days=warmup_days)
    for sym in symbols:
        for interval in intervals:
            start = wanted
            limit = YAHOO_MAX_DAYS.get(interval)
            if limit is not None:  # Yahoo refuses intraday windows older than this
                start = max(start, date.today() - timedelta(days=limit))
            if start >= last_day:
                failures[(sym.canonical, interval)] = f"Yahoo keeps only ~{limit} days of {interval} bars"
                continue
            req = DataRequest(
                symbol=sym,
                params={
                    "interval": interval,
                    "start": start.isoformat(),
                    "range": range_covering((last_day - start).days),  # providers without `start`
                },
                as_of=last_day,
            )
            try:
                result = await router.get(Capability.PRICES, req)
            except Exception as exc:  # any provider failure is reported, not raised
                failures[(sym.canonical, interval)] = f"{type(exc).__name__}: {exc}"
                continue
            store.put(sym.canonical, interval, result)
    return failures


class PointInTimeRouter(DataRouter):
    """A ``DataRouter`` that answers as if today were ``as_of``."""

    def __init__(self, base: DataRouter, store: PriceStore, as_of: date) -> None:
        super().__init__(base.providers())
        self._base = base
        self.store = store
        self.as_of = as_of

    async def get(self, cap: Capability, req: DataRequest, *, mode: RouterMode | None = None) -> DataResult:
        cutoff = min(req.as_of, self.as_of) if req.as_of else self.as_of
        if cap is Capability.PRICES:
            return self._prices(req, cutoff)
        if cap is Capability.QUOTE:
            return self._quote(req, cutoff)
        return await self._base.get(cap, req.model_copy(update={"as_of": cutoff}), mode=mode)

    def _stored(self, req: DataRequest, interval: str) -> tuple[OHLCVFrame, DataResult]:
        canonical = req.symbol.canonical if req.symbol is not None else ""
        frame = self.store.frame(canonical, interval)
        source = self.store.source(canonical, interval)
        if frame is None or source is None:
            # not preloaded: refuse rather than fetch a window that may run past as_of
            raise NoData(f"{canonical} {interval} bars were not preloaded for this backtest")
        return frame, source

    def _prices(self, req: DataRequest, cutoff: date) -> DataResult:
        interval = str(req.params.get("interval", "1d"))
        frame, source = self._stored(req, interval)
        days = RANGE_DAYS.get(str(req.params.get("range", "1y")), 366)
        start = cutoff - timedelta(days=days)
        bars = [b for b in frame.bars if start < b.ts.date() <= cutoff]
        if not bars:
            raise NoData(f"no {interval} bars for {frame.symbol} on or before {cutoff}")
        record = new_record(
            "data",
            None,
            source=source.provider,
            tool="ohlcv",
            params={"interval": interval, "range": req.params.get("range"), "point_in_time": True},
            as_of=cutoff,
            inputs=[r.id for r in source.records],
        )
        return DataResult(
            provider=source.provider,
            capability=Capability.PRICES,
            data=frame.model_copy(update={"bars": bars}),
            records=[record],
            status="ok",
        )

    def _quote(self, req: DataRequest, cutoff: date) -> DataResult:
        frame, source = self._stored(req, "1d")
        bars = [b for b in frame.bars if b.ts.date() <= cutoff]
        if not bars:
            raise NoData(f"no daily bar for {frame.symbol} on or before {cutoff}")
        last = bars[-1]
        prev = bars[-2] if len(bars) > 1 else None
        change = last.close - prev.close if prev is not None else None
        quote = Quote(
            symbol=frame.symbol,
            price=last.close,
            change=change,
            change_pct=(change / prev.close * 100) if prev is not None and change is not None else None,
            # the close is known at the end of the analysis day
            ts=datetime.combine(last.ts.date(), time(23, 59), tzinfo=last.ts.tzinfo),
            currency=req.symbol.currency if req.symbol is not None else "",
        )
        record = new_record(
            "data",
            str(last.close),
            source=source.provider,
            tool="quote",
            params={"point_in_time": True},
            as_of=cutoff,
            inputs=[r.id for r in source.records],
        )
        return DataResult(
            provider=source.provider, capability=Capability.QUOTE, data=quote, records=[record], status="ok"
        )
