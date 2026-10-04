"""Model inputs of the XAUUSD bull/bear models, computed from bars (F1: code, not the LLM).

A line-for-line port of ``notebook/xau_ml/features.py`` (the code the models were trained
with): Wilder smoothing seeded by an SMA, ATR from true range, RSI, MACD 12/26/9
(``adjust=False``), Bollinger 20/2 on population std, and the daily context merged as the
last day CLOSED at the decision bar's close. ``tests/domain/test_ml_features.py`` pins it
to values the notebook produced, so a drift here fails a test instead of silently feeding
the model different numbers.

Clock: the models were trained on broker-server time (New York + 7h — the 17:00 NY daily
break is 00:00 server, so a server day is one forex day). ``hour``/``dow`` and the daily
close times are read on that clock.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import numpy as np
import pandas as pd

from app.domain.models.market import OHLCVBar

Arr = np.ndarray
SERVER_OFFSET = pd.Timedelta(hours=7)
DAY = pd.Timedelta(days=1)


# -- primitives (same formulas as the notebook) ----------------------------------------------


def wilder(x: Arr, n: int) -> Arr:
    x = np.asarray(x, dtype="float64")
    out = np.full(len(x), np.nan)
    run = 0
    start = -1
    for i, v in enumerate(x):
        run = run + 1 if not np.isnan(v) else 0
        if run >= n:
            start = i
            break
    if start < 0:
        return out
    prev = float(x[start - n + 1 : start + 1].mean())
    out[start] = prev
    for i in range(start + 1, len(x)):
        prev = (prev * (n - 1) + x[i]) / n
        out[i] = prev
    return out


def sma(x: Arr, n: int) -> Arr:
    return np.asarray(pd.Series(x).rolling(n, min_periods=n).mean(), dtype="float64")


def ema(x: Arr, n: int) -> Arr:
    return np.asarray(pd.Series(x).ewm(span=n, adjust=False).mean(), dtype="float64")


def true_range(h: Arr, low: Arr, c: Arr) -> Arr:
    pc = np.r_[np.nan, c[:-1]]
    return np.asarray(np.fmax(np.fmax(h - low, np.abs(h - pc)), np.abs(low - pc)), dtype="float64")


def atr(h: Arr, low: Arr, c: Arr, n: int = 14) -> Arr:
    return wilder(true_range(h, low, c), n)


def rsi(c: Arr, n: int = 14) -> Arr:
    delta = np.r_[np.nan, np.diff(c)]
    gain = np.where(np.isnan(delta), np.nan, np.clip(delta, 0, None))
    loss = np.where(np.isnan(delta), np.nan, np.clip(-delta, 0, None))
    ag, al = wilder(gain, n), wilder(loss, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = 100 - 100 / (1 + ag / al)
    return np.asarray(np.where((al == 0) & ~np.isnan(ag), 100.0, out), dtype="float64")


def macd_hist(c: Arr) -> Arr:
    line = ema(c, 12) - ema(c, 26)
    signal = np.asarray(pd.Series(line).ewm(span=9, adjust=False).mean(), dtype="float64")
    return np.asarray(line - signal, dtype="float64")


def adx(h: Arr, low: Arr, c: Arr, n: int = 14) -> tuple[Arr, Arr]:
    """Wilder ADX and +DI - -DI."""
    up = np.r_[np.nan, np.diff(h)]
    down = np.r_[np.nan, -np.diff(low)]
    pdm = np.where((up > down) & (up > 0), up, 0.0)
    mdm = np.where((down > up) & (down > 0), down, 0.0)
    pdm[0] = mdm[0] = np.nan
    tr = true_range(h, low, c)
    tr[0] = np.nan
    atr_ = wilder(tr, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        pdi = 100 * wilder(pdm, n) / atr_
        mdi = 100 * wilder(mdm, n) / atr_
        dx = 100 * np.abs(pdi - mdi) / (pdi + mdi)
    return wilder(dx, n), pdi - mdi


def rolling_max(x: Arr, n: int) -> Arr:
    return np.asarray(pd.Series(x).rolling(n, min_periods=n).max(), dtype="float64")


def rolling_min(x: Arr, n: int) -> Arr:
    return np.asarray(pd.Series(x).rolling(n, min_periods=n).min(), dtype="float64")


def _shift(x: Arr, k: int) -> Arr:
    return np.asarray(np.r_[np.full(k, np.nan), x[:-k]], dtype="float64")


def _div(a: Arr, b: Arr) -> Arr:
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.asarray(a / b, dtype="float64")
    out[~np.isfinite(out)] = np.nan
    return out


# -- bars → frames on the server clock --------------------------------------------------------


def server_time(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """UTC instants → naive broker-server time (New York + 7h)."""
    idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    return idx.tz_convert("America/New_York").tz_localize(None) + SERVER_OFFSET


def bars_frame(bars: Sequence[OHLCVBar], step: pd.Timedelta, *, daily: bool = False) -> pd.DataFrame:
    """``date`` = bar open (server clock), ``t_close`` = its close. A daily bar is stamped at its
    session date (the provider dates it by the local trading day): that date IS the server day."""
    ts = pd.DatetimeIndex([pd.Timestamp(b.ts) for b in bars])
    if daily:
        date = (ts if ts.tz is None else ts.tz_convert("UTC").tz_localize(None)).normalize()
    else:
        date = server_time(ts)
    df = pd.DataFrame(
        {
            "date": date,
            "open": [float(b.open) for b in bars],
            "high": [float(b.high) for b in bars],
            "low": [float(b.low) for b in bars],
            "close": [float(b.close) for b in bars],
        }
    )
    df = df.drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)
    if daily:
        df = df[df["date"].dt.dayofweek < 5].reset_index(drop=True)
    df["t_close"] = df["date"] + step
    return df


# -- features ---------------------------------------------------------------------------------


def base_features(df: pd.DataFrame) -> pd.DataFrame:
    h, low, c = (df[k].to_numpy("float64") for k in ("high", "low", "close"))
    a14 = atr(h, low, c, 14)
    s = pd.Series(c)
    mid = s.rolling(20, min_periods=20).mean()
    std = s.rolling(20, min_periods=20).std(ddof=0)
    up, lo = (mid + 2 * std).to_numpy(), (mid - 2 * std).to_numpy()
    logret = np.log(c) - np.log(_shift(c, 1))
    idx = pd.DatetimeIndex(df["date"])
    cols: dict[str, Callable[[], Arr]] = {
        "atr_pct": lambda: _div(a14, c),
        "atr_regime": lambda: _div(a14, sma(a14, 96)),
        "rv_32": lambda: pd.Series(logret).rolling(32, min_periods=32).std().to_numpy(),
        "bb_width": lambda: _div(up - lo, mid.to_numpy()),
        "adx14": lambda: adx(h, low, c)[0],
        "hour": lambda: idx.hour.to_numpy().astype("float64"),
        "dow": lambda: idx.dayofweek.to_numpy().astype("float64"),
    }
    return pd.DataFrame({k: f() for k, f in cols.items()}, index=df.index)


def daily_features(d: pd.DataFrame) -> pd.DataFrame:
    """Daily context stamped at each day's CLOSE (``d1_t_close``)."""
    h, low, c = (d[k].to_numpy("float64") for k in ("high", "low", "close"))
    a = atr(h, low, c, 14)
    adx_, di = adx(h, low, c)
    s50, s200 = sma(c, 50), sma(c, 200)
    trend = np.where((c > s50) & (c > s200), 1.0, np.where((c < s50) & (c < s200), -1.0, 0.0))
    trend[np.isnan(s50) | np.isnan(s200)] = 0.0  # unknown never counts as a trend
    return pd.DataFrame(
        {
            "d1_t_close": d["t_close"].to_numpy(),
            "d1_rsi14": rsi(c, 14),
            "d1_adx14": adx_,
            "d1_di_diff": di,
            "d1_dist_sma50_atr": _div(c - s50, a),
            "d1_dist_sma200_atr": _div(c - s200, a),
            "d1_macd_hist_atr": _div(macd_hist(c), a),
            "d1_ret_atr_5": _div(c - _shift(c, 5), a),
            "d1_range_pos_20": _div(c - rolling_min(low, 20), rolling_max(h, 20) - rolling_min(low, 20)),
            "d1_atr_pct": _div(a, c),
            "d1_above_sma50": np.where(np.isnan(s50), np.nan, (c > s50).astype(float)),
            "d1_above_sma200": np.where(np.isnan(s200), np.nan, (c > s200).astype(float)),
            "d1_trend": trend,
            "d1_high": h,
            "d1_low": low,
        }
    )


def build(main: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    """Main bars + base features + the last daily bar closed at or before each bar's close."""
    left = pd.concat([main, base_features(main)], axis=1).sort_values("t_close")
    merged = pd.merge_asof(
        left,
        daily_features(daily).sort_values("d1_t_close"),
        left_on="t_close",
        right_on="d1_t_close",
        direction="backward",
        allow_exact_matches=True,
    )
    merged["d1_prev_range_pos"] = _div(
        (merged["close"] - merged["d1_low"]).to_numpy(), (merged["d1_high"] - merged["d1_low"]).to_numpy()
    )
    return merged.drop(columns=["d1_high", "d1_low"]).reset_index(drop=True)
