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
from typing import Any

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


# -- v0.3 features: market context (VIX/DXY/US10Y) + calendar + session ----------------------


def _daily_market_features(df: pd.DataFrame, prefix: str) -> pd.DataFrame:
    """Standard features from a single-column daily close series (same as notebook/xau_ml/market_data.py)."""
    c = df["close"].to_numpy("float64")
    out = pd.DataFrame(index=df.index)
    out[prefix] = c
    out[f"{prefix}_ret_5"] = (c / np.r_[np.full(5, np.nan), c[:-5]]) - 1
    s20 = sma(c, 20)
    std20 = np.asarray(pd.Series(c).rolling(20, min_periods=20).std(ddof=0), dtype="float64")
    out[f"{prefix}_dist_sma20"] = np.where(std20 > 0, (c - s20) / std20, 0.0)
    if prefix == "vix":
        out["vix_level"] = np.where(s20 > 0, c / s20, 1.0)
    if prefix == "us10y":
        out["us10y_chg_5"] = c - np.r_[np.full(5, np.nan), c[:-5]]
    # t_close for merge_asof: daily data closes → stamped to next day 00:00 server
    out["t_close"] = df.index + pd.Timedelta(days=1)
    return out


MARKET_SYMBOLS = [("^VIX", "vix"), ("DX-Y.NYB", "dxy"), ("^TNX", "us10y")]

MARKET_COLS = [
    "vix",
    "vix_ret_5",
    "vix_dist_sma20",
    "vix_level",
    "dxy",
    "dxy_ret_5",
    "dxy_dist_sma20",
    "us10y",
    "us10y_ret_5",
    "us10y_dist_sma20",
    "us10y_chg_5",
]


def add_market_features(
    frame: pd.DataFrame,
    market_daily: dict[str, pd.DataFrame] | None = None,
) -> pd.DataFrame:
    """Merge VIX/DXY/US10Y daily features onto intraday bars via backward merge_asof.

    ``market_daily`` maps prefix ("vix", "dxy", "us10y") → DataFrame with DatetimeIndex
    and a ``close`` column (daily). If None or a prefix is missing, those columns are NaN
    (LightGBM handles NaN natively).
    """
    out = frame.copy()
    market_daily = market_daily or {}
    for _, prefix in MARKET_SYMBOLS:
        raw = market_daily.get(prefix)
        if raw is None or raw.empty:
            for c in [c for c in MARKET_COLS if c.startswith(prefix)]:
                out[c] = np.nan
            continue
        feat = _daily_market_features(raw, prefix)
        left = out[["t_close"]].reset_index()
        merged = pd.merge_asof(
            left.sort_values("t_close"),
            feat.reset_index(drop=True).sort_values("t_close"),
            on="t_close",
            direction="backward",
        ).set_index("index")
        for c in merged.columns:
            if c != "t_close":
                out[c] = merged.loc[out.index, c].to_numpy()
    return out


# -- calendar features (deterministic from known event dates) ---------------------------------

# FOMC meeting dates (rate decision days): 2024-01 through 2026-12
_FOMC_DATES = [
    "2024-01-31",
    "2024-03-20",
    "2024-05-01",
    "2024-06-12",
    "2024-07-31",
    "2024-09-18",
    "2024-11-07",
    "2024-12-18",
    "2025-01-29",
    "2025-03-19",
    "2025-05-07",
    "2025-06-18",
    "2025-07-30",
    "2025-09-17",
    "2025-10-29",
    "2025-12-17",
    "2026-01-28",
    "2026-03-18",
    "2026-05-06",
    "2026-06-17",
    "2026-07-29",
    "2026-09-16",
    "2026-10-28",
    "2026-12-16",
]

_CPI_DATES = [
    "2024-01-11",
    "2024-02-13",
    "2024-03-12",
    "2024-04-10",
    "2024-05-15",
    "2024-06-12",
    "2024-07-11",
    "2024-08-14",
    "2024-09-11",
    "2024-10-10",
    "2024-11-13",
    "2024-12-11",
    "2025-01-15",
    "2025-02-12",
    "2025-03-12",
    "2025-04-10",
    "2025-05-13",
    "2025-06-11",
    "2025-07-15",
    "2025-08-12",
    "2025-09-10",
    "2025-10-14",
    "2025-11-12",
    "2025-12-10",
    "2026-01-13",
    "2026-02-11",
    "2026-03-11",
    "2026-04-14",
    "2026-05-12",
    "2026-06-10",
    "2026-07-14",
    "2026-08-12",
    "2026-09-11",
    "2026-10-13",
    "2026-11-10",
    "2026-12-09",
]

CALENDAR_COLS = [
    "cal_high_today",
    "cal_event_count",
    "cal_is_nfp_day",
    "cal_is_fomc_day",
    "cal_hours_to_next",
    "cal_hours_since_last",
]


def _first_fridays(start: str, end: str) -> list[pd.Timestamp]:
    days = pd.date_range(start, end, freq="D")
    return [d for d in days if d.dayofweek == 4 and d.day <= 7]


def _event_schedule(start: str = "2023-01-01", end: str = "2027-01-01") -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for d in _first_fridays(start, end):
        rows.append({"date": d, "server_hour": 15.5, "event": "NFP"})
    for d_str in _FOMC_DATES:
        dt = pd.Timestamp(d_str)
        if pd.Timestamp(start) <= dt <= pd.Timestamp(end):
            rows.append({"date": dt, "server_hour": 21.0, "event": "FOMC"})
    for d_str in _CPI_DATES:
        dt = pd.Timestamp(d_str)
        if pd.Timestamp(start) <= dt <= pd.Timestamp(end):
            rows.append({"date": dt, "server_hour": 15.5, "event": "CPI"})
    df = pd.DataFrame(rows) if rows else pd.DataFrame(columns=["date", "server_hour", "event"])
    df["date"] = pd.to_datetime(df["date"])
    df["event_time"] = df["date"] + pd.to_timedelta(df["server_hour"], unit="h")
    return df.sort_values("event_time").reset_index(drop=True)


def add_calendar_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Add deterministic calendar features. ``date`` = bar open time on server clock."""
    out = frame.copy()
    cal = _event_schedule()
    bar_day = out["date"].dt.normalize()
    bar_time = out["date"]
    event_days = set(cal["date"].dt.normalize().unique())
    out["cal_high_today"] = bar_day.isin(event_days).astype("float64")
    day_counts = cal.groupby(cal["date"].dt.normalize()).size()
    out["cal_event_count"] = bar_day.map(day_counts).fillna(0).astype("float64")
    nfp_days = set(cal[cal["event"] == "NFP"]["date"].dt.normalize().unique())
    out["cal_is_nfp_day"] = bar_day.isin(nfp_days).astype("float64")
    fomc_days = set(cal[cal["event"] == "FOMC"]["date"].dt.normalize().unique())
    out["cal_is_fomc_day"] = bar_day.isin(fomc_days).astype("float64")
    event_times = cal["event_time"].to_numpy().astype("datetime64[ns]")
    bar_times = bar_time.to_numpy().astype("datetime64[ns]")
    hours_to = np.full(len(frame), 48.0)
    hours_since = np.full(len(frame), 48.0)
    for i, bt in enumerate(bar_times):
        future = event_times[event_times > bt]
        if len(future):
            hours_to[i] = min(float((future[0] - bt) / np.timedelta64(1, "h")), 48.0)
        past = event_times[event_times <= bt]
        if len(past):
            hours_since[i] = min(float((bt - past[-1]) / np.timedelta64(1, "h")), 48.0)
    out["cal_hours_to_next"] = hours_to
    out["cal_hours_since_last"] = hours_since
    return out


def add_session(frame: pd.DataFrame) -> pd.DataFrame:
    """Session of the bar's server hour, exactly as ``notebook/xau_ml/features.add_extras`` trained
    it: 0 = Asia (hours 1-9), 1 = London (10-14), 2 = New York (every other hour, incl. 0 and 15-23).

    (Until 2026-10-05 this used 0-8 / 9-15 / 16-23, which disagreed with the trained feature at
    hours 0, 9 and 15; ``tests/domain/test_ml_session.py`` now pins all 24 hours.)"""
    h = frame["date"].dt.hour.to_numpy()
    out = frame.copy()
    out["session"] = np.select([(h >= 1) & (h <= 9), (h >= 10) & (h <= 14)], [0.0, 1.0], 2.0)
    return out


# -- context for the LLM gate (not model inputs) -------------------------------------------------
# Same definitions as the notebook's base/extras features (``features.py`` there); the gate's
# prompt was validated on these exact numbers, so they are computed the same way.

PACKET_FIELDS = (
    "rsi14", "ret_atr_4", "ret_atr_16", "ret_atr_48", "range_pos_96",
    "day_move_atr", "day_range_atr", "dist_pdh_atr", "dist_pdl_atr",
)  # fmt: skip


def add_packet_context(frame: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    """The intraday context columns the gate's prompt reads; ``frame`` is ``build()``'s output
    (server-clock ``date``, OHLC, ``t_close``), ``daily`` the daily ``bars_frame``."""
    out = frame.copy()
    o, h, low, c = (out[k].to_numpy("float64") for k in ("open", "high", "low", "close"))
    a14 = atr(h, low, c, 14)
    out["rsi14"] = rsi(c, 14)
    for k in (4, 16, 48):
        out[f"ret_atr_{k}"] = _div(c - _shift(c, k), a14)
    hi96, lo96 = rolling_max(h, 96), rolling_min(low, 96)
    out["range_pos_96"] = _div(c - lo96, hi96 - lo96)
    day = out["date"].dt.normalize()
    first_open = pd.Series(o).groupby(day.to_numpy()).transform("first").to_numpy()
    out["day_move_atr"] = _div(c - first_open, a14)
    run_hi = out.groupby(day)["high"].cummax().to_numpy()
    run_lo = out.groupby(day)["low"].cummin().to_numpy()
    out["day_range_atr"] = _div(run_hi - run_lo, a14)
    prev = daily[["t_close", "high", "low"]].sort_values("t_close").rename(columns={"t_close": "pd_t_close"})
    left = out[["t_close"]].reset_index()
    pdh = pd.merge_asof(
        left.sort_values("t_close"), prev, left_on="t_close", right_on="pd_t_close", direction="backward"
    ).set_index("index")
    out["dist_pdh_atr"] = _div(c - pdh.loc[out.index, "high"].to_numpy("float64"), a14)
    out["dist_pdl_atr"] = _div(c - pdh.loc[out.index, "low"].to_numpy("float64"), a14)
    return out


def calendar_around(bar_open: pd.Timestamp) -> dict[str, Any]:
    """Next / last scheduled high-impact USD release around a bar (server clock, the bar's OPEN
    time — what the notebook's gate experiment fed the LLM and what ``cal_hours_*`` use)."""
    cal = _event_schedule()
    times = cal["event_time"].to_numpy().astype("datetime64[ns]")
    t = np.datetime64(bar_open, "ns")
    out: dict[str, Any] = {}
    future = np.flatnonzero(times > t)
    past = np.flatnonzero(times <= t)
    if len(future):
        i = future[0]
        out["next_event"] = str(cal["event"].iloc[i])
        out["hours_to_next"] = round(float((times[i] - t) / np.timedelta64(1, "h")), 1)
    if len(past):
        i = past[-1]
        out["last_event"] = str(cal["event"].iloc[i])
        out["hours_since_last"] = round(float((t - times[i]) / np.timedelta64(1, "h")), 1)
    return out


# FOMC and CPI dates are listed through December 2026 (a first-Friday NFP is generated further, but
# the 2027 releases are unknown), so past this day the schedule is incomplete: the calendar
# features and the gate's calendar rule would be silently blind. Extend the lists, then move this.
SCHEDULE_COMPLETE_THROUGH = pd.Timestamp("2026-12-31")


def add_regime_features(frame: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    """Composite regime from daily SMA20/50/200 — merged backward like other HTF context.

    The regime score sums three components (-3..+3):
      - SMA50 vs SMA200 trend (+1/-1)
      - price vs SMA50 (+1/-1)
      - SMA20 vs SMA50 momentum (+1/-1)
    Bucketed: score >= 2 → UP (+1), score <= -2 → DOWN (-1), else SIDE (0).
    Also: trend_strength = (close - SMA50) / ATR14, SMA50 slope in ATR units.
    """
    c = daily["close"].to_numpy("float64")
    h, low = daily["high"].to_numpy("float64"), daily["low"].to_numpy("float64")
    s20, s50, s200 = sma(c, 20), sma(c, 50), sma(c, 200)
    a14 = atr(h, low, c, 14)

    trend_long = np.where(s50 > s200, 1, np.where(s50 < s200, -1, 0))
    price_vs_50 = np.where(c > s50, 1, -1)
    mom = np.where(s20 > s50, 1, np.where(s20 < s50, -1, 0))
    score = trend_long + price_vs_50 + mom
    regime = np.where(score >= 2, 1, np.where(score <= -2, -1, 0))

    with np.errstate(divide="ignore", invalid="ignore"):
        trend_str = np.where(a14 > 0, (c - s50) / a14, 0.0)
        slope = np.where(a14 > 0, (s50 - np.r_[np.full(5, np.nan), s50[:-5]]) / a14, 0.0)

    feat = pd.DataFrame(
        {
            "regime": regime.astype("float64"),
            "regime_trend_str": trend_str.astype("float64"),
            "regime_sma50_slope": slope.astype("float64"),
            "d1_t_close_regime": daily["t_close"].to_numpy(),
        },
        index=daily.index,
    )

    left = frame[["t_close"]].reset_index()
    merged = pd.merge_asof(
        left.sort_values("t_close"),
        feat.sort_values("d1_t_close_regime"),
        left_on="t_close",
        right_on="d1_t_close_regime",
        direction="backward",
    ).set_index("index")

    out = frame.copy()
    for col in ("regime", "regime_trend_str", "regime_sma50_slope"):
        out[col] = merged.loc[out.index, col].to_numpy()
    return out


REGIME_COLS = ["regime", "regime_trend_str", "regime_sma50_slope"]


def add_v3_features(
    frame: pd.DataFrame,
    groups: dict[str, list[str]] | None = None,
    market_daily: dict[str, pd.DataFrame] | None = None,
) -> pd.DataFrame:
    """Add the v0.3 feature groups that a model's metadata requests.

    ``groups`` is from the model JSON's ``feature_groups``; only add what the model needs.
    If ``groups`` is None, add all three (calendar + market + session).
    """
    out = frame
    need_cal = groups is None or bool(groups.get("calendar"))
    need_mkt = groups is None or bool(groups.get("market"))
    need_ses = groups is None or bool(groups.get("session"))
    if need_cal:
        out = add_calendar_features(out)
    if need_mkt:
        out = add_market_features(out, market_daily=market_daily)
    if need_ses:
        out = add_session(out)
    return out
