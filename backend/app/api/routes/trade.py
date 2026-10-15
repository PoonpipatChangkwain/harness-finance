"""Trading workflow endpoints (phase-10 §10.5, contracts.md §11).

Resume, approvals and raw graph state reuse the generic P09 endpoints
(``/runs/{id}/resume``, ``/approvals/{id}``, ``/runs/{id}/state``).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.api.routes.sessions import LLMChoice, _check_llm_choice
from app.domain.models.symbol import normalize_symbol, safe_path_component
from app.domain.workflows.trade_outcome import OutcomeError, TradeOutcomeInput
from app.domain.workflows.trading import TRADING_WORKFLOW, TradeRunInput
from app.harness.graph.checkpoint import graph_signature, thread_id
from app.harness.runtime.services import get_services
from app.harness.storage import approvals_repo, events_repo, runs_repo
from app.harness.workflow.runner import graph_state_snapshot
from app.harness.workflow.trading import (
    DecisionNotFound,
    MemoryDecisionLog,
    settle_trade,
    trade_date,
    trade_scope,
)

router = APIRouter(tags=["trade"])

_OPEN_STATUSES = ("running", "waiting_approval", "interrupted")


class TradeRunRequest(TradeRunInput):
    llm: LLMChoice | None = None


class TradeRunStarted(BaseModel):
    run_id: str
    thread_id: str


@router.post("/trade/runs", response_model=TradeRunStarted)
async def start_trade_run(body: TradeRunRequest) -> TradeRunStarted:
    if body.backtest_id is not None:  # auto-approval is the backtest runner's, never a user's
        raise HTTPException(status_code=400, detail="backtest_id is set by the backtest runner only")
    try:
        safe_path_component(normalize_symbol(body.symbol).canonical)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    run_input = body.model_dump(mode="json", exclude={"llm"})
    if body.llm is not None:
        run_input["llm"] = _check_llm_choice(body.llm)
    tid = thread_id(
        trade_scope(run_input), trade_date("", run_input), graph_signature(TRADING_WORKFLOW, run_input)
    )
    open_runs = await runs_repo.runs_for_thread(tid, statuses=_OPEN_STATUSES)
    if open_runs:
        raise HTTPException(
            status_code=409,
            detail=f"run {open_runs[0].id} for this symbol/date/graph is still {open_runs[0].status}; "
            "resume, approve or cancel it first",
        )
    run_id = await get_services().runs.start("trading", run_input)
    return TradeRunStarted(run_id=run_id, thread_id=tid)


def _brief(run: runs_repo.RunRecord) -> dict[str, Any]:
    out = run.output or {}
    return {
        "run_id": run.id,
        "symbol": out.get("symbol") or run.input.get("symbol"),
        "date": run.input.get("analysis_date"),
        "rating": out.get("rating"),
        "verdict": out.get("verdict"),
        "outcome": out.get("status"),
        "status": run.status,
        "created_at": run.started_at.isoformat(),
    }


@router.get("/trade/runs")
async def list_trade_runs(
    symbol: str | None = None, limit: int = Query(default=50, ge=1, le=200)
) -> dict[str, Any]:
    canonical: str | None = None
    if symbol:
        try:
            canonical = normalize_symbol(symbol).canonical
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    runs = await runs_repo.list_runs(workflow="trading", limit=limit)
    return {"runs": [_brief(r) for r in runs if canonical is None or trade_scope(r.input) == canonical]}


async def _node_states(run_id: str) -> dict[str, dict[str, Any]]:
    nodes: dict[str, dict[str, Any]] = {}
    for ev in await events_repo.events_after(run_id, 0):
        if ev.type == "node.started" and ev.node:
            nodes[ev.node] = {**nodes.get(ev.node, {}), "status": "running", "started_at": ev.ts.isoformat(),
                              "runs": nodes.get(ev.node, {}).get("runs", 0) + 1}  # fmt: skip
        elif ev.type == "node.finished" and ev.node:
            node = nodes.setdefault(ev.node, {})
            node.update(
                status=ev.payload.get("status"),
                finished_at=ev.ts.isoformat(),
                duration_ms=ev.payload.get("duration_ms"),
                summary=ev.payload.get("summary"),
            )
    return nodes


class RunDeleted(BaseModel):
    deleted: str


@router.delete("/trade/runs/{run_id}", response_model=RunDeleted)
async def delete_trade_run(run_id: str) -> RunDeleted:
    run = await runs_repo.get_run(run_id)
    if run is None or run.workflow != "trading":
        raise HTTPException(status_code=404, detail=f"trade run {run_id} not found")
    services = get_services()
    if services.runs.is_running(run_id):
        raise HTTPException(status_code=409, detail=f"run {run_id} is still running; stop it first")
    if run.thread_id is not None:
        await services.checkpoints.clear_checkpoint(trade_scope(run.input), run.thread_id)
    await runs_repo.delete_run(run_id)
    (services.settings.scratchpad_dir / f"{run_id}.jsonl").unlink(missing_ok=True)
    return RunDeleted(deleted=run_id)


@router.get("/trade/runs/{run_id}")
async def get_trade_run(run_id: str) -> dict[str, Any]:
    run = await runs_repo.get_run(run_id)
    if run is None or run.workflow != "trading":
        raise HTTPException(status_code=404, detail=f"trade run {run_id} not found")
    state: dict[str, Any] = dict((run.output or {}).get("state") or {})
    if not state and run.thread_id is not None:
        try:
            snapshot = await graph_state_snapshot(
                TRADING_WORKFLOW,
                get_services().checkpoints,
                scope=trade_scope(run.input),
                thread_id=run.thread_id,
            )
            state = dict(snapshot.values)
        except Exception:
            state = {}
    pending = await approvals_repo.pending_for_run(run_id) if run.status == "waiting_approval" else None
    found = await MemoryDecisionLog(get_services().memory).find(run_id)
    return {
        "trade_result": _result(found[1]) if found else None,
        "event_risk": state.get("event_risk"),
        "ml_signal": state.get("ml_signal") or None,  # {} = no model covers this symbol/horizon
        "run": run.model_dump(mode="json"),
        "input": run.input,
        "graph_view": TRADING_WORKFLOW.graph_view().model_dump(),
        "nodes": await _node_states(run_id),
        "instrument": state.get("instrument"),
        "market_snapshot": state.get("market_snapshot"),
        "portfolio_ctx": state.get("portfolio_ctx"),  # (P12) positions | flat | not_provided
        "reports": state.get("reports"),
        "invest_debate": state.get("invest_debate"),
        "research_plan": state.get("research_plan"),
        "trade_proposal": state.get("trade_proposal"),
        "risk_debate": state.get("risk_debate"),
        "decision": state.get("final_decision"),
        "policy": state.get("policy"),
        "live": state.get("live"),
        "approval": state.get("approval") or (pending.model_dump(mode="json") if pending else None),
        "outcome": state.get("outcome"),
        "report_md": (run.output or {}).get("report_md"),
    }


def _result(record: dict[str, Any]) -> dict[str, Any]:
    """The recorded decision + how it ended (``outcome`` is None until the trader settles it)."""
    return {
        "run_id": record.get("run_id"),
        "symbol": record.get("symbol"),
        "date": record.get("date"),
        "recorded_at": record.get("recorded_at"),
        "card": record.get("card"),
        "outcome": record.get("outcome"),
        "reflection": record.get("reflection"),
        "reflection_error": record.get("reflection_error"),
    }


@router.post("/trade/runs/{run_id}/outcome")
async def settle_trade_run(run_id: str, body: TradeOutcomeInput) -> dict[str, Any]:
    """The trader ticks how an approved trade ended (TP/SL/manual/time stop/not filled/cancelled);
    the system computes R and writes the lesson future runs on this symbol will read."""
    try:
        record = await settle_trade(get_services(), run_id, body)
    except DecisionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except OutcomeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _result(record)


@router.get("/trade/decisions")
async def list_trade_decisions(
    symbol: str | None = None,
    open_only: bool = False,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    canonical: str | None = None
    if symbol:
        try:
            canonical = normalize_symbol(symbol).canonical
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    rows = await MemoryDecisionLog(get_services().memory).list(canonical, limit=limit)
    results = [_result(r) for r in rows]
    if open_only:
        results = [r for r in results if r["outcome"] is None]
    return {"decisions": results}
