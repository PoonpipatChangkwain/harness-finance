"""Repository for ``sessions`` and their UI-visible ``messages`` (conversation shown in chat)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from ulid import ULID

from app.harness.storage.db import session_scope
from app.harness.storage.models import ApprovalRow, EventRow, MessageRow, ProvenanceRow, RunRow, SessionRow


class SessionRecord(BaseModel):
    id: str
    kind: str
    title: str | None
    created_at: datetime
    updated_at: datetime
    meta: dict[str, Any] = Field(default_factory=dict)


class MessageRecord(BaseModel):
    id: str
    session_id: str
    run_id: str | None
    role: str
    content: str
    created_at: datetime
    meta: dict[str, Any] = Field(default_factory=dict)


async def create_session(
    kind: str, title: str | None = None, meta: dict[str, Any] | None = None
) -> SessionRecord:
    now = datetime.now(UTC)
    row = SessionRow(
        id=f"ses_{ULID()}", kind=kind, title=title, created_at=now, updated_at=now, meta=meta or {}
    )
    async with session_scope() as s:
        s.add(row)
        await s.commit()
    return SessionRecord.model_validate(row, from_attributes=True)


async def get_session(session_id: str) -> SessionRecord | None:
    async with session_scope() as s:
        row = await s.get(SessionRow, session_id)
        return SessionRecord.model_validate(row, from_attributes=True) if row else None


async def list_sessions(kind: str | None = None, limit: int = 50) -> list[SessionRecord]:
    async with session_scope() as s:
        stmt = select(SessionRow).order_by(SessionRow.updated_at.desc()).limit(limit)
        if kind is not None:
            stmt = stmt.where(SessionRow.kind == kind)
        return [
            SessionRecord.model_validate(r, from_attributes=True) for r in (await s.execute(stmt)).scalars()
        ]


async def delete_session(session_id: str) -> list[str] | None:
    """Delete a session with its messages, runs and their events / provenance / approvals.

    One transaction; rows are removed explicitly (SQLite does not enforce the
    FK cascade unless ``PRAGMA foreign_keys`` is on). Long-term memory is not
    per-session and is kept. Returns the deleted run ids, or None if not found.
    """
    async with session_scope() as s:
        if await s.get(SessionRow, session_id) is None:
            return None
        run_ids = list((await s.execute(select(RunRow.id).where(RunRow.session_id == session_id))).scalars())
        if run_ids:
            for model in (EventRow, ProvenanceRow, ApprovalRow):
                await s.execute(delete(model).where(model.run_id.in_(run_ids)))
            await s.execute(delete(RunRow).where(RunRow.id.in_(run_ids)))
        await s.execute(delete(MessageRow).where(MessageRow.session_id == session_id))
        await s.execute(delete(SessionRow).where(SessionRow.id == session_id))
        await s.commit()
    return run_ids


async def set_title(session_id: str, title: str) -> None:
    async with session_scope() as s:
        row = await s.get(SessionRow, session_id)
        if row is not None:
            row.title = title[:300]
            await s.commit()


async def update_meta(session_id: str, values: dict[str, Any]) -> None:
    """Merge ``values`` into ``session.meta`` (a new dict: the JSON column is not mutation-tracked)."""
    async with session_scope() as s:
        row = await s.get(SessionRow, session_id)
        if row is not None:
            row.meta = {**(row.meta or {}), **values}
            await s.commit()


async def add_message(
    session_id: str, role: str, content: str, *, run_id: str | None = None, meta: dict[str, Any] | None = None
) -> MessageRecord:
    row = MessageRow(
        id=f"msg_{ULID()}",
        session_id=session_id,
        run_id=run_id,
        role=role,
        content=content,
        created_at=datetime.now(UTC),
        meta=meta or {},
    )
    async with session_scope() as s:
        s.add(row)
        session_row = await s.get(SessionRow, session_id)
        if session_row is not None:
            session_row.updated_at = row.created_at
        await s.commit()
    return MessageRecord.model_validate(row, from_attributes=True)


async def set_message_run(message_id: str, run_id: str) -> None:
    async with session_scope() as s:
        row = await s.get(MessageRow, message_id)
        if row is not None:
            row.run_id = run_id
            await s.commit()


async def list_messages(session_id: str) -> list[MessageRecord]:
    async with session_scope() as s:
        stmt = select(MessageRow).where(MessageRow.session_id == session_id).order_by(MessageRow.created_at)
        return [
            MessageRecord.model_validate(r, from_attributes=True) for r in (await s.execute(stmt)).scalars()
        ]
