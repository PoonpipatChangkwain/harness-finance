"""SQLAlchemy tables for the harness runtime (contracts.md §12, P05 §5.1)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DDL, JSON, DateTime, Float, ForeignKey, Index, Integer, String, Text, event
from sqlalchemy.orm import Mapped, mapped_column

from app.harness.storage.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class SessionRow(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class MessageRow(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class RunRow(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    session_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    workflow: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), index=True)
    input: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    thread_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    usage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class EventRow(Base):
    __tablename__ = "events"

    run_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    type: Mapped[str] = mapped_column(String(40))
    node: Mapped[str | None] = mapped_column(String(64), nullable=True)
    call_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parent_call_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class ProvenanceRow(Base):
    __tablename__ = "provenance"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    run_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    kind: Mapped[str] = mapped_column(String(24))
    value: Mapped[Any] = mapped_column(JSON, nullable=True)
    unit: Mapped[str | None] = mapped_column(String(24), nullable=True)
    source: Mapped[str] = mapped_column(String(64))
    tool: Mapped[str | None] = mapped_column(String(64), nullable=True)
    calculation: Mapped[str | None] = mapped_column(String(128), nullable=True)
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    inputs: Mapped[list[str]] = mapped_column(JSON, default=list)
    agent: Mapped[str | None] = mapped_column(String(64), nullable=True)
    as_of: Mapped[str | None] = mapped_column(String(40), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    url: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (Index("ix_provenance_run_id", "run_id"),)


class ApprovalRow(Base):
    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(40), index=True)
    node: Mapped[str | None] = mapped_column(String(64), nullable=True)
    kind: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16))
    request: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    response: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PortfolioRow(Base):
    """(P12) One book. Money is stored as decimal strings (exact), weights as 0..1 strings."""

    __tablename__ = "portfolios"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    base_currency: Mapped[str] = mapped_column(String(8), default="THB")
    cash: Mapped[str | None] = mapped_column(String(40), nullable=True)  # None = not given ≠ 0
    icon: Mapped[str | None] = mapped_column(String(40), nullable=True)
    color: Mapped[str | None] = mapped_column(String(20), nullable=True)
    provenance_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class PositionRow(Base):
    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    portfolio_id: Mapped[str] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"), index=True)
    sort: Mapped[int] = mapped_column(Integer, default=0)
    symbol_canonical: Mapped[str] = mapped_column(String(40))
    raw: Mapped[str] = mapped_column(String(40))
    symbol: Mapped[dict[str, Any]] = mapped_column(JSON)  # the full normalized Symbol
    quantity: Mapped[str] = mapped_column(String(40))
    avg_cost: Mapped[str | None] = mapped_column(String(40), nullable=True)
    declared_weight: Mapped[str | None] = mapped_column(String(40), nullable=True)
    target_weight: Mapped[str | None] = mapped_column(String(40), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class PortfolioRevisionRow(Base):
    """Every change of a book: a full snapshot + the ``kind=user_input`` provenance record."""

    __tablename__ = "portfolio_revisions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    portfolio_id: Mapped[str] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"), index=True)
    reason: Mapped[str] = mapped_column(String(40))  # created | edit | import | apply_plan
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    provenance_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    plan_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PlanRow(Base):
    __tablename__ = "plans"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    portfolio_id: Mapped[str] = mapped_column(String(40), index=True)
    run_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(16))  # approved | applied
    proposal: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    response: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    applied_revision_id: Mapped[str | None] = mapped_column(String(40), nullable=True)


class PortfolioSnapshotRow(Base):
    """Value history (weekly job / "run now"): headline numbers + the AI report's run."""

    __tablename__ = "portfolio_snapshots"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    portfolio_id: Mapped[str] = mapped_column(String(40), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # weekly | manual
    taken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    run_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(40), nullable=True)


class MemoryRow(Base):
    __tablename__ = "memory"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    namespace: Mapped[str] = mapped_column(String(64))
    key: Mapped[str] = mapped_column(String(256))
    value: Mapped[Any] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    __table_args__ = (Index("ux_memory_ns_key", "namespace", "key", unique=True),)


class SettingRow(Base):
    """(P13) User overrides from the Settings page, one row per section (never API keys)."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class BacktestCellRow(Base):
    """(P15) One (symbol, date) decision of a backtest; ``cache_key`` lets another backtest
    with the same model/prompt/graph settings reuse it instead of paying for the LLM again."""

    __tablename__ = "backtest_cells"
    __table_args__ = (Index("ix_backtest_cells_backtest", "backtest_id", "symbol", "day"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    backtest_id: Mapped[str] = mapped_column(String(40))
    symbol: Mapped[str] = mapped_column(String(32))
    day: Mapped[str] = mapped_column(String(10))  # ISO analysis date
    cache_key: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(16))  # pending | running | done | failed
    run_id: Mapped[str | None] = mapped_column(String(40), nullable=True)  # the trading run
    cached_from: Mapped[str | None] = mapped_column(String(40), nullable=True)  # cell id reused
    decision: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    usage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class BacktestSimRow(Base):
    """(P15) One portfolio simulation of a backtest's decisions (a capital / risk / cost setting)."""

    __tablename__ = "backtest_sims"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    backtest_id: Mapped[str] = mapped_column(String(40), index=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # trades, skipped, curves
    engine_version: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class UserMemoryRow(Base):
    """(P17) One durable fact about a user (contracts.md §13.1). Never edited in place: a change is
    a new row that ``supersedes`` the old one; only an explicit "forget" deletes."""

    __tablename__ = "user_memory"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    category: Mapped[str] = mapped_column(String(32))
    key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    text: Mapped[str] = mapped_column(Text)
    value_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    confidence: Mapped[float] = mapped_column(Float)
    importance: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(24))
    source_ref: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), index=True)
    supersedes: Mapped[str | None] = mapped_column(String(40), nullable=True)
    superseded_by: Mapped[str | None] = mapped_column(String(40), nullable=True)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sensitivity: Mapped[str] = mapped_column(String(12), default="normal")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_accessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    access_count: Mapped[int] = mapped_column(Integer, default=0)


class ReasoningBankRow(Base):
    """(P17) One anonymised lesson shared across users (ReasoningBank: title/description/content).
    ``success_rate`` is computed on read from ``success_count``/``use_count``; ``user_id`` is the
    producer, kept for audit only."""

    __tablename__ = "reasoning_bank"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    title: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(16))
    outcome_signal: Mapped[str] = mapped_column(String(16))
    judge_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    tags: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    use_count: Mapped[int] = mapped_column(Integer, default=0)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    source_count: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), index=True)
    prompt_version: Mapped[str] = mapped_column(String(40))
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source_run_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    user_id: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReasoningBankUseRow(Base):
    """(P17) A run that had a ReasoningBank item injected; ``outcome`` is filled by ``settle``."""

    __tablename__ = "reasoning_bank_uses"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    item_id: Mapped[str] = mapped_column(String(40), index=True)
    run_id: Mapped[str] = mapped_column(String(40), index=True)
    user_id: Mapped[str] = mapped_column(String(40))
    outcome: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# FTS5 tables (trigram: substring search that works for Thai, which has no spaces). Alembic 0006
# creates them in real databases; this hook does the same for ``create_all()`` in tests. The store
# keeps them in sync with the main rows (no triggers).
USER_MEMORY_FTS_DDL = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS user_memory_fts "
    "USING fts5(id UNINDEXED, user_id UNINDEXED, text, tokenize='trigram')"
)
REASONING_BANK_FTS_DDL = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS reasoning_bank_fts "
    "USING fts5(id UNINDEXED, title, description, content, tokenize='trigram')"
)
FTS_TABLES = ("user_memory_fts", "reasoning_bank_fts")

for _ddl in (USER_MEMORY_FTS_DDL, REASONING_BANK_FTS_DDL):
    event.listen(Base.metadata, "after_create", DDL(_ddl).execute_if(dialect="sqlite"))  # type: ignore[no-untyped-call]
