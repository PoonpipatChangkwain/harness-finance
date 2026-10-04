"""v0.2 evaluation protocol: expanding walk-forward folds (one per calendar month), label
uniqueness weights, and bootstrap confidence intervals.

Every fold trains only on rows strictly before its month (purged so no training label
window reaches the fold) and is embargoed for ``time_stop`` rows at its start.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from xau_ml.config import Horizon


@dataclass(frozen=True)
class Fold:
    name: str  # "2025-06"
    train: np.ndarray  # frame positions
    test: np.ndarray  # frame positions


def usable(fr: pd.DataFrame) -> np.ndarray:
    """Rows with both labels and no data-hole guard."""
    return (fr["y_bull"].notna() & fr["y_bear"].notna() & (fr["no_entry"] == 0)).to_numpy()


def monthly_folds(fr: pd.DataFrame, hz: Horizon, first: str, last: str, train_from: str) -> list[Fold]:
    ok = usable(fr)
    dates = fr["date"]
    pos = np.arange(len(fr))
    folds = []
    for month in pd.period_range(first, last, freq="M"):
        in_month = ((dates >= month.start_time) & (dates <= month.end_time)).to_numpy()
        test = pos[in_month & ok]
        if len(test) == 0:
            continue
        first_pos = int(pos[in_month].min())
        test = test[test >= first_pos + hz.time_stop]  # embargo
        train = pos[ok & (dates >= pd.Timestamp(train_from)).to_numpy() & (pos + 1 + hz.time_stop < first_pos)]  # purge
        if len(test) and len(train):
            folds.append(Fold(str(month), train, test))
    return folds


def assert_folds(fr: pd.DataFrame, folds: list[Fold], hz: Horizon) -> None:
    for f in folds:
        assert (f.train + 1 + hz.time_stop).max() < f.test.min(), f"{f.name}: a train label reaches the test month"
        assert fr["date"].iloc[f.train].max() < fr["date"].iloc[f.test].min()


def uniqueness(fr: pd.DataFrame, rows: np.ndarray, exit_col: str) -> np.ndarray:
    """Average uniqueness of each row's label (López de Prado, AFML ch. 4).

    A label spans bars [t+1, t+1+exit]; concurrency c[j] counts the labels covering bar j;
    the weight is the mean of 1/c over the label's own bars. Overlapping labels — the norm
    with 16/48-bar windows on every bar — share their information instead of counting it
    once per row.
    """
    starts = rows + 1
    ends = rows + 1 + np.maximum(fr[exit_col].to_numpy()[rows].astype(int), 0)
    n = len(fr) + 2
    conc = np.zeros(n)
    np.add.at(conc, starts, 1)
    np.add.at(conc, ends + 1, -1)
    conc = np.cumsum(conc)
    inv = np.where(conc > 0, 1.0 / np.maximum(conc, 1), 0.0)
    csum = np.r_[0.0, np.cumsum(inv)]
    w = (csum[ends + 1] - csum[starts]) / (ends - starts + 1)
    return w / w.mean()


def bootstrap_mean_ci(x: np.ndarray, n: int = 5000, alpha: float = 0.10, seed: int = 0) -> tuple[float, float]:
    if len(x) < 2:
        return (np.nan, np.nan)
    rng = np.random.default_rng(seed)
    means = rng.choice(x, size=(n, len(x)), replace=True).mean(axis=1)
    return float(np.quantile(means, alpha / 2)), float(np.quantile(means, 1 - alpha / 2))


def bootstrap_diff_ci(a: np.ndarray, b: np.ndarray, n: int = 5000, alpha: float = 0.10, seed: int = 0) -> tuple[float, float]:
    """CI of mean(a) − mean(b) for two independent trade samples (iid bootstrap)."""
    if len(a) < 2 or len(b) < 2:
        return (np.nan, np.nan)
    rng = np.random.default_rng(seed)
    da = rng.choice(a, size=(n, len(a)), replace=True).mean(axis=1)
    db = rng.choice(b, size=(n, len(b)), replace=True).mean(axis=1)
    d = da - db
    return float(np.quantile(d, alpha / 2)), float(np.quantile(d, 1 - alpha / 2))
