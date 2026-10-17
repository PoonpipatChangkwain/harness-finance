"""user_memory + reasoning_bank + reasoning_bank_uses and their FTS5 (trigram) tables (P17)

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-17

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_memory",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("user_id", sa.String(40), nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("key", sa.String(120), nullable=True),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("value_json", sa.JSON, nullable=True),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column("importance", sa.Integer, nullable=False),
        sa.Column("source", sa.String(24), nullable=False),
        sa.Column("source_ref", sa.JSON, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("supersedes", sa.String(40), nullable=True),
        sa.Column("superseded_by", sa.String(40), nullable=True),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sensitivity", sa.String(12), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_accessed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("access_count", sa.Integer, nullable=False),
    )
    op.create_index("ix_user_memory_user_id", "user_memory", ["user_id"])
    op.create_index("ix_user_memory_status", "user_memory", ["status"])
    op.create_table(
        "reasoning_bank",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("outcome_signal", sa.String(16), nullable=False),
        sa.Column("judge_score", sa.Float, nullable=True),
        sa.Column("tags", sa.JSON, nullable=False),
        sa.Column("use_count", sa.Integer, nullable=False),
        sa.Column("success_count", sa.Integer, nullable=False),
        sa.Column("source_count", sa.Integer, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("prompt_version", sa.String(40), nullable=False),
        sa.Column("model", sa.String(120), nullable=True),
        sa.Column("source_run_id", sa.String(40), nullable=True),
        sa.Column("user_id", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_reasoning_bank_status", "reasoning_bank", ["status"])
    op.create_table(
        "reasoning_bank_uses",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("item_id", sa.String(40), nullable=False),
        sa.Column("run_id", sa.String(40), nullable=False),
        sa.Column("user_id", sa.String(40), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_reasoning_bank_uses_item_id", "reasoning_bank_uses", ["item_id"])
    op.create_index("ix_reasoning_bank_uses_run_id", "reasoning_bank_uses", ["run_id"])
    # trigram tokenizer (SQLite >= 3.34): substring search for Thai, which has no word spaces
    op.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS user_memory_fts "
        "USING fts5(id UNINDEXED, user_id UNINDEXED, text, tokenize='trigram')"
    )
    op.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS reasoning_bank_fts "
        "USING fts5(id UNINDEXED, title, description, content, tokenize='trigram')"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS reasoning_bank_fts")
    op.execute("DROP TABLE IF EXISTS user_memory_fts")
    op.drop_index("ix_reasoning_bank_uses_run_id", "reasoning_bank_uses")
    op.drop_index("ix_reasoning_bank_uses_item_id", "reasoning_bank_uses")
    op.drop_table("reasoning_bank_uses")
    op.drop_index("ix_reasoning_bank_status", "reasoning_bank")
    op.drop_table("reasoning_bank")
    op.drop_index("ix_user_memory_status", "user_memory")
    op.drop_index("ix_user_memory_user_id", "user_memory")
    op.drop_table("user_memory")
