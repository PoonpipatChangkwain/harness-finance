"""Repository for the ``approvals`` aggregate (T4, phase-09 §9.6/§9.7)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy import select, update

from app.harness.storage.db import session_scope
from app.harness.storage.models import ApprovalRow

Outcome = Literal["approved", "rejected", "edited"]


class ApprovalRecord(BaseModel):
    id: str
    run_id: str
    node: str | None
    kind: str
    status: str  # pending | approved | rejected | edited
    request: dict[str, Any]
    response: dict[str, Any] | None
    created_at: datetime
    resolved_at: datetime | None


def _to_record(row: ApprovalRow) -> ApprovalRecord:
    return ApprovalRecord(
        id=row.id,
        run_id=row.run_id,
        node=row.node,
        kind=row.kind,
        status=row.status,
        request=row.request or {},
        response=row.response,
        created_at=row.created_at,
        resolved_at=row.resolved_at,
    )


async def create_approval(
    approval_id: str, run_id: str, *, node: str | None, kind: str, request: dict[str, Any]
) -> ApprovalRecord:
    row = ApprovalRow(id=approval_id, run_id=run_id, node=node, kind=kind, status="pending", request=request)
    async with session_scope() as s:
        s.add(row)
        await s.commit()
    return _to_record(row)


async def get_approval(approval_id: str) -> ApprovalRecord | None:
    async with session_scope() as s:
        row = await s.get(ApprovalRow, approval_id)
        return _to_record(row) if row else None


async def resolve_approval(
    approval_id: str, *, outcome: Outcome, response: dict[str, Any]
) -> ApprovalRecord | None:
    async with session_scope() as s:
        await s.execute(
            update(ApprovalRow)
            .where(ApprovalRow.id == approval_id)
            .values(status=outcome, response=response, resolved_at=datetime.now(UTC))
        )
        await s.commit()
        row = await s.get(ApprovalRow, approval_id)
        return _to_record(row) if row else None


async def pending_for_run(run_id: str) -> ApprovalRecord | None:
    async with session_scope() as s:
        stmt = (
            select(ApprovalRow)
            .where(ApprovalRow.run_id == run_id, ApprovalRow.status == "pending")
            .order_by(ApprovalRow.created_at.desc())
        )
        row = (await s.execute(stmt)).scalars().first()
        return _to_record(row) if row else None


async def list_for_run(run_id: str) -> list[ApprovalRecord]:
    async with session_scope() as s:
        stmt = select(ApprovalRow).where(ApprovalRow.run_id == run_id).order_by(ApprovalRow.created_at)
        return [_to_record(r) for r in (await s.execute(stmt)).scalars()]
