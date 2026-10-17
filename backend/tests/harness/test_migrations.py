"""``alembic upgrade head`` builds exactly the schema the SQLAlchemy models describe (P12 adds 5 tables,
P17 adds the memory tables + their FTS5 virtual tables, which are not SQLAlchemy models)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from app.harness.storage import models  # noqa: F401  (register tables)
from app.harness.storage.db import Base
from app.harness.storage.migrate import upgrade_head
from app.harness.storage.models import FTS_TABLES

P12_TABLES = {"portfolios", "positions", "portfolio_revisions", "plans", "portfolio_snapshots"}
P17_TABLES = {"user_memory", "reasoning_bank", "reasoning_bank_uses", *FTS_TABLES}


def _not_fts(name: str | None, type_: str, parent_names: Any) -> bool:
    """FTS5 virtual tables (and their ``_data``/``_idx``/… shadow tables) are created by raw DDL."""
    return not (type_ == "table" and name is not None and name.startswith(FTS_TABLES))


def test_upgrade_head_matches_models(tmp_path: Path) -> None:
    path = (tmp_path / "migrated.db").as_posix()
    upgrade_head(f"sqlite+aiosqlite:///{path}")
    engine = sa.create_engine(f"sqlite:///{path}")
    try:
        with engine.connect() as conn:
            assert set(sa.inspect(conn).get_table_names()) >= P12_TABLES | P17_TABLES
            context = MigrationContext.configure(conn, opts={"include_name": _not_fts})
            diff = compare_metadata(context, Base.metadata)
    finally:
        engine.dispose()
    assert diff == []


def test_fts_trigram_finds_thai_substring(tmp_path: Path) -> None:
    assert tuple(int(p) for p in sqlite3.sqlite_version.split(".")[:2]) >= (3, 34)  # trigram tokenizer
    path = tmp_path / "migrated.db"
    upgrade_head(f"sqlite+aiosqlite:///{path.as_posix()}")
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "INSERT INTO user_memory_fts(id, user_id, text) VALUES (?, ?, ?)",
            ("mem_1", "usr_default", "ผู้ใช้ชอบลงทุนระยะยาวแบบไม่เร่งรีบ"),
        )
        rows = conn.execute(
            "SELECT id FROM user_memory_fts WHERE user_memory_fts MATCH ?", ('"ระยะยาว"',)
        ).fetchall()
    finally:
        conn.close()
    assert rows == [("mem_1",)]
