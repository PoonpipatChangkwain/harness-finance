"""``alembic upgrade head`` builds exactly the schema the SQLAlchemy models describe (P12 adds 5 tables)."""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from app.harness.storage import models  # noqa: F401  (register tables)
from app.harness.storage.db import Base
from app.harness.storage.migrate import upgrade_head

P12_TABLES = {"portfolios", "positions", "portfolio_revisions", "plans", "portfolio_snapshots"}


def test_upgrade_head_matches_models(tmp_path: Path) -> None:
    path = (tmp_path / "migrated.db").as_posix()
    upgrade_head(f"sqlite+aiosqlite:///{path}")
    engine = sa.create_engine(f"sqlite:///{path}")
    try:
        with engine.connect() as conn:
            assert set(sa.inspect(conn).get_table_names()) >= P12_TABLES
            diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    finally:
        engine.dispose()
    assert diff == []
