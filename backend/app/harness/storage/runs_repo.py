"""Repository for the ``runs`` aggregate."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import delete, select, update

from app.harness.storage.db import session_scope
from app.harness.storage.models import ApprovalRow, EventRow, ProvenanceRow, RunRow


class RunRecord(BaseModel):
    id: str
    session_id: str | None
    workflow: str
    status: str
    input: dict[str, Any]
    output: dict[str, Any] | None
    error: str | None
    thread_id: str | None
    started_at: datetime
    finished_at: datetime | None
    usage: dict[str, Any]


def _to_record(row: RunRow) -> RunRecord:
    return RunRecord(
        id=row.id,
        session_id=row.session_id,
        workflow=row.workflow,
        status=row.status,
        input=row.input or {},
        output=row.output,
        error=row.error,
        thread_id=row.thread_id,
        started_at=row.started_at,
        finished_at=row.finished_at,
        usage=row.usage or {},
    )


async def create_run(
    run_id: str,
    workflow: str,
    input: dict[str, Any],
    *,
    session_id: str | None = None,
    thread_id: str | None = None,
) -> RunRecord:
    row = RunRow(
        id=run_id,
        session_id=session_id,
        workflow=workflow,
        status="running",
        input=input,
        thread_id=thread_id,
        started_at=datetime.now(UTC),
        usage={},
    )
    async with session_scope() as s:
        s.add(row)
        await s.commit()
    return _to_record(row)


async def get_run(run_id: str) -> RunRecord | None:
    async with session_scope() as s:
        row = await s.get(RunRow, run_id)
        return _to_record(row) if row else None


async def finish_run(
    run_id: str,
    status: str,
    *,
    output: dict[str, Any] | None = None,
    error: str | None = None,
    usage: dict[str, Any] | None = None,
) -> None:
    values: dict[str, Any] = {"status": status, "finished_at": datetime.now(UTC)}
    if output is not None:
        values["output"] = output
    if error is not None:
        values["error"] = error
    if usage is not None:
        values["usage"] = usage
    async with session_scope() as s:
        await s.execute(update(RunRow).where(RunRow.id == run_id).values(**values))
        await s.commit()


async def set_status(run_id: str, status: str, *, thread_id: str | None = None) -> None:
    """A status transition that is not a terminal ``finish_run`` (e.g. ``running`` → ``waiting_approval``)."""
    values: dict[str, Any] = {"status": status}
    if thread_id is not None:
        values["thread_id"] = thread_id
    async with session_scope() as s:
        await s.execute(update(RunRow).where(RunRow.id == run_id).values(**values))
        await s.commit()


async def update_usage(run_id: str, usage: dict[str, Any]) -> None:
    async with session_scope() as s:
        await s.execute(update(RunRow).where(RunRow.id == run_id).values(usage=usage))
        await s.commit()


async def mark_running_as_interrupted() -> list[str]:
    """Runs left ``running`` by a previous process are ``interrupted`` (resumable in P09)."""
    async with session_scope() as s:
        ids = list((await s.execute(select(RunRow.id).where(RunRow.status == "running"))).scalars())
        if ids:
            await s.execute(
                update(RunRow)
                .where(RunRow.id.in_(ids))
                .values(status="interrupted", finished_at=datetime.now(UTC))
            )
            await s.commit()
    return ids


async def list_runs(
    *, session_id: str | None = None, workflow: str | None = None, limit: int = 50
) -> list[RunRecord]:
    async with session_scope() as s:
        stmt = select(RunRow).order_by(RunRow.started_at.desc()).limit(limit)
        if session_id is not None:
            stmt = stmt.where(RunRow.session_id == session_id)
        if workflow is not None:
            stmt = stmt.where(RunRow.workflow == workflow)
        return [_to_record(r) for r in (await s.execute(stmt)).scalars()]


async def runs_for_thread(thread_id: str, *, statuses: tuple[str, ...]) -> list[RunRecord]:
    async with session_scope() as s:
        stmt = select(RunRow).where(RunRow.thread_id == thread_id, RunRow.status.in_(statuses))
        return [_to_record(r) for r in (await s.execute(stmt)).scalars()]


async def delete_run(run_id: str) -> bool:
    """Delete a standalone run (no session) with its events / provenance / approvals.

    For a run started outside a session (trading, portfolio): a session's own
    runs are deleted with it via ``sessions_repo.delete_session`` instead.
    Returns False if the run does not exist.
    """
    async with session_scope() as s:
        if await s.get(RunRow, run_id) is None:
            return False
        for model in (EventRow, ProvenanceRow, ApprovalRow):
            await s.execute(delete(model).where(model.run_id == run_id))
        await s.execute(delete(RunRow).where(RunRow.id == run_id))
        await s.commit()
    return True
