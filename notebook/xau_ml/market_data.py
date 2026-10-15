"""VIX and DXY features fetched from yfinance — external market context that gold reacts to.

Features returned per bar (merged on server clock via ``add_vix_features``):
  - ``vix``          — CBOE VIX close (fear gauge, daily)
  - ``vix_ret_5``    — 5-day VIX return (momentum in fear)
  - ``vix_level``    — VIX relative to its 20-day SMA (high = stressed)
  - ``dxy``          — US Dollar Index close (daily)
  - ``dxy_ret_5``    — 5-day DXY return
  - ``dxy_dist_sma20`` — DXY distance from SMA20 in std units
  - ``us10y``        — US 10Y Treasury yield close (daily)
  - ``us10y_chg_5``  — 5-day change in yield (bps)

All are daily frequency, merged backward on the bar's close time so there is no
look-ahead: if a bar closes before today's daily close, yesterday's value is used.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "market_cache"


def _cache_path(symbol: str) -> Path:
    safe = symbol.replace("^", "").replace("=", "").replace("-", "").replace(".", "")
    return CACHE_DIR / f"{safe}_daily.csv"


def fetch_daily(symbol: str, start: str = "2023-06-01", end: str | None = None,
                use_cache: bool = True) -> pd.DataFrame:
    """Fetch daily OHLCV from yfinance, with local CSV cache (no pyarrow needed)."""
    cache = _cache_path(symbol)
    if use_cache and cache.exists():
        df = pd.read_csv(cache, index_col=0, parse_dates=True)
        if end is None or df.index.max() >= pd.Timestamp(end):
            return df

    import yfinance as yf
    raw = yf.download(symbol, start=start, end=end, progress=False, auto_adjust=False)
    if isinstance(raw.columns, pd.MultiIndex):
        raw = raw.droplevel(1, axis=1)
    df = raw[["Close"]].dropna().rename(columns={"Close": "close"})
    df.index = pd.to_datetime(df.index).tz_localize(None)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(cache)
    return df


def _sma(x: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(x).rolling(n, min_periods=n).mean().to_numpy()


def _daily_features(df: pd.DataFrame, prefix: str) -> pd.DataFrame:
    """Standard daily context features from a single-column close series."""
    c = df["close"].to_numpy()
    out = pd.DataFrame(index=df.index)
    out[f"{prefix}"] = c
    out[f"{prefix}_ret_5"] = (c / np.r_[np.full(5, np.nan), c[:-5]]) - 1
    s20 = _sma(c, 20)
    std20 = pd.Series(c).rolling(20, min_periods=20).std(ddof=0).to_numpy()
    out[f"{prefix}_dist_sma20"] = np.where(std20 > 0, (c - s20) / std20, 0.0)
    # For VIX: also add level relative to SMA
    if prefix == "vix":
        out["vix_level"] = np.where(s20 > 0, c / s20, 1.0)
    # For yields: 5-day change in bps
    if prefix == "us10y":
        out["us10y_chg_5"] = c - np.r_[np.full(5, np.nan), c[:-5]]
    # t_close for merge_asof: assume daily data closes at 00:00 next server day
    # (server = NY+7h; NYSE close 16:00 NY = 23:00 server → stamped to next day 00:00)
    out["t_close"] = df.index + pd.Timedelta(days=1)
    return out


def add_market_features(bars: pd.DataFrame, start: str = "2023-06-01",
                        use_cache: bool = True) -> pd.DataFrame:
    """Merge VIX, DXY, US10Y daily features onto intraday bars via server close time.

    Backward merge ensures no look-ahead: today's bar gets yesterday's daily close
    if the bar closes before the daily data's stamp.
    """
    out = bars.copy()

    for symbol, prefix in [("^VIX", "vix"), ("DX-Y.NYB", "dxy"), ("^TNX", "us10y")]:
        try:
            raw = fetch_daily(symbol, start=start, use_cache=use_cache)
        except Exception:  # noqa: BLE001
            # If fetch fails, fill with NaN (model handles NaN via LightGBM native)
            for c in _expected_cols(prefix):
                out[c] = np.nan
            continue

        feat = _daily_features(raw, prefix)
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


def _expected_cols(prefix: str) -> list[str]:
    cols = [prefix, f"{prefix}_ret_5", f"{prefix}_dist_sma20"]
    if prefix == "vix":
        cols.append("vix_level")
    if prefix == "us10y":
        cols.append("us10y_chg_5")
    return cols


MARKET_COLS = [
    "vix", "vix_ret_5", "vix_dist_sma20", "vix_level",
    "dxy", "dxy_ret_5", "dxy_dist_sma20",
    "us10y", "us10y_ret_5", "us10y_dist_sma20", "us10y_chg_5",
]
