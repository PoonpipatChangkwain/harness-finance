"""backtest_cells + backtest_sims (P15); the backtest itself is a ``runs`` row

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-01

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "backtest_cells",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("backtest_id", sa.String(40), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("day", sa.String(10), nullable=False),
        sa.Column("cache_key", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("run_id", sa.String(40), nullable=True),
        sa.Column("cached_from", sa.String(40), nullable=True),
        sa.Column("decision", sa.JSON, nullable=True),
        sa.Column("usage", sa.JSON, nullable=False),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_backtest_cells_backtest", "backtest_cells", ["backtest_id", "symbol", "day"])
    op.create_index("ix_backtest_cells_cache_key", "backtest_cells", ["cache_key"])
    op.create_table(
        "backtest_sims",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("backtest_id", sa.String(40), nullable=False),
        sa.Column("config", sa.JSON, nullable=False),
        sa.Column("metrics", sa.JSON, nullable=False),
        sa.Column("result", sa.JSON, nullable=False),
        sa.Column("engine_version", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_backtest_sims_backtest_id", "backtest_sims", ["backtest_id"])


def downgrade() -> None:
    op.drop_index("ix_backtest_sims_backtest_id", "backtest_sims")
    op.drop_table("backtest_sims")
    op.drop_index("ix_backtest_cells_cache_key", "backtest_cells")
    op.drop_index("ix_backtest_cells_backtest", "backtest_cells")
    op.drop_table("backtest_cells")
