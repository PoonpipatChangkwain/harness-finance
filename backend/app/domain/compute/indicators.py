"""Technical indicators, computed in-house rather than fetched from a vendor.

Per docs/data-sources.md §3 (F1): TradingAgents fetches these from
`stockstats`; Product computes them itself so every value carries
provenance and has a unit test. Indicator set (SMA/EMA/RSI/MACD/Bollinger/
ATR) mirrors ``../TradingAgents/tradingagents/dataflows/vendors/yahoo/snapshot.py``
(``DEFAULT_SNAPSHOT_INDICATORS``) plus VWMA/Stochastic/OBV.

MACD's signal line is an SMA of the MACD line (ChrisMoody's ``CM_MacD_Ult_MTF``;
``signal_ma="ema"`` gives the classic form), with the histogram's four-colour state and the
signal cross; RSI adds the RSI-based MA and the regular divergences of TradingView's RSI
(pivot lookback 5/5, 5-60 bars apart).

RSI and ATR use Wilder smoothing (``alpha = 1/n``, recursive, seeded by a
plain SMA of the first ``n`` values) — the original and still-standard
definition, distinct from a plain EMA.
"""

from __future__ import annotations

from datetime import date
from itertools import pairwise
from typing import Any, Literal

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


RSI_DIVERGENCE_VERSION = "v1"
DIV_LOOKBACK = 5  # pivot lookback left/right (TradingView RSI "Calculate Divergence")
DIV_RANGE = (5, 60)  # bars between two pivots for a divergence to count
DIV_REPORT_MAX_BARS = 20  # the snapshot shows a divergence only while its pivot is this recent


def rsi_ma(rsi_values: list[float | None], n: int = 14) -> list[float | None]:
    """The RSI-based moving average of TradingView's RSI (default: SMA 14 of RSI)."""
    return _to_list(pd.Series(rsi_values, dtype="float64").rolling(window=n, min_periods=n).mean())


def _pivots(values: list[float | None], left: int, right: int, *, low: bool) -> list[int]:
    """Indexes of confirmed pivots: ``values[i]`` is the strict min (max) of the ``left`` bars
    before and ``right`` bars after it (TradingView ``ta.pivotlow`` / ``ta.pivothigh``)."""
    out: list[int] = []
    for i in range(left, len(values) - right):
        v = values[i]
        if v is None:
            continue
        window = [x for x in values[i - left : i + right + 1] if x is not None]
        if len(window) != left + right + 1:
            continue
        others = window[:left] + window[left + 1 :]
        if (low and all(v < o for o in others)) or (not low and all(v > o for o in others)):
            out.append(i)
    return out


def divergence_events(
    rsi_values: list[float | None], highs: list[float], lows: list[float]
) -> list[dict[str, Any]]:
    """Every regular RSI divergence in the series, oldest first: ``kind``, the two RSI pivot
    indexes (``pivots``) and ``known_at`` = the bar on which it is confirmed (the second pivot
    + ``DIV_LOOKBACK``) — a point-in-time reader may only use it from that bar on."""
    out: list[dict[str, Any]] = []
    for kind, low in (("bullish", True), ("bearish", False)):
        price = lows if low else highs
        for prev, cur in pairwise(_pivots(rsi_values, DIV_LOOKBACK, DIV_LOOKBACK, low=low)):
            if not DIV_RANGE[0] <= cur - prev <= DIV_RANGE[1]:
                continue
            rp, rc = rsi_values[prev], rsi_values[cur]
            if rp is None or rc is None:
                continue
            if (price[cur] < price[prev] and rc > rp) if low else (price[cur] > price[prev] and rc < rp):
                out.append({"kind": kind, "pivots": (prev, cur), "known_at": cur + DIV_LOOKBACK})
    out.sort(key=lambda e: e["known_at"])
    return out


def rsi_divergence(
    closes: list[float],
    highs: list[float],
    lows: list[float],
    n: int = 14,
    *,
    inputs: list[str] | None = None,
) -> ComputeResult[dict[str, dict[str, object] | None]]:
    """Regular RSI divergences as in TradingView's RSI indicator (``calculateDivergence``).

    Bullish: price makes a lower low while RSI makes a higher low at two consecutive RSI pivot
    lows 5-60 bars apart; bearish: higher high in price, lower high in RSI at RSI pivot highs.
    A pivot is confirmed ``DIV_LOOKBACK`` bars after it prints, so a signal never repaints.
    Returns the latest signal of each kind with its pivot's distance from the last bar.
    """
    r = rsi(closes, n).value
    out: dict[str, dict[str, object] | None] = {"bullish": None, "bearish": None}
    for ev in divergence_events(r, highs, lows):
        prev, cur = ev["pivots"]
        out[str(ev["kind"])] = {
            "pivot_bars_ago": len(r) - 1 - cur,
            "rsi": [r[prev], r[cur]],
            "price": [(lows if ev["kind"] == "bullish" else highs)[i] for i in (prev, cur)],
        }
    record = compute_record(
        f"compute.indicators.rsi_divergence@{RSI_DIVERGENCE_VERSION}",
        out,
        params={"n": n, "lookback": DIV_LOOKBACK, "range": list(DIV_RANGE)},
        inputs=inputs,
    )
    return ComputeResult(value=out, records=[record])


MacdSignalMa = Literal["sma", "ema"]
MACD_VERSION = "v2"  # v2: signal = SMA(macd) by default (ChrisMoody CM_MacD_Ult_MTF), was EMA


def macd(
    closes: list[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
    *,
    signal_ma: MacdSignalMa = "sma",
    inputs: list[str] | None = None,
) -> ComputeResult[dict[str, list[float | None]]]:
    """MACD 12/26 with a 9-bar signal line.

    ``signal_ma="sma"`` (default) follows ChrisMoody's ``CM_MacD_Ult_MTF`` (``signal =
    sma(macd, 9)``); ``"ema"`` is the classic Appel definition the ML features keep using.
    """
    s = _series(closes)
    ema_fast = s.ewm(span=fast, adjust=False).mean()
    ema_slow = s.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    if signal_ma == "sma":
        signal_line = macd_line.rolling(window=signal, min_periods=signal).mean()
    else:
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    value = {"macd": _to_list(macd_line), "signal": _to_list(signal_line), "hist": _to_list(hist)}
    record = compute_record(
        f"compute.indicators.macd@{MACD_VERSION}",
        {"fast": fast, "slow": slow, "signal": signal, "signal_ma": signal_ma},
        inputs=inputs,
    )
    return ComputeResult(value=value, records=[record])


MacdHistState = Literal["rising_above", "falling_above", "falling_below", "rising_below"]


def macd_hist_state(hist: list[float | None]) -> MacdHistState | None:
    """The histogram's colour in ``CM_MacD_Ult_MTF``: side of zero x direction vs the previous bar.

    aqua = rising above zero (momentum building), blue = falling above zero (fading),
    red = falling below zero, maroon = rising below zero (recovering).
    """
    if len(hist) < 2 or hist[-1] is None or hist[-2] is None:
        return None
    cur, prev = hist[-1], hist[-2]
    if cur > 0:
        return "rising_above" if cur > prev else "falling_above"
    return "rising_below" if cur > prev else "falling_below"


def macd_cross(
    macd_line: list[float | None], signal_line: list[float | None]
) -> Literal["bullish", "bearish"] | None:
    """A MACD/signal cross on the last bar (the dots in ``CM_MacD_Ult_MTF``)."""
    m1, m0, s1, s0 = macd_line[-1:], macd_line[-2:-1], signal_line[-1:], signal_line[-2:-1]
    vals = [*m1, *m0, *s1, *s0]
    if len(vals) < 4 or any(v is None for v in vals):
        return None
    now = vals[0] >= vals[2]  # type: ignore[operator]
    before = vals[1] >= vals[3]  # type: ignore[operator]
    if now == before:
        return None
    return "bullish" if now else "bearish"


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
    rsi_series = rsi(closes, 14).value
    rsi14 = rsi_series[-1]
    rsi_ma14 = rsi_ma(rsi_series, 14)[-1]
    divergence = rsi_divergence(closes, highs, lows, 14).value
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
            "rsi_ma14": rsi_ma14,
            "macd": macd_v["macd"][-1],
            "macd_signal": macd_v["signal"][-1],
            "macd_hist": macd_v["hist"][-1],
            "bollinger_upper": boll["upper"][-1],
            "bollinger_mid": boll["mid"][-1],
            "bollinger_lower": boll["lower"][-1],
            "atr14": atr14,
        },
        # TradingView RSI divergences + CM_MacD_Ult_MTF histogram colour / cross, as labels
        "signals": {
            "macd_hist_state": macd_hist_state(macd_v["hist"]),
            "macd_cross": macd_cross(macd_v["macd"], macd_v["signal"]),
            # only a recent divergence is a signal; an old one (e.g. 200 bars back) misled the desk
            "rsi_divergence": {
                k: (
                    v["pivot_bars_ago"]
                    if isinstance(v, dict) and int(str(v["pivot_bars_ago"])) <= DIV_REPORT_MAX_BARS
                    else None
                )
                for k, v in divergence.items()
            },
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
