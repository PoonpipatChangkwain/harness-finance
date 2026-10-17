"""(P17) ``user_memory`` rows + ``user_memory_fts`` kept in sync (contracts.md §13.1).

Every query is scoped by ``user_id`` in SQL. Nothing is edited in place except status /
bookkeeping fields: a changed fact is a new row that ``supersedes`` the old one, and only
``forget`` deletes (rows and their FTS entries, optionally the whole chain).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import bindparam, delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from app.domain.memory.schemas import (
    CATEGORIES,
    MEMORY_STATUSES,
    MemoryCategory,
    MemoryItemModel,
    MemorySource,
    MemoryStatus,
    Sensitivity,
)
from app.harness.storage.db import session_scope
from app.harness.storage.models import UserMemoryRow

DEFAULT_USER_ID = "usr_default"
USER_ID_PATTERN = r"^usr_[A-Za-z0-9_-]{1,40}$"
CURRENT_STATUSES: tuple[MemoryStatus, ...] = ("active", "candidate")

_SPLIT_RE = re.compile(r"[\s\"'`.,;:!?()\[\]{}<>/\\|*^~+=#@&%$_-]+")
_THAI_RE = re.compile(r"[฀-๿]")


def new_memory_id() -> str:
    return f"mem_{ULID()}"


def utcnow() -> datetime:
    return datetime.now(UTC)


def aware(dt: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes; every stored time is UTC."""
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def fts_query(text_: str, *, max_terms: int = 24) -> str | None:
    """An FTS5 MATCH expression for free text: quoted terms OR-ed, each ≥ 3 characters (trigram).

    Thai has no spaces, so a long Thai run is cut into overlapping 4-character windows (step 2):
    a stored sentence that shares any window matches, and bm25 ranks by how many it shares.
    """
    terms: list[str] = []
    for token in _SPLIT_RE.split(text_):
        token = token.strip()
        if len(token) < 3:
            continue
        if _THAI_RE.search(token) and len(token) > 6:
            pieces = [token[i : i + 4] for i in range(0, len(token) - 2, 2)]
            terms.extend(p for p in pieces if len(p) >= 3)
        else:
            terms.append(token)
    unique = list(dict.fromkeys(terms))[:max_terms]
    if not unique:
        return None
    return " OR ".join('"' + t.replace('"', '""') + '"' for t in unique)


class NewMemory(BaseModel):
    """A row to insert (the store assigns id and timestamps)."""

    category: MemoryCategory
    text: str
    key: str | None = None
    value_json: dict[str, Any] | None = None
    confidence: float = 0.9
    importance: int = 5
    source: MemorySource = "user_stated"
    source_ref: dict[str, Any] = Field(default_factory=dict)
    status: MemoryStatus = "active"
    sensitivity: Sensitivity = "normal"
    supersedes: str | None = None


def to_model(row: UserMemoryRow) -> MemoryItemModel:
    return MemoryItemModel(
        id=row.id,
        user_id=row.user_id,
        category=row.category,  # type: ignore[arg-type]
        key=row.key,
        text=row.text,
        value_json=row.value_json,
        confidence=row.confidence,
        importance=row.importance,
        source=row.source,  # type: ignore[arg-type]
        source_ref=dict(row.source_ref or {}),
        status=row.status,  # type: ignore[arg-type]
        supersedes=row.supersedes,
        superseded_by=row.superseded_by,
        valid_from=aware(row.valid_from) or utcnow(),
        valid_to=aware(row.valid_to),
        sensitivity=row.sensitivity,  # type: ignore[arg-type]
        created_at=aware(row.created_at) or utcnow(),
        updated_at=aware(row.updated_at) or utcnow(),
        last_accessed_at=aware(row.last_accessed_at),
        access_count=row.access_count,
    )


def sort_key(item: MemoryItemModel) -> tuple[int, int, float]:
    """category (contract order) → importance desc → newest first."""
    return (CATEGORIES.index(item.category), -item.importance, -item.created_at.timestamp())


class UserMemoryStore:
    # -- writes -------------------------------------------------------------------------------

    @staticmethod
    async def _insert(s: AsyncSession, user_id: str, new: NewMemory, now: datetime) -> UserMemoryRow:
        row = UserMemoryRow(
            id=new_memory_id(),
            user_id=user_id,
            category=new.category,
            key=new.key,
            text=new.text.strip(),
            value_json=new.value_json,
            confidence=new.confidence,
            importance=new.importance,
            source=new.source,
            source_ref=new.source_ref,
            status=new.status,
            supersedes=new.supersedes,
            superseded_by=None,
            valid_from=now,
            valid_to=None,
            sensitivity=new.sensitivity,
            created_at=now,
            updated_at=now,
            last_accessed_at=None,
            access_count=0,
        )
        s.add(row)
        await s.flush()
        await s.execute(
            text("INSERT INTO user_memory_fts(id, user_id, text) VALUES (:id, :uid, :text)"),
            {"id": row.id, "uid": user_id, "text": row.text},
        )
        return row

    async def add(self, user_id: str, new: NewMemory) -> MemoryItemModel:
        async with session_scope() as s:
            row = await self._insert(s, user_id, new, utcnow())
            await s.commit()
            return to_model(row)

    async def supersede(
        self, user_id: str, old_id: str, new: NewMemory
    ) -> tuple[MemoryItemModel, MemoryItemModel] | None:
        """Insert ``new`` (``supersedes=old_id``) and mark the old row superseded in one transaction."""
        now = utcnow()
        async with session_scope() as s:
            old = await self._row(s, user_id, old_id)
            if old is None:
                return None
            row = await self._insert(s, user_id, new.model_copy(update={"supersedes": old_id}), now)
            old.status = "superseded"
            old.superseded_by = row.id
            old.valid_to = now
            old.updated_at = now
            await s.commit()
            return to_model(row), to_model(old)

    async def link_candidate(self, user_id: str, new: NewMemory, pending_of: str) -> MemoryItemModel:
        """A candidate that will replace ``pending_of`` once confirmed (the old row stays active)."""
        return await self.add(
            user_id, new.model_copy(update={"supersedes": pending_of, "status": "candidate"})
        )

    async def set_status(
        self,
        user_id: str,
        memory_id: str,
        status: MemoryStatus,
        *,
        source: MemorySource | None = None,
        confidence: float | None = None,
    ) -> MemoryItemModel | None:
        now = utcnow()
        async with session_scope() as s:
            row = await self._row(s, user_id, memory_id)
            if row is None:
                return None
            row.status = status
            if source is not None:
                row.source = source
            if confidence is not None:
                row.confidence = confidence
            if status in ("superseded", "retired", "rejected"):
                row.valid_to = now
            row.updated_at = now
            await s.commit()
            return to_model(row)

    async def confirm(
        self, user_id: str, memory_id: str
    ) -> tuple[MemoryItemModel, list[MemoryItemModel]] | None:
        """candidate → active (``user_confirmed``, 0.95); the active row it replaces (``supersedes``, or
        the same ``key``) becomes superseded. Returns ``(item, superseded rows)``."""
        now = utcnow()
        async with session_scope() as s:
            row = await self._row(s, user_id, memory_id)
            if row is None:
                return None
            row.status = "active"
            row.source = "user_confirmed"
            row.confidence = 0.95
            row.valid_from = now
            row.updated_at = now
            stmt = select(UserMemoryRow).where(
                UserMemoryRow.user_id == user_id,
                UserMemoryRow.status == "active",
                UserMemoryRow.id != row.id,
            )
            targets: list[UserMemoryRow] = []
            for other in (await s.execute(stmt)).scalars():
                same_key = row.key is not None and other.key == row.key and other.category == row.category
                if other.id == row.supersedes or same_key:
                    targets.append(other)
            for other in targets:
                other.status = "superseded"
                other.superseded_by = row.id
                other.valid_to = now
                other.updated_at = now
            if row.supersedes is None and targets:
                row.supersedes = targets[0].id
            await s.commit()
            return to_model(row), [to_model(t) for t in targets]

    async def touch(self, user_id: str, memory_ids: Iterable[str]) -> None:
        ids = list(dict.fromkeys(memory_ids))
        if not ids:
            return
        async with session_scope() as s:
            await s.execute(
                update(UserMemoryRow)
                .where(UserMemoryRow.user_id == user_id, UserMemoryRow.id.in_(ids))
                .values(last_accessed_at=utcnow(), access_count=UserMemoryRow.access_count + 1)
            )
            await s.commit()

    async def forget(self, user_id: str, memory_id: str, *, cascade: bool = False) -> list[str]:
        """Hard delete (the user's explicit "forget"): the row, or its whole chain, and their FTS rows.

        Without ``cascade`` the neighbours' links to the deleted row are cleared.
        """
        async with session_scope() as s:
            row = await self._row(s, user_id, memory_id)
            if row is None:
                return []
            ids = [r.id for r in await self._chain(s, user_id, row)] if cascade else [row.id]
            await s.execute(
                delete(UserMemoryRow).where(UserMemoryRow.user_id == user_id, UserMemoryRow.id.in_(ids))
            )
            await s.execute(
                text("DELETE FROM user_memory_fts WHERE user_id = :uid AND id IN :ids").bindparams(
                    bindparam("ids", expanding=True)
                ),
                {"uid": user_id, "ids": ids},
            )
            if not cascade:
                await s.execute(
                    update(UserMemoryRow)
                    .where(UserMemoryRow.user_id == user_id, UserMemoryRow.supersedes.in_(ids))
                    .values(supersedes=None)
                )
                await s.execute(
                    update(UserMemoryRow)
                    .where(UserMemoryRow.user_id == user_id, UserMemoryRow.superseded_by.in_(ids))
                    .values(superseded_by=None)
                )
            await s.commit()
            return ids

    # -- reads -------------------------------------------------------------------------------------

    @staticmethod
    async def _row(s: AsyncSession, user_id: str, memory_id: str) -> UserMemoryRow | None:
        stmt = select(UserMemoryRow).where(UserMemoryRow.user_id == user_id, UserMemoryRow.id == memory_id)
        return (await s.execute(stmt)).scalar_one_or_none()

    @staticmethod
    async def _chain(s: AsyncSession, user_id: str, start: UserMemoryRow) -> list[UserMemoryRow]:
        """Every row linked to ``start`` through ``supersedes`` / ``superseded_by`` (both ways)."""
        seen: dict[str, UserMemoryRow] = {start.id: start}
        frontier = [start]
        while frontier:
            ids = [r.id for r in frontier]
            linked = {x for r in frontier for x in (r.supersedes, r.superseded_by) if x}
            stmt = select(UserMemoryRow).where(
                UserMemoryRow.user_id == user_id,
                (UserMemoryRow.id.in_(linked))
                | (UserMemoryRow.supersedes.in_(ids))
                | (UserMemoryRow.superseded_by.in_(ids)),
            )
            frontier = [r for r in (await s.execute(stmt)).scalars() if r.id not in seen]
            seen.update({r.id: r for r in frontier})
        return sorted(seen.values(), key=lambda r: (aware(r.created_at) or utcnow(), r.id))

    async def get(self, user_id: str, memory_id: str) -> MemoryItemModel | None:
        async with session_scope() as s:
            row = await self._row(s, user_id, memory_id)
            return to_model(row) if row else None

    async def history(self, user_id: str, memory_id: str) -> list[MemoryItemModel]:
        async with session_scope() as s:
            row = await self._row(s, user_id, memory_id)
            if row is None:
                return []
            return [to_model(r) for r in await self._chain(s, user_id, row)]

    async def list_items(
        self,
        user_id: str,
        *,
        statuses: Sequence[str] | None = None,
        category: str | None = None,
        key: str | None = None,
    ) -> list[MemoryItemModel]:
        async with session_scope() as s:
            stmt = select(UserMemoryRow).where(UserMemoryRow.user_id == user_id)
            if statuses:
                stmt = stmt.where(UserMemoryRow.status.in_(list(statuses)))
            if category:
                stmt = stmt.where(UserMemoryRow.category == category)
            if key:
                stmt = stmt.where(UserMemoryRow.key == key)
            items = [to_model(r) for r in (await s.execute(stmt)).scalars()]
        return sorted(items, key=sort_key)

    async def counts(self, user_id: str, *, category: str | None = None) -> dict[str, int]:
        async with session_scope() as s:
            stmt = (
                select(UserMemoryRow.status, func.count())
                .where(UserMemoryRow.user_id == user_id)
                .group_by(UserMemoryRow.status)
            )
            if category:
                stmt = stmt.where(UserMemoryRow.category == category)
            found = {str(status): int(n) for status, n in (await s.execute(stmt)).all()}
        return {status: found.get(status, 0) for status in MEMORY_STATUSES}

    async def search_fts(
        self,
        user_id: str,
        query: str,
        *,
        statuses: Sequence[str] = CURRENT_STATUSES,
        category: str | None = None,
        limit: int = 8,
    ) -> list[tuple[MemoryItemModel, float]]:
        """FTS hits of this user, best first, as ``(item, score)`` with score = -bm25 (higher = better)."""
        match = fts_query(query)
        if match is None:
            return []
        sql = (
            "SELECT user_memory_fts.id AS id, bm25(user_memory_fts) AS rank FROM user_memory_fts "
            "JOIN user_memory m ON m.id = user_memory_fts.id "
            "WHERE user_memory_fts MATCH :q AND user_memory_fts.user_id = :uid AND m.user_id = :uid "
            "AND m.status IN :statuses"
            + (" AND m.category = :category" if category else "")
            + " ORDER BY rank LIMIT :limit"
        )
        params: dict[str, Any] = {"q": match, "uid": user_id, "statuses": list(statuses), "limit": limit}
        if category:
            params["category"] = category
        async with session_scope() as s:
            ranked = (
                await s.execute(text(sql).bindparams(bindparam("statuses", expanding=True)), params)
            ).all()
            if not ranked:
                return []
            ids = [str(r.id) for r in ranked]
            rows = {
                r.id: r
                for r in (
                    await s.execute(
                        select(UserMemoryRow).where(
                            UserMemoryRow.user_id == user_id, UserMemoryRow.id.in_(ids)
                        )
                    )
                ).scalars()
            }
        return [(to_model(rows[str(r.id)]), -float(r.rank)) for r in ranked if str(r.id) in rows]

    async def fts_ids(self, user_id: str) -> set[str]:
        """Ids present in the FTS index for this user (consistency checks / tests)."""
        async with session_scope() as s:
            result = await s.execute(
                text("SELECT id FROM user_memory_fts WHERE user_id = :uid"), {"uid": user_id}
            )
            return {str(r[0]) for r in result.all()}
