"""Per-scope SQLite checkpointing (T3, phase-09 §9.4).

Ported from ``../TradingAgents/tradingagents/graph/checkpointer.py``: one
SQLite file per scope (a trading symbol, or ``_workflow_{id}`` for
research/portfolio), a ``thread_id`` derived from ``(scope, date, graph
signature)`` so a resume can never land on a differently-shaped graph, and
connections cached for the process lifetime (``AsyncSqliteSaver`` wraps one
``aiosqlite.Connection`` that must stay open across ``astream`` calls).
"""

from __future__ import annotations

import hashlib
import json
import weakref
from pathlib import Path
from typing import Any

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.domain.models.symbol import safe_path_component
from app.domain.workflows.spec import WorkflowSpec


def safe_scope(scope: str) -> str:
    """``scope`` is a symbol or ``_workflow_{id}``; both must be path-safe."""
    if scope.startswith("_workflow_"):
        rest = scope[len("_workflow_") :]
        return "_workflow_" + safe_path_component(rest or "workflow")
    return safe_path_component(scope)


def db_path(data_dir: Path, scope: str) -> Path:
    return data_dir / "checkpoints" / f"{safe_scope(scope)}.db"


def thread_id(scope: str, date: str, signature: str) -> str:
    digest = hashlib.sha256(f"{scope}:{date}:{signature}".encode()).hexdigest()
    return digest[:16]


def graph_signature(spec: WorkflowSpec, input: dict[str, Any]) -> str:
    """Hash of everything that changes the graph's *shape* for this input.

    Only ``spec.id``/``spec.version`` and the declared ``signature_fields``
    go in — anything else (free-text query, timestamps) must not affect
    whether a resume is allowed to reuse a checkpoint.
    """
    values = {field: input.get(field) for field in sorted(spec.signature_fields)}
    raw = {"id": spec.id, "version": spec.version, "fields": values}
    payload = json.dumps(raw, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


_OPEN_CACHES: weakref.WeakSet[CheckpointerCache] = weakref.WeakSet()


async def close_all_caches() -> None:
    """Close every cache's connections (app shutdown, test teardown)."""
    for cache in list(_OPEN_CACHES):
        await cache.close_all()


class CheckpointerCache:
    """One open ``AsyncSqliteSaver`` per scope, reused across runs of that scope."""

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._conns: dict[str, aiosqlite.Connection] = {}
        self._savers: dict[str, AsyncSqliteSaver] = {}
        _OPEN_CACHES.add(self)

    async def get(self, scope: str) -> AsyncSqliteSaver:
        key = safe_scope(scope)
        saver = self._savers.get(key)
        if saver is not None:
            return saver
        path = db_path(self._data_dir, scope)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(path)
        saver = AsyncSqliteSaver(conn)
        await saver.setup()
        self._conns[key] = conn
        self._savers[key] = saver
        return saver

    async def clear_checkpoint(self, scope: str, tid: str) -> None:
        saver = await self.get(scope)
        await saver.adelete_thread(tid)

    async def clear_all(self, scope: str) -> None:
        """Drop every checkpoint of ``scope`` and remove the WAL/SHM files too."""
        key = safe_scope(scope)
        conn = self._conns.pop(key, None)
        self._savers.pop(key, None)
        if conn is not None:
            await conn.close()
        path = db_path(self._data_dir, scope)
        for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
            candidate.unlink(missing_ok=True)

    async def close_all(self) -> None:
        for conn in self._conns.values():
            await conn.close()
        self._conns.clear()
        self._savers.clear()
