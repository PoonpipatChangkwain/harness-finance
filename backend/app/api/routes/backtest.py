"""Backtest endpoints (P15, contracts.md §11).

A backtest is a ``runs`` row (workflow ``backtest``): its live events, cancel and resume use
the generic ``/runs/{id}/events|cancel|resume``. Re-simulating with another capital/risk/cost
setting is ``POST /backtests/{id}/simulate`` — no LLM call, the decisions are stored.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ValidationError
from ulid import ULID

from app.api.routes.sessions import LLMChoice, _check_llm_choice
from app.domain.compute.backtest import SimConfig
from app.domain.workflows.backtest import MAX_CELLS, BacktestConfig
from app.harness.runtime.limits import RunLimits
from app.harness.runtime.services import get_services
from app.harness.storage import backtest_repo, runs_repo
from app.harness.workflow.backtest import estimate, simulate_backtest

router = APIRouter(tags=["backtest"])

BACKTEST_WALL_CLOCK_S = 24 * 3600


class BacktestStarted(BaseModel):
    backtest_id: str


class BacktestDeleted(BaseModel):
    deleted: str


def _checked(cfg: BacktestConfig) -> BacktestConfig:
    if cfg.llm is not None:
        choice = _check_llm_choice(LLMChoice(provider=cfg.llm.provider, model=cfg.llm.model))
        cfg = cfg.model_copy(update={"llm": cfg.llm.model_copy(update=choice)})
    return cfg


@router.post("/backtests/estimate")
async def estimate_backtest(body: BacktestConfig) -> dict[str, Any]:
    """How many cells, how many are already decided (cache), and what the rest may cost."""
    return await estimate(get_services(), body)


@router.post("/backtests", response_model=BacktestStarted)
async def start_backtest(body: BacktestConfig) -> BacktestStarted:
    cfg = _checked(body)
    est = await estimate(get_services(), cfg)
    if est["cells"] > MAX_CELLS:
        raise HTTPException(
            status_code=400,
            detail=f"~{est['cells']} cells exceed the limit of {MAX_CELLS}: "
            "shorten the range or raise the cadence",
        )
    services = get_services()
    limits = RunLimits.from_settings(services.settings, run_wall_clock_s=BACKTEST_WALL_CLOCK_S)
    run_id = await services.runs.start(
        "backtest", cfg.model_dump(mode="json"), limits=limits, thread_id=f"backtest_{ULID()}"
    )
    return BacktestStarted(backtest_id=run_id)


async def _require(backtest_id: str) -> runs_repo.RunRecord:
    run = await runs_repo.get_run(backtest_id)
    if run is None or run.workflow != "backtest":
        raise HTTPException(status_code=404, detail=f"backtest {backtest_id} not found")
    return run


def _brief(run: runs_repo.RunRecord) -> dict[str, Any]:
    out = run.output or {}
    m = out.get("metrics") or {}
    return {
        "backtest_id": run.id,
        "name": run.input.get("name") or "",
        "symbols": run.input.get("symbols") or [],
        "start_date": run.input.get("start_date"),
        "end_date": run.input.get("end_date"),
        "horizon": run.input.get("horizon"),
        "status": run.status,
        "created_at": run.started_at.isoformat(),
        "final_equity": m.get("final_equity"),
        "total_return": m.get("total_return"),
        "currency": m.get("currency"),
        "cost_usd": out.get("cost_usd"),
    }


@router.get("/backtests")
async def list_backtests(limit: int = Query(default=50, ge=1, le=200)) -> dict[str, Any]:
    return {"backtests": [_brief(r) for r in await runs_repo.list_runs(workflow="backtest", limit=limit)]}


def _sim_brief(sim: backtest_repo.SimRecord) -> dict[str, Any]:
    return {
        "sim_id": sim.id,
        "created_at": sim.created_at.isoformat(),
        "config": sim.config,
        "metrics": sim.metrics,
    }


@router.get("/backtests/{backtest_id}")
async def get_backtest(backtest_id: str) -> dict[str, Any]:
    run = await _require(backtest_id)
    cells = await backtest_repo.cells(backtest_id)
    sims = await backtest_repo.sims(backtest_id)
    latest = sims[-1] if sims else None
    return {
        **_brief(run),
        "run": run.model_dump(mode="json"),
        "config": run.input,
        "output": run.output,
        "running": get_services().runs.is_running(backtest_id),
        "cells": [c.model_dump(mode="json") for c in cells],
        "sims": [_sim_brief(s) for s in sims],
        "latest_sim": {**_sim_brief(latest), "result": latest.result} if latest else None,
    }


@router.post("/backtests/{backtest_id}/simulate")
async def simulate(backtest_id: str, body: SimConfig) -> dict[str, Any]:
    """Replay the stored decisions with another capital / risk / cost setting (no LLM)."""
    run = await _require(backtest_id)
    try:
        cfg = BacktestConfig.model_validate(run.input)
    except ValidationError as exc:  # e.g. the start date is no longer "in the past" rules
        raise HTTPException(status_code=409, detail=f"stored config no longer valid: {exc}") from exc
    try:
        sim = await simulate_backtest(get_services(), backtest_id, cfg, body)
    except ValueError as exc:  # missing FX for the chosen base currency
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {**_sim_brief(sim), "result": sim.result}


@router.get("/backtests/{backtest_id}/sims/{sim_id}")
async def get_sim(backtest_id: str, sim_id: str) -> dict[str, Any]:
    sim = await backtest_repo.get_sim(sim_id)
    if sim is None or sim.backtest_id != backtest_id:
        raise HTTPException(status_code=404, detail=f"simulation {sim_id} not found")
    return {**_sim_brief(sim), "result": sim.result}


@router.delete("/backtests/{backtest_id}", response_model=BacktestDeleted)
async def delete_backtest(backtest_id: str) -> BacktestDeleted:
    await _require(backtest_id)
    if get_services().runs.is_running(backtest_id):
        raise HTTPException(
            status_code=409, detail=f"backtest {backtest_id} is still running; cancel it first"
        )
    await backtest_repo.delete_backtest(backtest_id)
    await runs_repo.delete_run(backtest_id)
    return BacktestDeleted(deleted=backtest_id)
