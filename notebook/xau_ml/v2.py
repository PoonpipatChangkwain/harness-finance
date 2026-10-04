"""v0.2 experiments: a FIXED list, run under the same expanding monthly walk-forward.

Nothing here looks at a fold's own outcomes before predicting it:
* models are fitted on rows before the fold (``cv.monthly_folds``);
* the calibrator and τ/m that trade fold k come only from the out-of-fold predictions of
  folds < k ("nested"), so the trading result of every month is genuinely out of sample.
"""

from __future__ import annotations

from dataclasses import dataclass

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss, roc_auc_score

from xau_ml import sim as S
from xau_ml.config import MARGIN_GRID, TAU_GRID, Horizon, ModelParams
from xau_ml.cv import Fold, uniqueness
from xau_ml.model import Calibrator, fit_lgbm

SIDES = ("bull", "bear")
EXTRA_COLS = ["day_range_atr", "day_pos", "dist_pdh_atr", "dist_pdl_atr", "dist_pwh_atr", "dist_pwl_atr", "session"]
REDUCED_BASE = ["atr_regime", "atr_pct", "rv_32", "bb_width", "adx14", "hour", "dow"]


@dataclass(frozen=True)
class Experiment:
    id: str
    name: str
    model: str  # "lgbm" | "logit" | "lgbm3"
    weights: bool
    features: str  # "full" | "reduced" | "extras"


EXPERIMENTS = [
    Experiment("E0", "v0.1 setup (LightGBM pair, no weights)", "lgbm", False, "full"),
    Experiment("E1", "LightGBM pair + uniqueness weights", "lgbm", True, "full"),
    Experiment("E2", "Logistic regression pair + weights", "logit", True, "full"),
    Experiment("E3", "LightGBM pair + weights, reduced features", "lgbm", True, "reduced"),
    Experiment("E4", "LightGBM 3-class (long/short/none) + weights", "lgbm3", True, "full"),
    Experiment("E5", "LightGBM pair + weights + level/session features", "lgbm", True, "extras"),
]


def columns(exp: Experiment, all_cols: list[str]) -> list[str]:
    base = [c for c in all_cols if c not in EXTRA_COLS]
    if exp.features == "full":
        return base
    if exp.features == "reduced":
        return [c for c in base if c.startswith("d1_") or c in REDUCED_BASE]
    return base + EXTRA_COLS


# --- models -------------------------------------------------------------------------------


class LogitPair:
    """Median-impute + standardize (both fitted on the fold's TRAIN rows) + L2 logistic."""

    def __init__(self, C: float = 0.05):
        self.C = C
        self.sides: dict[str, dict] = {}

    def fit(self, X: pd.DataFrame, ys: dict[str, np.ndarray], ws: dict[str, np.ndarray | None]) -> LogitPair:
        med = X.median()
        Xf = X.fillna(med)
        mean, scale = Xf.mean(), Xf.std(ddof=0).replace(0, 1.0)
        Z = ((Xf - mean) / scale).to_numpy()
        for s in SIDES:
            lr = LogisticRegression(C=self.C, max_iter=3000).fit(Z, ys[s], sample_weight=ws[s])
            self.sides[s] = {"median": med.tolist(), "mean": mean.tolist(), "scale": scale.tolist(),
                             "coef": lr.coef_[0].tolist(), "intercept": float(lr.intercept_[0]), "cols": list(X.columns)}
        return self

    @staticmethod
    def proba(p: dict, X: pd.DataFrame) -> np.ndarray:
        Xf = X[p["cols"]].fillna(pd.Series(p["median"], index=p["cols"]))
        Z = (Xf.to_numpy() - np.array(p["mean"])) / np.array(p["scale"])
        return 1 / (1 + np.exp(-(Z @ np.array(p["coef"]) + p["intercept"])))

    def predict(self, X: pd.DataFrame) -> dict[str, np.ndarray]:
        return {s: self.proba(self.sides[s], X) for s in SIDES}


def fit_model(exp: Experiment, fr: pd.DataFrame, rows: np.ndarray, cols: list[str], hz: Horizon, mp: ModelParams):
    """Returns ``(model, predict_fn)``; ``predict_fn(X) -> {"bull": p, "bear": p}`` (raw)."""
    X = fr.iloc[rows][cols]
    ys = {s: fr[f"y_{s}"].to_numpy()[rows].astype(int) for s in SIDES}
    ws = {s: uniqueness(fr, rows, f"exit_{s}") if exp.weights else None for s in SIDES}
    if exp.model == "lgbm":
        boosters = {s: fit_lgbm(X, ys[s], hz, mp, w=ws[s]) for s in SIDES}
        return boosters, lambda Z: {s: boosters[s].predict(Z[cols]) for s in SIDES}
    if exp.model == "logit":
        m = LogitPair().fit(X, ys, ws)
        return m, m.predict
    if exp.model == "lgbm3":
        assert not (ys["bull"] & ys["bear"]).any(), "long and short cannot both win from one entry"
        y3 = ys["bull"] + 2 * ys["bear"]  # 0 none · 1 long wins · 2 short wins
        exit_max = np.maximum(fr["exit_bull"].to_numpy(), fr["exit_bear"].to_numpy())
        w = None
        if exp.weights:
            tmp = fr[["exit_bull"]].assign(exit_max=exit_max)
            w = uniqueness(tmp, rows, "exit_max")
        b = fit_lgbm(X, y3, hz, mp, w=w, extra_params={"objective": "multiclass", "num_class": 3, "metric": "multi_logloss"})

        def pred(Z: pd.DataFrame) -> dict[str, np.ndarray]:
            p = b.predict(Z[cols])
            return {"bull": p[:, 1], "bear": p[:, 2]}

        return b, pred
    raise ValueError(exp.model)


# --- walk-forward ---------------------------------------------------------------------------


def run_oof(exp: Experiment, fr: pd.DataFrame, folds: list[Fold], cols: list[str], hz: Horizon,
            mp: ModelParams | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Out-of-fold raw probabilities for every test row + per-fold ML metrics."""
    mp = mp or ModelParams()
    parts, rows = [], []
    for f in folds:
        _, predict = fit_model(exp, fr, f.train, cols, hz, mp)
        p = predict(fr.iloc[f.test])
        parts.append(pd.DataFrame({"pos": f.test, "fold": f.name, "p_bull_raw": p["bull"], "p_bear_raw": p["bear"]}))
        rec = {"fold": f.name, "train_rows": len(f.train), "test_rows": len(f.test)}
        for s in SIDES:
            y = fr[f"y_{s}"].to_numpy()[f.test]
            rec[f"auc_{s}"] = roc_auc_score(y, p[s]) if 0 < y.mean() < 1 else np.nan
            rec[f"logloss_{s}"] = log_loss(y, np.clip(p[s], 1e-6, 1 - 1e-6), labels=[0, 1])
            rec[f"logloss_clim_{s}"] = log_loss(y, np.full(len(y), fr[f"y_{s}"].to_numpy()[f.train].mean()), labels=[0, 1])
        rows.append(rec)
    return pd.concat(parts, ignore_index=True), pd.DataFrame(rows)


def nested_trading(fr: pd.DataFrame, oof: pd.DataFrame, fold_names: list[str], burn_in: int, min_trades: int):
    """Trade fold k with a Platt calibrator and τ/m fitted on the OOF rows of folds < k only."""
    sig = np.zeros(len(oof), dtype="int8")
    cal = pd.DataFrame(np.nan, index=oof.index, columns=["p_bull", "p_bear"], dtype="float64")
    log = []
    for k, name in enumerate(fold_names):
        cur = (oof["fold"] == name).to_numpy()
        prior = oof["fold"].isin(fold_names[:k]).to_numpy()
        if k < burn_in or not prior.any():
            continue
        pp = oof[prior]
        cals = {s: Calibrator.fit("platt", pp[f"p_{s}_raw"].to_numpy(), fr[f"y_{s}"].to_numpy()[pp["pos"]]) for s in SIDES}
        pb_prior, pr_prior = (cals[s](pp[f"p_{s}_raw"].to_numpy()) for s in SIDES)
        _, best = S.select_tau_m(fr, pp["pos"].to_numpy(), pb_prior, pr_prior, TAU_GRID, MARGIN_GRID, min_trades)
        pb, pr = (cals[s](oof.loc[cur, f"p_{s}_raw"].to_numpy()) for s in SIDES)
        cal.loc[cur, "p_bull"], cal.loc[cur, "p_bear"] = pb, pr
        if best is not None:
            sig[cur] = S.signals(pb, pr, best["tau"], best["m"])
        log.append({"fold": name, "tau": best["tau"] if best else None, "m": best["m"] if best else None,
                    "trades_prior_at_choice": best["trades"] if best else 0,
                    "signals": int((sig[cur] != 0).sum()), "long_signals": int((sig[cur] == 1).sum())})
    scored = oof["fold"].isin(fold_names[burn_in:]).to_numpy()
    trades = S.run(fr, oof["pos"].to_numpy()[scored], sig[scored])
    return trades, pd.DataFrame(log), cal, sig, scored


def baseline_trades(fr: pd.DataFrame, pos: np.ndarray, kind: str) -> pd.DataFrame:
    n = len(pos)
    if kind == "always_long":
        s = np.ones(n, dtype="int8")
    elif kind == "always_short":
        s = -np.ones(n, dtype="int8")
    else:  # sma20/50 cross on the main bars, read at the decision bar
        from xau_ml.features import sma

        c = fr["close"].to_numpy()
        s20, s50 = sma(c, 20), sma(c, 50)
        up = (s20 > s50) & (np.r_[np.nan, s20[:-1]] <= np.r_[np.nan, s50[:-1]])
        dn = (s20 < s50) & (np.r_[np.nan, s20[:-1]] >= np.r_[np.nan, s50[:-1]])
        s = np.where(up, 1, np.where(dn, -1, 0)).astype("int8")[pos]
    return S.run(fr, pos, s)


def lgb_version() -> str:
    return lgb.__version__
