"""Run endpoints: status, cancel, SSE event stream (replayable), scratchpad, provenance (contracts.md §11)."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request
from sse_starlette import EventSourceResponse

from app.harness.runtime.services import get_services
from app.harness.state.scratchpad import read_entries
from app.harness.storage import approvals_repo, runs_repo
from app.harness.workflow.registry import GRAPH_WORKFLOWS, scope_for
from app.harness.workflow.runner import ResumeRejected, graph_state_snapshot

router = APIRouter(tags=["runs"])

_RUN_ID_RE = re.compile(r"^run_[0-9A-HJKMNP-TV-Z]{26}$")
_RESUMABLE_STATUSES = ("waiting_approval", "interrupted", "failed")
HEARTBEAT_S = 15


async def _require_run(run_id: str) -> runs_repo.RunRecord:
    if not _RUN_ID_RE.match(run_id):
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    run = await runs_repo.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    return run


def _is_resumable(run: runs_repo.RunRecord) -> bool:
    return (
        run.status in _RESUMABLE_STATUSES
        and run.thread_id is not None
        and run.workflow in get_services().runs.resume_fns
    )


async def _current_node(run: runs_repo.RunRecord) -> str | None:
    spec = GRAPH_WORKFLOWS.get(run.workflow)
    if spec is None or run.thread_id is None:
        return None
    try:
        snapshot = await graph_state_snapshot(
            spec,
            get_services().checkpoints,
            scope=scope_for(run.workflow, run.input),
            thread_id=run.thread_id,
        )
    except Exception:
        return None
    return snapshot.next[0] if snapshot.next else None


@router.get("/runs/{run_id}")
async def get_run(run_id: str) -> dict[str, Any]:
    run = await _require_run(run_id)
    resumable = _is_resumable(run)
    current_node = await _current_node(run) if resumable else None
    pending_record = (
        await approvals_repo.pending_for_run(run_id) if run.status == "waiting_approval" else None
    )
    pending = pending_record.model_dump(mode="json") if pending_record else None
    return {
        "run": run.model_dump(mode="json"),
        "active": get_services().runs.is_running(run_id),
        "current_node": current_node,
        "pending_approval": pending,
        "resumable": resumable,
    }


@router.get("/runs/{run_id}/state")
async def get_run_state(run_id: str) -> dict[str, Any]:
    run = await _require_run(run_id)
    spec = GRAPH_WORKFLOWS.get(run.workflow)
    if spec is None or run.thread_id is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} has no graph checkpoint")
    snapshot = await graph_state_snapshot(
        spec, get_services().checkpoints, scope=scope_for(run.workflow, run.input), thread_id=run.thread_id
    )
    return {
        "values": snapshot.values,
        "next": list(snapshot.next),
        "thread_id": run.thread_id,
    }


@router.post("/runs/{run_id}/resume")
async def resume_run(run_id: str) -> dict[str, Any]:
    run = await _require_run(run_id)
    services = get_services()
    if services.runs.is_running(run_id):
        raise HTTPException(status_code=409, detail=f"run {run_id} is already active")
    resumed_from_node = await _current_node(run)
    try:
        await services.runs.resume(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ResumeRejected as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"run_id": run_id, "resumed_from_node": resumed_from_node}


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str) -> dict[str, Any]:
    await _require_run(run_id)
    cancelled = await get_services().runs.cancel(run_id, wait=False)
    return {"run_id": run_id, "cancelled": cancelled}


def _start_seq(last_event_id: str | None, after: int | None) -> int:
    if last_event_id:
        try:
            return max(0, int(last_event_id))
        except ValueError:
            pass
    return max(0, after or 0)


@router.get("/runs/{run_id}/events")
async def run_events(
    run_id: str,
    request: Request,
    after: int | None = Query(default=None, ge=0),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> EventSourceResponse:
    run = await _require_run(run_id)
    start = _start_seq(last_event_id, after)
    bus = get_services().bus

    async def stream() -> AsyncIterator[dict[str, Any]]:
        async for env in bus.subscribe(run_id, start, session_id=run.session_id):
            if await request.is_disconnected():
                break
            message: dict[str, Any] = {"event": env.type, "data": env.model_dump_json()}
            if not env.ephemeral:
                message["id"] = str(env.seq)
            yield message

    return EventSourceResponse(stream(), ping=HEARTBEAT_S)


@router.get("/runs/{run_id}/scratchpad")
async def run_scratchpad(
    run_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=1000),
    tool: str | None = None,
) -> dict[str, Any]:
    await _require_run(run_id)
    path = get_services().settings.scratchpad_dir / f"{run_id}.jsonl"
    entries = read_entries(path)
    if tool:
        entries = [e for e in entries if e.get("tool") == tool]
    return {"total": len(entries), "offset": offset, "entries": entries[offset : offset + limit]}


@router.get("/runs/{run_id}/provenance")
async def run_provenance(run_id: str) -> dict[str, Any]:
    await _require_run(run_id)
    records = await get_services().provenance.list(run_id)
    return {"records": [r.model_dump(mode="json") for r in records]}


@router.get("/provenance/{provenance_id}")
async def get_provenance(provenance_id: str, lineage: int = Query(default=0, ge=0, le=1)) -> dict[str, Any]:
    store = get_services().provenance
    if lineage:
        tree = await store.lineage(provenance_id)
        if tree is None:
            raise HTTPException(status_code=404, detail=f"provenance {provenance_id} not found")
        return {"lineage": tree.model_dump(mode="json")}
    record = await store.get(provenance_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"provenance {provenance_id} not found")
    return {"record": record.model_dump(mode="json")}
