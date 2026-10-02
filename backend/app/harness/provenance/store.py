"""SQLite-backed provenance store with lineage (F3).

The domain creates ``ProvenanceRecord`` objects and hands them back; the
harness persists them here (upsert by id) and answers "where did this
number come from" by walking ``inputs`` (``lineage``).
Append-only-log idea from ``../TradingAgents/tradingagents/decision_log.py``.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select

from app.domain.models.provenance import ProvenanceRecord
from app.harness.storage.db import session_scope
from app.harness.storage.models import ProvenanceRow


class LineageNode(BaseModel):
    record: ProvenanceRecord
    inputs: list[LineageNode] = []
    missing_inputs: list[str] = []


def _as_of_str(value: date | datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _as_of_parse(value: str | None) -> date | datetime | None:
    if not value:
        return None
    if "T" in value:
        return datetime.fromisoformat(value)
    return date.fromisoformat(value)


def _to_record(row: ProvenanceRow) -> ProvenanceRecord:
    return ProvenanceRecord(
        id=row.id,
        kind=row.kind,  # type: ignore[arg-type]
        value=row.value,
        unit=row.unit,
        source=row.source,
        tool=row.tool,
        calculation=row.calculation,
        params=row.params or {},
        inputs=row.inputs or [],
        agent=row.agent,
        run_id=row.run_id,
        as_of=_as_of_parse(row.as_of),
        fetched_at=row.fetched_at,
        url=row.url,
    )


def _jsonable(value: Any) -> Any:
    from pydantic_core import to_jsonable_python

    return to_jsonable_python(value, fallback=str)


class ProvenanceStore:
    async def add_many(
        self, records: list[ProvenanceRecord], run_id: str | None = None, *, tool: str | None = None
    ) -> list[str]:
        if not records:
            return []
        async with session_scope() as s:
            for rec in records:
                row = await s.get(ProvenanceRow, rec.id)
                values = {
                    "run_id": rec.run_id or run_id,
                    "kind": rec.kind,
                    "value": _jsonable(rec.value),
                    "unit": rec.unit,
                    "source": rec.source,
                    "tool": rec.tool or tool,
                    "calculation": rec.calculation,
                    "params": _jsonable(rec.params),
                    "inputs": list(rec.inputs),
                    "agent": rec.agent,
                    "as_of": _as_of_str(rec.as_of),
                    "fetched_at": rec.fetched_at,
                    "url": rec.url,
                }
                if row is None:
                    s.add(ProvenanceRow(id=rec.id, **values))
                else:
                    for key, value in values.items():
                        setattr(row, key, value)
            await s.commit()
        return [r.id for r in records]

    async def get(self, provenance_id: str) -> ProvenanceRecord | None:
        async with session_scope() as s:
            row = await s.get(ProvenanceRow, provenance_id)
            return _to_record(row) if row else None

    async def get_many(self, ids: list[str]) -> dict[str, ProvenanceRecord]:
        if not ids:
            return {}
        async with session_scope() as s:
            rows = (await s.execute(select(ProvenanceRow).where(ProvenanceRow.id.in_(ids)))).scalars()
            return {r.id: _to_record(r) for r in rows}

    async def list(self, run_id: str) -> list[ProvenanceRecord]:
        async with session_scope() as s:
            stmt = (
                select(ProvenanceRow).where(ProvenanceRow.run_id == run_id).order_by(ProvenanceRow.fetched_at)
            )
            return [_to_record(r) for r in (await s.execute(stmt)).scalars()]

    async def lineage(self, provenance_id: str, depth: int = 5) -> LineageNode | None:
        root = await self.get(provenance_id)
        if root is None:
            return None
        return await self._expand(root, depth, {root.id})

    async def _expand(self, record: ProvenanceRecord, depth: int, seen: set[str]) -> LineageNode:
        node = LineageNode(record=record)
        if depth <= 0 or not record.inputs:
            return node
        found = await self.get_many([i for i in record.inputs if i not in seen])
        for input_id in record.inputs:
            if input_id in seen:
                continue  # cycle guard
            child = found.get(input_id)
            if child is None:
                node.missing_inputs.append(input_id)
                continue
            node.inputs.append(await self._expand(child, depth - 1, seen | {input_id}))
        return node
