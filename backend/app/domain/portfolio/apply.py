"""Apply an approved rebalance plan to a book (P12 §12.3 "Apply to portfolio"). Pure compute.

Quantities move by each leg's lot-rounded share count; a buy blends the average cost
(``(q·avg + s·price) / (q + s)``, local currency), a sell keeps it; a position that reaches
zero is removed; cash moves by the plan's traded value (base currency). Returns the new
positions, cash and a before/after diff for the confirmation dialog.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.domain.compute.base import ComputeResult, compute_record
from app.domain.models.portfolio import Portfolio, Position
from app.domain.models.symbol import normalize_symbol


def apply_legs(
    portfolio: Portfolio, legs: list[dict[str, Any]], *, inputs: list[str] | None = None
) -> ComputeResult[dict[str, Any]]:
    by_symbol = {p.symbol.canonical: p for p in portfolio.positions}
    order = [p.symbol.canonical for p in portfolio.positions]
    diff: list[dict[str, Any]] = []
    traded = Decimal(0)
    for leg in legs:
        shares = Decimal(str(leg.get("shares") or 0))
        if shares == 0:
            continue
        symbol = str(leg["symbol"])
        price = Decimal(str(leg["price"])) if leg.get("price") is not None else None
        traded += Decimal(str(leg.get("value") or 0))
        current = by_symbol.get(symbol)
        before = current.quantity if current is not None else Decimal(0)
        after = before + shares
        avg = current.avg_cost if current is not None else None
        if shares > 0 and price is not None:
            avg = price if (avg is None or before <= 0) else (before * avg + shares * price) / after
        if current is None:
            current = Position(symbol=normalize_symbol(symbol), quantity=after, avg_cost=avg)
            order.append(symbol)
        else:
            current = current.model_copy(update={"quantity": after, "avg_cost": avg})
        by_symbol[symbol] = current
        diff.append({"symbol": symbol, "before": str(before), "after": str(after), "change": str(shares)})
    positions = [by_symbol[s] for s in order if by_symbol[s].quantity != 0]
    cash_before = portfolio.cash
    cash_after: Decimal | None = (cash_before or Decimal(0)) - traded
    if cash_before is None and cash_after == 0:
        cash_after = None  # still "not given", not a fabricated zero
    value = {
        "positions": positions,
        "cash_before": cash_before,
        "cash_after": cash_after,
        "diff": diff,
    }
    record = compute_record(
        "compute.portfolio.apply_plan@v1",
        {"diff": diff, "cash_after": None if cash_after is None else str(cash_after)},
        params={"legs": len(legs)},
        inputs=inputs,
        unit=portfolio.base_currency,
    )
    return ComputeResult(value=value, records=[record])
