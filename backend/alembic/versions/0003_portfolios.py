"""portfolio tables: portfolios, positions, portfolio_revisions, plans, portfolio_snapshots (P12)

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-26

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "portfolios",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("base_currency", sa.String(8), nullable=False),
        sa.Column("cash", sa.String(40)),
        sa.Column("icon", sa.String(40)),
        sa.Column("color", sa.String(20)),
        sa.Column("provenance_id", sa.String(40)),
        sa.Column("created_at", _TS, nullable=False),
        sa.Column("updated_at", _TS, nullable=False),
        sa.Column("meta", sa.JSON, nullable=False),
    )
    op.create_table(
        "positions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "portfolio_id", sa.String(40), sa.ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("sort", sa.Integer, nullable=False),
        sa.Column("symbol_canonical", sa.String(40), nullable=False),
        sa.Column("raw", sa.String(40), nullable=False),
        sa.Column("symbol", sa.JSON, nullable=False),
        sa.Column("quantity", sa.String(40), nullable=False),
        sa.Column("avg_cost", sa.String(40)),
        sa.Column("declared_weight", sa.String(40)),
        sa.Column("target_weight", sa.String(40)),
        sa.Column("note", sa.Text),
    )
    op.create_index("ix_positions_portfolio_id", "positions", ["portfolio_id"])
    op.create_table(
        "portfolio_revisions",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column(
            "portfolio_id", sa.String(40), sa.ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("reason", sa.String(40), nullable=False),
        sa.Column("snapshot", sa.JSON, nullable=False),
        sa.Column("provenance_id", sa.String(40)),
        sa.Column("plan_id", sa.String(40)),
        sa.Column("created_at", _TS, nullable=False),
    )
    op.create_index("ix_portfolio_revisions_portfolio_id", "portfolio_revisions", ["portfolio_id"])
    op.create_table(
        "plans",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("portfolio_id", sa.String(40), nullable=False),
        sa.Column("run_id", sa.String(40)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("proposal", sa.JSON, nullable=False),
        sa.Column("response", sa.JSON),
        sa.Column("created_at", _TS, nullable=False),
        sa.Column("approved_at", _TS),
        sa.Column("applied_at", _TS),
        sa.Column("applied_revision_id", sa.String(40)),
    )
    op.create_index("ix_plans_portfolio_id", "plans", ["portfolio_id"])
    op.create_index("ix_plans_run_id", "plans", ["run_id"])
    op.create_table(
        "portfolio_snapshots",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("portfolio_id", sa.String(40), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("taken_at", _TS, nullable=False),
        sa.Column("summary", sa.JSON, nullable=False),
        sa.Column("run_id", sa.String(40)),
        sa.Column("session_id", sa.String(40)),
    )
    op.create_index("ix_portfolio_snapshots_portfolio_id", "portfolio_snapshots", ["portfolio_id"])


def downgrade() -> None:
    for table in ("portfolio_snapshots", "plans", "portfolio_revisions", "positions", "portfolios"):
        op.drop_table(table)
