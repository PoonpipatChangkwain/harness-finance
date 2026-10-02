"""Technical indicators, computed in-house rather than fetched from a vendor.

Per docs/data-sources.md §3 (F1): TradingAgents fetches these from
`stockstats`; Product computes them itself so every value carries
provenance and has a unit test. Indicator set (SMA/EMA/RSI/MACD/Bollinger/
ATR) mirrors ``../TradingAgents/tradingagents/dataflows/vendors/yahoo/snapshot.py``
(``DEFAULT_SNAPSHOT_INDICATORS``) plus VWMA/Stochastic/OBV.

RSI and ATR use Wilder smoothing (``alpha = 1/n``, recursive, seeded by a
plain SMA of the first ``n`` values) — the original and still-standard
definition, distinct from a plain EMA.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from app.domain.compute.base import ComputeResult, compute_record
from app.domain.models.market import OHLCVFrame

_VERSION = "v1"


def _series(closes: list[float]) -> pd.Series:
    return pd.Series(closes, dtype="float64")


def _to_list(s: pd.Series) -> list[float | None]:
    return [None if pd.isna(x) else float(x) for x in s]


def sma(
    closes: list[float], n: int = 20, *, inputs: list[str] | None = None
) -> ComputeResult[list[float | None]]:
    value = _to_list(_series(closes).rolling(window=n, min_periods=n).mean())
    record = compute_record(f"compute.indicators.sma@{_VERSION}", {"n": n}, params={"n": n}, inputs=inputs)
    return ComputeResult(value=value, records=[record])


def ema(
    closes: list[float], n: int = 20, *, inputs: list[str] | None = None
) -> ComputeResult[list[float | None]]:
    value = _to_list(_series(closes).ewm(span=n, adjust=False, min_periods=n).mean())
    record = compute_record(f"compute.indicators.ema@{_VERSION}", {"n": n}, params={"n": n}, inputs=inputs)
    return ComputeResult(value=value, records=[record])


def _wilder(s: pd.Series, n: int) -> pd.Series:
    """Wilder smoothing: SMA-seeded recursive average with alpha = 1/n."""
    seed = s.rolling(window=n, min_periods=n).mean()
    out = seed.copy()
    first_idx = seed.first_valid_index()
    if first_idx is None:
        return out
    pos = int(s.index.get_loc(first_idx))  # type: ignore[arg-type]
    prev = float(seed.iloc[pos])
    for i in range(pos + 1, len(s)):
        prev = (prev * (n - 1) + float(s.iloc[i])) / n
        out.iloc[i] = prev
    return out


def rsi(
    closes: list[float], n: int = 14, *, inputs: list[str] | None = None
) -> ComputeResult[list[float | None]]:
    s = _series(closes)
    delta = s.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = _wilder(gain, n)
    avg_loss = _wilder(loss, n)
    rs = avg_gain / avg_loss.replace(0.0, pd.NA)
    value_series = 100 - (100 / (1 + rs))
    value_series = value_series.where(avg_loss != 0, 100.0)
    value = _to_list(value_series)
    record = compute_record(f"compute.indicators.rsi@{_VERSION}", {"n": n}, params={"n": n}, inputs=inputs)
    return ComputeResult(value=value, records=[record])


def macd(
    closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9, *, inputs: list[str] | None = None
) -> ComputeResult[dict[str, list[float | None]]]:
    s = _series(closes)
    ema_fast = s.ewm(span=fast, adjust=False).mean()
    ema_slow = s.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    value = {"macd": _to_list(macd_line), "signal": _to_list(signal_line), "hist": _to_list(hist)}
    record = compute_record(
        f"compute.indicators.macd@{_VERSION}", {"fast": fast, "slow": slow, "signal": signal}, inputs=inputs
    )
    return ComputeResult(value=value, records=[record])


def bollinger(
    closes: list[float], n: int = 20, k: float = 2.0, *, inputs: list[str] | None = None
) -> ComputeResult[dict[str, list[float | None]]]:
    s = _series(closes)
    mid = s.rolling(window=n, min_periods=n).mean()
    std = s.rolling(window=n, min_periods=n).std(ddof=0)
    upper = mid + k * std
    lower = mid - k * std
    value = {"mid": _to_list(mid), "upper": _to_list(upper), "lower": _to_list(lower)}
    record = compute_record(f"compute.indicators.bollinger@{_VERSION}", {"n": n, "k": k}, inputs=inputs)
    return ComputeResult(value=value, records=[record])


def atr(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    n: int = 14,
    *,
    inputs: list[str] | None = None,
) -> ComputeResult[list[float | None]]:
    h, low_s, c = _series(highs), _series(lows), _series(closes)
    prev_close = c.shift(1)
    tr = pd.concat([(h - low_s), (h - prev_close).abs(), (low_s - prev_close).abs()], axis=1).max(axis=1)
    value = _to_list(_wilder(tr, n))
    record = compute_record(f"compute.indicators.atr@{_VERSION}", {"n": n}, params={"n": n}, inputs=inputs)
    return ComputeResult(value=value, records=[record])


def vwma(
    closes: list[float], volumes: list[float], n: int = 20, *, inputs: list[str] | None = None
) -> ComputeResult[list[float | None]]:
    c, v = _series(closes), _series(volumes)
    num = (c * v).rolling(window=n, min_periods=n).sum()
    den = v.rolling(window=n, min_periods=n).sum()
    value = _to_list(num / den.replace(0.0, pd.NA))
    record = compute_record(f"compute.indicators.vwma@{_VERSION}", {"n": n}, params={"n": n}, inputs=inputs)
    return ComputeResult(value=value, records=[record])


def stoch(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    n: int = 14,
    d: int = 3,
    *,
    inputs: list[str] | None = None,
) -> ComputeResult[dict[str, list[float | None]]]:
    h, low_s, c = _series(highs), _series(lows), _series(closes)
    lowest = low_s.rolling(window=n, min_periods=n).min()
    highest = h.rolling(window=n, min_periods=n).max()
    denom = (highest - lowest).replace(0.0, pd.NA)
    k = 100 * (c - lowest) / denom
    d_line = k.rolling(window=d, min_periods=d).mean()
    value = {"k": _to_list(k), "d": _to_list(d_line)}
    record = compute_record(f"compute.indicators.stoch@{_VERSION}", {"n": n, "d": d}, inputs=inputs)
    return ComputeResult(value=value, records=[record])


def obv(
    closes: list[float], volumes: list[float], *, inputs: list[str] | None = None
) -> ComputeResult[list[float]]:
    c, v = _series(closes), _series(volumes)
    direction = c.diff().apply(lambda x: 1.0 if x > 0 else (-1.0 if x < 0 else 0.0))
    direction.iloc[0] = 0.0
    value = (direction * v).cumsum()
    result = [float(x) for x in value]
    record = compute_record(f"compute.indicators.obv@{_VERSION}", None, inputs=inputs)
    return ComputeResult(value=result, records=[record])


_SNAPSHOT_INDICATORS = ("sma20", "sma50", "sma200", "rsi14", "macd", "bollinger", "atr14")


def verified_snapshot(
    frame: OHLCVFrame, as_of: date, *, inputs: list[str] | None = None
) -> ComputeResult[dict[str, object]]:
    """Ground-truth snapshot the analyst is told to treat as source of truth.

    Ported concept from ``build_verified_market_snapshot``: latest row on or
    before ``as_of``, a fixed indicator set, recent closes, %% change over a
    few horizons, and the 52-week high/low.
    """
    cut = frame.as_of(as_of)
    if not cut.bars:
        raise ValueError(f"no bars on or before {as_of} for {frame.symbol}")

    closes = [float(b.close) for b in cut.bars]
    highs = [float(b.high) for b in cut.bars]
    lows = [float(b.low) for b in cut.bars]
    volumes = [float(b.volume) for b in cut.bars]
    latest = cut.bars[-1]

    sma20 = sma(closes, 20).value[-1]
    sma50 = sma(closes, 50).value[-1]
    sma200 = sma(closes, 200).value[-1]
    rsi14 = rsi(closes, 14).value[-1]
    macd_v = macd(closes).value
    boll = bollinger(closes).value
    atr14 = atr(highs, lows, closes, 14).value[-1]

    def pct_change(periods: int) -> float | None:
        if len(closes) <= periods:
            return None
        return closes[-1] / closes[-1 - periods] - 1.0

    window_365 = cut.bars[-252:] if len(cut.bars) > 252 else cut.bars
    high_52w = max(float(b.high) for b in window_365)
    low_52w = min(float(b.low) for b in window_365)

    value = {
        "symbol": frame.symbol,
        "as_of": as_of.isoformat(),
        "latest_bar": {
            "ts": latest.ts.isoformat(),
            "open": float(latest.open),
            "high": float(latest.high),
            "low": float(latest.low),
            "close": float(latest.close),
            "volume": latest.volume,
        },
        "indicators": {
            "sma20": sma20,
            "sma50": sma50,
            "sma200": sma200,
            "rsi14": rsi14,
            "macd": macd_v["macd"][-1],
            "macd_signal": macd_v["signal"][-1],
            "macd_hist": macd_v["hist"][-1],
            "bollinger_upper": boll["upper"][-1],
            "bollinger_mid": boll["mid"][-1],
            "bollinger_lower": boll["lower"][-1],
            "atr14": atr14,
        },
        "recent_closes": [{"ts": b.ts.isoformat(), "close": float(b.close)} for b in cut.bars[-10:]],
        "change_pct": {"1d": pct_change(1), "5d": pct_change(5), "1m": pct_change(21), "3m": pct_change(63)},
        "high_52w": high_52w,
        "low_52w": low_52w,
        "volume_latest": volumes[-1],
    }
    record = compute_record(
        f"compute.indicators.verified_snapshot@{_VERSION}", {"symbol": frame.symbol}, inputs=inputs
    )
    return ComputeResult(value=value, records=[record])
