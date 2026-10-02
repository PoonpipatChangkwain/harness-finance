"""Return series statistics: simple/log returns, volatility, drawdown, beta, VaR.

Statistics use ``float`` (money uses ``Decimal`` elsewhere); every public
function returns a ``ComputeResult`` with provenance (F1, contracts.md §2).
"""

from __future__ import annotations

import math
from decimal import Decimal

from app.domain.compute.base import ComputeResult, compute_record

_VERSION = "v1"


def _to_floats(closes: list[Decimal] | list[float]) -> list[float]:
    return [float(c) for c in closes]


def simple_returns(
    closes: list[Decimal] | list[float], *, inputs: list[str] | None = None
) -> ComputeResult[list[float]]:
    xs = _to_floats(closes)
    rets = [(xs[i] / xs[i - 1] - 1.0) for i in range(1, len(xs))] if len(xs) > 1 else []
    record = compute_record(f"compute.returns.simple_returns@{_VERSION}", rets, inputs=inputs)
    return ComputeResult(value=rets, records=[record])


def log_returns(
    closes: list[Decimal] | list[float], *, inputs: list[str] | None = None
) -> ComputeResult[list[float]]:
    xs = _to_floats(closes)
    rets = [math.log(xs[i] / xs[i - 1]) for i in range(1, len(xs))] if len(xs) > 1 else []
    record = compute_record(f"compute.returns.log_returns@{_VERSION}", rets, inputs=inputs)
    return ComputeResult(value=rets, records=[record])


def cumulative_return(
    closes: list[Decimal] | list[float], *, inputs: list[str] | None = None
) -> ComputeResult[float]:
    xs = _to_floats(closes)
    value = (xs[-1] / xs[0] - 1.0) if len(xs) >= 2 and xs[0] != 0 else 0.0
    record = compute_record(f"compute.returns.cumulative_return@{_VERSION}", value, inputs=inputs)
    return ComputeResult(value=value, records=[record])


def annualized_volatility(
    returns: list[float], periods_per_year: int = 252, *, inputs: list[str] | None = None
) -> ComputeResult[float]:
    n = len(returns)
    if n < 2:
        value = 0.0
    else:
        mean = sum(returns) / n
        var = sum((r - mean) ** 2 for r in returns) / (n - 1)
        value = math.sqrt(var) * math.sqrt(periods_per_year)
    record = compute_record(
        f"compute.returns.annualized_volatility@{_VERSION}",
        value,
        params={"periods_per_year": periods_per_year},
        inputs=inputs,
    )
    return ComputeResult(value=value, records=[record])


def max_drawdown(
    closes: list[Decimal] | list[float], dates: list[object] | None = None, *, inputs: list[str] | None = None
) -> ComputeResult[dict[str, object]]:
    xs = _to_floats(closes)
    if not xs:
        value: dict[str, object] = {"mdd": 0.0, "peak_index": None, "trough_index": None}
        return ComputeResult(
            value=value,
            records=[compute_record(f"compute.returns.max_drawdown@{_VERSION}", value, inputs=inputs)],
        )
    peak = xs[0]
    peak_idx = 0
    mdd = 0.0
    mdd_peak_idx = 0
    mdd_trough_idx = 0
    for i, x in enumerate(xs):
        if x > peak:
            peak = x
            peak_idx = i
        drawdown = (x / peak - 1.0) if peak != 0 else 0.0
        if drawdown < mdd:
            mdd = drawdown
            mdd_peak_idx = peak_idx
            mdd_trough_idx = i
    peak_date = dates[mdd_peak_idx] if dates else None
    trough_date = dates[mdd_trough_idx] if dates else None
    value = {
        "mdd": mdd,
        "peak_index": mdd_peak_idx,
        "trough_index": mdd_trough_idx,
        "peak_date": peak_date,
        "trough_date": trough_date,
    }
    record = compute_record(f"compute.returns.max_drawdown@{_VERSION}", value, inputs=inputs)
    return ComputeResult(value=value, records=[record])


def beta(
    asset_ret: list[float], bench_ret: list[float], *, inputs: list[str] | None = None
) -> ComputeResult[float | None]:
    n = min(len(asset_ret), len(bench_ret))
    if n < 2:
        return ComputeResult(
            value=None, records=[compute_record(f"compute.returns.beta@{_VERSION}", None, inputs=inputs)]
        )
    a = asset_ret[-n:]
    b = bench_ret[-n:]
    mean_a = sum(a) / n
    mean_b = sum(b) / n
    cov = sum((a[i] - mean_a) * (b[i] - mean_b) for i in range(n)) / (n - 1)
    var_b = sum((x - mean_b) ** 2 for x in b) / (n - 1)
    value = (cov / var_b) if var_b != 0 else None
    record = compute_record(f"compute.returns.beta@{_VERSION}", value, inputs=inputs)
    return ComputeResult(value=value, records=[record])


def correlation_matrix(
    returns_by_symbol: dict[str, list[float]], *, inputs: list[str] | None = None
) -> ComputeResult[dict[str, dict[str, float]]]:
    symbols = list(returns_by_symbol)
    matrix: dict[str, dict[str, float]] = {s: {} for s in symbols}
    for i, s1 in enumerate(symbols):
        for s2 in symbols[i:]:
            r1, r2 = returns_by_symbol[s1], returns_by_symbol[s2]
            n = min(len(r1), len(r2))
            if n < 2:
                corr = 0.0
            else:
                a, b = r1[-n:], r2[-n:]
                mean_a, mean_b = sum(a) / n, sum(b) / n
                cov = sum((a[k] - mean_a) * (b[k] - mean_b) for k in range(n))
                var_a = sum((x - mean_a) ** 2 for x in a)
                var_b = sum((x - mean_b) ** 2 for x in b)
                denom = math.sqrt(var_a * var_b)
                corr = (cov / denom) if denom != 0 else 0.0
            matrix[s1][s2] = corr
            matrix[s2][s1] = corr
    record = compute_record(f"compute.returns.correlation_matrix@{_VERSION}", matrix, inputs=inputs)
    return ComputeResult(value=matrix, records=[record])


def sharpe(
    returns: list[float], rf_annual: float, periods_per_year: int = 252, *, inputs: list[str] | None = None
) -> ComputeResult[float]:
    n = len(returns)
    if n < 2:
        value = 0.0
    else:
        mean = sum(returns) / n
        std = math.sqrt(sum((r - mean) ** 2 for r in returns) / (n - 1))
        rf_period = rf_annual / periods_per_year
        value = ((mean - rf_period) / std) * math.sqrt(periods_per_year) if std != 0 else 0.0
    record = compute_record(
        f"compute.returns.sharpe@{_VERSION}", value, params={"rf_annual": rf_annual}, inputs=inputs
    )
    return ComputeResult(value=value, records=[record])


def sortino(
    returns: list[float], rf_annual: float, periods_per_year: int = 252, *, inputs: list[str] | None = None
) -> ComputeResult[float]:
    n = len(returns)
    rf_period = rf_annual / periods_per_year
    downside = [min(0.0, r - rf_period) for r in returns]
    if n < 2:
        value = 0.0
    else:
        mean = sum(returns) / n
        downside_dev = math.sqrt(sum(d * d for d in downside) / n)
        value = (
            ((mean - rf_period) / downside_dev) * math.sqrt(periods_per_year) if downside_dev != 0 else 0.0
        )
    record = compute_record(
        f"compute.returns.sortino@{_VERSION}", value, params={"rf_annual": rf_annual}, inputs=inputs
    )
    return ComputeResult(value=value, records=[record])


def historical_var(
    returns: list[float], level: float = 0.95, *, inputs: list[str] | None = None
) -> ComputeResult[float]:
    if not returns:
        value = 0.0
    else:
        sorted_r = sorted(returns)
        idx = int((1 - level) * len(sorted_r))
        idx = min(max(idx, 0), len(sorted_r) - 1)
        value = -sorted_r[idx]
    record = compute_record(
        f"compute.returns.historical_var@{_VERSION}", value, params={"level": level}, inputs=inputs
    )
    return ComputeResult(value=value, records=[record])


def cvar(
    returns: list[float], level: float = 0.95, *, inputs: list[str] | None = None
) -> ComputeResult[float]:
    if not returns:
        value = 0.0
    else:
        sorted_r = sorted(returns)
        idx = int((1 - level) * len(sorted_r))
        idx = min(max(idx, 0), len(sorted_r) - 1)
        tail = sorted_r[: idx + 1]
        value = -(sum(tail) / len(tail)) if tail else 0.0
    record = compute_record(f"compute.returns.cvar@{_VERSION}", value, params={"level": level}, inputs=inputs)
    return ComputeResult(value=value, records=[record])


def risk_contribution(
    returns_by_symbol: dict[str, list[float]],
    weights_map: dict[str, float],
    *,
    inputs: list[str] | None = None,
) -> ComputeResult[dict[str, float]]:
    """Each position's share of portfolio variance: ``w_i (Σw)_i / w'Σw`` (sums to 1).

    Uses the sample covariance of the aligned (last ``n``) returns. Symbols
    without a weight or returns are left out; zero portfolio variance gives ``{}``.
    """
    symbols = [s for s in returns_by_symbol if weights_map.get(s)]
    value: dict[str, float] = {}
    if symbols:
        n = min(len(returns_by_symbol[s]) for s in symbols)
        if n >= 2:
            series = {s: returns_by_symbol[s][-n:] for s in symbols}
            means = {s: sum(series[s]) / n for s in symbols}

            def cov(a: str, b: str) -> float:
                return sum((series[a][k] - means[a]) * (series[b][k] - means[b]) for k in range(n)) / (n - 1)

            sigma_w = {a: sum(cov(a, b) * weights_map[b] for b in symbols) for a in symbols}
            variance = sum(weights_map[a] * sigma_w[a] for a in symbols)
            if variance > 0:
                value = {a: weights_map[a] * sigma_w[a] / variance for a in symbols}
    record = compute_record(f"compute.returns.risk_contribution@{_VERSION}", value, inputs=inputs)
    return ComputeResult(value=value, records=[record])


def portfolio_returns(
    returns_by_symbol: dict[str, list[float]],
    weights_map: dict[str, float],
    *,
    inputs: list[str] | None = None,
) -> ComputeResult[list[float]]:
    symbols = [s for s in returns_by_symbol if s in weights_map]
    if not symbols:
        record = compute_record(f"compute.returns.portfolio_returns@{_VERSION}", [], inputs=inputs)
        return ComputeResult(value=[], records=[record])
    n = min(len(returns_by_symbol[s]) for s in symbols)
    combined = [sum(weights_map[s] * returns_by_symbol[s][-n:][i] for s in symbols) for i in range(n)]
    record = compute_record(f"compute.returns.portfolio_returns@{_VERSION}", combined, inputs=inputs)
    return ComputeResult(value=combined, records=[record])
