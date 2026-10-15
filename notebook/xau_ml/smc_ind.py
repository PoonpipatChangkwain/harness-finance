"""MACD (ChrisMoody, SMA signal) + TradingView RSI (MA, divergence) + SMC features for the ML.

Every number comes from the backend's own compute (``backend/app/domain/compute``) so the
research uses exactly what the trading desk sees — no second implementation:

* ``indicators.macd`` (signal = SMA9), ``indicators.rsi``/``rsi_ma``/``divergence_events``;
* ``smc.smc_feature_series`` — point-in-time LuxAlgo-port state on every bar (one pass);
* ``smc_setup`` rules (sweep continuation + swing trend + momentum) re-applied per bar.

All features are causal: row ``t`` uses bars ``<= t`` (``divergence_events`` gives the bar a
divergence becomes known; the SMC pass is bar-by-bar). The notebook re-checks this by
truncation.
"""

from __future__ import annotations

import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd

BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domain.compute import indicators as ind  # noqa: E402
from app.domain.compute.smc import smc_feature_series  # noqa: E402
from app.domain.compute.smc_setup import DIV_RECENT, MAX_AGE  # noqa: E402
from app.domain.models.market import OHLCVBar  # noqa: E402

DIV_CAP = 60  # bars-since-divergence feature cap

IND_COLS = [
    "macd2_hist_atr",  # MACD - SMA9(MACD), in ATR
    "macd2_hist_up",  # +1 histogram rising vs the previous bar, -1 falling (CM_MacD colour)
    "macd2_above0",  # +1 histogram above zero, -1 below
    "macd2_cross",  # +1 bullish MACD/signal cross on this bar, -1 bearish, 0 none
    "rsi_minus_ma",  # RSI14 - SMA14(RSI14)
    "div_bull_since",  # bars since a regular bullish RSI divergence became known (cap 60, nan none)
    "div_bear_since",
]


def _bars(df: pd.DataFrame) -> list[OHLCVBar]:
    return [
        OHLCVBar(
            ts=datetime.fromisoformat(str(t)),
            open=Decimal(repr(o)),
            high=Decimal(repr(h)),
            low=Decimal(repr(lo)),
            close=Decimal(repr(c)),
            volume=0,
        )
        for t, o, h, lo, c in zip(df["date"], df["open"], df["high"], df["low"], df["close"], strict=True)
    ]


def indicator_features(df: pd.DataFrame, atr14: np.ndarray) -> pd.DataFrame:
    """``df`` = contiguous main bars (sorted); ``atr14`` aligned with it."""
    c = df["close"].tolist()
    h = df["high"].tolist()
    lo = df["low"].tolist()
    m = ind.macd(c).value
    hist = np.array([np.nan if v is None else v for v in m["hist"]])
    line = np.array([np.nan if v is None else v for v in m["macd"]])
    sig = np.array([np.nan if v is None else v for v in m["signal"]])
    r = ind.rsi(c, 14).value
    rma = ind.rsi_ma(r, 14)
    rr = np.array([np.nan if v is None else v for v in r])
    rm = np.array([np.nan if v is None else v for v in rma])

    prev_hist = np.r_[np.nan, hist[:-1]]
    diff, prev_diff = line - sig, np.r_[np.nan, (line - sig)[:-1]]
    cross = np.where((diff > 0) & (prev_diff <= 0), 1.0, np.where((diff < 0) & (prev_diff >= 0), -1.0, 0.0))
    cross[np.isnan(diff) | np.isnan(prev_diff)] = 0.0

    n = len(df)
    since = {"bullish": np.full(n, np.nan), "bearish": np.full(n, np.nan)}
    for kind in since:
        known = np.full(n, -1)
        for e in ind.divergence_events(r, h, lo):
            if e["kind"] == kind and e["known_at"] < n:
                known[e["known_at"]] = e["known_at"]
        last = pd.Series(np.where(known >= 0, np.arange(n), np.nan)).ffill().to_numpy()
        s = np.arange(n) - last
        since[kind] = np.where(np.isnan(s), np.nan, np.minimum(s, DIV_CAP))

    with np.errstate(invalid="ignore", divide="ignore"):
        out = pd.DataFrame(
            {
                "macd2_hist_atr": hist / atr14,
                "macd2_hist_up": np.where(np.isnan(prev_hist) | np.isnan(hist), np.nan, np.sign(hist - prev_hist)),
                "macd2_above0": np.where(np.isnan(hist), np.nan, np.sign(hist)),
                "macd2_cross": cross,
                "rsi_minus_ma": rr - rm,
                "div_bull_since": since["bullish"],
                "div_bear_since": since["bearish"],
            },
            index=df.index,
        )
    return out


def smc_features(df: pd.DataFrame) -> pd.DataFrame:
    rows = smc_feature_series(_bars(df)).value
    out = pd.DataFrame(rows, index=df.index)
    return out.astype("float64")


SMC_COLS = [
    "smc_swing_trend",
    "smc_internal_trend",
    "smc_pd_pos",
    "smc_bull_ob_dist",
    "smc_bear_ob_dist",
    "smc_bull_fvg_dist",
    "smc_bear_fvg_dist",
    "smc_swing_break_since",
    "smc_swing_break_bias",
    "smc_swing_break_choch",
    "smc_internal_break_since",
    "smc_internal_break_bias",
    "smc_cont_sweep_since",
    "smc_cont_sweep_side",
    "smc_cont_sweep_with_trend",
    "smc_rev_sweep_since",
    "smc_rev_sweep_side",
]


# --- the desk's rules, per bar ---------------------------------------------------------------


def momentum_side(feat: pd.DataFrame) -> np.ndarray:
    """+1 where the long momentum rule holds, -1 where the short one does, 0 neither
    (``smc_setup.momentum``: MACD histogram rising/falling or a cross, AND RSI vs its MA or a
    divergence known within ``DIV_RECENT`` bars). Both cannot hold on one bar."""
    up, cross, rmm = feat["macd2_hist_up"], feat["macd2_cross"], feat["rsi_minus_ma"]
    long_ = ((up > 0) | (cross > 0)) & ((rmm > 0) | (feat["div_bull_since"] <= DIV_RECENT))
    short = ((up < 0) | (cross < 0)) & ((rmm < 0) | (feat["div_bear_since"] <= DIV_RECENT))
    return np.where(long_ & ~short, 1, np.where(short & ~long_, -1, 0)).astype("int8")


def setup_side(feat: pd.DataFrame, df: pd.DataFrame, max_age: int = MAX_AGE) -> np.ndarray:
    """+1/-1 where the desk's sweep-continuation setup is active on that bar, else 0.

    Same checks as ``smc_setup.sweep_continuation_setup`` on the NEWEST continuation sweep
    (the backend also falls back to an older active one inside the window — rare):
    within ``max_age`` bars, with the swing trend at the sweep and now, momentum on the
    sweep bar, no close beyond the sweeping bar's extreme since.
    """
    n = len(feat)
    since = feat["smc_cont_sweep_since"].to_numpy()
    side = feat["smc_cont_sweep_side"].to_numpy()
    with_trend = feat["smc_cont_sweep_with_trend"].to_numpy()
    trend = feat["smc_swing_trend"].to_numpy()
    mom = momentum_side(feat)
    c, h, lo = df["close"].to_numpy(), df["high"].to_numpy(), df["low"].to_numpy()
    out = np.zeros(n, dtype="int8")
    for t in range(n):
        s = since[t]
        if np.isnan(s) or s > max_age or side[t] == 0 or with_trend[t] != 1 or trend[t] != side[t]:
            continue
        j = t - int(s)
        if mom[j] != side[t]:
            continue
        after = c[j + 1 : t + 1]
        if (side[t] > 0 and (after < lo[j]).any()) or (side[t] < 0 and (after > h[j]).any()):
            continue
        out[t] = side[t]
    return out
