"""Signal rule + a replica of the harness simulator's trade loop, in R.

The per-row outcome (``r_*``/``exit_*`` from ``labels.py``) already replays ``engine.py``
for one trade; this loop adds what the account does: one position at a time (a new
decision while a position is open is skipped, as ``already_open`` does), the round-trip
cost, and a compounding equity curve risking ``RISK_PCT`` per trade.

Differences from the live engine, stated so nobody over-reads the numbers: no reversal
on an opposite signal (the engine closes and re-opens), no lot rounding or margin.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from xau_ml.config import COST_PRICE, RISK_PCT


def signals(p_bull: np.ndarray, p_bear: np.ndarray, tau: float, margin: float) -> np.ndarray:
    """+1 long when P_bull ≥ τ and P_bull − P_bear ≥ m; −1 short mirrored; else 0."""
    long_ = (p_bull >= tau) & (p_bull - p_bear >= margin) & (p_bull > p_bear)
    short = (p_bear >= tau) & (p_bear - p_bull >= margin) & (p_bear > p_bull)
    return np.where(long_, 1, np.where(short, -1, 0)).astype("int8")


def run(
    fr: pd.DataFrame, rows_idx: np.ndarray, sig: np.ndarray, cost_price: float = COST_PRICE, risk_pct: float = RISK_PCT
) -> pd.DataFrame:
    """Trades taken by ``sig`` (aligned with ``rows_idx``, the positions of the allowed
    decision rows in the FULL contiguous frame ``fr``); exits are located in ``fr``'s bars."""
    r = {1: fr["r_bull"].to_numpy(), -1: fr["r_bear"].to_numpy()}
    off = {1: fr["exit_bull"].to_numpy(), -1: fr["exit_bear"].to_numpy()}
    risk = fr["risk"].to_numpy()
    dates = fr["date"].to_numpy()
    rows = []
    free_from = 0  # first frame position at which a new decision may open a trade
    for t, s in zip(np.asarray(rows_idx), np.asarray(sig)):
        s = int(s)
        if s == 0 or t < free_from or t + 1 >= len(fr) or np.isnan(r[s][t]):
            continue
        exit_idx = t + 1 + int(off[s][t])
        cost_r = cost_price / risk[t]
        rows.append(
            {
                "decided": dates[t],
                "entry": dates[t + 1],
                "exit": dates[min(exit_idx, len(fr) - 1)],
                "side": "long" if s == 1 else "short",
                "r_gross": r[s][t],
                "r": r[s][t] - cost_r,
                "bars": int(off[s][t]),
            }
        )
        free_from = exit_idx  # a decision at the exit bar's close may trade again
    trades = pd.DataFrame(rows, columns=["decided", "entry", "exit", "side", "r_gross", "r", "bars"])
    if not trades.empty:
        trades["equity"] = np.cumprod(1 + risk_pct * trades["r"].to_numpy())
    return trades


def metrics(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {"trades": 0, "win_rate": np.nan, "profit_factor": np.nan, "expectancy_r": np.nan,
                "total_r": 0.0, "max_dd_pct": 0.0, "long_share": np.nan, "longs": 0, "shorts": 0}
    r = trades["r"].to_numpy()
    wins, losses = r[r > 0].sum(), -r[r < 0].sum()
    eq = np.r_[1.0, trades["equity"].to_numpy()]
    dd = 1 - eq / np.maximum.accumulate(eq)
    longs = int((trades["side"] == "long").sum())
    return {
        "trades": len(r),
        "win_rate": float((r > 0).mean()),
        "profit_factor": float(wins / losses) if losses > 0 else np.inf,
        "expectancy_r": float(r.mean()),
        "total_r": float(r.sum()),
        "max_dd_pct": float(100 * dd.max()),
        "long_share": longs / len(r),
        "longs": longs,
        "shorts": len(r) - longs,
    }


def select_tau_m(
    fr: pd.DataFrame, rows_idx: np.ndarray, p_bull: np.ndarray, p_bear: np.ndarray, taus, margins, min_trades: int
) -> tuple[pd.DataFrame, dict | None]:
    """Grid on VALIDATION only. Score = Σr/√n (a t-stat proxy that rewards both edge and
    sample size); candidates need ≥ ``min_trades`` and PF > 1. None if nothing qualifies."""
    rows = []
    for tau in taus:
        for m in margins:
            mt = metrics(run(fr, rows_idx, signals(p_bull, p_bear, tau, m)))
            score = mt["total_r"] / np.sqrt(mt["trades"]) if mt["trades"] else -np.inf
            rows.append({"tau": tau, "m": m, **mt, "score": score})
    grid = pd.DataFrame(rows)
    ok = grid[(grid["trades"] >= min_trades) & (grid["profit_factor"] > 1)]
    best = None if ok.empty else ok.sort_values(["score", "tau"], ascending=[False, False]).iloc[0].to_dict()
    return grid, best
