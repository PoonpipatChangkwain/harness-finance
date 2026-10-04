"""The reserved Aug-Sep 2026 holdout: bars the training notebooks never loaded.

The broker CSVs end 2026-01-30, so the holdout comes from the same feed the harness
backtest uses for XAUUSD — COMEX gold futures ``GC=F`` via yfinance (the backend's
``spot_metals`` provider shifts them by a constant basis, which every model feature
ignores except the tiny ``atr_pct``/``d1_atr_pct`` level effect, ~1%).

Bars are converted to the broker server clock the models were trained on (server = New
York + 7h, so the daily 17:00 NY break is 00:00 server and a server day = one forex day);
the 1d context is resampled from 1h on that clock. ``data.load``'s guard against the
holdout stays in place: this module is the only door into Aug-Sep 2026.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from xau_ml import features as F
from xau_ml import sim as S
from xau_ml.config import HORIZONS, LABEL_SETS, TF_DELTA
from xau_ml.labels import add_labels
from xau_ml.predictor import Predictor

PROXY = "GC=F"
HOLDOUT = ("2026-08-01", "2026-09-30 23:59")
SERVER_OFFSET = pd.Timedelta(hours=7)  # server = New York + 7h (see features.add_extras)


def _to_server(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        df = df.droplevel(1, axis=1)
    idx = df.index.tz_convert("America/New_York").tz_localize(None) + SERVER_OFFSET
    out = pd.DataFrame(
        {"date": idx, **{c.lower(): df[c].to_numpy("float64") for c in ("Open", "High", "Low", "Close", "Volume")}}
    )
    return out.dropna(subset=["open", "high", "low", "close"]).sort_values("date").reset_index(drop=True)


def fetch(tf: str, start: str | None = None, period: str | None = None) -> pd.DataFrame:
    import yfinance as yf

    raw = yf.download(PROXY, interval=tf, start=start, period=period, progress=False, auto_adjust=False)
    out = _to_server(raw)
    out["t_close"] = out["date"] + TF_DELTA[tf]
    return out


def daily_from_hourly(h1: pd.DataFrame) -> pd.DataFrame:
    g = h1.groupby(h1["date"].dt.normalize())
    d = pd.DataFrame(
        {"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
         "close": g["close"].last(), "volume": g["volume"].sum()}
    ).rename_axis("date").reset_index()
    d = d[d["date"].dt.dayofweek < 5].reset_index(drop=True)  # no stray weekend stubs
    d["t_close"] = d["date"] + TF_DELTA["1d"]
    return d


def load_bars() -> dict[str, pd.DataFrame]:
    """15m (yfinance keeps ~60 days), 1h since 2025 (also the 1d source)."""
    h1 = fetch("1h", start="2025-01-01")
    return {"15m": fetch("15m", period="60d"), "1h": h1, "1d": daily_from_hourly(h1)}


def gap_report(bars: pd.DataFrame, tf: str) -> pd.DataFrame:
    """Open-vs-previous-close jumps in ATR inside the holdout (contract rolls show up here)."""
    a = F.atr(bars["high"].to_numpy(), bars["low"].to_numpy(), bars["close"].to_numpy(), 14)
    jump = (bars["open"] - bars["close"].shift(1)).abs() / pd.Series(a).shift(1)
    out = bars.assign(gap_atr=jump)[["date", "open", "gap_atr"]]
    lo, hi = (pd.Timestamp(x) for x in HOLDOUT)
    out = out[(out["date"] >= lo) & (out["date"] <= hi)]
    return out.sort_values("gap_atr", ascending=False).head(5).reset_index(drop=True)


def frame(meta_path, bars: dict[str, pd.DataFrame]) -> tuple[Predictor, pd.DataFrame]:
    """Features + triple-barrier outcomes (same label set the model was trained on) + decisions."""
    pred = Predictor.load(meta_path)
    m = pred.meta
    hz = HORIZONS[m["horizon"]]
    ls = LABEL_SETS["ct25" if m["label"].get("counter_trend_tp_r") else "base"]
    htfs = {tf: bars[tf] for tf in m["context_timeframes"]}
    fr = F.build(bars[m["timeframe"]], m["timeframe"], htfs)
    fr = add_labels(fr, hz, ls)
    dec = pred.decide(fr)
    return pred, pd.concat([fr, dec], axis=1)


def holdout_rows(fr: pd.DataFrame) -> np.ndarray:
    lo, hi = (pd.Timestamp(x) for x in HOLDOUT)
    ok = (fr["date"] >= lo) & (fr["date"] <= hi) & fr["y_bull"].notna() & fr["y_bear"].notna()
    ok &= fr["no_entry"] == 0
    return np.flatnonzero(ok.to_numpy())


def backtest(fr: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """The model's trades and the naive baselines on the same rows and simulator."""
    from xau_ml.v2 import baseline_trades

    pos = holdout_rows(fr)
    out = {"model": S.run(fr, pos, fr["signal"].to_numpy()[pos])}
    for kind in ("always_long", "always_short", "sma_cross"):
        out[kind] = baseline_trades(fr, pos, kind)
    return out


def summary(results: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows = []
    for name, tr in results.items():
        mt = S.metrics(tr)
        mt["return_pct"] = float(100 * (tr["equity"].iloc[-1] - 1)) if len(tr) else 0.0
        rows.append({"strategy": name, **mt})
    return pd.DataFrame(rows).set_index("strategy")


def by_month(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    g = trades.groupby(pd.to_datetime(trades["decided"]).dt.to_period("M"))
    return pd.DataFrame({name: S.metrics(t.assign(equity=np.cumprod(1 + 0.01 * t["r"].to_numpy())))
                         for name, t in g}).T[["trades", "win_rate", "profit_factor", "expectancy_r", "total_r", "longs", "shorts"]]
