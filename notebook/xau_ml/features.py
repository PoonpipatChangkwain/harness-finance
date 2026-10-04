"""Causal features. Every function reads bars up to and including bar ``t`` only.

Formulas match ``backend/app/domain/compute/indicators.py`` (Wilder smoothing seeded by an
SMA, ATR from true range, RSI, MACD 12/26/9 with ``adjust=False``, Bollinger 20/2 with
population std) so the backend can compute the same inputs later (F1).

The base-timeframe features are plain functions of OHLCV arrays: ``pybroker_indicators``
wraps the very same functions as pybroker indicators, and ``base_features`` uses them for
the deterministic predictor — one implementation, two callers.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pandas as pd

from xau_ml.config import TF_DELTA

Arrays = dict[str, np.ndarray]  # open/high/low/close/volume/date


# --- primitives ---------------------------------------------------------------------------


def wilder(x: np.ndarray, n: int) -> np.ndarray:
    """SMA-seeded recursive mean with alpha = 1/n (backend ``_wilder``)."""
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


def sma(x: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(x).rolling(n, min_periods=n).mean().to_numpy()


def ema(x: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(x).ewm(span=n, adjust=False).mean().to_numpy()


def true_range(h: np.ndarray, low: np.ndarray, c: np.ndarray) -> np.ndarray:
    pc = np.r_[np.nan, c[:-1]]
    return np.fmax(np.fmax(h - low, np.abs(h - pc)), np.abs(low - pc))


def atr(h: np.ndarray, low: np.ndarray, c: np.ndarray, n: int = 14) -> np.ndarray:
    return wilder(true_range(h, low, c), n)


def rsi(c: np.ndarray, n: int = 14) -> np.ndarray:
    delta = np.r_[np.nan, np.diff(c)]
    gain = np.where(np.isnan(delta), np.nan, np.clip(delta, 0, None))
    loss = np.where(np.isnan(delta), np.nan, np.clip(-delta, 0, None))
    ag, al = wilder(gain, n), wilder(loss, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = 100 - 100 / (1 + ag / al)
    return np.where((al == 0) & ~np.isnan(ag), 100.0, out)


def macd(c: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    line = ema(c, 12) - ema(c, 26)
    signal = pd.Series(line).ewm(span=9, adjust=False).mean().to_numpy()
    return line, line - signal


def adx(h: np.ndarray, low: np.ndarray, c: np.ndarray, n: int = 14) -> tuple[np.ndarray, np.ndarray]:
    """Wilder ADX and +DI − −DI."""
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


def rolling_max(x: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(x).rolling(n, min_periods=n).max().to_numpy()


def rolling_min(x: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(x).rolling(n, min_periods=n).min().to_numpy()


def _shift(x: np.ndarray, k: int) -> np.ndarray:
    return np.r_[np.full(k, np.nan), x[:-k]]


def _div(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        out = a / b
    out[~np.isfinite(out)] = np.nan
    return out


# --- base-timeframe features (one function per column) ------------------------------------


def _atr14(a: Arrays) -> np.ndarray:
    return atr(a["high"], a["low"], a["close"], 14)


def _ret_atr(k: int) -> Callable[[Arrays], np.ndarray]:
    return lambda a: _div(a["close"] - _shift(a["close"], k), _atr14(a))


def _logret(k: int) -> Callable[[Arrays], np.ndarray]:
    return lambda a: np.log(a["close"]) - np.log(_shift(a["close"], k))


def _dist_sma(n: int) -> Callable[[Arrays], np.ndarray]:
    return lambda a: _div(a["close"] - sma(a["close"], n), _atr14(a))


def _sma_slope(n: int, k: int = 4) -> Callable[[Arrays], np.ndarray]:
    def f(a: Arrays) -> np.ndarray:
        s = sma(a["close"], n)
        return _div(s - _shift(s, k), _atr14(a))

    return f


def _range_pos(n: int) -> Callable[[Arrays], np.ndarray]:
    def f(a: Arrays) -> np.ndarray:
        hi, lo = rolling_max(a["high"], n), rolling_min(a["low"], n)
        return _div(a["close"] - lo, hi - lo)

    return f


def _bb(a: Arrays) -> tuple[np.ndarray, np.ndarray]:
    s = pd.Series(a["close"])
    mid = s.rolling(20, min_periods=20).mean()
    std = s.rolling(20, min_periods=20).std(ddof=0)
    up, lo = (mid + 2 * std).to_numpy(), (mid - 2 * std).to_numpy()
    return _div(a["close"] - lo, up - lo), _div(up - lo, mid.to_numpy())


def _vol_ratio(a: Arrays) -> np.ndarray:
    v = a["volume"]
    return _div(v, pd.Series(v).rolling(48, min_periods=48).mean().to_numpy())


def _hour(a: Arrays) -> np.ndarray:
    return pd.DatetimeIndex(a["date"]).hour.to_numpy().astype("float64")


def _dow(a: Arrays) -> np.ndarray:
    return pd.DatetimeIndex(a["date"]).dayofweek.to_numpy().astype("float64")


def _day_move(a: Arrays) -> np.ndarray:
    """Close minus the server day's first open, in ATR (intraday only — known at t)."""
    day = pd.DatetimeIndex(a["date"]).normalize()
    first_open = pd.Series(a["open"]).groupby(day).transform("first").to_numpy()
    return _div(a["close"] - first_open, _atr14(a))


BASE_FEATURES: dict[str, Callable[[Arrays], np.ndarray]] = {
    "logret_1": _logret(1),
    "ret_atr_4": _ret_atr(4),
    "ret_atr_16": _ret_atr(16),
    "ret_atr_48": _ret_atr(48),
    "atr_pct": lambda a: _div(_atr14(a), a["close"]),
    "atr_regime": lambda a: _div(_atr14(a), sma(_atr14(a), 96)),
    "rv_32": lambda a: pd.Series(_logret(1)(a)).rolling(32, min_periods=32).std().to_numpy(),
    "rsi14": lambda a: rsi(a["close"], 14),
    "rsi14_chg4": lambda a: rsi(a["close"], 14) - _shift(rsi(a["close"], 14), 4),
    "macd_atr": lambda a: _div(macd(a["close"])[0], _atr14(a)),
    "macd_hist_atr": lambda a: _div(macd(a["close"])[1], _atr14(a)),
    "macd_hist_chg": lambda a: _div(macd(a["close"])[1] - _shift(macd(a["close"])[1], 1), _atr14(a)),
    "bb_pctb": lambda a: _bb(a)[0],
    "bb_width": lambda a: _bb(a)[1],
    "dist_sma20_atr": _dist_sma(20),
    "dist_sma50_atr": _dist_sma(50),
    "dist_sma200_atr": _dist_sma(200),
    "sma20_slope_atr": _sma_slope(20),
    "sma50_slope_atr": _sma_slope(50),
    "range_pos_16": _range_pos(16),
    "range_pos_48": _range_pos(48),
    "range_pos_96": _range_pos(96),
    "vol_ratio_48": _vol_ratio,
    "body_atr": lambda a: _div(a["close"] - a["open"], _atr14(a)),
    "upper_wick_atr": lambda a: _div(a["high"] - np.fmax(a["open"], a["close"]), _atr14(a)),
    "lower_wick_atr": lambda a: _div(np.fmin(a["open"], a["close"]) - a["low"], _atr14(a)),
    "adx14": lambda a: adx(a["high"], a["low"], a["close"])[0],
    "di_diff": lambda a: adx(a["high"], a["low"], a["close"])[1],
    "day_move_atr": _day_move,
    "hour": _hour,
    "dow": _dow,
}


def arrays(df: pd.DataFrame) -> Arrays:
    return {k: df[k].to_numpy() for k in ("open", "high", "low", "close", "volume", "date")}


def base_features(df: pd.DataFrame) -> pd.DataFrame:
    a = arrays(df)
    out = pd.DataFrame({name: fn(a) for name, fn in BASE_FEATURES.items()}, index=df.index)
    out["atr14"] = _atr14(a)  # not a model input: sizes R
    return out


# --- higher-timeframe context -------------------------------------------------------------


def htf_features(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    """Context columns of a higher timeframe, stamped at that bar's CLOSE time."""
    h, low, c = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    a = atr(h, low, c, 14)
    adx_, di = adx(h, low, c)
    s50, s200 = sma(c, 50), sma(c, 200)
    p = "d1_" if tf == "1d" else "h1_"
    out = pd.DataFrame(
        {
            f"{p}t_close": df["t_close"].to_numpy(),
            f"{p}rsi14": rsi(c, 14),
            f"{p}adx14": adx_,
            f"{p}di_diff": di,
            f"{p}dist_sma50_atr": _div(c - s50, a),
            f"{p}dist_sma200_atr": _div(c - s200, a),
            f"{p}macd_hist_atr": _div(macd(c)[1], a),
            f"{p}ret_atr_5": _div(c - _shift(c, 5), a),
            f"{p}range_pos_20": _div(c - rolling_min(low, 20), rolling_max(h, 20) - rolling_min(low, 20)),
            f"{p}atr_pct": _div(a, c),
        }
    )
    if tf == "1d":
        above50 = np.where(np.isnan(s50), np.nan, (c > s50).astype(float))
        above200 = np.where(np.isnan(s200), np.nan, (c > s200).astype(float))
        trend = np.where((c > s50) & (c > s200), 1.0, np.where((c < s50) & (c < s200), -1.0, 0.0))
        trend[np.isnan(s50) | np.isnan(s200)] = 0.0  # unknown never counts as a trend (fail open)
        out["d1_above_sma50"] = above50
        out["d1_above_sma200"] = above200
        out["d1_trend"] = trend
        out["d1_high"] = h
        out["d1_low"] = low
    return out


def merge_htf(base: pd.DataFrame, htf: pd.DataFrame, tf: str) -> pd.DataFrame:
    """Attach the last HTF bar CLOSED at or before the base bar's close (= decision time)."""
    p = "d1_" if tf == "1d" else "h1_"
    left = base.sort_values("t_close")
    merged = pd.merge_asof(
        left,
        htf.sort_values(f"{p}t_close"),
        left_on="t_close",
        right_on=f"{p}t_close",
        direction="backward",
        allow_exact_matches=True,
    )
    merged.index = left.index
    if tf == "1d":  # position inside the last closed day's range, with today's price
        merged["d1_prev_range_pos"] = _div(
            (merged["close"] - merged["d1_low"]).to_numpy(), (merged["d1_high"] - merged["d1_low"]).to_numpy()
        )
        merged = merged.drop(columns=["d1_high", "d1_low"])
    return merged


def build(main: pd.DataFrame, tf: str, htfs: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Main bars + base features + every HTF context (each merged on close time)."""
    out = pd.concat([main, base_features(main)], axis=1)
    for htf_tf, htf_df in htfs.items():
        out = merge_htf(out, htf_features(htf_df, htf_tf), htf_tf)
    assert (out["t_close"] - out["date"] == TF_DELTA[tf]).all()
    return out.sort_values("date").reset_index(drop=True)


def add_extras(frame: pd.DataFrame, d1_raw: pd.DataFrame) -> pd.DataFrame:
    """v0.2 features — levels traders watch, all known at the decision bar's close:
    today's running range (server day), the last CLOSED day's and week's high/low as
    distances in ATR, and the session of the server hour (server = New York + 7h)."""
    out = frame.copy()
    atr_ = out["atr14"].to_numpy()
    c = out["close"].to_numpy()
    day = out["date"].dt.normalize()
    run_hi = out.groupby(day)["high"].cummax().to_numpy()
    run_lo = out.groupby(day)["low"].cummin().to_numpy()
    out["day_range_atr"] = _div(run_hi - run_lo, atr_)
    out["day_pos"] = _div(c - run_lo, run_hi - run_lo)

    d = d1_raw[["date", "t_close", "high", "low"]].sort_values("t_close")
    left = out[["t_close"]].reset_index()
    pdh = pd.merge_asof(left.sort_values("t_close"), d.rename(columns={"t_close": "pd_t_close"}),
                        left_on="t_close", right_on="pd_t_close", direction="backward").set_index("index")
    out["dist_pdh_atr"] = _div(c - pdh.loc[out.index, "high"].to_numpy(), atr_)
    out["dist_pdl_atr"] = _div(c - pdh.loc[out.index, "low"].to_numpy(), atr_)

    wk = d.assign(week=d["date"].dt.to_period("W-SUN"))
    weekly = wk.groupby("week").agg(high=("high", "max"), low=("low", "min")).reset_index()
    # a week becomes usable at its CALENDAR end (Sunday 24:00 server), not at its last bar:
    # stamping by the last bar would let a live feed (which only has this week's bars so far)
    # match the week still in progress — a train/serve mismatch the truncation test catches
    weekly["w_t_close"] = weekly["week"].dt.end_time.dt.ceil("D")
    pw = pd.merge_asof(left.sort_values("t_close"), weekly.sort_values("w_t_close")[["w_t_close", "high", "low"]],
                       left_on="t_close", right_on="w_t_close", direction="backward").set_index("index")
    out["dist_pwh_atr"] = _div(c - pw.loc[out.index, "high"].to_numpy(), atr_)
    out["dist_pwl_atr"] = _div(c - pw.loc[out.index, "low"].to_numpy(), atr_)
    w_close = pw.loc[out.index, "w_t_close"]
    assert (w_close.isna() | (w_close <= out["t_close"])).all()

    hour = out["date"].dt.hour
    out["session"] = np.select([hour.between(1, 9), hour.between(10, 14)], [0.0, 1.0], 2.0)  # asia / london / new york
    out["pw_t_close"] = w_close.to_numpy()
    out["pd_t_close"] = pdh.loc[out.index, "pd_t_close"].to_numpy()
    return out


TIME_COLS = ("h1_t_close", "d1_t_close", "pw_t_close", "pd_t_close")
NOT_FEATURES = {"date", "t_close", "open", "high", "low", "close", "volume", "atr14", "risk", "symbol", "no_entry", *TIME_COLS}
LABEL_PREFIXES = ("y_", "r_", "exit_", "ct_", "tp_")  # outcomes of the future: never inputs


def feature_columns(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if c not in NOT_FEATURES and not c.startswith(LABEL_PREFIXES)]
