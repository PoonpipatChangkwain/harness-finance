"""Single place for every fixed choice of the XAUUSD bull/bear study.

Nothing here is tuned on the test window: splits and horizons are set before any model
is fitted, and the label rules mirror the harness simulator
(``backend/app/domain/compute/backtest/engine.py``) and horizons
(``backend/app/domain/models/horizon.py``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent  # Product/notebook
DATA_DIR = ROOT / "data"
MODEL_DIR = ROOT / "models"

SYMBOL = "XAUUSD"
VERSION = "xau-bullbear-0.1.0"

# --- time windows -------------------------------------------------------------------------
# The user's call (2026-10-04): only the 2025+ regime is relevant — before 2025 a 10-20 pip
# move in gold took hours. Older bars are loaded only as indicator warm-up, never as rows.
WARMUP_START = {"15m": "2024-10-01", "1h": "2024-06-01", "1d": "2023-06-01"}
TRAIN = ("2025-01-01", "2025-07-31 23:59")
VAL = ("2025-08-01", "2025-09-30 23:59")
TEST = ("2025-10-01", "2026-01-30 23:59")
# Aug-Sep 2026 is reserved for the live harness backtest API; nothing at/after this loads.
FORBIDDEN_FROM = pd.Timestamp("2026-08-01")

TF_DELTA = {"15m": pd.Timedelta(minutes=15), "1h": pd.Timedelta(hours=1), "1d": pd.Timedelta(days=1)}
BARS_PER_YEAR = {"15m": 23 * 4 * 5 * 52, "1h": 23 * 5 * 52}


@dataclass(frozen=True)
class Horizon:
    """One trading horizon of the system (P10/P15)."""

    name: str
    tf: str  # main bars
    sl_atr: float  # stop distance R = sl_atr x ATR14(tf)
    time_stop: int  # bars held after the entry bar (engine.py ``bars_held`` semantics)
    htf: tuple[str, ...]  # higher timeframes merged as context features


HORIZONS = {
    "intraday": Horizon("intraday", "15m", 1.5, 16, ("1h", "1d")),
    "swing": Horizon("swing", "1h", 2.0, 48, ("1d",)),
}


@dataclass(frozen=True)
class LabelSet:
    """Target R:R of a label set. ``ct_tp_r`` applies when the trade fades the daily trend
    (daily close on the trend's side of BOTH SMA50 and SMA200 — ``_counter_trend`` in
    workflows/trading.py); ``None`` = one TP for every trade."""

    name: str
    tp_r: float = 1.5
    ct_tp_r: float | None = None


LABEL_SETS = {
    "base": LabelSet("base", 1.5, None),
    "ct25": LabelSet("ct25", 1.5, 2.5),
}

# --- trading / evaluation -----------------------------------------------------------------
RISK_PCT = 0.01  # equity risked per trade (compounding) for equity curves / drawdown
COST_PRICE = 0.30  # round-trip spread+commission in USD per oz, charged in both simulators
INITIAL_CASH = 100_000.0


@dataclass(frozen=True)
class ModelParams:
    params: dict = field(
        default_factory=lambda: {
            "objective": "binary",
            "learning_rate": 0.03,
            "num_leaves": 15,
            "max_depth": 5,
            "min_child_samples": 150,
            "feature_fraction": 0.7,
            "bagging_fraction": 0.8,
            "bagging_freq": 1,
            "lambda_l2": 5.0,
            "verbose": -1,
            "seed": 42,
            "deterministic": True,
            "num_threads": 4,
        }
    )
    max_rounds: int = 1500
    early_stopping: int = 100
    inner_val_frac: float = 0.2  # tail of TRAIN used for early stopping (purged)


# --- v0.2 (expanding monthly walk-forward over all usable data; no clean holdout left) -----
VERSION_V2 = "xau-bullbear-0.2.0"
MODEL_DIR_V2 = MODEL_DIR / "v2"
WF2_FIRST_FOLD = "2025-04"  # first test month (Jan-Mar = initial training only)
WF2_LAST_FOLD = "2026-01"
WF2_BURN_IN = 2  # folds that only seed the nested calibrator/τ/m (Apr-May); trading scored from June
SEEN_FROM = "2025-10-01"  # v0.1's test window — already looked at, reported separately

TAU_GRID =[round(0.30 + 0.025 * i, 3) for i in range(17)]  # 0.30 … 0.70
MARGIN_GRID = [0.0, 0.05, 0.10, 0.15, 0.20]
MIN_VAL_TRADES = {"15m": 40, "1h": 15}
