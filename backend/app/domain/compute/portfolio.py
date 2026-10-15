"""Deterministic portfolio math (F1): weights, P&L, concentration, exposure, rebalancing.

Money uses ``Decimal`` throughout; weights are proportions in [0, 1] and are
only ever formatted as a percentage at the UI boundary. Every function
returns a ``ComputeResult`` carrying provenance (contracts.md §2).
"""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal
from typing import Literal

from app.domain.compute.base import ComputeResult, compute_record
from app.domain.models.portfolio import Position

_VERSION = "v1"


def market_values(
    positions: list[Position],
    prices: dict[str, Decimal],
    fx: dict[str, Decimal],
    base_ccy: str,
    *,
    inputs: list[str] | None = None,
) -> ComputeResult[dict[str, Decimal]]:
    """Market value of each position, converted into ``base_ccy``.

    ``prices`` maps ``symbol.canonical`` -> last price in the symbol's own
    currency. ``fx`` maps a currency code -> multiplier into ``base_ccy``
    (``fx[base_ccy]`` is always 1).
    """
    values: dict[str, Decimal] = {}
    for pos in positions:
        price = prices.get(pos.symbol.canonical)
        if price is None:
            continue
        rate = fx.get(pos.symbol.currency, Decimal(1) if pos.symbol.currency == base_ccy else None)
        if rate is None:
            continue
        values[pos.symbol.canonical] = pos.quantity * price * rate
    record = compute_record(
        f"compute.portfolio.market_values@{_VERSION}",
        {k: str(v) for k, v in values.items()},
        params={"base_ccy": base_ccy},
        inputs=inputs,
    )
    return ComputeResult(value=values, records=[record])


def weights(
    values: dict[str, Decimal], cash: Decimal | None, *, inputs: list[str] | None = None
) -> ComputeResult[dict[str, Decimal]]:
    """Proportional weight of each position plus ``"CASH"``; sums to 1 (+-1e-9)."""
    cash_value = cash or Decimal(0)
    total = sum(values.values(), Decimal(0)) + cash_value
    result: dict[str, Decimal] = {}
    if total == 0:
        record = compute_record(
            f"compute.portfolio.weights@{_VERSION}", {}, params={"total": "0"}, inputs=inputs
        )
        return ComputeResult(value={}, records=[record])
    for symbol, value in values.items():
        result[symbol] = value / total
    result["CASH"] = cash_value / total
    record = compute_record(
        f"compute.portfolio.weights@{_VERSION}",
        {k: str(v) for k, v in result.items()},
        params={"total": str(total)},
        inputs=inputs,
    )
    return ComputeResult(value=result, records=[record])


def unrealized_pnl(
    position: Position, price: Decimal, *, inputs: list[str] | None = None
) -> ComputeResult[tuple[Decimal, Decimal] | None]:
    """(absolute P&L, pct P&L) for a position, or None when ``avg_cost`` is unknown."""
    if position.avg_cost is None:
        record = compute_record(
            f"compute.portfolio.unrealized_pnl@{_VERSION}",
            None,
            params={"reason": "no_avg_cost"},
            inputs=inputs,
        )
        return ComputeResult(value=None, records=[record])
    abs_pnl = (price - position.avg_cost) * position.quantity
    pct_pnl = (price - position.avg_cost) / position.avg_cost
    record = compute_record(
        f"compute.portfolio.unrealized_pnl@{_VERSION}",
        {"abs": str(abs_pnl), "pct": str(pct_pnl)},
        params={"price": str(price), "avg_cost": str(position.avg_cost)},
        inputs=inputs,
    )
    return ComputeResult(value=(abs_pnl, pct_pnl), records=[record])


def concentration(
    weights_map: dict[str, Decimal], *, inputs: list[str] | None = None
) -> ComputeResult[dict[str, float]]:
    """HHI, top-1, top-3 weight and the effective number of positions (1/HHI)."""
    non_cash = {k: v for k, v in weights_map.items() if k != "CASH"}
    ws = sorted((float(v) for v in non_cash.values()), reverse=True)
    hhi = sum(w * w for w in ws)
    top1 = ws[0] if ws else 0.0
    top3 = sum(ws[:3])
    effective_n = (1.0 / hhi) if hhi > 0 else 0.0
    value = {"hhi": hhi, "top1": top1, "top3": top3, "effective_n": effective_n}
    record = compute_record(f"compute.portfolio.concentration@{_VERSION}", value, inputs=inputs)
    return ComputeResult(value=value, records=[record])


def exposure(
    weights_map: dict[str, Decimal],
    by: Literal["sector", "asset_type", "currency", "market"],
    meta: dict[str, dict[str, str]],
    *,
    inputs: list[str] | None = None,
) -> ComputeResult[dict[str, Decimal]]:
    """Sum of weights grouped by a symbol attribute (sector, asset type, ...)."""
    grouped: dict[str, Decimal] = {}
    for symbol, w in weights_map.items():
        key = "CASH" if symbol == "CASH" else meta.get(symbol, {}).get(by, "unknown")
        grouped[key] = grouped.get(key, Decimal(0)) + w
    record = compute_record(
        f"compute.portfolio.exposure@{_VERSION}",
        {k: str(v) for k, v in grouped.items()},
        params={"by": by},
        inputs=inputs,
    )
    return ComputeResult(value=grouped, records=[record])


class RebalanceLeg:
    __slots__ = ("action", "shares", "symbol", "value")

    def __init__(self, symbol: str, action: Literal["buy", "sell", "hold"], shares: int, value: Decimal):
        self.symbol = symbol
        self.action = action
        self.shares = shares
        self.value = value

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, RebalanceLeg):
            return NotImplemented
        return (self.symbol, self.action, self.shares, self.value) == (
            other.symbol,
            other.action,
            other.shares,
            other.value,
        )

    def __repr__(self) -> str:  # pragma: no cover - debug only
        return f"RebalanceLeg({self.symbol!r}, {self.action!r}, {self.shares}, {self.value})"


def rebalance_trades(
    current_w: dict[str, Decimal],
    target_w: dict[str, Decimal],
    total_value: Decimal,
    prices: dict[str, Decimal],
    lot_size: dict[str, int],
    *,
    inputs: list[str] | None = None,
) -> ComputeResult[dict[str, object]]:
    """Shares to buy/sell to move from ``current_w`` toward ``target_w``.

    Rounds down to whole lots (Thai equities: 100) so the plan never spends
    more cash than available; the un-traded remainder stays as cash.
    """
    legs: list[RebalanceLeg] = []
    cash_delta = Decimal(0)
    symbols = set(current_w) | set(target_w)
    symbols.discard("CASH")
    for symbol in sorted(symbols):
        price = prices.get(symbol)
        if price is None or price == 0:
            continue
        cur = current_w.get(symbol, Decimal(0))
        tgt = target_w.get(symbol, Decimal(0))
        delta_value = (tgt - cur) * total_value
        lot = lot_size.get(symbol, 1)
        raw_shares = delta_value / price
        lots = int((abs(raw_shares) / lot).to_integral_value(rounding=ROUND_DOWN))
        shares = lots * lot
        if shares == 0:
            legs.append(RebalanceLeg(symbol, "hold", 0, Decimal(0)))
            continue
        signed_shares = shares if delta_value > 0 else -shares
        traded_value = Decimal(signed_shares) * price
        cash_delta -= traded_value
        legs.append(RebalanceLeg(symbol, "buy" if signed_shares > 0 else "sell", signed_shares, traded_value))
    value: dict[str, object] = {
        "legs": [
            {"symbol": leg.symbol, "action": leg.action, "shares": leg.shares, "value": str(leg.value)}
            for leg in legs
        ],
        "cash_delta": str(cash_delta),
    }
    record = compute_record(f"compute.portfolio.rebalance_trades@{_VERSION}", value, inputs=inputs)
    return ComputeResult(value=value, records=[record])


def trim_buys_to_cash(
    legs: list[dict[str, object]],
    prices: dict[str, Decimal],
    lot_size: dict[str, int],
    cash: Decimal,
    *,
    inputs: list[str] | None = None,
) -> ComputeResult[dict[str, object]]:
    """Cut buy legs lot by lot (largest first) until cash after the trades is not negative.

    ``rebalance_trades`` floors buys *and* sells to whole lots, so a sell that
    loses a partial lot can leave the plan a little short of cash; this never
    lets a plan spend money the account does not have.
    """
    out = [dict(leg) for leg in legs]
    cash_after = cash - sum((Decimal(str(leg["value"])) for leg in out), Decimal(0))
    trimmed: dict[str, int] = {}
    while cash_after < 0:
        buys = [leg for leg in out if int(str(leg["shares"])) > 0]
        if not buys:
            break
        leg = max(buys, key=lambda x: Decimal(str(x["value"])))
        symbol = str(leg["symbol"])
        lot = lot_size.get(symbol, 1)
        shares = int(str(leg["shares"])) - lot
        price = prices[symbol]
        leg["shares"] = shares
        leg["value"] = str(Decimal(shares) * price)
        leg["action"] = "buy" if shares > 0 else "hold"
        cash_after += Decimal(lot) * price
        trimmed[symbol] = trimmed.get(symbol, 0) + lot
    value: dict[str, object] = {"legs": out, "cash_after": str(cash_after), "trimmed": trimmed}
    record = compute_record(
        f"compute.portfolio.trim_buys_to_cash@{_VERSION}",
        {"cash_after": str(cash_after), "trimmed": trimmed},
        params={"cash": str(cash)},
        inputs=inputs,
    )
    return ComputeResult(value=value, records=[record])


def pnl_summary(
    rows: list[dict[str, Decimal | None]], *, inputs: list[str] | None = None
) -> ComputeResult[dict[str, Decimal | None]]:
    """Portfolio totals from per-position values already converted to the base currency (P12).

    Each row has ``value`` (market value), ``cost`` (quantity * avg cost, None when
    the cost is unknown) and ``prev_value`` (quantity * previous close, None when
    unknown). Unrealized P&L only counts rows with a cost; day P&L only rows with a
    previous close — a missing input is left out, never guessed as zero.
    """
    zero = Decimal(0)
    invested = sum((r["value"] or zero for r in rows), zero)
    costed = [r for r in rows if r["cost"] is not None and r["value"] is not None]
    cost = sum((r["cost"] or zero for r in costed), zero)
    pnl = sum(((r["value"] or zero) - (r["cost"] or zero) for r in costed), zero)
    dated = [r for r in rows if r["prev_value"] is not None and r["value"] is not None]
    prev = sum((r["prev_value"] or zero for r in dated), zero)
    day = sum(((r["value"] or zero) - (r["prev_value"] or zero) for r in dated), zero)
    value: dict[str, Decimal | None] = {
        "invested_value": invested,
        "cost_basis": cost if costed else None,
        "unrealized_pnl": pnl if costed else None,
        "unrealized_pnl_pct": (pnl / cost) if costed and cost != 0 else None,
        "day_pnl": day if dated else None,
        "day_pnl_pct": (day / prev) if dated and prev != 0 else None,
    }
    record = compute_record(
        f"compute.portfolio.pnl_summary@{_VERSION}",
        {k: None if v is None else str(v) for k, v in value.items()},
        params={"rows": len(rows), "costed": len(costed), "dated": len(dated)},
        inputs=inputs,
    )
    return ComputeResult(value=value, records=[record])


def units_from_value(
    value: Decimal, pnl_pct: Decimal | None, price: Decimal, *, inputs: list[str] | None = None
) -> ComputeResult[dict[str, Decimal | None]]:
    """Units and average cost from "market value + % gain" (the Dime!-style entry, P12 §12.7).

    ``value`` and ``price`` are in the symbol's own currency; ``pnl_pct`` is a
    fraction (0.1452 = +14.52%). ``units = value / price`` and
    ``cost_basis = value / (1 + pnl_pct)`` so later prices move value and P&L.
    (P16) ``pnl_pct=None`` ("value only") gives the units and leaves the cost unknown.
    """
    if price <= 0:
        raise ValueError("price must be positive")
    if pnl_pct is not None and pnl_pct <= -1:
        raise ValueError("pnl_pct must be greater than -100%")
    units = value / price
    cost_basis = value / (1 + pnl_pct) if pnl_pct is not None else None
    avg_cost = (cost_basis / units if units != 0 else Decimal(0)) if cost_basis is not None else None
    out: dict[str, Decimal | None] = {"units": units, "cost_basis": cost_basis, "avg_cost": avg_cost}
    record = compute_record(
        f"compute.portfolio.units_from_value@{_VERSION}",
        {k: None if v is None else str(v) for k, v in out.items()},
        params={
            "value": str(value),
            "pnl_pct": None if pnl_pct is None else str(pnl_pct),
            "price": str(price),
        },
        inputs=inputs,
    )
    return ComputeResult(value=out, records=[record])


def _positive(value: Decimal, label: str) -> None:
    if value <= 0:
        raise ValueError(f"{label}ต้องมากกว่า 0 (ได้ {value})")


def _pnl_pct_ok(pnl_pct: Decimal) -> None:
    if pnl_pct <= -1:
        raise ValueError(f"% กำไร/ขาดทุนต้องมากกว่า -100% (ได้ {pnl_pct:%})")


def avg_cost_from_pnl_pct(
    price: Decimal, pnl_pct: Decimal, *, inputs: list[str] | None = None
) -> ComputeResult[dict[str, Decimal]]:
    """Average cost from "latest price + % gain" (P16): ``avg_cost = price / (1 + pnl_pct)``.

    ``pnl_pct`` is a fraction (0.0769 = +7.69%, negative = loss).
    """
    _positive(price, "ราคาล่าสุด")
    _pnl_pct_ok(pnl_pct)
    out = {"avg_cost": price / (1 + pnl_pct)}
    record = compute_record(
        f"compute.portfolio.avg_cost_from_pnl_pct@{_VERSION}",
        {k: str(v) for k, v in out.items()},
        params={"price": str(price), "pnl_pct": str(pnl_pct)},
        inputs=inputs,
    )
    return ComputeResult(value=out, records=[record])


def avg_cost_from_pnl_amount(
    quantity: Decimal, price: Decimal, pnl_amount: Decimal, *, inputs: list[str] | None = None
) -> ComputeResult[dict[str, Decimal]]:
    """Average cost from "quantity + gain in money" (P16).

    ``cost_basis = quantity * price - pnl_amount`` and ``avg_cost = cost_basis / quantity``;
    ``pnl_amount`` is in the symbol's currency, negative = loss. A loss larger than the
    market value would mean a cost basis <= 0 and is refused.
    """
    _positive(quantity, "จำนวนหุ้น")
    _positive(price, "ราคาล่าสุด")
    cost_basis = quantity * price - pnl_amount
    if cost_basis <= 0:
        raise ValueError("กำไรที่กรอกมากกว่ามูลค่าปัจจุบัน ทำให้ต้นทุนรวม ≤ 0 — ตรวจเครื่องหมายหรือจำนวนอีกครั้ง")
    out = {"avg_cost": cost_basis / quantity, "cost_basis": cost_basis}
    record = compute_record(
        f"compute.portfolio.avg_cost_from_pnl_amount@{_VERSION}",
        {k: str(v) for k, v in out.items()},
        params={"quantity": str(quantity), "price": str(price), "pnl_amount": str(pnl_amount)},
        inputs=inputs,
    )
    return ComputeResult(value=out, records=[record])


def units_from_value_and_pnl_amount(
    value: Decimal, pnl_amount: Decimal, price: Decimal, *, inputs: list[str] | None = None
) -> ComputeResult[dict[str, Decimal]]:
    """Units and average cost from "market value + gain in money" (P16).

    ``units = value / price``, ``cost_basis = value - pnl_amount``,
    ``avg_cost = cost_basis / units`` (all in the symbol's currency).
    """
    _positive(value, "มูลค่า")
    _positive(price, "ราคาล่าสุด")
    cost_basis = value - pnl_amount
    if cost_basis <= 0:
        raise ValueError("กำไรที่กรอกไม่น้อยกว่ามูลค่าปัจจุบัน ทำให้ต้นทุนรวม ≤ 0 — ตรวจเครื่องหมายหรือตัวเลขอีกครั้ง")
    units = value / price
    out = {"units": units, "cost_basis": cost_basis, "avg_cost": cost_basis / units}
    record = compute_record(
        f"compute.portfolio.units_from_value_and_pnl_amount@{_VERSION}",
        {k: str(v) for k, v in out.items()},
        params={"value": str(value), "pnl_amount": str(pnl_amount), "price": str(price)},
        inputs=inputs,
    )
    return ComputeResult(value=out, records=[record])


def avg_cost_from_cost_total(
    quantity: Decimal, cost_total: Decimal, *, inputs: list[str] | None = None
) -> ComputeResult[dict[str, Decimal]]:
    """Average cost from "quantity + total cost paid" (P16): ``avg_cost = cost_total / quantity``.

    ``cost_total`` may be 0 (shares received for free) but never negative.
    """
    _positive(quantity, "จำนวนหุ้น")
    if cost_total < 0:
        raise ValueError(f"ต้นทุนรวมต้องไม่ติดลบ (ได้ {cost_total})")
    out = {"avg_cost": cost_total / quantity, "cost_basis": cost_total}
    record = compute_record(
        f"compute.portfolio.avg_cost_from_cost_total@{_VERSION}",
        {k: str(v) for k, v in out.items()},
        params={"quantity": str(quantity), "cost_total": str(cost_total)},
        inputs=inputs,
    )
    return ComputeResult(value=out, records=[record])


def position_value(
    quantity: Decimal,
    avg_cost: Decimal | None,
    price: Decimal | None,
    *,
    inputs: list[str] | None = None,
) -> ComputeResult[dict[str, Decimal | None]]:
    """One holding's market value, cost basis and unrealized P&L in the symbol's currency (P16
    entry preview). A missing price or cost leaves the numbers that need it ``None`` — never 0.
    """
    market_value = quantity * price if price is not None else None
    cost_basis = quantity * avg_cost if avg_cost is not None else None
    pnl = market_value - cost_basis if market_value is not None and cost_basis is not None else None
    pnl_pct = pnl / cost_basis if pnl is not None and cost_basis else None
    out: dict[str, Decimal | None] = {
        "market_value": market_value,
        "cost_basis": cost_basis,
        "unrealized_pnl": pnl,
        "unrealized_pnl_pct": pnl_pct,
    }
    record = compute_record(
        f"compute.portfolio.position_value@{_VERSION}",
        {k: None if v is None else str(v) for k, v in out.items()},
        params={
            "quantity": str(quantity),
            "avg_cost": None if avg_cost is None else str(avg_cost),
            "price": None if price is None else str(price),
        },
        inputs=inputs,
    )
    return ComputeResult(value=out, records=[record])


def post_trade(
    values: dict[str, Decimal],
    cash: Decimal,
    legs: list[dict[str, object]],
    *,
    inputs: list[str] | None = None,
) -> ComputeResult[dict[str, object]]:
    """Values, weights (incl. ``"CASH"``), cash and turnover after applying rebalance legs.

    ``values`` are current market values in the base currency, ``legs`` come from
    ``rebalance_trades`` (``value`` = signed traded value in the base currency).
    Turnover is gross traded value / portfolio value.
    """
    after = dict(values)
    traded = Decimal(0)
    cash_after = cash
    for leg in legs:
        v = Decimal(str(leg["value"]))
        symbol = str(leg["symbol"])
        after[symbol] = after.get(symbol, Decimal(0)) + v
        cash_after -= v
        traded += abs(v)
    total = sum(values.values(), Decimal(0)) + cash
    weights_after = {s: (v / total) for s, v in after.items()} if total else {}
    if total:
        weights_after["CASH"] = cash_after / total
    out: dict[str, object] = {
        "values": after,
        "weights": weights_after,
        "cash": cash_after,
        "turnover": (traded / total) if total else Decimal(0),
        "traded_value": traded,
    }
    record = compute_record(
        f"compute.portfolio.post_trade@{_VERSION}",
        {
            "weights": {k: str(v) for k, v in weights_after.items()},
            "cash": str(cash_after),
            "turnover": str(out["turnover"]),
        },
        params={"legs": len(legs), "total": str(total)},
        inputs=inputs,
    )
    return ComputeResult(value=out, records=[record])


def declared_weights_only(positions: list[Position]) -> bool:
    """True when every position carries a declared %% weight but zero quantity.

    Used when the user only knows allocation percentages, not exact share
    counts (contracts.md §5): the declared weight is then treated as the
    actual weight instead of one derived from price * quantity.
    """
    if not positions:
        return False
    return all(p.declared_weight is not None and p.quantity == 0 for p in positions)
