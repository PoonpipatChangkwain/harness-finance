"""harness runtime tables: sessions, messages, runs, events, provenance, approvals, memory

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-25

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "sessions",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("title", sa.String(300)),
        sa.Column("created_at", _TS, nullable=False),
        sa.Column("updated_at", _TS, nullable=False),
        sa.Column("meta", sa.JSON, nullable=False),
    )
    op.create_table(
        "messages",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column(
            "session_id", sa.String(40), sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("run_id", sa.String(40)),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("created_at", _TS, nullable=False),
        sa.Column("meta", sa.JSON, nullable=False),
    )
    op.create_index("ix_messages_session_id", "messages", ["session_id"])
    op.create_index("ix_messages_run_id", "messages", ["run_id"])
    op.create_table(
        "runs",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("session_id", sa.String(40)),
        sa.Column("workflow", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("input", sa.JSON, nullable=False),
        sa.Column("output", sa.JSON),
        sa.Column("error", sa.Text),
        sa.Column("thread_id", sa.String(128)),
        sa.Column("started_at", _TS, nullable=False),
        sa.Column("finished_at", _TS),
        sa.Column("usage", sa.JSON, nullable=False),
    )
    op.create_index("ix_runs_session_id", "runs", ["session_id"])
    op.create_index("ix_runs_status", "runs", ["status"])
    op.create_table(
        "events",
        sa.Column("run_id", sa.String(40), primary_key=True),
        sa.Column("seq", sa.Integer, primary_key=True),
        sa.Column("ts", _TS, nullable=False),
        sa.Column("type", sa.String(40), nullable=False),
        sa.Column("node", sa.String(64)),
        sa.Column("call_id", sa.String(64)),
        sa.Column("parent_call_id", sa.String(64)),
        sa.Column("payload", sa.JSON, nullable=False),
    )
    op.create_table(
        "provenance",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("run_id", sa.String(40)),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("value", sa.JSON),
        sa.Column("unit", sa.String(24)),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("tool", sa.String(64)),
        sa.Column("calculation", sa.String(128)),
        sa.Column("params", sa.JSON, nullable=False),
        sa.Column("inputs", sa.JSON, nullable=False),
        sa.Column("agent", sa.String(64)),
        sa.Column("as_of", sa.String(40)),
        sa.Column("fetched_at", _TS, nullable=False),
        sa.Column("url", sa.Text),
    )
    op.create_index("ix_provenance_run_id", "provenance", ["run_id"])
    op.create_table(
        "approvals",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("run_id", sa.String(40), nullable=False),
        sa.Column("node", sa.String(64)),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("request", sa.JSON, nullable=False),
        sa.Column("response", sa.JSON),
        sa.Column("created_at", _TS, nullable=False),
        sa.Column("resolved_at", _TS),
    )
    op.create_index("ix_approvals_run_id", "approvals", ["run_id"])
    op.create_table(
        "memory",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("namespace", sa.String(64), nullable=False),
        sa.Column("key", sa.String(256), nullable=False),
        sa.Column("value", sa.JSON),
        sa.Column("updated_at", _TS, nullable=False),
    )
    op.create_index("ux_memory_ns_key", "memory", ["namespace", "key"], unique=True)


def downgrade() -> None:
    for table in ("memory", "approvals", "provenance", "events", "runs", "messages", "sessions"):
        op.drop_table(table)
