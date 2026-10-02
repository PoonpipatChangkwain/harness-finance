"""Weekly portfolio report (P12 §12.7): every Friday 03:00 Asia/Bangkok.

For each book with positions: fresh analytics → a value snapshot (history), then an
ordinary ``research`` run in a new ``kind=portfolio`` session with the
``portfolio-risk-review`` skill and that book selected — so the report is a normal run
the UI can open and replay. Nothing here sends an order or touches positions.

Catch-up: the last completed slot is kept in memory (``scheduler/portfolio_weekly``).
When the backend starts after a missed Friday slot (machine was off), the job runs
right away. Bangkok has no DST, so a fixed UTC+7 offset is exact (and needs no tzdata).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from app.harness.storage import portfolio_repo, sessions_repo

logger = logging.getLogger(__name__)

BANGKOK = timezone(timedelta(hours=7), "Asia/Bangkok")
WEEKDAY = 4  # Friday
HOUR = 3
MEMORY_NS = "scheduler"
MEMORY_KEY = "portfolio_weekly"
SKILL = "portfolio-risk-review"


def last_slot(now: datetime) -> datetime:
    """The most recent Friday 03:00 Bangkok at or before ``now`` (UTC-aware result)."""
    local = now.astimezone(BANGKOK)
    slot = local.replace(hour=HOUR, minute=0, second=0, microsecond=0) - timedelta(
        days=(local.weekday() - WEEKDAY) % 7
    )
    if slot > local:
        slot -= timedelta(days=7)
    return slot.astimezone(UTC)


def next_slot(now: datetime) -> datetime:
    return last_slot(now) + timedelta(days=7)


def _headline(a: Any) -> dict[str, Any]:
    def pv(x: Any) -> dict[str, Any] | None:
        return x.model_dump() if x is not None else None

    return {
        "base_currency": a.base_currency,
        "as_of": a.as_of.isoformat() if a.as_of else None,
        "total_value": pv(a.total_value),
        "cash": pv(a.cash),
        "cost_basis": pv(a.cost_basis),
        "unrealized_pnl": pv(a.unrealized_pnl),
        "unrealized_pnl_pct": pv(a.unrealized_pnl_pct),
        "day_pnl_pct": pv(a.day_pnl_pct),
        "weights": a.weights,
        "volatility": pv(a.risk.volatility) if a.risk else None,
        "max_drawdown": pv(a.risk.max_drawdown) if a.risk else None,
        "warnings": [w.message for w in a.warnings],
    }


class WeeklyReportJob:
    def __init__(self, services: Any, *, clock: Any = None) -> None:
        self.services = services
        self.clock = clock or (lambda: datetime.now(UTC))
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    async def last_run(self) -> datetime | None:
        item = await self.services.memory.get(MEMORY_NS, MEMORY_KEY)
        value = (item.value or {}).get("last_run") if item is not None else None
        return datetime.fromisoformat(value) if value else None

    async def run_once(
        self, *, kind: str = "weekly", portfolio_ids: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """Snapshot + AI report for every book with positions (or only ``portfolio_ids``)."""
        async with self._lock:
            now = self.clock()
            results: list[dict[str, Any]] = []
            for portfolio in await portfolio_repo.list_portfolios():
                if portfolio_ids is not None and portfolio.id not in portfolio_ids:
                    continue
                if not portfolio.positions:
                    continue
                results.append(await self._report(portfolio, kind, now))
            if portfolio_ids is None:
                await self.services.memory.put(
                    MEMORY_NS,
                    MEMORY_KEY,
                    {"last_run": now.isoformat(), "kind": kind, "reports": len(results)},
                )
            return results

    async def _report(self, portfolio: Any, kind: str, now: datetime) -> dict[str, Any]:
        try:
            analytics = await self.services.portfolio.analytics(portfolio, fresh=True)
            summary = _headline(analytics)
        except Exception as exc:  # partial failure: still record the attempt
            logger.exception("weekly analytics failed for %s", portfolio.id)
            summary = {"error": f"{type(exc).__name__}: {exc}"}
        date = now.astimezone(BANGKOK).date().isoformat()
        title = f"รายงานพอร์ตรายสัปดาห์ · {portfolio.name} · {date}"
        text = (
            f"ตรวจความเสี่ยงพอร์ต {portfolio.name} ประจำสัปดาห์ ({date}): สรุปมูลค่า กำไร/ขาดทุน "
            "ความกระจุกตัว ความเสี่ยง และตัวที่ขับความเสี่ยงหลัก"
        )
        skill = SKILL if self.services.skills.is_available(SKILL) else None
        session = await sessions_repo.create_session(
            "portfolio", title, meta={"portfolio_ids": [portfolio.id], "weekly": True}
        )
        message = await sessions_repo.add_message(
            session.id, "user", text, meta={"skill": skill, "weekly": True}
        )
        run_id: str | None = None
        try:
            run_id = await self.services.runs.start(
                "research",
                {"text": text, "skill": skill, "history": [], "portfolio_ids": [portfolio.id]},
                session_id=session.id,
            )
            await sessions_repo.set_message_run(message.id, run_id)
        except Exception as exc:
            logger.exception("weekly report run could not start for %s", portfolio.id)
            summary["run_error"] = f"{type(exc).__name__}: {exc}"
        snap = await portfolio_repo.add_snapshot(
            portfolio.id, kind=kind, summary=summary, run_id=run_id, session_id=session.id
        )
        return {
            "portfolio_id": portfolio.id,
            "snapshot_id": snap.id,
            "run_id": run_id,
            "session_id": session.id,
        }

    async def catch_up(self) -> bool:
        """Run now if a scheduled slot passed since the last run (or it never ran and books exist)."""
        last = await self.last_run()
        slot = last_slot(self.clock())
        if last is not None and last >= slot:
            return False
        books = [p for p in await portfolio_repo.list_portfolios() if p.positions]
        if last is None and not books:
            await self.services.memory.put(MEMORY_NS, MEMORY_KEY, {"last_run": self.clock().isoformat(),
                                                                   "kind": "init", "reports": 0})  # fmt: skip
            return False
        await self.run_once(kind="weekly")
        return True

    async def _loop(self) -> None:
        try:
            await self.catch_up()
        except Exception:
            logger.exception("weekly portfolio catch-up failed")
        while True:
            now = self.clock()
            await asyncio.sleep(max(1.0, (next_slot(now) - now).total_seconds()))
            try:
                await self.run_once(kind="weekly")
            except Exception:
                logger.exception("weekly portfolio report failed")

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="portfolio-weekly")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
