"""Harness side of the backtest (Phase 15.3): cells → trading runs → simulation.

The backtest is itself a run (workflow ``backtest``) so it gets the event stream, cancel and
resume of every other run. Its steps:

1. **preload** the bars of every symbol (decision interval + daily) once, from 400 days
   before the start to today;
2. **decisions** — one cell per (symbol, analysis date); a cell another backtest already
   decided with the same settings is copied (``cache_key``), the rest start a child
   ``trading`` run inside ``use_router(PointInTimeRouter(day))`` so nothing it fetches is
   dated after the cell's day; ``concurrency`` runs at a time, ``budget_usd`` stops new ones;
3. **simulate** the decisions with the config's ``SimConfig`` (and the SMA baseline, and
   buy & hold) — the API re-simulates with other capital later without any LLM call.

Resume = run the workflow again: finished cells are kept, cells a dead process left
``running`` start over.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections import OrderedDict
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic_core import to_jsonable_python

from app.domain.compute.backtest import (
    ENGINE_VERSION,
    SimConfig,
    buy_and_hold,
    performance,
    simulate,
    sma_cross,
)
from app.domain.compute.backtest.models import SimDecision
from app.domain.data.backtest import PointInTimeRouter, PriceStore, preload_prices
from app.domain.data.registry import use_router
from app.domain.models.market import OHLCVBar
from app.domain.models.symbol import Symbol, normalize_symbol
from app.domain.workflows.backtest import MAX_CELLS, BacktestConfig, cell_decision, grid, sim_decision
from app.domain.workflows.trading import TRADING_WORKFLOW, lot_size_for
from app.harness.runtime.context import RunContext
from app.harness.storage import backtest_repo, runs_repo

logger = logging.getLogger(__name__)

WARMUP_DAYS = 400
_PROMPTS = Path(__file__).resolve().parents[2] / "domain" / "agents" / "prompts"


# -- cache key -------------------------------------------------------------------------------


def _prompt_digest() -> str:
    """Changes whenever a trading prompt changes: a decision made with old prompts is not reused."""
    h = hashlib.sha256(f"{TRADING_WORKFLOW.id}@{TRADING_WORKFLOW.version}".encode())
    for path in sorted(_PROMPTS.glob("trade_*.md")):
        h.update(path.name.encode())
        h.update(path.read_bytes())
    return h.hexdigest()[:16]


def cache_key(cfg: BacktestConfig, symbol: str, day: date, *, models: dict[str, str] | None = None) -> str:
    raw = {
        "symbol": symbol,
        "day": day.isoformat(),
        **cfg.cache_fields(),
        "models": models,  # the configured deep/quick models when no LLM was picked
        "prompts": _prompt_digest(),
    }
    return hashlib.sha256(json.dumps(raw, sort_keys=True, default=str).encode()).hexdigest()[:32]


def _configured_models(services: Any, cfg: BacktestConfig) -> dict[str, str] | None:
    if cfg.llm is not None:
        return None
    try:
        llm = services.get_llm()
        return {"deep": str(llm.primary("deep")), "quick": str(llm.primary("quick"))}
    except Exception:  # no LLM configured: the run will fail anyway; keep the key stable
        return {}


# -- market data -----------------------------------------------------------------------------


class Market:
    """Preloaded bars of a backtest (incl. FX for the simulation's base currency)."""

    def __init__(self, cfg: BacktestConfig) -> None:
        self.cfg = cfg
        self.store = PriceStore()
        self.failures: dict[tuple[str, str], str] = {}
        self.symbols: dict[str, Symbol] = {s: normalize_symbol(s) for s in cfg.symbols}

    def daily(self, symbol: str) -> list[OHLCVBar]:
        frame = self.store.frame(symbol, "1d")
        return frame.bars if frame else []

    def decision_bars(self, symbol: str) -> tuple[str, list[OHLCVBar]]:
        """The decision interval's bars when Yahoo had them, else the daily ones."""
        frame = self.store.frame(symbol, self.cfg.interval)
        if frame is not None and frame.bars:
            return self.cfg.interval, frame.bars
        return "1d", self.daily(symbol)

    async def load(self, router: Any) -> None:
        start = self.cfg.start_date
        intervals = sorted({self.cfg.interval, "1d"})
        self.failures = await preload_prices(
            router,
            self.store,
            list(self.symbols.values()),
            intervals,
            start,
            date.today(),
            warmup_days=WARMUP_DAYS,
        )

    async def fx(self, router: Any, base: str) -> dict[str, list[tuple[date, Any]]]:
        """Daily FX of each symbol currency into ``base`` (e.g. USDTHB=X: THB per USD)."""
        out: dict[str, list[tuple[date, Any]]] = {}
        for ccy in sorted({s.currency for s in self.symbols.values()} - {base}):
            pair = normalize_symbol(f"{ccy}{base}")
            if self.store.frame(pair.canonical, "1d") is None:
                failed = await preload_prices(
                    router, self.store, [pair], ["1d"], self.cfg.start_date, date.today(), warmup_days=30
                )
                if failed:
                    raise ValueError(f"no FX {ccy}->{base}: {next(iter(failed.values()))}")
            frame = self.store.frame(pair.canonical, "1d")
            assert frame is not None
            out[ccy] = [(b.ts.date(), b.close) for b in frame.bars]
        return out


_MARKETS: OrderedDict[str, Market] = OrderedDict()  # backtest id → bars, so re-simulating is instant


async def market_for(services: Any, backtest_id: str, cfg: BacktestConfig) -> Market:
    market = _MARKETS.get(backtest_id)
    if market is None:
        market = Market(cfg)
        await market.load(services.router())
        _MARKETS[backtest_id] = market
        while len(_MARKETS) > 4:
            _MARKETS.popitem(last=False)
    _MARKETS.move_to_end(backtest_id)
    return market


# -- simulation ------------------------------------------------------------------------------


def _window(bars: list[OHLCVBar], cfg: BacktestConfig) -> list[OHLCVBar]:
    return [b for b in bars if cfg.start_date <= b.ts.date() <= cfg.end_date]


def _curve(points: list[tuple[date, Any]]) -> list[dict[str, Any]]:
    return [{"day": d.isoformat(), "equity": str(v)} for d, v in points]


async def simulate_backtest(
    services: Any, backtest_id: str, cfg: BacktestConfig, sim_cfg: SimConfig
) -> backtest_repo.SimRecord:
    """Simulate the finished cells of ``backtest_id`` over ``[start, end]`` with ``sim_cfg``.

    Positions still open on ``end_date`` are closed there (``end_of_test``), so the result
    and its buy & hold benchmark cover exactly the tested window.
    """
    market = await market_for(services, backtest_id, cfg)
    fx = await market.fx(services.router(), sim_cfg.base_currency)
    cells = [c for c in await backtest_repo.cells(backtest_id) if c.status == "done" and c.decision]
    decisions: list[SimDecision] = [
        sim_decision(c.symbol, date.fromisoformat(c.day), c.decision or {}, c.run_id or c.cached_from or c.id)
        for c in cells
    ]
    bars: dict[str, list[OHLCVBar]] = {}
    intervals: dict[str, str] = {}
    for sym in cfg.symbols:
        interval, series = market.decision_bars(sym)
        intervals[sym] = interval
        bars[sym] = _window(series, cfg)
    currencies = {s: market.symbols[s].currency for s in cfg.symbols}
    decisions = [
        d if d.currency else d.model_copy(update={"currency": currencies[d.symbol]}) for d in decisions
    ]

    sim = simulate(decisions, bars, sim_cfg, fx=fx).value
    daily = {s: _window(market.daily(s), cfg) for s in cfg.symbols}
    bench = buy_and_hold(daily, currencies, sim_cfg, fx=fx).value
    perf = performance(sim, benchmark=bench).value
    result: dict[str, Any] = {
        "trades": [t.model_dump(mode="json") for t in sim.trades],
        "skipped": [s.model_dump(mode="json") for s in sim.skipped],
        "equity_curve": [p.model_dump(mode="json") for p in sim.equity_curve],
        "benchmark": _curve(bench),
        "intervals": intervals,
    }
    if cfg.baseline:
        base_decisions: list[SimDecision] = []
        for sym in cfg.symbols:
            s = market.symbols[sym]
            base_decisions += [
                d
                for d in sma_cross(
                    sym,
                    market.daily(sym),
                    currency=s.currency,
                    asset_type=s.asset_type.value,
                    lot_size=Decimal(str(lot_size_for(s.asset_type, s.market))),
                )
                if cfg.start_date <= d.decided_on <= cfg.end_date
            ]
        base = simulate(base_decisions, daily, sim_cfg, fx=fx).value
        result["baseline"] = {
            "name": "SMA20/50 cross",
            "metrics": to_jsonable_python(performance(base, benchmark=bench).value),
            "equity_curve": [{"day": p.day.isoformat(), "equity": str(p.equity)} for p in base.equity_curve],
        }
    return await backtest_repo.add_sim(
        backtest_id,
        sim_cfg.model_dump(mode="json"),
        to_jsonable_python(perf),
        result,
        ENGINE_VERSION,
    )


# -- the workflow ----------------------------------------------------------------------------


class _Tally:
    def __init__(self, cells: list[backtest_repo.CellRecord]) -> None:
        self.total = len(cells)
        self.done = sum(c.status == "done" for c in cells)
        self.cached = sum(c.status == "done" and c.cached_from is not None for c in cells)
        self.failed = sum(c.status == "failed" for c in cells)
        self.running = 0
        self.cost = sum(float((c.usage or {}).get("cost_usd") or 0.0) for c in cells)

    def payload(self, phase: str, message: str | None = None) -> dict[str, Any]:
        return {
            "phase": phase, "total": self.total, "done": self.done, "cached": self.cached,
            "failed": self.failed, "running": self.running, "cost_usd": round(self.cost, 6),
            "message": message,
        }  # fmt: skip


async def _ensure_cells(
    ctx: RunContext, cfg: BacktestConfig, market: Market, models: dict[str, str] | None
) -> list[backtest_repo.CellRecord]:
    existing = await backtest_repo.cells(ctx.run_id)
    have = {(c.symbol, c.day) for c in existing}
    every = cfg.sessions_between
    rows: list[tuple[str, str, str]] = []
    for sym in cfg.symbols:
        days = grid(market.daily(sym), cfg.start_date, cfg.end_date, every)
        if not days:
            reason = market.failures.get((sym, "1d"), "no trading days in the range")
            await ctx.emit.emit("warning", {"code": "backtest_symbol_skipped", "message": f"{sym}: {reason}"})
        rows += [
            (sym, d.isoformat(), cache_key(cfg, sym, d, models=models))
            for d in days
            if (sym, d.isoformat()) not in have
        ]
    new = await backtest_repo.add_cells(ctx.run_id, rows) if rows else []
    cells = existing + new
    if len(cells) > MAX_CELLS:
        raise ValueError(
            f"{len(cells)} cells exceed the limit of {MAX_CELLS}: shorten the range or raise the cadence"
        )
    # a dead process left these running: their trading runs were interrupted, start them over
    for c in cells:
        if c.status == "running":
            await backtest_repo.update_cell(c.id, status="pending", run_id=None)
            c.status, c.run_id = "pending", None
    # reuse the same decision from another backtest
    pending = [c for c in cells if c.status == "pending"]
    hits = await backtest_repo.find_cached([c.cache_key for c in pending], exclude_backtest=ctx.run_id)
    for c in pending:
        hit = hits.get(c.cache_key)
        if hit is not None:
            await backtest_repo.update_cell(
                c.id, status="done", decision=hit.decision, run_id=hit.run_id, cached_from=hit.id, usage={}
            )
            c.status, c.decision, c.run_id, c.cached_from = "done", hit.decision, hit.run_id, hit.id
    return cells


async def _cell_event(ctx: RunContext, c: backtest_repo.CellRecord, error: str | None = None) -> None:
    d = c.decision or {}
    await ctx.emit.emit(
        "backtest.cell",
        {
            "cell_id": c.id, "symbol": c.symbol, "day": c.day, "status": c.status, "run_id": c.run_id,
            "rating": d.get("rating"), "side": d.get("side"), "outcome": d.get("outcome"),
            "cached": c.cached_from is not None, "error": error,
        },
    )  # fmt: skip


async def backtest_workflow(ctx: RunContext) -> dict[str, Any] | None:
    services = ctx.services
    cfg = BacktestConfig.model_validate(ctx.input)
    await ctx.emit.emit("backtest.progress", {"phase": "preload", "message": "downloading bars"})
    market = await market_for(services, ctx.run_id, cfg)
    for (sym, interval), reason in market.failures.items():
        if interval != "1d":
            await ctx.emit.emit(
                "warning",
                {
                    "code": "intraday_unavailable",
                    "message": f"{sym} {interval}: {reason}; daily bars are used",
                },
            )
    cells = await _ensure_cells(ctx, cfg, market, _configured_models(services, cfg))
    tally = _Tally(cells)
    await ctx.emit.emit("backtest.progress", tally.payload("decisions"))

    sem = asyncio.Semaphore(cfg.concurrency)
    budget_hit = False
    children: set[str] = set()

    async def run_cell(c: backtest_repo.CellRecord) -> None:
        nonlocal budget_hit
        async with sem:
            if ctx.cancel.cancelled:
                return
            if cfg.budget_usd is not None and tally.cost >= cfg.budget_usd:
                budget_hit = True
                return
            day = date.fromisoformat(c.day)
            pit = PointInTimeRouter(services.router(), market.store, day)
            with use_router(pit):  # the child run's task inherits this router
                child = await services.runs.start("trading", cfg.run_input(c.symbol, day, ctx.run_id))
            children.add(child)
            c.status, c.run_id = "running", child
            tally.running += 1
            await backtest_repo.update_cell(c.id, status="running", run_id=child)
            await _cell_event(ctx, c)
            try:
                await services.runs.wait(child)
            finally:
                children.discard(child)
                tally.running -= 1
            rec = await runs_repo.get_run(child)
            status = rec.status if rec else "failed"
            usage = rec.usage if rec else {}
            tally.cost += float((usage or {}).get("cost_usd") or 0.0)
            error = None
            if status == "completed":
                c.status, c.decision = "done", cell_decision(status, rec.output if rec else None)
                tally.done += 1
            else:
                c.status = "failed"
                error = (rec.error or rec.status) if rec else "run record missing"
                tally.failed += 1
            await backtest_repo.update_cell(
                c.id, status=c.status, decision=c.decision, usage=usage or {}, error=error
            )
            await _cell_event(ctx, c, error)
            await ctx.emit.emit("backtest.progress", tally.payload("decisions"))

    todo = [c for c in cells if c.status == "pending"]
    try:
        await asyncio.gather(*(run_cell(c) for c in todo))
    except asyncio.CancelledError:
        for child in list(children):  # don't leave paid runs going after the backtest stopped
            await services.runs.cancel(child, "backtest cancelled", wait=False)
        raise
    if ctx.cancel.cancelled:
        return None
    if budget_hit:
        await ctx.emit.emit(
            "guard.limit",
            {
                "kind": "budget",
                "detail": f"budget ${cfg.budget_usd} reached; the remaining cells were not run",
            },
        )

    await ctx.emit.emit("backtest.progress", tally.payload("simulate"))
    sim = await simulate_backtest(services, ctx.run_id, cfg, cfg.sim)
    m = sim.metrics
    await ctx.emit.emit(
        "backtest.sim",
        {"sim_id": sim.id, "final_equity": str(m["final_equity"]), "total_return": float(m["total_return"]),
         "trades": int(m["trades"])},
    )  # fmt: skip
    await ctx.emit.emit("backtest.progress", tally.payload("done"))
    return {
        "cells": {"total": tally.total, "done": tally.done, "cached": tally.cached, "failed": tally.failed},
        "pending": tally.total - tally.done - tally.failed,
        "cost_usd": round(tally.cost, 6),
        "budget_hit": budget_hit,
        "sim_id": sim.id,
        "metrics": m,
    }


async def backtest_workflow_resume(ctx: RunContext, resume_value: Any) -> dict[str, Any] | None:
    return await backtest_workflow(ctx)


# -- estimate --------------------------------------------------------------------------------


def _approx_days(cfg: BacktestConfig) -> list[date]:
    """Weekdays of the range (holidays not known without the bars), every ``cadence``."""
    end = min(cfg.end_date, date.today() - timedelta(days=1))
    days = [cfg.start_date + timedelta(days=i) for i in range((end - cfg.start_date).days + 1)]
    return [d for d in days if d.weekday() < 5][:: cfg.sessions_between]


async def estimate(services: Any, cfg: BacktestConfig) -> dict[str, Any]:
    days = _approx_days(cfg)
    models = _configured_models(services, cfg)
    keys = [cache_key(cfg, s, d, models=models) for s in cfg.symbols for d in days]
    cells = len(keys)
    cached = await backtest_repo.count_cached(keys)
    recent = [
        r for r in await runs_repo.list_runs(workflow="trading", limit=100)
        if r.status == "completed" and (r.usage or {}).get("llm_calls")
        and (cfg.llm is None or (r.input.get("llm") or {}) == cfg.llm.model_dump())
    ][:30]  # fmt: skip
    per_run: dict[str, float | None] = {"cost_usd": None, "llm_calls": None, "minutes": None}
    if recent:
        n = len(recent)
        per_run["cost_usd"] = sum(float(r.usage.get("cost_usd") or 0) for r in recent) / n
        per_run["llm_calls"] = sum(float(r.usage.get("llm_calls") or 0) for r in recent) / n
        spans = [(r.finished_at - r.started_at).total_seconds() / 60 for r in recent if r.finished_at]
        per_run["minutes"] = sum(spans) / len(spans) if spans else None
    to_run = cells - cached
    return {
        "cells": cells,
        "cached": cached,
        "to_run": to_run,
        "approximate": True,  # weekdays; exchange holidays drop a few cells
        "limit": MAX_CELLS,
        "per_run": per_run,
        "based_on_runs": len(recent),
        "est_cost_usd": per_run["cost_usd"] * to_run if per_run["cost_usd"] is not None else None,
        "est_llm_calls": per_run["llm_calls"] * to_run if per_run["llm_calls"] is not None else None,
        "est_minutes": per_run["minutes"] * to_run / cfg.concurrency
        if per_run["minutes"] is not None
        else None,
    }
