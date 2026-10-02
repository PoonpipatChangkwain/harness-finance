"""Repository for the append-only ``events`` log."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select

from app.harness.storage.db import session_scope
from app.harness.storage.models import EventRow


async def insert_event(
    run_id: str,
    seq: int,
    ts: datetime,
    type: str,
    payload: dict[str, Any],
    *,
    node: str | None = None,
    call_id: str | None = None,
    parent_call_id: str | None = None,
) -> None:
    async with session_scope() as s:
        s.add(
            EventRow(
                run_id=run_id,
                seq=seq,
                ts=ts,
                type=type,
                payload=payload,
                node=node,
                call_id=call_id,
                parent_call_id=parent_call_id,
            )
        )
        await s.commit()


async def max_seq(run_id: str) -> int:
    async with session_scope() as s:
        value = (await s.execute(select(func.max(EventRow.seq)).where(EventRow.run_id == run_id))).scalar()
        return int(value or 0)


async def events_after(run_id: str, after_seq: int) -> list[EventRow]:
    async with session_scope() as s:
        stmt = (
            select(EventRow).where(EventRow.run_id == run_id, EventRow.seq > after_seq).order_by(EventRow.seq)
        )
        return list((await s.execute(stmt)).scalars())
