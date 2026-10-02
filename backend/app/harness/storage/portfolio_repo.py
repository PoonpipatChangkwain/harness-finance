"""Repository for portfolios, positions, revisions, plans and value snapshots (P12 §12.1, §12.7).

Portfolios are persistent memory (D2): written only by explicit user actions (PUT,
import, "apply plan") — never by an agent. Every write replaces the positions and
appends a revision holding the full snapshot plus a ``kind=user_input`` provenance
record, so every later number can be traced back to what the user entered (F3).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel
from sqlalchemy import delete, select
from ulid import ULID

from app.domain.models.portfolio import Portfolio, Position
from app.domain.models.provenance import ProvenanceRecord, new_record
from app.domain.models.symbol import Symbol
from app.harness.provenance.store import ProvenanceStore
from app.harness.storage.db import session_scope
from app.harness.storage.models import (
    PlanRow,
    PortfolioRevisionRow,
    PortfolioRow,
    PortfolioSnapshotRow,
    PositionRow,
)

DEFAULT_PORTFOLIO_ID = "pf_default"


def new_portfolio_id() -> str:
    return f"pf_{ULID()}"


def _dec(value: str | None) -> Decimal | None:
    return None if value is None else Decimal(value)


def _str(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _to_portfolio(row: PortfolioRow, positions: list[PositionRow]) -> Portfolio:
    return Portfolio(
        id=row.id,
        name=row.name,
        base_currency=row.base_currency,
        cash=_dec(row.cash),
        icon=row.icon,
        color=row.color,
        provenance_id=row.provenance_id,
        updated_at=row.updated_at,
        positions=[
            Position(
                symbol=Symbol.model_validate(p.symbol),
                quantity=Decimal(p.quantity),
                avg_cost=_dec(p.avg_cost),
                declared_weight=_dec(p.declared_weight),
                target_weight=_dec(p.target_weight),
                note=p.note,
            )
            for p in sorted(positions, key=lambda p: (p.sort, p.id))
        ],
    )


def snapshot_of(portfolio: Portfolio) -> dict[str, Any]:
    return portfolio.model_dump(mode="json", exclude={"provenance_id"})


async def _load(s: Any, portfolio_id: str) -> Portfolio | None:
    row = await s.get(PortfolioRow, portfolio_id)
    if row is None:
        return None
    stmt = select(PositionRow).where(PositionRow.portfolio_id == portfolio_id)
    return _to_portfolio(row, list((await s.execute(stmt)).scalars()))


async def get_portfolio(portfolio_id: str) -> Portfolio | None:
    async with session_scope() as s:
        return await _load(s, portfolio_id)


async def list_portfolios() -> list[Portfolio]:
    async with session_scope() as s:
        rows = list((await s.execute(select(PortfolioRow).order_by(PortfolioRow.created_at))).scalars())
        positions = list((await s.execute(select(PositionRow))).scalars())
    by_pf: dict[str, list[PositionRow]] = {}
    for p in positions:
        by_pf.setdefault(p.portfolio_id, []).append(p)
    return [_to_portfolio(r, by_pf.get(r.id, [])) for r in rows]


class RevisionRecord(BaseModel):
    id: str
    portfolio_id: str
    reason: str
    snapshot: dict[str, Any]
    provenance_id: str | None
    plan_id: str | None
    created_at: datetime


async def save_portfolio(
    portfolio_id: str,
    *,
    name: str,
    base_currency: str,
    cash: Decimal | None,
    positions: list[Position],
    reason: str,
    icon: str | None = None,
    color: str | None = None,
    plan_id: str | None = None,
    input_records: list[ProvenanceRecord] | None = None,
    provenance: ProvenanceStore | None = None,
) -> tuple[Portfolio, RevisionRecord]:
    """Create or fully replace a book, append a revision and its ``user_input`` record."""
    now = datetime.now(UTC)
    revision_id = f"rev_{ULID()}"
    inputs = input_records or []
    async with session_scope() as s:
        row = await s.get(PortfolioRow, portfolio_id)
        if row is None:
            row = PortfolioRow(id=portfolio_id, created_at=now, meta={})
            s.add(row)
        row.name = name
        row.base_currency = base_currency.upper()
        row.cash = _str(cash)
        row.icon = icon
        row.color = color
        row.updated_at = now
        await s.execute(delete(PositionRow).where(PositionRow.portfolio_id == portfolio_id))
        for i, pos in enumerate(positions):
            s.add(
                PositionRow(
                    portfolio_id=portfolio_id,
                    sort=i,
                    symbol_canonical=pos.symbol.canonical,
                    raw=pos.symbol.raw[:40],
                    symbol=pos.symbol.model_dump(mode="json"),
                    quantity=str(pos.quantity),
                    avg_cost=_str(pos.avg_cost),
                    declared_weight=_str(pos.declared_weight),
                    target_weight=_str(pos.target_weight),
                    note=pos.note,
                )
            )
        await s.flush()
        saved = await _load(s, portfolio_id)
        assert saved is not None
        snapshot = snapshot_of(saved)
        record = new_record(
            "user_input",
            snapshot,
            source="user",
            tool="portfolio.save",
            unit=saved.base_currency,
            params={"portfolio_id": portfolio_id, "revision_id": revision_id, "reason": reason},
            inputs=[r.id for r in inputs],
        )
        row.provenance_id = record.id
        revision = PortfolioRevisionRow(
            id=revision_id,
            portfolio_id=portfolio_id,
            reason=reason,
            snapshot=snapshot,
            provenance_id=record.id,
            plan_id=plan_id,
            created_at=now,
        )
        s.add(revision)
        await s.commit()
    await (provenance or ProvenanceStore()).add_many([*inputs, record])
    saved = saved.model_copy(update={"provenance_id": record.id})
    return saved, _to_revision(revision)


async def update_meta(
    portfolio_id: str, *, name: str | None = None, icon: str | None = None, color: str | None = None
) -> Portfolio | None:
    async with session_scope() as s:
        row = await s.get(PortfolioRow, portfolio_id)
        if row is None:
            return None
        if name is not None:
            row.name = name
        if icon is not None:
            row.icon = icon or None
        if color is not None:
            row.color = color or None
        row.updated_at = datetime.now(UTC)
        await s.commit()
        return await _load(s, portfolio_id)


async def delete_portfolio(portfolio_id: str) -> bool:
    async with session_scope() as s:
        row = await s.get(PortfolioRow, portfolio_id)
        if row is None:
            return False
        await s.execute(delete(PositionRow).where(PositionRow.portfolio_id == portfolio_id))
        await s.execute(delete(PortfolioRevisionRow).where(PortfolioRevisionRow.portfolio_id == portfolio_id))
        await s.delete(row)
        await s.commit()
        return True


def _to_revision(row: PortfolioRevisionRow) -> RevisionRecord:
    return RevisionRecord(
        id=row.id,
        portfolio_id=row.portfolio_id,
        reason=row.reason,
        snapshot=row.snapshot or {},
        provenance_id=row.provenance_id,
        plan_id=row.plan_id,
        created_at=row.created_at,
    )


async def list_revisions(portfolio_id: str, limit: int = 50) -> list[RevisionRecord]:
    async with session_scope() as s:
        stmt = (
            select(PortfolioRevisionRow)
            .where(PortfolioRevisionRow.portfolio_id == portfolio_id)
            .order_by(PortfolioRevisionRow.created_at.desc(), PortfolioRevisionRow.id.desc())
            .limit(limit)
        )
        return [_to_revision(r) for r in (await s.execute(stmt)).scalars()]


# -- plans --------------------------------------------------------------------------------------


class PlanRecord(BaseModel):
    id: str
    portfolio_id: str
    run_id: str | None
    status: str
    proposal: dict[str, Any]
    response: dict[str, Any] | None
    created_at: datetime
    approved_at: datetime | None
    applied_at: datetime | None
    applied_revision_id: str | None


def _to_plan(row: PlanRow) -> PlanRecord:
    return PlanRecord(
        id=row.id,
        portfolio_id=row.portfolio_id,
        run_id=row.run_id,
        status=row.status,
        proposal=row.proposal or {},
        response=row.response,
        created_at=row.created_at,
        approved_at=row.approved_at,
        applied_at=row.applied_at,
        applied_revision_id=row.applied_revision_id,
    )


async def save_plan(record: dict[str, Any]) -> str:
    """Store an approved plan; keyed by run so a crash-resumed ``save_plan`` node overwrites."""
    now = datetime.now(UTC)
    async with session_scope() as s:
        row = None
        if record.get("run_id"):
            stmt = select(PlanRow).where(PlanRow.run_id == record["run_id"])
            row = (await s.execute(stmt)).scalars().first()
        if row is None:
            row = PlanRow(id=f"plan_{ULID()}", created_at=now)
            s.add(row)
        row.portfolio_id = record["portfolio_id"]
        row.run_id = record.get("run_id")
        row.status = record.get("status", "approved")
        row.proposal = record.get("proposal") or {}
        row.response = record.get("response")
        row.approved_at = now
        await s.commit()
        return row.id


async def get_plan(plan_id: str) -> PlanRecord | None:
    async with session_scope() as s:
        row = await s.get(PlanRow, plan_id)
        return _to_plan(row) if row else None


async def list_plans(portfolio_id: str, limit: int = 50) -> list[PlanRecord]:
    async with session_scope() as s:
        stmt = (
            select(PlanRow)
            .where(PlanRow.portfolio_id == portfolio_id)
            .order_by(PlanRow.created_at.desc())
            .limit(limit)
        )
        return [_to_plan(r) for r in (await s.execute(stmt)).scalars()]


async def mark_plan_applied(plan_id: str, revision_id: str) -> None:
    async with session_scope() as s:
        row = await s.get(PlanRow, plan_id)
        if row is not None:
            row.status = "applied"
            row.applied_at = datetime.now(UTC)
            row.applied_revision_id = revision_id
            await s.commit()


# -- value snapshots (weekly job) ---------------------------------------------------------------


class SnapshotRecord(BaseModel):
    id: str
    portfolio_id: str
    kind: str
    taken_at: datetime
    summary: dict[str, Any]
    run_id: str | None
    session_id: str | None


def _to_snapshot(row: PortfolioSnapshotRow) -> SnapshotRecord:
    return SnapshotRecord(
        id=row.id,
        portfolio_id=row.portfolio_id,
        kind=row.kind,
        taken_at=row.taken_at,
        summary=row.summary or {},
        run_id=row.run_id,
        session_id=row.session_id,
    )


async def add_snapshot(
    portfolio_id: str,
    *,
    kind: str,
    summary: dict[str, Any],
    run_id: str | None = None,
    session_id: str | None = None,
) -> SnapshotRecord:
    row = PortfolioSnapshotRow(
        id=f"snap_{ULID()}",
        portfolio_id=portfolio_id,
        kind=kind,
        taken_at=datetime.now(UTC),
        summary=summary,
        run_id=run_id,
        session_id=session_id,
    )
    async with session_scope() as s:
        s.add(row)
        await s.commit()
    return _to_snapshot(row)


async def list_snapshots(portfolio_id: str | None = None, limit: int = 50) -> list[SnapshotRecord]:
    async with session_scope() as s:
        stmt = select(PortfolioSnapshotRow).order_by(PortfolioSnapshotRow.taken_at.desc()).limit(limit)
        if portfolio_id is not None:
            stmt = stmt.where(PortfolioSnapshotRow.portfolio_id == portfolio_id)
        return [_to_snapshot(r) for r in (await s.execute(stmt)).scalars()]
