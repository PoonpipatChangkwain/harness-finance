"""Deterministic bull/bear model signal (F1): bars in, calibrated probabilities out.

The models are trained in ``notebook/`` and copied here as LightGBM text files + a metadata
JSON (features in order, label rules, Platt calibrators, signal rule, file hashes).

Two architectures are supported (selected by ``model.kind`` in the JSON):

**lgbm_pair** (v0.3): two binary classifiers (bull/bear). Signal via τ/m threshold rule.
**stacked_ensemble** (v0.5): two specialist classifiers (bull/bear with scale_pos_weight)
  + a 3-class meta-model that decides LONG/SHORT/FLAT from the specialist outputs plus
  market context (regime, VIX, ADX, session). The meta-model fixes the bull bias of v0.3
  by learning WHEN to go short from regime context.

Each model answers, at the close of the decision bar:
- ``p_bull``: P(a long entered at the next bar's open reaches +TP before -1R), and
- ``p_bear``: the same for a short,

with R = 1.5 x ATR14 of the main bars, TP = 1.5R (2.5R against the daily SMA50/200 trend)
and a 16-bar time stop (intraday) or 48-bar time stop (swing).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd

from app.domain.compute.base import ComputeResult, compute_record
from app.domain.compute.ml.features import (
    DAY,
    PACKET_FIELDS,
    add_packet_context,
    add_regime_features,
    add_v3_features,
    bars_frame,
    build,
    calendar_around,
)
from app.domain.models.market import OHLCVBar

MODEL_DIR = Path(__file__).resolve().parent / "models"
# (canonical symbol, horizon) → model stem in MODEL_DIR
REGISTRY: dict[tuple[str, str], str] = {
    ("XAUUSD", "intraday"): "xau_intraday_ct25",
    ("XAUUSD", "swing"): "xau_swing_ct25",
}
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


def _meta_signal(
    meta_booster: lgb.Booster,
    p_bull_raw: float,
    p_bear_raw: float,
    context: dict[str, float],
    meta_features: list[str],
    min_conf: float,
) -> tuple[str, float]:
    """Stacked ensemble: meta-model decides LONG/SHORT/FLAT from specialist outputs + context.

    Returns (signal, confidence) where confidence is the max class probability.
    """
    row: dict[str, float] = {
        "p_bull": p_bull_raw,
        "p_bear": p_bear_raw,
        "p_diff": p_bull_raw - p_bear_raw,
        "p_max": max(p_bull_raw, p_bear_raw),
        "p_sum": p_bull_raw + p_bear_raw,
        **context,
    }
    x = pd.DataFrame([{f: row.get(f, np.nan) for f in meta_features}])
    probs = np.asarray(meta_booster.predict(x))[0]  # [flat, long, short]
    pred_class = int(probs.argmax())
    max_prob = float(probs.max())
    if max_prob < min_conf:
        return "flat", max_prob
    return {0: "flat", 1: "long", 2: "short"}[pred_class], max_prob


def _atr_multiple(risk: str) -> float | None:
    """'2.0 x ATR14 (Wilder)' → 2.0: the label's stop distance, the model's own risk unit."""
    m = re.match(r"\s*([\d.]+)\s*x\s*ATR", risk, re.IGNORECASE)
    return float(m.group(1)) if m else None


def _track_record(meta: dict[str, Any]) -> dict[str, Any]:
    """The walk-forward result the notebook reported: model vs always-long."""
    metrics = meta.get("metrics") or {}
    rows = {r["strategy"]: r for r in metrics.get("walkforward_trading") or []}
    # v0.5 uses experiment names like "C: meta (conf>0.40)"; v0.3 uses "F3"
    exp_id = meta.get("experiment", {}).get("id", "")
    model = rows.get(exp_id, {})
    long_ = rows.get("always_long", {})
    keys = ("trades", "win_rate", "profit_factor", "expectancy_r", "long_share")
    period = metrics.get("period", "walk-forward 2025-06..2026-01")
    return {
        "period": period,
        "model": {k: model.get(k) for k in keys},
        "always_long": {k: long_.get(k) for k in keys},
    }


def feature_frame(
    stem: str,
    main: list[OHLCVBar],
    daily: list[OHLCVBar],
    market_daily: dict[str, pd.DataFrame] | None = None,
) -> pd.DataFrame:
    meta, _ = load(stem)
    tf = meta["timeframe"]
    daily_frame = bars_frame(daily, DAY, daily=True)
    frame = build(bars_frame(main, STEP[tf]), daily_frame)
    # v0.3: add market/calendar/session features if the model needs them
    groups = meta.get("feature_groups")
    if meta.get("v3_features") and groups:
        frame = add_v3_features(frame, groups=groups, market_daily=market_daily)
    # v0.5: add regime features if the model needs them
    if groups and groups.get("regime"):
        frame = add_regime_features(frame, daily_frame)
    return frame


def _is_nfp_day(frame: pd.DataFrame) -> bool:
    """Check if the last bar falls on an NFP day (first Friday of the month)."""
    if "cal_is_nfp_day" in frame.columns:
        return bool(frame["cal_is_nfp_day"].iloc[-1] > 0.5)
    dt = frame["date"].iloc[-1]
    return bool(dt.dayofweek == 4 and dt.day <= 7)


def predict(
    stem: str,
    main: list[OHLCVBar],
    daily: list[OHLCVBar],
    inputs: list[str] | None = None,
    market_daily: dict[str, pd.DataFrame] | None = None,
) -> ComputeResult[dict[str, Any]]:
    """Probabilities at the LAST bar of ``main`` (the caller cuts bars at the decision time)."""
    meta, boosters = load(stem)
    if len(main) < MIN_MAIN_BARS or len(daily) < MIN_DAILY_BARS:
        raise ValueError(
            f"{stem} needs ≥{MIN_MAIN_BARS} {meta['timeframe']} and ≥{MIN_DAILY_BARS} daily bars "
            f"(got {len(main)} / {len(daily)})"
        )
    frame = feature_frame(stem, main, daily, market_daily=market_daily)
    x = frame[meta["features"]].iloc[[-1]].astype("float64")
    missing = [c for c in meta["features"] if np.isnan(x[c].iloc[0])]
    raw = {side: float(np.asarray(boosters[side].predict(x))[0]) for side in ("bull", "bear")}
    p = {side: _platt(meta["calibrators"][side], raw[side]) for side in ("bull", "bear")}
    rule = meta["signal_rule"]
    label = meta["label"]
    trend = float(x["d1_trend"].iloc[0])
    nfp_day = _is_nfp_day(frame)

    # Signal decision depends on model kind
    model_kind = meta.get("model", {}).get("kind", "lgbm_pair")
    if model_kind == "stacked_ensemble" and "meta" in boosters:
        # v0.5: meta-model decides LONG/SHORT/FLAT
        meta_feats = meta.get("meta_features", [])
        meta_ctx_cols = meta.get("meta_context_features", [])
        min_conf = float(rule.get("min_conf", 0.40))
        # Gather context from the full frame (not just specialist features)
        full_row = frame.iloc[-1]
        context = {
            c: float(full_row[c]) if c in full_row.index and not pd.isna(full_row[c]) else np.nan
            for c in meta_ctx_cols
        }
        signal, confidence = _meta_signal(
            boosters["meta"],
            raw["bull"],
            raw["bear"],
            context,
            meta_feats,
            min_conf,
        )
        confidence = round(confidence, 4)
    else:
        # v0.3: tau/m threshold rule
        signal = _signal(p["bull"], p["bear"], float(rule["tau"]), float(rule["m"]))
        confidence = round(abs(p["bull"] - p["bear"]), 4)

    # v0.3 context: VIX level and session for the LLM's review
    vix_val = float(x["vix"].iloc[0]) if "vix" in x.columns and not np.isnan(x["vix"].iloc[0]) else None
    vix_lvl = (
        float(x["vix_level"].iloc[0])
        if "vix_level" in x.columns and not np.isnan(x["vix_level"].iloc[0])
        else None
    )
    session_val: float | None = float(x["session"].iloc[0]) if "session" in x.columns else None
    session_map = {0.0: "asia", 1.0: "london", 2.0: "new_york"}
    session_name = session_map.get(session_val) if session_val is not None else None

    # context the gate's prompt reads (not model inputs): same numbers the notebook experiment used
    ctx_row = add_packet_context(frame, bars_frame(daily, DAY, daily=True)).iloc[-1]
    last = frame.iloc[-1]
    packet_cols = (
        *PACKET_FIELDS, "adx14", "atr_regime", "bb_width", "d1_rsi14", "d1_adx14", "d1_range_pos_20",
        "d1_ret_atr_5", "d1_dist_sma200_atr", "regime", "regime_trend_str", "regime_sma50_slope",
        "vix", "vix_level", "vix_ret_5", "dxy_dist_sma20", "dxy_ret_5", "us10y", "us10y_chg_5",
        "cal_is_nfp_day", "cal_is_fomc_day",
    )  # fmt: skip
    packet = {c: (None if pd.isna(v := ctx_row.get(c, np.nan)) else round(float(v), 4)) for c in packet_cols}
    calendar = calendar_around(pd.Timestamp(last["date"]))

    value: dict[str, Any] = {
        "model": stem,
        "version": meta["version"],
        "horizon": meta["horizon"],
        "timeframe": meta["timeframe"],
        "bar_ts": main[-1].ts.isoformat(),
        "bar_server_ts": str(last["date"]),
        "p_bull": round(p["bull"], 4),
        "p_bear": round(p["bear"], 4),
        "p_bull_raw": round(raw["bull"], 4),
        "p_bear_raw": round(raw["bear"], 4),
        "context": packet,
        "calendar": calendar,
        "signal": signal,
        "confidence": confidence,
        "tau": rule.get("tau"),
        "margin": rule.get("m"),
        "stop": label["risk"],
        "stop_atr_multiple": _atr_multiple(label["risk"]),
        "tp_r_long": label["counter_trend_tp_r"] if trend < 0 else label["tp_r"],
        "tp_r_short": label["counter_trend_tp_r"] if trend > 0 else label["tp_r"],
        "time_stop_bars": label["time_stop_bars"],
        "daily_trend": {1.0: "up", -1.0: "down"}.get(trend, "mixed"),
        "missing_features": missing,
        "is_nfp_day": nfp_day,
        "vix": vix_val,
        "vix_level": vix_lvl,
        "session": session_name,
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
