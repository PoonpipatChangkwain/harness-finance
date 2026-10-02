"""Position sizing and risk/reward math (F1). Money uses Decimal throughout."""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal
from typing import Literal

from app.domain.compute.base import ComputeResult, compute_record

_VERSION = "v1"

Side = Literal["long", "short"]


def position_size(
    equity: Decimal,
    risk_pct: Decimal,
    entry: Decimal,
    stop: Decimal,
    lot_size: int = 1,
    *,
    inputs: list[str] | None = None,
) -> ComputeResult[dict[str, object]]:
    """Units to trade so a stop-out loses at most ``risk_pct`` of ``equity``."""
    stop_distance = abs(entry - stop)
    if stop_distance == 0:
        value = {"units": 0, "risk_amount": "0"}
        return ComputeResult(
            value=value,
            records=[compute_record(f"compute.risk.position_size@{_VERSION}", value, inputs=inputs)],
        )
    risk_amount = equity * risk_pct
    raw_units = risk_amount / stop_distance
    lots = int((raw_units / lot_size).to_integral_value(rounding=ROUND_DOWN))
    units = lots * lot_size
    actual_risk = Decimal(units) * stop_distance
    value = {"units": units, "risk_amount": str(actual_risk)}
    record = compute_record(
        f"compute.risk.position_size@{_VERSION}",
        value,
        params={"equity": str(equity), "risk_pct": str(risk_pct), "entry": str(entry), "stop": str(stop)},
        inputs=inputs,
    )
    return ComputeResult(value=value, records=[record])


def stop_from_atr(
    entry: Decimal, atr_value: Decimal, multiple: Decimal, side: Side, *, inputs: list[str] | None = None
) -> ComputeResult[Decimal]:
    offset = atr_value * multiple
    value = entry - offset if side == "long" else entry + offset
    record = compute_record(
        f"compute.risk.stop_from_atr@{_VERSION}",
        str(value),
        params={"entry": str(entry), "atr": str(atr_value), "multiple": str(multiple), "side": side},
        inputs=inputs,
    )
    return ComputeResult(value=value, records=[record])


def risk_reward(
    entry: Decimal, stop: Decimal, target: Decimal, side: Side, *, inputs: list[str] | None = None
) -> ComputeResult[Decimal | None]:
    if side == "long":
        risk = entry - stop
        reward = target - entry
    else:
        risk = stop - entry
        reward = entry - target
    value = (reward / risk) if risk > 0 else None
    record = compute_record(
        f"compute.risk.risk_reward@{_VERSION}",
        str(value) if value is not None else None,
        params={"entry": str(entry), "stop": str(stop), "target": str(target), "side": side},
        inputs=inputs,
    )
    return ComputeResult(value=value, records=[record])


def realized_r(
    entry: Decimal, stop: Decimal, exit_price: Decimal, side: Side, *, inputs: list[str] | None = None
) -> ComputeResult[Decimal | None]:
    """Result of a closed trade in units of its initial risk: +2 = won twice the risk, -1 = full stop."""
    risk = (entry - stop) if side == "long" else (stop - entry)
    pnl = (exit_price - entry) if side == "long" else (entry - exit_price)
    value = (pnl / risk) if risk > 0 else None
    record = compute_record(
        f"compute.risk.realized_r@{_VERSION}",
        str(value) if value is not None else None,
        params={"entry": str(entry), "stop": str(stop), "exit": str(exit_price), "side": side},
        inputs=inputs,
    )
    return ComputeResult(value=value, records=[record])


OrderType = Literal["market", "limit", "stop"]


def pending_order(
    order_type: OrderType,
    side: Side,
    order_price: Decimal | None,
    market_price: Decimal | None,
    atr_value: Decimal | None,
    max_distance_atr: Decimal,
    *,
    inputs: list[str] | None = None,
) -> ComputeResult[dict[str, object]]:
    """Name and check a pending entry order against the current price.

    A limit waits for a better price (buy below / sell above the market), a stop for a
    breakout (buy above / sell below). The order price must also sit within
    ``max_distance_atr`` ATRs of the market, so a far-away level cannot buy a flattering
    R:R. Anything that cannot be checked (no market price, no ATR) fails closed with an
    ``error``; a market order is always valid.
    """
    buy = side == "long"
    if order_type == "market":
        value: dict[str, object] = {"order": "market", "distance_atr": None, "error": None}
        return ComputeResult(value=value, records=[])
    name = f"{'buy' if buy else 'sell'}_{order_type}"
    label = name.replace("_", " ")
    distance: Decimal | None = None
    error: str | None = None
    if order_price is None:
        error = f"{label}: order_price is missing"
    elif market_price is None:
        error = f"{label}: no current price to check the order against"
    else:
        # limit: buy below / sell above the market; stop: buy above / sell below
        below = order_type == "limit" if buy else order_type == "stop"
        if below and not order_price < market_price:
            error = f"{label} {order_price} must be below the current price {market_price}"
        elif not below and not order_price > market_price:
            error = f"{label} {order_price} must be above the current price {market_price}"
        if atr_value is None or atr_value <= 0:
            error = error or f"{label}: no ATR to measure the order's distance from the price"
        else:
            distance = abs(order_price - market_price) / atr_value
            if error is None and distance > max_distance_atr:
                error = (
                    f"{label} {order_price} is {distance:.2f} ATR from the current price {market_price} "
                    f"(max {max_distance_atr} ATR)"
                )
    value = {
        "order": name,
        "distance_atr": str(distance.quantize(Decimal("0.0001"))) if distance is not None else None,
        "error": error,
    }
    record = compute_record(
        f"compute.trade.order@{_VERSION}",
        value,
        params={
            "order_type": order_type,
            "side": side,
            "order_price": str(order_price),
            "market_price": str(market_price),
            "atr": str(atr_value),
            "max_distance_atr": str(max_distance_atr),
        },
        inputs=inputs,
    )
    return ComputeResult(value=value, records=[record])


_JPY_PAIRS = {"JPY"}


def forex_pip_value(
    pair: str, lot_units: Decimal, account_ccy: str, rate: Decimal, *, inputs: list[str] | None = None
) -> ComputeResult[Decimal]:
    """Value of one pip move for ``lot_units`` of ``pair``, in ``account_ccy``.

    ``rate`` is the quote currency -> account currency conversion rate (1 if
    the quote currency already equals ``account_ccy``). JPY-quoted pairs use
    a pip size of 0.01; every other pair uses 0.0001.
    """
    quote_ccy = pair[3:6].upper() if len(pair) >= 6 else pair[-3:].upper()
    pip_size = Decimal("0.01") if quote_ccy in _JPY_PAIRS else Decimal("0.0001")
    value = lot_units * pip_size * rate
    record = compute_record(
        f"compute.risk.forex_pip_value@{_VERSION}",
        str(value),
        params={"pair": pair, "lot_units": str(lot_units), "account_ccy": account_ccy, "rate": str(rate)},
        inputs=inputs,
    )
    return ComputeResult(value=value, records=[record])
