"""Performance of a simulated portfolio and its buy-and-hold benchmark (Phase 15, layer B).

Money stays ``Decimal``; ratios are ``float`` like ``compute/returns.py`` (whose
drawdown/Sharpe/Sortino are reused, so the portfolio page and the backtest agree).
"""

from __future__ import annotations

import bisect
from collections import Counter
from datetime import date
from decimal import Decimal
from itertools import pairwise
from typing import Any

from app.domain.compute import returns as rt
from app.domain.compute.backtest.models import ENGINE_VERSION, SimConfig, SimResult
from app.domain.compute.base import ComputeResult, compute_record
from app.domain.models.market import OHLCVBar

_CENT = Decimal("0.01")
FxSeries = dict[str, list[tuple[date, Decimal]]]


def _money(x: Decimal) -> Decimal:
    return x.quantize(_CENT)


def _years(first: date, last: date) -> float:
    return (last - first).days / 365.25


def _cagr(start: Decimal, end: Decimal, years: float) -> float | None:
    if years <= 0 or start <= 0 or end <= 0:
        return None
    growth: float = float(end / start) ** (1 / years)
    return growth - 1


def _daily_returns(values: list[Decimal]) -> list[float]:
    """Simple returns; a step from a non-positive equity has no meaningful return and is skipped."""
    return [float(b / a) - 1 for a, b in pairwise(values) if a > 0]


def performance(
    result: SimResult,
    *,
    benchmark: list[tuple[date, Decimal]] | None = None,
    inputs: list[str] | None = None,
) -> ComputeResult[dict[str, Any]]:
    """Headline numbers of a simulation (and alpha against ``benchmark`` when given)."""
    cfg = result.config
    curve = result.equity_curve
    equity = [p.equity for p in curve]
    days = [p.day for p in curve]
    rets = _daily_returns(equity)
    trades = result.trades
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    gross_win = sum((t.pnl for t in wins), Decimal(0))
    gross_loss = -sum((t.pnl for t in losses), Decimal(0))
    rs = [float(t.r_multiple) for t in trades if t.r_multiple is not None]
    total_return = float(result.final_equity / cfg.initial_capital) - 1
    years = _years(days[0], days[-1]) if days else 0.0

    value: dict[str, Any] = {
        "initial_capital": _money(cfg.initial_capital),
        "final_equity": _money(result.final_equity),
        "profit": _money(result.final_equity - cfg.initial_capital),
        "currency": cfg.base_currency,
        "total_return": total_return,
        "cagr": _cagr(cfg.initial_capital, result.final_equity, years),
        "max_drawdown": rt.max_drawdown(equity, list(days)).value["mdd"] if equity else 0.0,
        "sharpe": rt.sharpe(rets, 0.0).value,
        "sortino": rt.sortino(rets, 0.0).value,
        "exposure": (sum(1 for p in curve if p.open_positions) / len(curve)) if curve else 0.0,
        "first_day": days[0].isoformat() if days else None,
        "last_day": days[-1].isoformat() if days else None,
        "trades": len(trades),
        "win_rate": len(wins) / len(trades) if trades else None,
        "avg_win": _money(gross_win / len(wins)) if wins else None,
        "avg_loss": _money(-gross_loss / len(losses)) if losses else None,
        "profit_factor": float(gross_win / gross_loss) if gross_loss > 0 else None,
        "expectancy_r": sum(rs) / len(rs) if rs else None,
        "avg_bars_held": sum(t.bars_held for t in trades) / len(trades) if trades else None,
        "fees": _money(sum((t.fees for t in trades), Decimal(0))),
        "exits": dict(Counter(t.exit_reason for t in trades)),
        "skipped": dict(Counter(s.reason for s in result.skipped)),
    }
    if benchmark:
        bench_return = float(benchmark[-1][1] / benchmark[0][1]) - 1 if benchmark[0][1] > 0 else None
        value["benchmark_return"] = bench_return
        value["alpha"] = total_return - bench_return if bench_return is not None else None
    record = compute_record(
        f"compute.backtest.performance@{ENGINE_VERSION}",
        {k: (str(v) if isinstance(v, Decimal) else v) for k, v in value.items()},
        inputs=inputs,
        unit=cfg.base_currency,
    )
    return ComputeResult(value=value, records=[record])


def buy_and_hold(
    bars: dict[str, list[OHLCVBar]],
    currencies: dict[str, str],
    config: SimConfig,
    *,
    first_day: date | None = None,
    fx: FxSeries | None = None,
    inputs: list[str] | None = None,
) -> ComputeResult[list[tuple[date, Decimal]]]:
    """Equal-weight buy at each symbol's first open (on/after ``first_day``), held to the end.

    Fractional units (it measures the market, not lot rounding); commission on the way in.
    """
    fx = {k: sorted(v) for k, v in (fx or {}).items()}

    def rate(ccy: str, day: date) -> Decimal:
        if ccy == config.base_currency:
            return Decimal(1)
        series = fx[ccy]
        i = bisect.bisect_right(series, day, key=lambda p: p[0])
        return series[max(i - 1, 0)][1]

    series = {s: [b for b in bs if first_day is None or b.ts.date() >= first_day] for s, bs in bars.items()}
    series = {s: bs for s, bs in series.items() if bs}
    if not series:
        return ComputeResult(value=[], records=[])
    alloc = config.initial_capital / len(series)
    units: dict[str, Decimal] = {}
    for s, bs in series.items():
        r = rate(currencies[s], bs[0].ts.date())
        fee = (
            max(alloc * config.commission_pct, config.min_commission) if config.commission_pct else Decimal(0)
        )
        units[s] = (alloc - fee) / (bs[0].open * r)
    by_day: dict[date, dict[str, Decimal]] = {}
    for s, bs in series.items():
        for b in bs:
            by_day.setdefault(b.ts.date(), {})[s] = b.close
    last: dict[str, Decimal] = {s: bs[0].open for s, bs in series.items()}
    curve: list[tuple[date, Decimal]] = []
    for day in sorted(by_day):
        last.update(by_day[day])
        value = sum((units[s] * last[s] * rate(currencies[s], day) for s in series), Decimal(0))
        curve.append((day, value))
    record = compute_record(
        f"compute.backtest.buy_and_hold@{ENGINE_VERSION}",
        {"symbols": sorted(series), "final": str(curve[-1][1])},
        inputs=inputs,
        unit=config.base_currency,
    )
    return ComputeResult(value=curve, records=[record])
