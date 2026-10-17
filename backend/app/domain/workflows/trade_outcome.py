"""Closing the loop on a recorded trade decision: the human's outcome → R → a lesson.

Ported concept from ``../TradingAgents/tradingagents/graph/reflection.py`` (``Reflector``):
once the outcome is known, a quick model writes a 2-4 sentence lesson that is stored with
the decision and fed back to the bull/bear researchers and the portfolio manager of the
next run on the same symbol (``DecisionLogPort.lessons``).

The outcome itself is the trader's record — the system sends no orders, so only the human
knows whether the stop or a target was hit, or where they exited. Every number of the
result (exit price, R) is computed here, never by the model (F1).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.domain.agents.library import get_role, role_profile
from app.domain.compute.risk import realized_r
from app.domain.memory.tags import market_tag
from app.domain.models.provenance import ProvenanceRecord
from app.domain.ports import LLMMessage

OutcomeKind = Literal["tp", "sl", "manual_exit", "time_stop", "not_filled", "cancelled"]
FILLED: frozenset[str] = frozenset({"tp", "sl", "manual_exit", "time_stop"})
LABELS = {
    "tp": "take-profit hit",
    "sl": "stop-loss hit",
    "manual_exit": "closed manually",
    "time_stop": "closed at the time stop",
    "not_filled": "entry never filled",
    "cancelled": "cancelled before entry",
}


class TradeOutcomeInput(BaseModel):
    outcome: OutcomeKind
    exit_price: float | None = Field(default=None, gt=0, description="Required for manual/time-stop exits")
    target_index: int = Field(default=0, ge=0, le=4, description="Which target was hit (tp), 0 = first")
    note: str = Field(default="", max_length=1000)


class OutcomeError(ValueError):
    """The outcome does not fit the recorded decision (e.g. a TP on a card with no target)."""


def resolve_outcome(
    card: dict[str, Any], inp: TradeOutcomeInput, *, inputs: list[str] | None = None
) -> tuple[dict[str, Any], list[ProvenanceRecord]]:
    """Exit price and R of a settled decision; raises ``OutcomeError`` when they cannot be known."""
    side = card.get("side")
    entry, stop = card.get("entry"), card.get("stop")
    value: dict[str, Any] = {
        "kind": inp.outcome,
        "label": LABELS[inp.outcome],
        "note": inp.note,
        "settled_at": datetime.now(UTC).isoformat(),
        "exit_price": None,
        "r_multiple": None,
    }
    if inp.outcome not in FILLED:
        return value, []
    if side not in ("long", "short") or entry is None or stop is None:
        raise OutcomeError("this decision has no filled position (flat or no stop) to settle")
    exit_price = inp.exit_price
    if exit_price is None and inp.outcome == "tp":
        targets = card.get("targets") or []
        if inp.target_index >= len(targets):
            raise OutcomeError(f"the decision has {len(targets)} target(s); give the exit price")
        exit_price = float(targets[inp.target_index])
    elif exit_price is None and inp.outcome == "sl":
        exit_price = float(stop)
    if exit_price is None:
        raise OutcomeError(f"{inp.outcome}: exit_price is required")
    result = realized_r(
        Decimal(str(entry)), Decimal(str(stop)), Decimal(str(exit_price)), side, inputs=inputs
    )
    value.update(
        exit_price=exit_price,
        r_multiple=round(float(result.value), 4) if result.value is not None else None,
    )
    return value, list(result.records)


def reflection_messages(record: dict[str, Any], outcome: dict[str, Any], language: str) -> list[LLMMessage]:
    card = record.get("card") or {}
    r = outcome.get("r_multiple")
    result = f"{r:+.2f}R" if isinstance(r, int | float) else "no position (no R)"
    user = (
        f"Instrument: {card.get('display') or card.get('symbol')} · horizon {card.get('horizon')} "
        f"({card.get('interval')} bars) · decided {record.get('recorded_at') or record.get('date')}\n"
        f"Decision: {card.get('rating')} / {card.get('side')} · entry {card.get('entry')} "
        f"· stop {card.get('stop')} · targets {card.get('targets')} · R:R {card.get('risk_reward')} "
        f"· time stop {card.get('time_stop_bars')} bars ({card.get('time_stop_at')})\n"
        f"Invalidation: {card.get('invalidation') or '(none given)'}\n"
        f"Thesis: {record.get('thesis') or '(none)'}\n\n"
        f"Outcome: {outcome['label']} · exit {outcome.get('exit_price')} · result {result}\n"
        f"Trader's note: {outcome.get('note') or '(none)'}\n\n"
        f"Write the lesson in {language}."
    )
    return [
        LLMMessage(role="system", content=role_profile(get_role("trade_reflector"))),
        LLMMessage(role="user", content=user),
    ]


# (P17) ReasoningBank: a settled trade with an R becomes an anonymised lesson for every trader.
R_SUCCESS = 0.2  # |R| within ±0.2 is a scratch: "mixed"


def trade_lesson_inputs(record: dict[str, Any]) -> dict[str, Any] | None:
    """``bank.distill`` arguments for a settled decision, or None when there is nothing to learn
    (no R: not filled / cancelled). Pure: the summary says what happened; the bank scrubs it."""
    outcome = record.get("outcome") or {}
    r = outcome.get("r_multiple")
    if not isinstance(r, int | float):
        return None
    card = record.get("card") or {}
    signal = "success" if r > R_SUCCESS else "failure" if r < -R_SUCCESS else "mixed"
    summary = "\n".join(
        [
            f"Trade: {card.get('side')} · horizon {card.get('horizon')} ({card.get('interval')} bars) · "
            f"order {card.get('order') or card.get('order_type') or 'market'} "
            f"· R:R {card.get('risk_reward')}",
            f"Invalidation: {card.get('invalidation') or '(none)'}",
            f"Thesis: {str(record.get('thesis') or '(none)')[:1500]}",
            f"Outcome: {outcome.get('label')} · result {r:+.2f}R",
            f"Trader's note: {outcome.get('note') or '(none)'}",
            f"Reflection: {record.get('reflection') or '(none)'}",
        ]
    )
    symbol = str(record.get("symbol") or card.get("symbol") or "")
    tags = {
        "workflow": "trade",
        "market": market_tag(symbol) if symbol else None,
        "horizon": card.get("horizon"),
    }
    return {
        "run_summary": summary,
        "success": r > 0,
        "score": max(0.0, min(1.0, 0.5 + r / 4)),
        "signal": signal,
        "tags": {k: v for k, v in tags.items() if v},
    }
