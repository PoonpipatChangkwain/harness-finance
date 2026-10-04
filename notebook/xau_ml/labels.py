"""Triple-barrier labels that replay the harness simulator's rules bar by bar.

Decision at the close of bar ``t`` → market entry at ``open[t+1]`` (the entry bar,
``bars_held = 0``). R = ``sl_atr x ATR14[t]``. Per bar, in ``engine.py`` order:

1. bars after the entry bar: an open already past the stop → stop-out at that open (worse
   than −1R); an open already past the target → target at that open;
2. the bar's low/high reaching the stop or the target — both in one bar = the stop
   (``intrabar_rule="stop_first"``);
3. after ``time_stop`` more bars (``bars_held >= time_stop``) → exit at that bar's close,
   label 0.

Outputs per decision row: ``y`` (1 = target first), ``r`` (realized R, gross of costs),
``exit_off`` (bars after the entry bar until the exit). Rows whose window runs past the
data end get NaN — never a guessed label.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from numba import njit

from xau_ml.config import Horizon, LabelSet


@njit(cache=True)
def _barrier(o, h, lo, c, risk, tp_r, n_hold, side):  # pragma: no cover - numba
    n = len(o)
    y = np.full(n, np.nan)
    r = np.full(n, np.nan)
    off = np.full(n, -1, dtype=np.int64)
    for t in range(n):
        e = t + 1
        last = e + n_hold
        if last >= n or not (risk[t] > 0) or np.isnan(tp_r[t]):
            continue
        entry = o[e]
        R = risk[t]
        if side == 1:
            sl = entry - R
            tp = entry + tp_r[t] * R
        else:
            sl = entry + R
            tp = entry - tp_r[t] * R
        done = False
        for j in range(e, last + 1):
            if j > e:  # 1. gap at the open
                if (side == 1 and o[j] <= sl) or (side == -1 and o[j] >= sl):
                    y[t], r[t], off[t] = 0.0, side * (o[j] - entry) / R, j - e
                    done = True
                    break
                if (side == 1 and o[j] >= tp) or (side == -1 and o[j] <= tp):
                    y[t], r[t], off[t] = 1.0, side * (o[j] - entry) / R, j - e
                    done = True
                    break
            hit_sl = lo[j] <= sl if side == 1 else h[j] >= sl  # 2. inside the bar
            hit_tp = h[j] >= tp if side == 1 else lo[j] <= tp
            if hit_sl:
                y[t], r[t], off[t] = 0.0, -1.0, j - e
                done = True
                break
            if hit_tp:
                y[t], r[t], off[t] = 1.0, tp_r[t], j - e
                done = True
                break
        if not done:  # 3. time stop at the close
            y[t], r[t], off[t] = 0.0, side * (c[last] - entry) / R, n_hold
    return y, r, off


def tp_multiple(df: pd.DataFrame, ls: LabelSet, side: int) -> np.ndarray:
    """TP in R per row: ``ct_tp_r`` when the trade fades the daily trend."""
    tp = np.full(len(df), ls.tp_r)
    if ls.ct_tp_r is not None:
        trend = df["d1_trend"].fillna(0).to_numpy()
        tp[trend == -side] = ls.ct_tp_r
    return tp


HOLE = pd.Timedelta(days=4)  # longer than any weekend/holiday break: missing data, not a closed market
LOOKBACK = 200  # longest base-feature window (SMA200)


def data_holes(df: pd.DataFrame, n_hold: int) -> tuple[np.ndarray, np.ndarray]:
    """``crosses[t]`` — the trade window of a decision at ``t`` (entry bar + ``n_hold``) spans a
    hole, so its outcome would be priced weeks later; ``post[t]`` — ``t`` sits within
    ``LOOKBACK`` bars after a hole, so its feature windows straddle missing data."""
    gap = (df["date"].diff() > HOLE).to_numpy()
    n = len(df)
    crosses, post = np.zeros(n, bool), np.zeros(n, bool)
    for k in np.flatnonzero(gap):  # hole between rows k-1 and k
        crosses[max(0, k - 1 - n_hold) : k] = True  # t < k <= t + 1 + n_hold
        post[k : k + LOOKBACK] = True
    return crosses, post


def add_labels(df: pd.DataFrame, hz: Horizon, ls: LabelSet) -> pd.DataFrame:
    """Adds ``y_{bull,bear}``, ``r_{bull,bear}``, ``exit_{bull,bear}``, ``ct_{bull,bear}``,
    ``tp_*``, ``risk`` and ``no_entry`` (data-hole guard, see ``data_holes``)."""
    out = df.copy()
    crosses, post = data_holes(out, hz.time_stop)
    o, h, lo, c = (out[k].to_numpy("float64") for k in ("open", "high", "low", "close"))
    risk = hz.sl_atr * out["atr14"].to_numpy("float64")
    for name, side in (("bull", 1), ("bear", -1)):
        tp = tp_multiple(out, ls, side)
        y, r, off = _barrier(o, h, lo, c, risk, tp, hz.time_stop, side)
        y[crosses], r[crosses], off[crosses] = np.nan, np.nan, -1
        out[f"y_{name}"] = y
        out[f"r_{name}"] = r
        out[f"exit_{name}"] = off
        out[f"ct_{name}"] = (out["d1_trend"].fillna(0).to_numpy() == -side).astype("int8")
        out[f"tp_{name}"] = tp
    out["risk"] = risk
    out["no_entry"] = (crosses | post).astype("float64")
    return out


def reference_label(df: pd.DataFrame, t: int, hz: Horizon, tp_r: float, side: int) -> tuple[float, float, int]:
    """Slow, obviously-correct re-implementation for spot checks of ``_barrier``."""
    e = t + 1
    entry = df["open"].iat[e]
    R = hz.sl_atr * df["atr14"].iat[t]
    sl, tp = (entry - R, entry + tp_r * R) if side == 1 else (entry + R, entry - tp_r * R)
    for j in range(e, e + hz.time_stop + 1):
        o, hi, low = df["open"].iat[j], df["high"].iat[j], df["low"].iat[j]
        if j > e:
            if side * (o - sl) <= 0:
                return 0.0, side * (o - entry) / R, j - e
            if side * (o - tp) >= 0:
                return 1.0, side * (o - entry) / R, j - e
        adverse = low if side == 1 else hi
        favorable = hi if side == 1 else low
        if side * (adverse - sl) <= 0:
            return 0.0, -1.0, j - e
        if side * (favorable - tp) >= 0:
            return 1.0, tp_r, j - e
    last = e + hz.time_stop
    return 0.0, side * (df["close"].iat[last] - entry) / R, hz.time_stop
