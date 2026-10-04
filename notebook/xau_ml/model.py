"""Splits with purge/embargo, LightGBM training, probability calibration.

Every learned object is plain data so the backend can load it without pickles:
the booster as LightGBM's text format, the calibrator as numbers in JSON.
"""

from __future__ import annotations

from dataclasses import dataclass

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss

from xau_ml.config import TEST, TRAIN, VAL, Horizon, ModelParams


# --- splits -------------------------------------------------------------------------------


def split_masks(df: pd.DataFrame, hz: Horizon) -> dict[str, np.ndarray]:
    """Time splits on ``df`` rows (sorted, one row per bar).

    purge   — drop the last ``time_stop + 1`` rows of train and of val: their label
              window (entry bar + time_stop bars) reaches into the next split;
    embargo — drop the first ``time_stop`` rows of val and of test, so no bar inside a
              purged label window is ever scored either.
    Rows without a label (window past the data end) are dropped everywhere.
    """
    d = df["date"]
    gap = hz.time_stop + 1
    masks = {}
    for name, (a, b) in (("train", TRAIN), ("val", VAL), ("test", TEST)):
        m = np.array((d >= pd.Timestamp(a)) & (d <= pd.Timestamp(b)), dtype=bool)
        idx = np.flatnonzero(m)
        if name in ("train", "val"):
            m[idx[-gap:]] = False
        if name in ("val", "test"):
            m[idx[: hz.time_stop]] = False
        m &= df["y_bull"].notna().to_numpy() & df["y_bear"].notna().to_numpy()
        m &= df["no_entry"].to_numpy() == 0  # data holes (labels.data_holes)
        masks[name] = m
    return masks


def assert_disjoint(df: pd.DataFrame, masks: dict[str, np.ndarray], hz: Horizon) -> None:
    """No label window of an earlier split may touch a row of a later one."""
    pos = np.arange(len(df))
    for early, late in (("train", "val"), ("val", "test"), ("train", "test")):
        last_exit = (pos[masks[early]] + 1 + hz.time_stop).max()
        first_late = pos[masks[late]].min()
        assert last_exit < first_late, f"{early} labels overlap {late}"
        assert df["date"][masks[early]].max() < df["date"][masks[late]].min()


# --- training -----------------------------------------------------------------------------


def fit_lgbm(
    X: pd.DataFrame,
    y: np.ndarray,
    hz: Horizon,
    mp: ModelParams,
    device: str = "cpu",
    w: np.ndarray | None = None,
    extra_params: dict | None = None,
) -> lgb.Booster:
    """Early stopping on the time-ordered tail of the TRAIN rows, purged by the label length.
    ``w`` = optional sample weights (v0.2 uniqueness); ``extra_params`` e.g. multiclass."""
    n = len(X)
    cut = int(n * (1 - mp.inner_val_frac))
    fit_idx = np.arange(0, max(cut - hz.time_stop - 1, 1))
    es_idx = np.arange(cut, n)
    params = {**mp.params, "device_type": device, **(extra_params or {})}
    wf = None if w is None else w[fit_idx]
    we = None if w is None else w[es_idx]
    dtrain = lgb.Dataset(X.iloc[fit_idx], y[fit_idx], weight=wf, free_raw_data=False)
    dval = lgb.Dataset(X.iloc[es_idx], y[es_idx], weight=we, reference=dtrain)
    booster = lgb.train(
        params,
        dtrain,
        num_boost_round=mp.max_rounds,
        valid_sets=[dval],
        callbacks=[lgb.early_stopping(mp.early_stopping, verbose=False)],
    )
    # refit on all train rows with the round count found above (no peeking past TRAIN)
    best = max(booster.best_iteration, 20)
    return lgb.train(params, lgb.Dataset(X, y, weight=w), num_boost_round=best)


# --- calibration --------------------------------------------------------------------------


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


@dataclass
class Calibrator:
    kind: str  # "isotonic" | "platt"
    x: list[float] | None = None
    y: list[float] | None = None
    a: float | None = None
    b: float | None = None

    @classmethod
    def fit(cls, kind: str, p: np.ndarray, y: np.ndarray) -> Calibrator:
        if kind == "isotonic":
            iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0).fit(p, y)
            return cls("isotonic", x=iso.X_thresholds_.tolist(), y=iso.y_thresholds_.tolist())
        lr = LogisticRegression(C=1e6).fit(_logit(p).reshape(-1, 1), y)
        return cls("platt", a=float(lr.coef_[0, 0]), b=float(lr.intercept_[0]))

    def __call__(self, p: np.ndarray) -> np.ndarray:
        p = np.asarray(p, dtype="float64")
        if self.kind == "isotonic":
            return np.interp(p, self.x, self.y)
        return 1 / (1 + np.exp(-(self.a * _logit(p) + self.b)))

    def to_json(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}


def choose_calibrator(p: np.ndarray, y: np.ndarray) -> tuple[Calibrator, pd.DataFrame]:
    """Pick isotonic vs Platt by two-fold time cross-fit INSIDE validation, then refit the
    winner on all of validation. Test is never involved."""
    half = len(p) // 2
    folds = [(slice(0, half), slice(half, None)), (slice(half, None), slice(0, half))]
    rows = []
    for kind in ("isotonic", "platt"):
        briers = [brier_score_loss(y[ev], Calibrator.fit(kind, p[fi], y[fi])(p[ev])) for fi, ev in folds]
        rows.append({"kind": kind, "brier_crossfit": float(np.mean(briers))})
    rows.append({"kind": "raw", "brier_crossfit": float(brier_score_loss(y, p))})
    table = pd.DataFrame(rows)
    best = table[table["kind"] != "raw"].sort_values("brier_crossfit").iloc[0]["kind"]
    return Calibrator.fit(best, p, y), table


def precision_at(p: np.ndarray, y: np.ndarray, thresholds=(0.3, 0.35, 0.4, 0.45, 0.5, 0.55, 0.6)) -> pd.DataFrame:
    rows = []
    for t in thresholds:
        sel = p >= t
        rows.append({"threshold": t, "n": int(sel.sum()), "coverage": float(sel.mean()),
                     "precision": float(y[sel].mean()) if sel.any() else np.nan})
    return pd.DataFrame(rows)
