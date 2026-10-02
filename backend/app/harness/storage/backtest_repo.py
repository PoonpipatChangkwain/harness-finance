"""Repository for backtest cells and simulations (P15). The backtest itself is a ``runs`` row."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import delete, select
from ulid import ULID

from app.harness.storage.db import session_scope
from app.harness.storage.models import BacktestCellRow, BacktestSimRow


class CellRecord(BaseModel):
    id: str
    backtest_id: str
    symbol: str
    day: str
    cache_key: str
    status: str
    run_id: str | None = None
    cached_from: str | None = None
    decision: dict[str, Any] | None = None
    usage: dict[str, Any] = {}
    error: str | None = None
    updated_at: datetime


class SimRecord(BaseModel):
    id: str
    backtest_id: str
    config: dict[str, Any]
    metrics: dict[str, Any]
    result: dict[str, Any]
    engine_version: str
    created_at: datetime


def _cell(row: BacktestCellRow) -> CellRecord:
    return CellRecord.model_validate(row, from_attributes=True)


def _sim(row: BacktestSimRow) -> SimRecord:
    return SimRecord.model_validate(row, from_attributes=True)


async def cells(backtest_id: str) -> list[CellRecord]:
    async with session_scope() as s:
        rows = (
            await s.execute(
                select(BacktestCellRow)
                .where(BacktestCellRow.backtest_id == backtest_id)
                .order_by(BacktestCellRow.day, BacktestCellRow.symbol)
            )
        ).scalars()
        return [_cell(r) for r in rows]


async def add_cells(backtest_id: str, rows: list[tuple[str, str, str]]) -> list[CellRecord]:
    """Create ``(symbol, day, cache_key)`` cells as ``pending``."""
    async with session_scope() as s:
        new = [
            BacktestCellRow(
                id=f"cell_{ULID()}", backtest_id=backtest_id, symbol=sym, day=day, cache_key=key,
                status="pending", usage={},
            )
            for sym, day, key in rows
        ]  # fmt: skip
        s.add_all(new)
        await s.commit()
        return [_cell(r) for r in new]


async def update_cell(cell_id: str, **fields: Any) -> None:
    async with session_scope() as s:
        row = await s.get(BacktestCellRow, cell_id)
        if row is None:
            return
        for k, v in fields.items():
            setattr(row, k, v)
        await s.commit()


async def find_cached(cache_keys: list[str], *, exclude_backtest: str) -> dict[str, CellRecord]:
    """A finished cell of *another* backtest per cache key (the newest wins)."""
    if not cache_keys:
        return {}
    async with session_scope() as s:
        rows = (
            await s.execute(
                select(BacktestCellRow)
                .where(
                    BacktestCellRow.cache_key.in_(cache_keys),
                    BacktestCellRow.status == "done",
                    BacktestCellRow.backtest_id != exclude_backtest,
                )
                .order_by(BacktestCellRow.updated_at)
            )
        ).scalars()
        return {r.cache_key: _cell(r) for r in rows}


async def count_cached(cache_keys: list[str]) -> int:
    if not cache_keys:
        return 0
    async with session_scope() as s:
        rows = (
            await s.execute(
                select(BacktestCellRow.cache_key)
                .where(BacktestCellRow.cache_key.in_(cache_keys), BacktestCellRow.status == "done")
                .distinct()
            )
        ).scalars()
        return len(list(rows))


async def add_sim(
    backtest_id: str, config: dict[str, Any], metrics: dict[str, Any], result: dict[str, Any], engine: str
) -> SimRecord:
    async with session_scope() as s:
        row = BacktestSimRow(
            id=f"sim_{ULID()}",
            backtest_id=backtest_id,
            config=config,
            metrics=metrics,
            result=result,
            engine_version=engine,
        )
        s.add(row)
        await s.commit()
        await s.refresh(row)
        return _sim(row)


async def sims(backtest_id: str) -> list[SimRecord]:
    async with session_scope() as s:
        rows = (
            await s.execute(
                select(BacktestSimRow)
                .where(BacktestSimRow.backtest_id == backtest_id)
                .order_by(BacktestSimRow.created_at)
            )
        ).scalars()
        return [_sim(r) for r in rows]


async def get_sim(sim_id: str) -> SimRecord | None:
    async with session_scope() as s:
        row = await s.get(BacktestSimRow, sim_id)
        return _sim(row) if row is not None else None


async def delete_backtest(backtest_id: str) -> None:
    """Cells and sims; the trading runs the cells point to stay (other backtests may reuse them)."""
    async with session_scope() as s:
        await s.execute(delete(BacktestCellRow).where(BacktestCellRow.backtest_id == backtest_id))
        await s.execute(delete(BacktestSimRow).where(BacktestSimRow.backtest_id == backtest_id))
        await s.commit()
