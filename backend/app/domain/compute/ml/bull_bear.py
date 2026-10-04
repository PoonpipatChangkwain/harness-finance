"""Deterministic bull/bear model signal (F1): bars in, calibrated probabilities out.

The models are trained in ``notebook/`` (``xau_bull_bear_ml_v2.ipynb``) and copied here as
LightGBM text files + a metadata JSON (features in order, label rules, Platt calibrators,
the τ/m signal rule, file hashes). Each model answers, at the close of the decision bar:

- ``p_bull``: P(a long entered at the next bar's open reaches +TP before -1R), and
- ``p_bear``: the same for a short,

with R = 1.5 x ATR14 of the main bars, TP = 1.5R (2.5R against the daily SMA50/200 trend)
and a 16-bar time stop — the trading workflow's own intraday rules. The LLMs get these
numbers as evidence; they never compute them.

Only horizons with a validated model are served: the v0.2 swing model was dropped (its
calibrator is flat — it says "long" on nearly every bar, no better than always-long).
"""

from __future__ import annotations

import hashlib
import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd

from app.domain.compute.base import ComputeResult, compute_record
from app.domain.compute.ml.features import DAY, bars_frame, build
from app.domain.models.market import OHLCVBar

MODEL_DIR = Path(__file__).resolve().parent / "models"
# (canonical symbol, horizon) → model stem in MODEL_DIR
REGISTRY: dict[tuple[str, str], str] = {("XAUUSD", "intraday"): "xau_intraday_ct25"}
STEP = {"15m": pd.Timedelta(minutes=15), "1h": pd.Timedelta(hours=1)}
MIN_MAIN_BARS = 300  # ATR regime (SMA96 of ATR14) + ADX warm-up
MIN_DAILY_BARS = 230  # SMA200 + ATR14


def model_for(symbol: str, horizon: str) -> str | None:
    return REGISTRY.get((symbol, horizon))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


@lru_cache(maxsize=4)
def load(stem: str) -> tuple[dict[str, Any], dict[str, lgb.Booster]]:
    meta: dict[str, Any] = json.loads((MODEL_DIR / f"{stem}.json").read_text(encoding="utf-8"))
    boosters: dict[str, lgb.Booster] = {}
    for side, f in meta["files"].items():
        path = MODEL_DIR / f["file"]
        if _sha(path) != f["sha256_16"]:
            raise ValueError(f"{path.name} does not match the hash in {stem}.json")
        boosters[side] = lgb.Booster(model_file=str(path))
    return meta, boosters


def _platt(cal: dict[str, Any], p: float) -> float:
    q = min(max(p, 1e-6), 1 - 1e-6)
    z = float(cal["a"]) * math.log(q / (1 - q)) + float(cal["b"])
    return 1 / (1 + math.exp(-z))


def _signal(p_bull: float, p_bear: float, tau: float, m: float) -> str:
    if p_bull >= tau and p_bull - p_bear >= m and p_bull > p_bear:
        return "long"
    if p_bear >= tau and p_bear - p_bull >= m and p_bear > p_bull:
        return "short"
    return "flat"


def _track_record(meta: dict[str, Any]) -> dict[str, Any]:
    """The walk-forward result the notebook reported (2025-06..2026-01): model vs always-long."""
    rows = {r["strategy"]: r for r in (meta.get("metrics") or {}).get("walkforward_trading") or []}
    model = rows.get(meta.get("experiment", {}).get("id", ""), {})
    long_ = rows.get("always_long", {})
    keys = ("trades", "win_rate", "profit_factor", "expectancy_r", "long_share")
    return {
        "period": "walk-forward 2025-06..2026-01",
        "model": {k: model.get(k) for k in keys},
        "always_long": {k: long_.get(k) for k in keys},
    }


def feature_frame(stem: str, main: list[OHLCVBar], daily: list[OHLCVBar]) -> pd.DataFrame:
    meta, _ = load(stem)
    tf = meta["timeframe"]
    return build(bars_frame(main, STEP[tf]), bars_frame(daily, DAY, daily=True))


def predict(
    stem: str, main: list[OHLCVBar], daily: list[OHLCVBar], inputs: list[str] | None = None
) -> ComputeResult[dict[str, Any]]:
    """Probabilities at the LAST bar of ``main`` (the caller cuts bars at the decision time)."""
    meta, boosters = load(stem)
    if len(main) < MIN_MAIN_BARS or len(daily) < MIN_DAILY_BARS:
        raise ValueError(
            f"{stem} needs ≥{MIN_MAIN_BARS} {meta['timeframe']} and ≥{MIN_DAILY_BARS} daily bars "
            f"(got {len(main)} / {len(daily)})"
        )
    frame = feature_frame(stem, main, daily)
    x = frame[meta["features"]].iloc[[-1]].astype("float64")
    missing = [c for c in meta["features"] if np.isnan(x[c].iloc[0])]
    raw = {side: float(np.asarray(boosters[side].predict(x))[0]) for side in ("bull", "bear")}
    p = {side: _platt(meta["calibrators"][side], raw[side]) for side in ("bull", "bear")}
    rule = meta["signal_rule"]
    label = meta["label"]
    trend = float(x["d1_trend"].iloc[0])
    value: dict[str, Any] = {
        "model": stem,
        "version": meta["version"],
        "horizon": meta["horizon"],
        "timeframe": meta["timeframe"],
        "bar_ts": main[-1].ts.isoformat(),
        "p_bull": round(p["bull"], 4),
        "p_bear": round(p["bear"], 4),
        "signal": _signal(p["bull"], p["bear"], float(rule["tau"]), float(rule["m"])),
        "tau": rule["tau"],
        "margin": rule["m"],
        "stop": label["risk"],
        "tp_r_long": label["counter_trend_tp_r"] if trend < 0 else label["tp_r"],
        "tp_r_short": label["counter_trend_tp_r"] if trend > 0 else label["tp_r"],
        "time_stop_bars": label["time_stop_bars"],
        "daily_trend": {1.0: "up", -1.0: "down"}.get(trend, "mixed"),
        "missing_features": missing,
        "track_record": _track_record(meta),
    }
    record = compute_record(
        f"compute.ml.bull_bear@{meta['version']}",
        {"p_bull": value["p_bull"], "p_bear": value["p_bear"], "signal": value["signal"]},
        params={
            "model": stem,
            "bar_ts": value["bar_ts"],
            "files": {k: v["sha256_16"] for k, v in meta["files"].items()},
        },
        inputs=inputs,
    )
    return ComputeResult(value=value, records=[record])
