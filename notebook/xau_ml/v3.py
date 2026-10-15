"""v0.3 experiments: news/calendar + VIX/DXY/yield + session features, training from 2024.

Changes from v0.2:
  * Training window starts 2024-01-01 (was 2025-01-01) — a full year more context
    including noise, outliers, and regime diversity.
  * New feature groups:
    - Economic calendar (NFP, FOMC, CPI — deterministic, no API call)
    - Market context (VIX, DXY, US10Y — daily from yfinance, cached)
    - Trading session (Asia 0, London 1, New York 2 — from server hour)
      already in v0.2's ``session`` via add_extras; now promoted to the reduced set.
  * Experiments test whether calendar/market features improve on E3's reduced set.
  * Walk-forward starts earlier (2024-04) to use the extra training data.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.metrics import log_loss, roc_auc_score

from xau_ml import sim as S
from xau_ml.calendar import CALENDAR_COLS, add_calendar_features
from xau_ml.config import MARGIN_GRID, TAU_GRID, Horizon, ModelParams
from xau_ml.cv import Fold, uniqueness
from xau_ml.market_data import MARKET_COLS, add_market_features
from xau_ml.model import Calibrator, fit_lgbm
from xau_ml.v2 import EXTRA_COLS, REDUCED_BASE, SIDES, LogitPair

# v0.2's reduced set (the winner) is the baseline for v0.3 experiments
V3_SESSION_COLS = ["session"]  # already in EXTRA_COLS, promoted to reduced

# New feature groups
V3_CALENDAR = CALENDAR_COLS
V3_MARKET = MARKET_COLS

# v0.3 reduced = v0.2 reduced + session + calendar + market
V3_REDUCED_PLUS = REDUCED_BASE + V3_SESSION_COLS + V3_CALENDAR + V3_MARKET


@dataclass(frozen=True)
class Experiment:
    id: str
    name: str
    model: str  # "lgbm"
    weights: bool
    features: str  # "v2_reduced" | "v3_reduced_cal" | "v3_reduced_market" | "v3_reduced_all" | "v3_full"


EXPERIMENTS = [
    Experiment("F0", "v0.2 E3 baseline (reduced 20, weights)", "lgbm", True, "v2_reduced"),
    Experiment("F1", "v0.2 E3 + session", "lgbm", True, "v3_reduced_session"),
    Experiment("F2", "v0.2 E3 + calendar features", "lgbm", True, "v3_reduced_cal"),
    Experiment("F3", "v0.2 E3 + market features (VIX/DXY/US10Y)", "lgbm", True, "v3_reduced_market"),
    Experiment("F4", "v0.2 E3 + calendar + market + session", "lgbm", True, "v3_reduced_all"),
    Experiment("F5", "full features + calendar + market + session", "lgbm", True, "v3_full"),
]


def columns(exp: Experiment, all_cols: list[str]) -> list[str]:
    """Select feature columns for an experiment."""
    base_reduced = [c for c in all_cols if c.startswith("d1_") or c in REDUCED_BASE]

    if exp.features == "v2_reduced":
        return base_reduced
    if exp.features == "v3_reduced_session":
        return base_reduced + [c for c in V3_SESSION_COLS if c in all_cols]
    if exp.features == "v3_reduced_cal":
        return base_reduced + [c for c in V3_CALENDAR if c in all_cols]
    if exp.features == "v3_reduced_market":
        return base_reduced + [c for c in V3_MARKET if c in all_cols]
    if exp.features == "v3_reduced_all":
        return base_reduced + [c for c in (V3_SESSION_COLS + V3_CALENDAR + V3_MARKET) if c in all_cols]
    if exp.features == "v3_full":
        # All available features including calendar + market
        # (all_cols already includes calendar/market from add_v3_features;
        # just exclude the v0.2 extras that didn't help, except session)
        exclude = set(EXTRA_COLS) - {"session"}
        return [c for c in all_cols if c not in exclude]
    raise ValueError(exp.features)


def add_v3_features(frame: pd.DataFrame, d1_raw: pd.DataFrame | None = None,
                    use_cache: bool = True) -> pd.DataFrame:
    """Add calendar + market + session features to a frame that already has v0.2 features.

    Session is already in v0.2 (``features.add_extras``); this adds calendar and market.
    """
    out = add_calendar_features(frame)
    out = add_market_features(out, use_cache=use_cache)
    return out


# --- models (same as v0.2, LightGBM pair only) -------------------------------------------


def fit_model(exp: Experiment, fr: pd.DataFrame, rows: np.ndarray, cols: list[str],
              hz: Horizon, mp: ModelParams):
    """Returns ``(model, predict_fn)``; ``predict_fn(X) -> {"bull": p, "bear": p}`` (raw)."""
    X = fr.iloc[rows][cols]
    ys = {s: fr[f"y_{s}"].to_numpy()[rows].astype(int) for s in SIDES}
    ws = {s: uniqueness(fr, rows, f"exit_{s}") if exp.weights else None for s in SIDES}
    boosters = {s: fit_lgbm(X, ys[s], hz, mp, w=ws[s]) for s in SIDES}
    return boosters, lambda Z: {s: boosters[s].predict(Z[cols]) for s in SIDES}


# --- walk-forward (same protocol as v0.2) -------------------------------------------------


def run_oof(exp: Experiment, fr: pd.DataFrame, folds: list[Fold], cols: list[str],
            hz: Horizon, mp: ModelParams | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Out-of-fold raw probabilities for every test row + per-fold ML metrics."""
    mp = mp or ModelParams()
    parts, rows = [], []
    for f in folds:
        _, predict = fit_model(exp, fr, f.train, cols, hz, mp)
        p = predict(fr.iloc[f.test])
        parts.append(pd.DataFrame({
            "pos": f.test, "fold": f.name,
            "p_bull_raw": p["bull"], "p_bear_raw": p["bear"],
        }))
        rec = {"fold": f.name, "train_rows": len(f.train), "test_rows": len(f.test)}
        for s in SIDES:
            y = fr[f"y_{s}"].to_numpy()[f.test]
            rec[f"auc_{s}"] = roc_auc_score(y, p[s]) if 0 < y.mean() < 1 else np.nan
            rec[f"logloss_{s}"] = log_loss(y, np.clip(p[s], 1e-6, 1 - 1e-6), labels=[0, 1])
            rec[f"logloss_clim_{s}"] = log_loss(
                y, np.full(len(y), fr[f"y_{s}"].to_numpy()[f.train].mean()), labels=[0, 1])
        rows.append(rec)
    return pd.concat(parts, ignore_index=True), pd.DataFrame(rows)


def nested_trading(fr: pd.DataFrame, oof: pd.DataFrame, fold_names: list[str],
                   burn_in: int, min_trades: int):
    """Trade fold k with a Platt calibrator and tau/m fitted on the OOF rows of folds < k only."""
    sig = np.zeros(len(oof), dtype="int8")
    cal = pd.DataFrame(np.nan, index=oof.index, columns=["p_bull", "p_bear"], dtype="float64")
    log = []
    for k, name in enumerate(fold_names):
        cur = (oof["fold"] == name).to_numpy()
        prior = oof["fold"].isin(fold_names[:k]).to_numpy()
        if k < burn_in or not prior.any():
            continue
        pp = oof[prior]
        cals = {s: Calibrator.fit("platt", pp[f"p_{s}_raw"].to_numpy(),
                                  fr[f"y_{s}"].to_numpy()[pp["pos"]])
                for s in SIDES}
        pb_prior, pr_prior = (cals[s](pp[f"p_{s}_raw"].to_numpy()) for s in SIDES)
        _, best = S.select_tau_m(fr, pp["pos"].to_numpy(), pb_prior, pr_prior,
                                 TAU_GRID, MARGIN_GRID, min_trades)
        pb, pr = (cals[s](oof.loc[cur, f"p_{s}_raw"].to_numpy()) for s in SIDES)
        cal.loc[cur, "p_bull"], cal.loc[cur, "p_bear"] = pb, pr
        if best is not None:
            sig[cur] = S.signals(pb, pr, best["tau"], best["m"])
        log.append({
            "fold": name,
            "tau": best["tau"] if best else None,
            "m": best["m"] if best else None,
            "trades_prior_at_choice": best["trades"] if best else 0,
            "signals": int((sig[cur] != 0).sum()),
            "long_signals": int((sig[cur] == 1).sum()),
        })
    scored = oof["fold"].isin(fold_names[burn_in:]).to_numpy()
    trades = S.run(fr, oof["pos"].to_numpy()[scored], sig[scored])
    return trades, pd.DataFrame(log), cal, sig, scored


def baseline_trades(fr: pd.DataFrame, pos: np.ndarray, kind: str) -> pd.DataFrame:
    from xau_ml.v2 import baseline_trades as _bt
    return _bt(fr, pos, kind)
