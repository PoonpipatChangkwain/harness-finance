"""Load the broker CSVs and audit them (duplicates, gaps, weekends, bad prices, timezone)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from xau_ml.config import DATA_DIR, FORBIDDEN_FROM, TF_DELTA


def load(tf: str, start: str | None = None, end: str | None = None) -> pd.DataFrame:
    """Bars of ``tf`` with ``date`` = bar OPEN time (broker server clock) and ``t_close``."""
    df = pd.read_csv(DATA_DIR / f"XAU_{tf}_data.csv", sep=";")
    df.columns = [c.lower() for c in df.columns]
    df["date"] = pd.to_datetime(df["date"], format="%Y.%m.%d %H:%M")
    df = df.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    if start is not None:
        df = df[df["date"] >= pd.Timestamp(start)]
    if end is not None:
        df = df[df["date"] <= pd.Timestamp(end)]
    assert df["date"].max() < FORBIDDEN_FROM, "Aug-Sep 2026 is reserved for the harness backtest"
    df = df.reset_index(drop=True)
    df["t_close"] = df["date"] + TF_DELTA[tf]
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = df[col].astype("float64")
    return df


def csv_duplicates(tf: str) -> pd.DataFrame:
    """Rows of the raw CSV whose timestamp appears more than once (``load`` keeps the last)."""
    df = pd.read_csv(DATA_DIR / f"XAU_{tf}_data.csv", sep=";")
    return df[df["Date"].duplicated(keep=False)]


def quality_report(raw: pd.DataFrame, tf: str) -> dict:
    """Counts of every data problem the features or labels could trip on."""
    d = raw
    step = TF_DELTA[tf]
    gaps = d["date"].diff().dropna()
    prev = d["date"].shift(1)
    weekend_gap = (prev.dt.dayofweek == 4) & (d["date"].dt.dayofweek == 0)
    is_gap = gaps > step
    daily_break = is_gap & ~weekend_gap.loc[gaps.index] & (gaps <= pd.Timedelta(hours=2) + step) & (
        d.loc[gaps.index, "date"].dt.hour.isin([0, 1])
    )
    other = is_gap & ~weekend_gap.loc[gaps.index] & ~daily_break
    ohlc_bad = (
        (d["high"] < d[["open", "close"]].max(axis=1))
        | (d["low"] > d[["open", "close"]].min(axis=1))
        | (d["high"] < d["low"])
        | (d[["open", "high", "low", "close"]] <= 0).any(axis=1)
    )
    logret = np.log(d["close"]).diff()
    z = (logret - logret.rolling(500, min_periods=100).mean()) / logret.rolling(500, min_periods=100).std()
    rng = (d["high"] - d["low"]) / d["close"]
    rng_z = rng / rng.rolling(500, min_periods=100).median()
    expected = {"15m": 4 * 23 * 5, "1h": 23 * 5, "1d": 5}[tf]
    week = d.set_index("date").resample("W-SUN").size()
    week = week[week > 0]
    return {
        "rows": len(d),
        "first": d["date"].min(),
        "last": d["date"].max(),
        "non_monotonic": int((gaps <= pd.Timedelta(0)).sum()),
        "weekend_bars(Sat/Sun)": int(d["date"].dt.dayofweek.isin([5, 6]).sum()),
        "gaps_weekend": int((is_gap & weekend_gap.loc[gaps.index]).sum()),
        "gaps_daily_break": int(daily_break.sum()),
        "gaps_other(holiday/missing)": int(other.sum()),
        "bars_missing_in_other_gaps": int(((gaps[other] / step) - 1).sum()),
        "ohlc_inconsistent": int(ohlc_bad.sum()),
        "zero_volume": int((d["volume"] <= 0).sum()),
        "abs_return_z>8": int((z.abs() > 8).sum()),
        "range>10x_median": int((rng_z > 10).sum()),
        "full_weeks_coverage_%": round(100 * float((week / expected).clip(upper=1).median()), 1),
    }


def other_gaps(raw: pd.DataFrame, tf: str) -> pd.DataFrame:
    """Every gap that is neither a weekend nor the daily 00:00-01:00 break."""
    step = TF_DELTA[tf]
    d = raw[["date"]].copy()
    d["prev"] = d["date"].shift(1)
    d["gap"] = d["date"] - d["prev"]
    weekend = (d["prev"].dt.dayofweek == 4) & (d["date"].dt.dayofweek == 0)
    brk = (d["gap"] <= pd.Timedelta(hours=2) + step) & d["date"].dt.hour.isin([0, 1])
    return d[(d["gap"] > step) & ~weekend & ~brk].reset_index(drop=True)


def outliers(raw: pd.DataFrame, z_thr: float = 8.0) -> pd.DataFrame:
    logret = np.log(raw["close"]).diff()
    z = (logret - logret.rolling(500, min_periods=100).mean()) / logret.rolling(500, min_periods=100).std()
    out = raw.assign(logret=logret, z=z)
    return out[out["z"].abs() > z_thr]


def first_fridays(start: str, end: str) -> list[pd.Timestamp]:
    days = pd.date_range(start, end, freq="D")
    return [d for d in days if d.dayofweek == 4 and d.day <= 7]


def nfp_server_times(m15: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    """US payrolls print at 08:30 New York. Find the 15m bar with the largest range in the
    server-clock window 12:00-18:00 on each first Friday, and compare with 08:30 NY in UTC."""
    rows = []
    for day in first_fridays(start, end):
        w = m15[(m15["date"] >= day + pd.Timedelta(hours=12)) & (m15["date"] < day + pd.Timedelta(hours=18))]
        if w.empty:
            continue
        top = w.loc[(w["high"] - w["low"]).idxmax()]
        ny = pd.Timestamp(day.date()).tz_localize("America/New_York") + pd.Timedelta(hours=8, minutes=30)
        utc = ny.tz_convert("UTC").tz_localize(None)
        server = top["date"]
        rows.append(
            {
                "day": day.date(),
                "max_range_bar_server": server.strftime("%H:%M"),
                "nfp_utc": utc.strftime("%H:%M"),
                "server_minus_utc_h": (server - utc).total_seconds() / 3600 % 24,
                "range_x_median": (top["high"] - top["low"]) / (w["high"] - w["low"]).median(),
            }
        )
    return pd.DataFrame(rows)


def resample_daily(m15: pd.DataFrame) -> pd.DataFrame:
    """15m bars → server-day OHLC, to check the 1d file uses the same day boundary."""
    g = m15.set_index("date").resample("1D")
    out = pd.DataFrame(
        {"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(), "close": g["close"].last()}
    ).dropna()
    return out
