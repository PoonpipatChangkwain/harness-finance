"""Async SQLAlchemy engine/session setup for the app's SQLite store.

Table models live in ``models.py`` (contracts.md §12). The schema is owned
by Alembic (``alembic upgrade head``, run on API startup via
``migrate.py``); tests call ``create_all()`` on a throwaway database after
``configure(url)``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.settings import get_settings


class Base(DeclarativeBase):
    pass


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _sqlite_pragmas(dbapi_conn: Any, _record: Any) -> None:
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def _make_engine(url: str) -> AsyncEngine:
    engine = create_async_engine(url, echo=False, connect_args={"timeout": 30})
    if url.startswith("sqlite"):
        event.listen(engine.sync_engine, "connect", _sqlite_pragmas)
    return engine


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = _make_engine(get_settings().db_url)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _session_factory


def configure(url: str) -> AsyncEngine:
    """Point the process at a different database (tests, tools). Returns the new engine."""
    global _engine, _session_factory
    _engine = _make_engine(url)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


async def dispose() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


async def create_all() -> None:
    from app.harness.storage import models  # noqa: F401  (register tables)

    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    factory = get_session_factory()
    async with factory() as session:
        yield session
