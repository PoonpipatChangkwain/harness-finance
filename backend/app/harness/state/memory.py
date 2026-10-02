"""Persistent key-value memory by namespace (D2): user prefs, portfolio, past decisions.

Pattern from ``../dexter/src/memory/``. Only written through explicit steps
(the ``memory_update`` tool in P07, "save decision" nodes) — never
automatically from conversation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select

from app.harness.storage.db import session_scope
from app.harness.storage.models import MemoryRow


class MemoryItem(BaseModel):
    namespace: str
    key: str
    value: Any
    updated_at: datetime


class MemoryStore:
    async def get(self, namespace: str, key: str) -> MemoryItem | None:
        async with session_scope() as s:
            stmt = select(MemoryRow).where(MemoryRow.namespace == namespace, MemoryRow.key == key)
            row = (await s.execute(stmt)).scalar_one_or_none()
            return self._item(row) if row else None

    async def put(self, namespace: str, key: str, value: Any) -> MemoryItem:
        now = datetime.now(UTC)
        async with session_scope() as s:
            stmt = select(MemoryRow).where(MemoryRow.namespace == namespace, MemoryRow.key == key)
            row = (await s.execute(stmt)).scalar_one_or_none()
            if row is None:
                row = MemoryRow(namespace=namespace, key=key, value=value, updated_at=now)
                s.add(row)
            else:
                row.value = value
                row.updated_at = now
            await s.commit()
            return self._item(row)

    async def search(self, namespace: str, prefix: str = "", limit: int = 50) -> list[MemoryItem]:
        async with session_scope() as s:
            stmt = select(MemoryRow).where(MemoryRow.namespace == namespace)
            if prefix:
                stmt = stmt.where(MemoryRow.key.startswith(prefix, autoescape=True))
            stmt = stmt.order_by(MemoryRow.key).limit(limit)
            return [self._item(r) for r in (await s.execute(stmt)).scalars()]

    async def delete(self, namespace: str, key: str) -> bool:
        async with session_scope() as s:
            stmt = select(MemoryRow).where(MemoryRow.namespace == namespace, MemoryRow.key == key)
            row = (await s.execute(stmt)).scalar_one_or_none()
            if row is None:
                return False
            await s.delete(row)
            await s.commit()
            return True

    @staticmethod
    def _item(row: MemoryRow) -> MemoryItem:
        return MemoryItem(namespace=row.namespace, key=row.key, value=row.value, updated_at=row.updated_at)
