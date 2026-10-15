"""Internal compute tools of the trading workflow (P10, F1/F3).

Called by workflow nodes through the executor — so arguments are validated,
results land in the scratchpad and every number gets a provenance record — but
``internal=True`` keeps them out of any LLM's tool list:

- ``trade_snapshot``: the verified market snapshot ``load_context`` treats as
  ground truth for prices (entry, ATR) — the same computation as
  ``get_market_snapshot``, under its own call budget.
- ``trade_quote``: the live price that replaces the snapshot close as entry just
  before approval and again when the human approves (the run takes minutes).
- ``trade_event_risk``: the economic calendar over the holding period (ForexFactory),
  the same data as ``get_economic_calendar`` under its own call budget.
- ``trade_ml_signal``: the deterministic bull/bear model's probabilities at the last
  closed bar (``compute/ml/bull_bear.py``) — evidence for the desk, never an order.
- ``trade_smc``: Smart Money Concepts (``compute/smc.py``) on closed bars — the
  structure/levels block the LLM-only desk reads when no ML signal leads.
- ``size_trade``: turns the trader's *basis* (ATR multiple or a level) into a
  stop price, targets' risk/reward, units, notional and position weight. The
  LLM never supplies any of these numbers.

``limit_by`` partitions the per-run call budget by ``purpose``/``attempt`` so a
policy retry or the portfolio manager's resize is never mistaken for a
repeated query.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, Field

from app.domain.compute import smc, smc_setup
from app.domain.compute.base import compute_record
from app.domain.compute.ml import bull_bear
from app.domain.compute.ml.features import MARKET_SYMBOLS
from app.domain.compute.risk import OrderType, pending_order, position_size, risk_reward, stop_from_atr
from app.domain.data.base import Capability
from app.domain.models.horizon import INTERVAL_MINUTES, INTERVAL_RANGE, time_stop_at
from app.domain.models.market import Quote
from app.domain.tools.base import ToolContext, ToolOutput, ToolSpec
from app.domain.tools.calendar import CalendarArgs, calendar_window
from app.domain.tools.common import MarketHint, derived_record, fetch, to_symbol
from app.domain.tools.market import SnapshotInterval, _snapshot

_VERSION = "v1"


class TradeSnapshotArgs(BaseModel):
    symbol: str = Field(min_length=1)
    market: MarketHint | None = None
    as_of: date | None = None
    interval: SnapshotInterval = "1d"
    purpose: str = "load_context"


async def trade_snapshot(args: TradeSnapshotArgs, ctx: ToolContext) -> ToolOutput:
    sym = to_symbol(args.symbol, args.market)
    value, records, _ = await _snapshot(sym, args.as_of, args.interval)
    close = (value.get("latest_bar") or {}).get("close")
    return ToolOutput(
        data={"currency": sym.currency, **value},
        summary=f"{sym.canonical} {args.interval} as of {value.get('as_of')} · close {close}",
        records=records,
    )


class TradeEventRiskArgs(CalendarArgs):
    purpose: str = "load_context"


async def trade_event_risk(args: TradeEventRiskArgs, ctx: ToolContext) -> ToolOutput:
    return await calendar_window(args)


class TradeQuoteArgs(BaseModel):
    symbol: str = Field(min_length=1)
    market: MarketHint | None = None
    purpose: str = "refresh"


async def trade_quote(args: TradeQuoteArgs, ctx: ToolContext) -> ToolOutput:
    sym = to_symbol(args.symbol, args.market)
    result = await fetch(Capability.QUOTE, sym)
    quote: Quote = result.data
    value = {"provider": result.provider, **quote.model_dump(mode="json")}
    return ToolOutput(
        data=value,
        summary=f"{sym.canonical} live {quote.price} {quote.currency}",
        records=[*result.records, derived_record(result, value, tool="trade_quote", unit=quote.currency)],
        status=result.status,
    )


async def _fetch_market_daily(as_of: date | None = None) -> dict[str, pd.DataFrame]:
    """Fetch VIX/DXY/US10Y daily closes for v0.3 market features.

    Uses the data layer's yfinance provider (same as main bars). Each symbol's daily close
    series is returned as a DataFrame with DatetimeIndex and a ``close`` column.
    """
    from app.domain.models.symbol import AssetType, Market, Symbol

    result: dict[str, pd.DataFrame] = {}
    for yf_sym, prefix in MARKET_SYMBOLS:
        try:
            sym = Symbol(
                raw=yf_sym,
                canonical=yf_sym,
                display=yf_sym,
                asset_type=AssetType.INDEX,
                market=Market.US,
                currency="USD",
            )
            res = await fetch(Capability.PRICES, sym, as_of=as_of, range="1y", interval="1d")
            frame = res.data
            if as_of is not None:
                frame = frame.as_of(as_of)
            bars = list(frame.bars)
            if bars:
                idx = pd.DatetimeIndex([pd.Timestamp(b.ts).tz_localize(None) for b in bars])
                result[prefix] = pd.DataFrame(
                    {"close": [float(b.close) for b in bars]},
                    index=idx,
                )
        except Exception:
            pass  # LightGBM handles NaN natively; missing market data is not fatal
    return result


class TradeMlSignalArgs(BaseModel):
    symbol: str = Field(min_length=1)
    horizon: str = "intraday"
    as_of: date | None = None
    purpose: str = "load_context"


async def trade_ml_signal(args: TradeMlSignalArgs, ctx: ToolContext) -> ToolOutput:
    sym = to_symbol(args.symbol)
    stem = bull_bear.model_for(sym.canonical, args.horizon)
    if stem is None:
        raise ValueError(f"no validated ML model for {sym.canonical} {args.horizon}")
    meta, _ = bull_bear.load(stem)
    tf = meta["timeframe"]
    main_res = await fetch(Capability.PRICES, sym, as_of=args.as_of, range=INTERVAL_RANGE[tf], interval=tf)
    daily_res = await fetch(Capability.PRICES, sym, as_of=args.as_of, range="2y", interval="1d")
    main_frame, daily_frame = main_res.data, daily_res.data
    if args.as_of is not None:
        main_frame, daily_frame = main_frame.as_of(args.as_of), daily_frame.as_of(args.as_of)
    # only CLOSED bars: a live feed's last bar may still be forming
    now = datetime.now(UTC)
    step = timedelta(minutes=INTERVAL_MINUTES[tf])
    main = [b for b in main_frame.bars if b.ts + step <= now]
    all_records = [*main_res.records, *daily_res.records]

    # v0.3: fetch VIX/DXY/US10Y daily data if the model needs market features
    market_daily: dict[str, pd.DataFrame] | None = None
    groups = meta.get("feature_groups") or {}
    if meta.get("v3_features") and groups.get("market"):
        market_daily = await _fetch_market_daily(args.as_of)

    result = bull_bear.predict(
        stem,
        main,
        list(daily_frame.bars),
        inputs=[r.id for r in all_records],
        market_daily=market_daily,
    )
    v = result.value
    return ToolOutput(
        data=v,
        summary=(
            f"{sym.canonical} {stem} @ {v['bar_ts']}: P(bull) {v['p_bull']:.2f} · "
            f"P(bear) {v['p_bear']:.2f} → {v['signal']}"
            f"{' [NFP DAY]' if v.get('is_nfp_day') else ''}"
        ),
        records=[*all_records, *result.records],
    )


class TradeSmcArgs(BaseModel):
    symbol: str = Field(min_length=1)
    interval: SnapshotInterval = "1h"
    as_of: date | None = None
    purpose: str = "load_context"


async def trade_smc(args: TradeSmcArgs, ctx: ToolContext) -> ToolOutput:
    sym = to_symbol(args.symbol)
    res = await fetch(
        Capability.PRICES, sym, as_of=args.as_of, range=INTERVAL_RANGE[args.interval], interval=args.interval
    )
    frame = res.data if args.as_of is None else res.data.as_of(args.as_of)
    # only CLOSED bars: a structure break is confirmed on a close, not on a forming bar
    now = datetime.now(UTC)
    step = timedelta(minutes=INTERVAL_MINUTES[args.interval])
    bars = [b for b in frame.bars if b.ts + step <= now]
    result = smc.smart_money_concepts(bars, inputs=[r.id for r in res.records])
    setup = smc_setup.sweep_continuation_setup(bars, result.value, inputs=[r.id for r in result.records])
    v = {"interval": args.interval, **result.value, "setup": setup.value}
    return ToolOutput(
        data=v,
        summary=(
            f"{sym.canonical} SMC {args.interval} @ {v['bar_ts']}: swing {v['swing']['trend']} · "
            f"internal {v['internal']['trend']} · setup "
            f"{setup.value['side'] if setup.value['active'] else 'none'}"
        ),
        records=[*res.records, *result.records, *setup.records],
    )


DEFAULT_MAX_ORDER_DISTANCE_ATR = 2.0


class SizeTradeArgs(BaseModel):
    symbol: str
    side: Literal["long", "short", "flat"]
    entry: float = Field(gt=0, description="Current price: the entry of a market order")
    # pending entry (limit / stop): ``order_price`` becomes the entry, checked against ``entry``
    order_type: OrderType = "market"
    order_price: float | None = Field(default=None, gt=0)
    order_valid_bars: int | None = Field(default=None, ge=1, le=2000)
    max_order_distance_atr: float = Field(default=DEFAULT_MAX_ORDER_DISTANCE_ATR, gt=0, le=20)
    stop_basis: Literal["atr", "level"] = "atr"
    atr: float | None = Field(default=None, ge=0)
    atr_multiple: float | None = Field(default=None, gt=0, le=10)
    stop_level: float | None = Field(default=None, gt=0)
    targets: list[float] = Field(default_factory=list)
    target_r: list[float] = Field(
        default_factory=list,
        description="Targets as multiples of the stop distance (R) from the entry: re-priced with it",
    )
    equity: float = Field(gt=0, description="Account equity (or assumed notional), instrument currency")
    risk_pct: float = Field(default=0.01, gt=0, le=0.05, description="Equity fraction risked at the stop")
    size_fraction: float = Field(default=1.0, ge=0, le=1, description="Portfolio manager's keep-ratio")
    lot_size: float = Field(default=1, gt=0)
    max_position_weight: float | None = Field(
        default=None, gt=0, le=1, description="Cap units so notional/equity stays within this (cash equities)"
    )
    # stop floor: a stop closer than this many ATR to the entry is widened to it (needs ``atr``)
    min_stop_atr: float | None = Field(default=None, gt=0, le=10)
    # system-set first target at this R multiple of the (floored) stop distance; the trader's
    # ``targets`` beyond it become later targets, closer ones are dropped
    first_target_r: float | None = Field(default=None, gt=0, le=20)
    currency: str = ""
    # time stop: ``time_stop_bars`` bars of ``interval`` after ``start`` (the price's timestamp)
    time_stop_bars: int | None = Field(default=None, ge=1, le=2000)
    interval: SnapshotInterval = "1d"
    start: datetime | None = None
    attempt: str = "trader#1"
    input_provenance_ids: list[str] = Field(default_factory=list)


def _d(x: float) -> Decimal:
    return Decimal(str(x))


async def size_trade(args: SizeTradeArgs, ctx: ToolContext) -> ToolOutput:
    inputs = list(args.input_provenance_ids)
    entry = _d(args.entry)
    records = []
    notes: list[str] = []

    if args.side == "flat":
        value = {
            "side": "flat",
            "entry": args.entry,
            "order_type": "market",
            "order": "market",
            "market_price": args.entry,
            "order_error": None,
            "stop": None,
            "targets": [],
            "risk_reward": [],
            "units": 0,
            "risk_amount": 0.0,
            "notional": 0.0,
            "position_weight": 0.0,
            "risk_fraction": 0.0,
            "currency": args.currency,
            "notes": ["flat: no position"],
        }
        records.append(compute_record(f"compute.trade.size@{_VERSION}", value, inputs=inputs))
        return ToolOutput(data=value, summary=f"{args.symbol} flat · no position", records=records)

    side: Literal["long", "short"] = args.side
    market = entry
    order = pending_order(
        args.order_type,
        side,
        _d(args.order_price) if args.order_price is not None else None,
        market,
        _d(args.atr) if args.atr else None,
        _d(args.max_order_distance_atr),
        inputs=inputs,
    )
    records += order.records
    if args.order_type != "market" and args.order_price is not None:
        entry = _d(args.order_price)
    if order.value["error"]:
        notes.append(str(order.value["error"]))
    stop: Decimal | None = None
    if args.stop_basis == "atr":
        if args.atr and args.atr_multiple:
            res = stop_from_atr(entry, _d(args.atr), _d(args.atr_multiple), side, inputs=inputs)
            stop, records = res.value, [*records, *res.records]
        else:
            notes.append("stop_basis=atr but ATR or multiple missing")
    elif args.stop_level is not None:
        level = _d(args.stop_level)
        if (side == "long" and level < entry) or (side == "short" and level > entry):
            stop = level
            records.append(
                compute_record(
                    f"compute.trade.stop_level@{_VERSION}",
                    str(level),
                    params={"entry": str(entry), "side": side},
                    inputs=inputs,
                )
            )
        else:
            notes.append(f"stop level {args.stop_level} is on the wrong side of entry {entry}")
    else:
        notes.append("stop_basis=level but no stop_level")

    if stop is not None and args.min_stop_atr and args.atr:
        floor = _d(args.atr) * _d(args.min_stop_atr)
        if abs(entry - stop) < floor:
            proposed = stop
            stop = entry - floor if side == "long" else entry + floor
            notes.append(
                f"stop widened from {float(proposed):g} to {float(stop):g}: at least "
                f"{args.min_stop_atr:g} x ATR ({float(floor):g}) from the entry"
            )
            records.append(
                compute_record(
                    f"compute.trade.stop_floor@{_VERSION}",
                    str(stop),
                    params={
                        "entry": str(entry),
                        "proposed": str(proposed),
                        "atr": args.atr,
                        "min_stop_atr": args.min_stop_atr,
                    },
                    inputs=inputs,
                )
            )

    rr_values: list[float | None] = []
    targets: list[float] = []
    if stop is not None:
        wanted = list(args.targets)
        if args.first_target_r is not None:
            risk_dist = abs(entry - stop)
            first = entry + risk_dist * _d(args.first_target_r)
            if side == "short":
                first = entry - risk_dist * _d(args.first_target_r)
            beyond = [t for t in wanted if (_d(t) > first if side == "long" else _d(t) < first)]
            if len(beyond) != len(wanted):
                notes.append(
                    f"{len(wanted) - len(beyond)} proposed target(s) inside the system's first target "
                    f"{float(first):g} dropped"
                )
            wanted = [float(first), *beyond]
            records.append(
                compute_record(
                    f"compute.trade.first_target@{_VERSION}",
                    str(first),
                    params={"entry": str(entry), "stop": str(stop), "r": args.first_target_r, "side": side},
                    inputs=inputs,
                )
            )
        for r in args.target_r:  # R multiples → prices at this entry, so a live re-price moves them too
            offset = abs(entry - stop) * _d(r)
            price = entry + offset if side == "long" else entry - offset
            wanted.append(float(price))
            records.append(
                compute_record(
                    f"compute.trade.target_from_r@{_VERSION}",
                    str(price),
                    params={"entry": str(entry), "stop": str(stop), "r": r, "side": side},
                    inputs=inputs,
                )
            )
        for t in wanted:
            target = _d(t)
            if (side == "long" and target <= entry) or (side == "short" and target >= entry):
                notes.append(f"target {t} is not beyond entry {entry}")
                continue
            rr = risk_reward(entry, stop, target, side, inputs=inputs)
            records += rr.records
            targets.append(t)
            rr_values.append(float(rr.value) if rr.value is not None else None)

    units: float = 0
    risk_amount = 0.0
    if stop is not None:
        risk = _d(args.risk_pct) * _d(args.size_fraction)
        lot = _d(args.lot_size)
        sized = position_size(_d(args.equity), risk, entry, stop, 1, inputs=inputs)
        raw_units = Decimal(str(sized.value["units"])) if lot >= 1 else _raw_units(args, entry, stop, risk)
        if args.max_position_weight is not None:
            cap = _d(args.equity) * _d(args.max_position_weight) / entry
            if raw_units > cap:
                notes.append(f"units capped by max position weight {args.max_position_weight:.0%}")
                raw_units = cap
        lots = (raw_units / lot).to_integral_value(rounding=ROUND_DOWN)
        units_d = lots * lot
        units = float(units_d) if lot < 1 else int(units_d)
        risk_amount = float(units_d * abs(entry - stop))
        records += sized.records

    notional = float(_d(units) * entry) if units else 0.0
    weight = notional / args.equity if args.equity else 0.0
    pending = args.order_type != "market"
    distance = order.value["distance_atr"]
    value = {
        "side": side,
        "entry": float(entry),
        "order_type": args.order_type,
        "order": order.value["order"],
        "market_price": args.entry,
        "order_distance_atr": float(str(distance)) if distance is not None else None,
        "order_error": order.value["error"],
        "stop": float(stop) if stop is not None else None,
        "targets": targets,
        "risk_reward": rr_values,
        "units": units,
        "risk_amount": round(risk_amount, 6),
        "notional": round(notional, 6),
        "position_weight": round(weight, 6),
        "risk_fraction": round(risk_amount / args.equity, 6) if args.equity else 0.0,
        "equity": args.equity,
        "currency": args.currency,
        "notes": notes,
    }
    records.append(
        compute_record(
            f"compute.trade.size@{_VERSION}",
            {k: value[k] for k in ("stop", "units", "notional", "position_weight", "risk_fraction")},
            params={
                "entry": float(entry),
                "equity": args.equity,
                "risk_pct": args.risk_pct,
                "size_fraction": args.size_fraction,
                "lot_size": args.lot_size,
                "basis": args.stop_basis,
            },
            inputs=inputs,
            unit=args.currency or None,
        )
    )
    value.update(_time_stop(args, pending))
    if value["time_stop_at"] is not None:
        records.append(
            compute_record(
                f"compute.trade.time_stop@{_VERSION}",
                value["time_stop_at"],
                params={"start": str(args.start), "interval": args.interval, "bars": args.time_stop_bars},
                inputs=inputs,
            )
        )
    if value["order_expires_at"] is not None:
        records.append(
            compute_record(
                f"compute.trade.order_expiry@{_VERSION}",
                value["order_expires_at"],
                params={
                    "start": str(args.start),
                    "interval": args.interval,
                    "bars": value["order_valid_bars"],
                },
                inputs=inputs,
            )
        )
    stop_txt = f"{value['stop']:.4g}" if value["stop"] is not None else "none"
    how = (
        f"{str(value['order']).replace('_', ' ')} @ {float(entry):.4g}"
        if pending
        else f"entry {float(entry):.4g}"
    )
    return ToolOutput(
        data=value,
        summary=f"{args.symbol} {side} · {how} · stop {stop_txt} · {units} units",
        records=records,
        status="ok" if stop is not None else "partial",
    )


def _time_stop(args: SizeTradeArgs, pending: bool) -> dict[str, Any]:
    """Time stop (and a pending order's expiry) as wall-clock deadlines from ``start``.

    A pending order's time stop counts from the fill, which is unknown, so it has no deadline.
    """
    valid_bars = (args.order_valid_bars or args.time_stop_bars) if pending else None
    at = (
        time_stop_at(args.start, args.interval, args.time_stop_bars).isoformat()
        if args.start is not None and args.time_stop_bars and not pending
        else None
    )
    expires = (
        time_stop_at(args.start, args.interval, valid_bars).isoformat()
        if args.start is not None and valid_bars
        else None
    )
    return {
        "time_stop_bars": args.time_stop_bars,
        "interval": args.interval,
        "time_stop_at": at,
        "time_stop_from": "fill" if pending else "entry",
        "order_valid_bars": valid_bars,
        "order_expires_at": expires,
    }


def _raw_units(args: SizeTradeArgs, entry: Decimal, stop: Decimal, risk: Decimal) -> Decimal:
    """Fractional sizing (crypto): position_size floors to whole units, so redo it unfloored."""
    return (_d(args.equity) * risk) / abs(entry - stop)


TRADING_TOOLS: list[ToolSpec] = [
    ToolSpec(
        name="trade_snapshot",
        description="(internal) verified market snapshot for the trading workflow",
        args_model=TradeSnapshotArgs,
        category="compute",
        requires=frozenset({Capability.PRICES}),
        limit_by="purpose",
        internal=True,
        fn=trade_snapshot,
    ),
    ToolSpec(
        name="trade_quote",
        description="(internal) live price used as entry just before and at approval",
        args_model=TradeQuoteArgs,
        category="data",
        requires=frozenset({Capability.QUOTE}),
        limit_by="purpose",
        internal=True,
        fn=trade_quote,
    ),
    ToolSpec(
        name="trade_event_risk",
        description="(internal) scheduled high/medium-impact releases over the trade's holding period",
        args_model=TradeEventRiskArgs,
        category="data",
        requires=frozenset({Capability.ECONOMIC_CALENDAR}),
        limit_by="purpose",
        internal=True,
        fn=trade_event_risk,
    ),
    ToolSpec(
        name="trade_ml_signal",
        description="(internal) bull/bear ML model probabilities at the last closed bar",
        args_model=TradeMlSignalArgs,
        category="compute",
        requires=frozenset({Capability.PRICES}),
        limit_by="purpose",
        internal=True,
        fn=trade_ml_signal,
    ),
    ToolSpec(
        name="trade_smc",
        description="(internal) Smart Money Concepts structure, order blocks, liquidity and gaps",
        args_model=TradeSmcArgs,
        category="compute",
        requires=frozenset({Capability.PRICES}),
        limit_by="purpose",
        internal=True,
        fn=trade_smc,
    ),
    ToolSpec(
        name="size_trade",
        description="(internal) stop, targets' risk/reward and position size from the trader's basis",
        args_model=SizeTradeArgs,
        category="compute",
        limit_by="attempt",
        internal=True,
        fn=size_trade,
    ),
]
