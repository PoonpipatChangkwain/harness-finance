"""Portfolio simulator: replay decisions over real bars with a cash ledger (Phase 15, layer B).

Deterministic and LLM-free (F1): the same decisions, bars and ``SimConfig`` always give
the same result to the last digit, so a backtest can be re-simulated with another starting
capital in milliseconds.

Per bar of a symbol, in order (the open is the only price whose time is known):

1. **gap at the open** — an open position whose open is already past its stop/target
   exits at the open (not at the level);
2. **new decisions** dated before this bar: a decision against an open position closes it
   at the open (``reverse``); a market order fills at the open (+ slippage) unless the open
   is already past its stop or first target (``gap_invalidated``); a limit/stop order is
   placed;
3. **pending orders** fill when the bar trades through their price; the fill bar checks
   only the stop (whether the target came first inside that bar is unknowable);
4. **intrabar exits** — the low/high reaching the stop or a target; both in one bar is
   resolved by ``intrabar_rule`` (default: the stop, pessimistic);
5. **time stop** after ``time_stop_bars`` bars → exit at the close;
6. the symbol's last bar closes what is left (``end_of_test``).

Account model: ``cash`` is the realized balance; equity = cash + unrealized P&L; an open
position holds margin = notional / leverage, and a new one is refused when equity minus
margin in use cannot carry it (leverage 1 = a cash account). Sizing: units so a stop-out
loses ``risk_pct x size_fraction`` of equity (or of the initial capital without
compounding), capped by ``max_position_weight``, rounded down to whole lots.
"""

from __future__ import annotations

import bisect
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_DOWN, Decimal
from itertools import groupby

from app.domain.compute.backtest.models import (
    ENGINE_VERSION,
    EquityPoint,
    ExitReason,
    SimConfig,
    SimDecision,
    SimResult,
    Skipped,
    SkipReason,
    Trade,
)
from app.domain.compute.base import ComputeResult, compute_record
from app.domain.compute.risk import realized_r
from app.domain.models.market import OHLCVBar

_BPS = Decimal(10_000)
ZERO = Decimal(0)

FxSeries = dict[str, list[tuple[date, Decimal]]]  # currency → [(day, base units per 1 currency unit)]


@dataclass
class _Position:
    d: SimDecision
    side: str
    entry_at: datetime
    entry_price: Decimal
    units: Decimal  # still open
    initial_units: Decimal
    stop: Decimal
    targets: list[Decimal]
    target_count: int
    fees: Decimal = ZERO
    realized: Decimal = ZERO  # base currency, gross of fees
    exit_value: Decimal = ZERO  # Σ units x price over partial exits (instrument currency)
    bars_held: int = 0
    gap: bool = False


@dataclass
class _Pending:
    d: SimDecision
    bars_left: int
    placed_at: datetime  # its placement bar already tried it (inside ``_decide``)


@dataclass
class _Book:
    config: SimConfig
    fx: FxSeries
    cash: Decimal
    positions: dict[str, _Position] = field(default_factory=dict)
    pending: dict[str, _Pending] = field(default_factory=dict)
    last_close: dict[str, Decimal] = field(default_factory=dict)
    trades: list[Trade] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)

    # -- money ------------------------------------------------------------------------------

    def rate(self, currency: str, day: date) -> Decimal:
        if currency == self.config.base_currency:
            return Decimal(1)
        series = self.fx[currency]
        i = bisect.bisect_right(series, day, key=lambda p: p[0])
        return series[max(i - 1, 0)][1]  # before the first quote: the first one

    def fee(self, notional_base: Decimal) -> Decimal:
        c = self.config
        if c.commission_pct == 0 and c.min_commission == 0:
            return ZERO
        return max(abs(notional_base) * c.commission_pct, c.min_commission)

    def slip(self, price: Decimal, buying: bool) -> Decimal:
        s = self.config.slippage_bps / _BPS
        return price * (1 + s) if buying else price * (1 - s)

    def unrealized(self, day: date) -> Decimal:
        total = ZERO
        for sym, p in self.positions.items():
            sign = 1 if p.side == "long" else -1
            px = self.last_close.get(sym, p.entry_price)
            total += (px - p.entry_price) * p.units * sign * self.rate(p.d.currency, day)
        return total

    def margin(self, day: date) -> Decimal:
        return sum(
            (
                self.notional(p, day) / self.config.leverage_for(p.d.asset_type)
                for p in self.positions.values()
            ),
            ZERO,
        )

    def notional(self, p: _Position, day: date) -> Decimal:
        px = self.last_close.get(p.d.symbol, p.entry_price)
        return p.units * px * self.rate(p.d.currency, day)

    def equity(self, day: date) -> Decimal:
        return self.cash + self.unrealized(day)

    # -- bookkeeping ------------------------------------------------------------------------

    def skip(self, d: SimDecision, reason: SkipReason, detail: str = "") -> None:
        self.skipped.append(
            Skipped(symbol=d.symbol, ref=d.ref, decided_on=d.decided_on, reason=reason, detail=detail)
        )

    def close(
        self,
        p: _Position,
        units: Decimal,
        price: Decimal,
        at: datetime,
        reason: ExitReason,
        gap: bool = False,
    ) -> None:
        units = min(units, p.units)
        sign = 1 if p.side == "long" else -1
        rate = self.rate(p.d.currency, at.date())
        gross = (price - p.entry_price) * units * sign * rate
        fee = self.fee(units * price * rate)
        self.cash += gross - fee
        p.fees += fee
        p.realized += gross
        p.exit_value += units * price
        p.units -= units
        p.gap = p.gap or gap
        if p.units > 0:
            return
        exited = p.initial_units
        avg_exit = p.exit_value / exited
        r = realized_r(p.entry_price, p.stop, avg_exit, "long" if p.side == "long" else "short").value
        self.trades.append(
            Trade(
                symbol=p.d.symbol,
                side="long" if p.side == "long" else "short",
                ref=p.d.ref,
                decided_on=p.d.decided_on,
                entry_at=p.entry_at,
                entry_price=p.entry_price,
                exit_at=at,
                exit_price=avg_exit,
                units=exited,
                stop=p.stop,
                exit_reason=reason,
                gap=p.gap,
                fees=p.fees,
                pnl=p.realized - p.fees,
                r_multiple=r,
                bars_held=p.bars_held,
            )
        )
        del self.positions[p.d.symbol]


# -- level helpers (long / short mirrored) ----------------------------------------------------


def _past_stop(side: str, price: Decimal, stop: Decimal) -> bool:
    return price <= stop if side == "long" else price >= stop


def _past_target(side: str, price: Decimal, target: Decimal) -> bool:
    return price >= target if side == "long" else price <= target


def _adverse(side: str, bar: OHLCVBar) -> Decimal:
    return bar.low if side == "long" else bar.high


def _favorable(side: str, bar: OHLCVBar) -> Decimal:
    return bar.high if side == "long" else bar.low


def _lots(units: Decimal, lot: Decimal) -> Decimal:
    if units <= 0:
        return ZERO
    return (units / lot).to_integral_value(rounding=ROUND_DOWN) * lot


# -- the simulation ---------------------------------------------------------------------------


def _open(book: _Book, d: SimDecision, fill: Decimal, bar: OHLCVBar) -> _Position | None:
    """Size and open ``d`` at ``fill``; records a skip and returns None when it cannot."""
    c = book.config
    day = bar.ts.date()
    assert d.stop is not None  # checked by the caller
    side = d.side
    if _past_stop(side, fill, d.stop) or (d.targets and _past_target(side, fill, d.targets[0])):
        book.skip(d, "gap_invalidated", f"fill {fill} is already past the stop or the first target")
        return None
    if len(book.positions) >= c.max_open_positions:
        book.skip(d, "max_positions", f"{c.max_open_positions} positions already open")
        return None
    rate = book.rate(d.currency, day)
    equity = book.equity(day)
    basis = equity if c.compounding else c.initial_capital
    distance = abs(fill - d.stop) * rate
    units = basis * c.risk_pct * d.size_fraction / distance
    if c.max_position_weight is not None:
        units = min(units, basis * c.max_position_weight / (fill * rate))
    units = _lots(units, d.lot_size)
    if units == 0:
        book.skip(d, "below_one_lot", f"risk budget {basis * c.risk_pct * d.size_fraction:.2f} buys < 1 lot")
        return None
    free = equity - book.margin(day)
    affordable = _lots(free * c.leverage_for(d.asset_type) / (fill * rate), d.lot_size) if free > 0 else ZERO
    units = min(units, affordable)
    if units == 0:
        book.skip(d, "no_buying_power", f"free equity {free:.2f}")
        return None
    targets = [t for t in d.targets if _past_target(side, t, fill) and t != fill]
    p = _Position(
        d=d,
        side=side,
        entry_at=bar.ts,
        entry_price=fill,
        units=units,
        initial_units=units,
        stop=d.stop,
        targets=targets,
        target_count=len(targets),
    )
    fee = book.fee(units * fill * rate)
    book.cash -= fee
    p.fees = fee
    book.positions[d.symbol] = p
    return p


def _exit_at_open(book: _Book, p: _Position, bar: OHLCVBar) -> None:
    """The open already past the stop / a target: exit there (the level was never tradable)."""
    if _past_stop(p.side, bar.open, p.stop):
        book.close(p, p.units, book.slip(bar.open, buying=p.side == "short"), bar.ts, "sl", gap=True)
    elif p.targets and _past_target(p.side, bar.open, p.targets[0]):
        _take_targets(book, p, bar, at_open=True)


def _exit_on_bar(book: _Book, p: _Position, bar: OHLCVBar, *, allow_target: bool) -> None:
    """Stop / targets reached inside the bar (after the open)."""
    c = book.config
    side = p.side
    selling = side == "long"
    hit_stop = _past_stop(side, _adverse(side, bar), p.stop)
    hit_target = allow_target and bool(p.targets) and _past_target(side, _favorable(side, bar), p.targets[0])
    if hit_stop and (not hit_target or c.intrabar_rule == "stop_first"):
        book.close(p, p.units, book.slip(p.stop, buying=not selling), bar.ts, "sl")
        return
    if hit_target:
        _take_targets(book, p, bar, at_open=False)
        if p.d.symbol in book.positions and hit_stop:  # target_first: the rest is stopped after
            book.close(p, p.units, book.slip(p.stop, buying=not selling), bar.ts, "sl")


def _take_targets(book: _Book, p: _Position, bar: OHLCVBar, *, at_open: bool) -> None:
    """Close at each target the bar reached; a target exit is a resting limit (no slippage)."""
    side = p.side
    reach = bar.open if at_open else _favorable(side, bar)
    split = book.config.scale_out == "equal_split" and p.target_count > 1
    while p.targets and _past_target(side, reach, p.targets[0]) and p.d.symbol in book.positions:
        target = p.targets.pop(0)
        price = bar.open if at_open else target  # a gap past the target fills at the (better) open
        chunk = _lots(p.initial_units / p.target_count, p.d.lot_size) if split else p.units
        if not p.targets or chunk == 0:
            chunk = p.units
        book.close(p, chunk, price, bar.ts, "tp", gap=at_open)


def _try_fill(book: _Book, pend: _Pending, bar: OHLCVBar) -> None:
    d = pend.d
    assert d.order_price is not None
    price = d.order_price
    buying = d.side == "long"
    touched: bool
    if d.order_type == "limit":  # buy below / sell above
        touched = bar.low <= price if buying else bar.high >= price
        fill = (min(bar.open, price) if buying else max(bar.open, price)) if touched else ZERO
    else:  # stop entry: buy above / sell below (slips like a market order)
        touched = bar.high >= price if buying else bar.low <= price
        fill = (
            book.slip(max(bar.open, price) if buying else min(bar.open, price), buying) if touched else ZERO
        )
    if touched:
        del book.pending[d.symbol]
        p = _open(book, d, fill, bar)
        if p is not None:
            _exit_on_bar(book, p, bar, allow_target=False)
        return
    pend.bars_left -= 1
    if pend.bars_left <= 0:
        del book.pending[d.symbol]
        book.skip(d, "order_expired", f"{d.order_type} {price} not reached")


def _decide(book: _Book, d: SimDecision, bar: OHLCVBar) -> None:
    c = book.config
    if d.side == "flat" or d.size_fraction == 0:  # no new view: an open position keeps its own exits
        book.skip(d, "flat")
        return
    held = book.positions.get(d.symbol)
    if held is not None and held.side != d.side:  # the opposite view closes it, even if it can't flip
        book.close(held, held.units, book.slip(bar.open, buying=held.side == "short"), bar.ts, "reverse")
        held = None
    if d.side == "short" and d.asset_type not in c.shortable:
        book.skip(d, "short_not_allowed", f"{d.asset_type} cannot be sold short in this account")
        return
    ref_price = d.order_price if d.order_type != "market" and d.order_price is not None else bar.open
    if d.stop is None or (d.order_type != "market" and d.order_price is None):
        book.skip(d, "invalid_levels", "no stop" if d.stop is None else "pending order without a price")
        return
    if d.order_type != "market" and _past_stop(d.side, ref_price, d.stop):
        book.skip(d, "invalid_levels", f"stop {d.stop} is not beyond the order price {ref_price}")
        return
    if held is not None:
        book.skip(d, "already_open", f"a {held.side} position is already open")
        return
    old = book.pending.pop(d.symbol, None)
    if old is not None:
        book.skip(old.d, "order_expired", "replaced by a newer decision")
    if d.order_type == "market":
        p = _open(book, d, book.slip(bar.open, buying=d.side == "long"), bar)
        if p is not None:
            _exit_on_bar(book, p, bar, allow_target=True)
        return
    book.pending[d.symbol] = _Pending(
        d=d, bars_left=d.order_valid_bars or c.default_order_valid_bars, placed_at=bar.ts
    )
    _try_fill(book, book.pending[d.symbol], bar)


def simulate(
    decisions: list[SimDecision],
    bars: dict[str, list[OHLCVBar]],
    config: SimConfig,
    *,
    fx: FxSeries | None = None,
    inputs: list[str] | None = None,
) -> ComputeResult[SimResult]:
    """Replay ``decisions`` over ``bars`` (per symbol, any one interval) with ``config``.

    ``fx`` gives, per non-base currency, the base-currency value of one unit by day.
    Raises ``ValueError`` when a decision's currency has no FX series.
    """
    fx = {k: sorted(v) for k, v in (fx or {}).items()}
    missing = {d.currency for d in decisions} - set(fx) - {config.base_currency}
    if missing:
        raise ValueError(f"no FX series to convert {sorted(missing)} into {config.base_currency}")

    book = _Book(config=config, fx=fx, cash=config.initial_capital)
    queues: dict[str, deque[SimDecision]] = {
        s: deque(sorted((d for d in decisions if d.symbol == s), key=lambda d: d.decided_on))
        for s in {d.symbol for d in decisions}
    }
    last_ts = {s: b[-1].ts for s, b in bars.items() if b}
    stream = sorted(((b.ts, s, b) for s, bs in bars.items() for b in bs), key=lambda x: (x[0], x[1]))
    curve: dict[date, EquityPoint] = {}

    for ts, group in groupby(stream, key=lambda x: x[0]):
        day = ts.date()
        for _, sym, bar in group:
            held = book.positions.get(sym)
            if held is not None:
                held.bars_held += 1
                _exit_at_open(book, held, bar)
            queue = queues.get(sym)
            while queue and queue[0].decided_on < day:
                _decide(book, queue.popleft(), bar)  # may reverse `held` at the open
            if held is not None and book.positions.get(sym) is held:
                _exit_on_bar(book, held, bar, allow_target=True)
            pend = book.pending.get(sym)
            if pend is not None and pend.placed_at != ts:
                _try_fill(book, pend, bar)
            book.last_close[sym] = bar.close
            p = book.positions.get(sym)
            if p is not None and p.d.time_stop_bars and p.bars_held >= p.d.time_stop_bars:
                book.close(p, p.units, book.slip(bar.close, buying=p.side == "short"), ts, "time_stop")
            if ts == last_ts.get(sym):
                p = book.positions.get(sym)
                if p is not None:
                    book.close(p, p.units, book.slip(bar.close, buying=p.side == "short"), ts, "end_of_test")
                pend = book.pending.pop(sym, None)
                if pend is not None:
                    book.skip(pend.d, "order_expired", "the test ended before it filled")
        equity = book.equity(day)
        gross = sum((abs(book.notional(p, day)) for p in book.positions.values()), ZERO)
        curve[day] = EquityPoint(
            day=day,
            equity=equity,
            cash=equity - book.margin(day),
            gross_exposure=gross / equity if equity > 0 else ZERO,
            open_positions=len(book.positions),
        )

    for queue in queues.values():
        for d in queue:
            book.skip(d, "no_bars", "no bar after the decision date")

    points = list(curve.values())
    final = points[-1].equity if points else config.initial_capital
    result = SimResult(
        config=config, trades=book.trades, skipped=book.skipped, equity_curve=points, final_equity=final
    )
    record = compute_record(
        f"compute.backtest.simulate@{ENGINE_VERSION}",
        {"final_equity": str(final), "trades": len(book.trades), "skipped": len(book.skipped)},
        params=config.model_dump(mode="json"),
        inputs=inputs,
        unit=config.base_currency,
    )
    return ComputeResult(value=result, records=[record])
