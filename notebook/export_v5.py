"""Export v5 stacked-ensemble model artifacts for both intraday and swing horizons.

Re-trains the final specialist models + meta-model from scratch (the notebook was run
via nbconvert so Python objects aren't in memory) and saves all artifacts to
``notebook/models/v5/``.

Run:  cd notebook && python export_v5.py
"""
from __future__ import annotations

import hashlib
import json
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning, module="lightgbm")

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import log_loss, roc_auc_score

sys.path.insert(0, str(Path(__file__).resolve().parent))

from xau_ml import features as F
from xau_ml import holdout as H
from xau_ml import sim as S
from xau_ml.config import (
    HORIZONS,
    LABEL_SETS,
    MARGIN_GRID,
    MIN_VAL_TRADES,
    MODEL_DIR,
    TAU_GRID,
    TRAIN,
    WF2_BURN_IN,
    WF2_FIRST_FOLD,
    WF2_LAST_FOLD,
    Horizon,
    ModelParams,
)
from xau_ml.cv import Fold, assert_folds, monthly_folds, uniqueness
from xau_ml.data import load as load_tf
from xau_ml.labels import add_labels
from xau_ml.model import Calibrator, fit_lgbm
from xau_ml.v2 import REDUCED_BASE, SIDES, baseline_trades
from xau_ml.v2 import nested_trading as nested_trading_v2
from xau_ml.v3 import V3_CALENDAR, V3_MARKET, V3_SESSION_COLS, add_v3_features

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VERSION = "xau-bullbear-0.5.0"
MODEL_DIR_V5 = MODEL_DIR / "v5"
REGIME_COLS = ["regime", "regime_trend_str", "regime_sma50_slope"]

META_MP_PARAMS: dict = {
    "objective": "multiclass",
    "num_class": 3,
    "metric": "multi_logloss",
    "learning_rate": 0.05,
    "num_leaves": 8,
    "max_depth": 3,
    "min_child_samples": 80,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "lambda_l2": 10.0,
    "verbose": -1,
    "seed": 42,
    "deterministic": True,
    "num_threads": 4,
}
META_MAX_ROUNDS = 500

META_CONTEXT_COLS_ALL = [
    "regime", "regime_trend_str", "regime_sma50_slope",
    "atr_regime", "adx14", "session",
    "d1_adx14", "d1_rsi14", "d1_trend",
    "vix_level", "dxy_dist_sma20",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def add_regime_features(fr: pd.DataFrame, d1: pd.DataFrame) -> pd.DataFrame:
    """Composite regime from daily SMA20/50/200 -- merged backward like other HTF context."""
    c = d1["close"].to_numpy()
    h, low = d1["high"].to_numpy(), d1["low"].to_numpy()
    sma20 = pd.Series(c).rolling(20).mean().to_numpy()
    sma50 = pd.Series(c).rolling(50).mean().to_numpy()
    sma200 = pd.Series(c).rolling(200).mean().to_numpy()
    a14 = F.atr(h, low, c, 14)

    trend_long = np.where(sma50 > sma200, 1, np.where(sma50 < sma200, -1, 0))
    price_vs_50 = np.where(c > sma50, 1, -1)
    mom = np.where(sma20 > sma50, 1, np.where(sma20 < sma50, -1, 0))
    score = trend_long + price_vs_50 + mom
    regime = np.where(score >= 2, 1, np.where(score <= -2, -1, 0))

    with np.errstate(divide="ignore", invalid="ignore"):
        trend_str = np.where(a14 > 0, (c - sma50) / a14, 0.0)
        slope = np.where(a14 > 0, (sma50 - np.r_[np.full(5, np.nan), sma50[:-5]]) / a14, 0.0)

    feat = pd.DataFrame({
        "regime": regime.astype(float),
        "regime_trend_str": trend_str,
        "regime_sma50_slope": slope,
        "d1_t_close_regime": d1["t_close"].to_numpy(),
    }, index=d1.index)

    left = fr[["t_close"]].reset_index()
    merged = pd.merge_asof(
        left.sort_values("t_close"),
        feat.sort_values("d1_t_close_regime"),
        left_on="t_close",
        right_on="d1_t_close_regime",
        direction="backward",
    ).set_index("index")

    out = fr.copy()
    for col in ["regime", "regime_trend_str", "regime_sma50_slope"]:
        out[col] = merged.loc[out.index, col].to_numpy()
    return out


def fit_specialist(X: pd.DataFrame, y: np.ndarray, hz: Horizon, mp: ModelParams,
                   w: np.ndarray | None = None) -> tuple[lgb.Booster, float]:
    """LightGBM binary classifier with scale_pos_weight for class balancing.
    Returns (booster, scale_pos_weight_used)."""
    params = {**mp.params}
    pos_rate = float(y.mean())
    spw = 1.0
    if 0 < pos_rate < 1:
        spw = (1 - pos_rate) / pos_rate
    booster = fit_lgbm(X, y, hz, mp, w=w, extra_params={"scale_pos_weight": spw})
    return booster, spw


def build_l1_cols(fr: pd.DataFrame) -> list[str]:
    """Level-1 specialist feature columns."""
    all_cols = F.feature_columns(fr)
    base_reduced = [c for c in all_cols if c.startswith("d1_") or c in REDUCED_BASE]
    extra = [c for c in (V3_SESSION_COLS + V3_CALENDAR + V3_MARKET + REGIME_COLS) if c in all_cols]
    cols = base_reduced + extra
    return list(dict.fromkeys(cols))  # deduplicate preserving order


def build_meta_context_cols(fr: pd.DataFrame) -> list[str]:
    return [c for c in META_CONTEXT_COLS_ALL if c in fr.columns]


def build_meta_features(oof_df: pd.DataFrame, fr: pd.DataFrame,
                        context_cols: list[str]) -> pd.DataFrame:
    pos = oof_df["pos"].to_numpy()
    meta = pd.DataFrame({
        "p_bull": oof_df["p_bull_raw"].to_numpy(),
        "p_bear": oof_df["p_bear_raw"].to_numpy(),
        "p_diff": oof_df["p_bull_raw"].to_numpy() - oof_df["p_bear_raw"].to_numpy(),
        "p_max": np.maximum(oof_df["p_bull_raw"].to_numpy(), oof_df["p_bear_raw"].to_numpy()),
        "p_sum": oof_df["p_bull_raw"].to_numpy() + oof_df["p_bear_raw"].to_numpy(),
    }, index=oof_df.index)
    for c in context_cols:
        meta[c] = fr[c].to_numpy()[pos]
    return meta


def build_meta_labels(fr: pd.DataFrame, pos: np.ndarray) -> np.ndarray:
    y_bull = fr["y_bull"].to_numpy()[pos].astype(int)
    y_bear = fr["y_bear"].to_numpy()[pos].astype(int)
    return np.where(y_bull == 1, 1, np.where(y_bear == 1, 2, 0))


def run_specialists_oof(fr: pd.DataFrame, folds: list[Fold], cols: list[str],
                        hz: Horizon, mp: ModelParams
                        ) -> tuple[pd.DataFrame, pd.DataFrame]:
    parts: list[pd.DataFrame] = []
    metric_rows: list[dict] = []
    for f in folds:
        X_train = fr.iloc[f.train][cols]
        X_test = fr.iloc[f.test][cols]
        preds = {}
        for side in SIDES:
            y_train = fr[f"y_{side}"].to_numpy()[f.train].astype(int)
            w = uniqueness(fr, f.train, f"exit_{side}")
            booster, _ = fit_specialist(X_train, y_train, hz, mp, w=w)
            preds[side] = booster.predict(X_test)

        parts.append(pd.DataFrame({
            "pos": f.test, "fold": f.name,
            "p_bull_raw": preds["bull"], "p_bear_raw": preds["bear"],
        }))
        rec: dict = {"fold": f.name, "train_rows": len(f.train), "test_rows": len(f.test)}
        for side in SIDES:
            y_test = fr[f"y_{side}"].to_numpy()[f.test]
            rec[f"auc_{side}"] = float(roc_auc_score(y_test, preds[side])) if 0 < y_test.mean() < 1 else float("nan")
            rec[f"logloss_{side}"] = float(log_loss(y_test, np.clip(preds[side], 1e-6, 1 - 1e-6), labels=[0, 1]))
        metric_rows.append(rec)

    return pd.concat(parts, ignore_index=True), pd.DataFrame(metric_rows)


def nested_ensemble_trading(fr: pd.DataFrame, oof: pd.DataFrame,
                            meta_X: pd.DataFrame, meta_y: np.ndarray,
                            fold_names: list[str], burn_in: int,
                            min_conf: float = 0.0):
    sig = np.zeros(len(oof), dtype="int8")
    log_rows: list[dict] = []

    for k, name in enumerate(fold_names):
        cur = (oof["fold"] == name).to_numpy()
        prior = oof["fold"].isin(fold_names[:k]).to_numpy()
        if k < burn_in or not prior.any():
            continue

        X_meta_train = meta_X.loc[prior]
        y_meta_train = meta_y[prior]
        meta_booster = lgb.train(
            {**META_MP_PARAMS},
            lgb.Dataset(X_meta_train, y_meta_train, free_raw_data=False),
            num_boost_round=META_MAX_ROUNDS,
        )
        probs = meta_booster.predict(meta_X.loc[cur])
        pred_class = probs.argmax(axis=1)
        max_prob = probs.max(axis=1)
        fold_sig = np.where(pred_class == 1, 1, np.where(pred_class == 2, -1, 0)).astype("int8")
        if min_conf > 0:
            fold_sig[max_prob < min_conf] = 0
        sig[cur] = fold_sig
        log_rows.append({
            "fold": name,
            "signals": int((fold_sig != 0).sum()),
            "long_signals": int((fold_sig == 1).sum()),
            "short_signals": int((fold_sig == -1).sum()),
        })

    scored = oof["fold"].isin(fold_names[burn_in:]).to_numpy()
    trades = S.run(fr, oof["pos"].to_numpy()[scored], sig[scored])
    return trades, pd.DataFrame(log_rows), sig, scored


def _metrics_dict(trades: pd.DataFrame) -> dict:
    """S.metrics but with all values JSON-safe."""
    m = S.metrics(trades)
    return {k: (None if (isinstance(v, float) and (np.isnan(v) or np.isinf(v))) else v)
            for k, v in m.items()}


# ---------------------------------------------------------------------------
# Per-horizon export
# ---------------------------------------------------------------------------

def export_horizon(hz_name: str) -> dict:
    """Build frame, walk-forward, train final models, evaluate holdout, save artifacts."""
    hz = HORIZONS[hz_name]
    ls = LABEL_SETS["ct25"]
    mp = ModelParams()
    stem = f"xau_{hz_name}_ct25"

    print(f"\n{'='*70}")
    print(f"  {hz_name.upper()} horizon ({hz.tf})")
    print(f"{'='*70}")

    # --- 1. Load bars + build frame ----------------------------------------
    print("[1/7] Loading bars...")
    bars: dict[str, pd.DataFrame] = {hz.tf: load_tf(hz.tf)}
    for tf in hz.htf:
        bars[tf] = load_tf(tf)

    print("[2/7] Building features + labels...")
    fr = F.build(bars[hz.tf], hz.tf, {tf: bars[tf] for tf in hz.htf})
    fr = F.add_extras(fr, bars["1d"])
    fr = add_v3_features(fr)
    fr = add_labels(fr, hz, ls)
    fr = add_regime_features(fr, bars["1d"])
    print(f"       Frame: {len(fr)} bars, {fr['date'].min()} to {fr['date'].max()}")

    l1_cols = build_l1_cols(fr)
    meta_ctx_cols = build_meta_context_cols(fr)
    print(f"       L1 features: {len(l1_cols)}, meta context: {len(meta_ctx_cols)}")

    # --- 2. Walk-forward folds + OOF --------------------------------------
    print("[3/7] Walk-forward specialist OOF...")
    folds = monthly_folds(fr, hz, WF2_FIRST_FOLD, WF2_LAST_FOLD, train_from=TRAIN[0])
    assert_folds(fr, folds, hz)
    fold_names = [f.name for f in folds]

    oof, ml_metrics = run_specialists_oof(fr, folds, l1_cols, hz, mp)
    print(f"       OOF rows: {len(oof)}, folds: {len(folds)}")
    mean_auc_bull = float(ml_metrics["auc_bull"].mean())
    mean_auc_bear = float(ml_metrics["auc_bear"].mean())
    print(f"       Mean AUC bull={mean_auc_bull:.4f}, bear={mean_auc_bear:.4f}")

    meta_X = build_meta_features(oof, fr, meta_ctx_cols)
    meta_y = build_meta_labels(fr, oof["pos"].to_numpy())

    # --- 3. Walk-forward trading (all experiments) -------------------------
    print("[4/7] Walk-forward trading experiments...")
    trades_A, _, _, _, _ = nested_trading_v2(
        fr, oof, fold_names, WF2_BURN_IN, MIN_VAL_TRADES[hz.tf])
    trades_B, _, _, _ = nested_ensemble_trading(
        fr, oof, meta_X, meta_y, fold_names, WF2_BURN_IN, min_conf=0.0)
    trades_C, _, _, _ = nested_ensemble_trading(
        fr, oof, meta_X, meta_y, fold_names, WF2_BURN_IN, min_conf=0.40)
    trades_D, _, _, _ = nested_ensemble_trading(
        fr, oof, meta_X, meta_y, fold_names, WF2_BURN_IN, min_conf=0.50)

    # Baselines on scored OOF positions
    scored_pos = oof.loc[oof["fold"].isin(fold_names[WF2_BURN_IN:]), "pos"].to_numpy()
    trades_long = baseline_trades(fr, scored_pos, "always_long")
    trades_short = baseline_trades(fr, scored_pos, "always_short")
    trades_sma = baseline_trades(fr, scored_pos, "sma_cross")

    wf_trading: list[dict] = []
    for name, tr in [("A: specialist + tau/m", trades_A),
                     ("B: meta-model", trades_B),
                     ("C: meta (conf>0.40)", trades_C),
                     ("D: meta (conf>0.50)", trades_D),
                     ("always_long", trades_long),
                     ("always_short", trades_short),
                     ("sma20_50_cross", trades_sma)]:
        wf_trading.append({"strategy": name, **_metrics_dict(tr)})

    print("       Walk-forward results:")
    for r in wf_trading:
        if r["trades"] > 0:
            wr = r.get("win_rate")
            wr_s = f"{wr:.3f}" if wr is not None else "N/A"
            pf = r.get("profit_factor")
            pf_s = f"{pf:.3f}" if pf is not None else "N/A"
            print(f"         {r['strategy']:30s}  trades={r['trades']:4d}  WR={wr_s}  PF={pf_s}  E[R]={r.get('expectancy_r', 0):.4f}")

    # --- 4. Train FINAL specialists on ALL fold training data ---------------
    print("[5/7] Training FINAL specialists + meta-model...")
    all_train = np.concatenate([f.train for f in folds])
    X_all_train = fr.iloc[all_train][l1_cols]

    specialists: dict[str, lgb.Booster] = {}
    spw_info: dict[str, float] = {}
    for side in SIDES:
        y_all = fr[f"y_{side}"].to_numpy()[all_train].astype(int)
        w_all = uniqueness(fr, all_train, f"exit_{side}")
        booster, spw = fit_specialist(X_all_train, y_all, hz, mp, w=w_all)
        specialists[side] = booster
        spw_info[side] = spw
        print(f"       {side} specialist: pos_rate={y_all.mean():.3f}, spw={spw:.2f}, trees={booster.num_trees()}")

    # Train FINAL meta-model on ALL OOF
    meta_booster = lgb.train(
        {**META_MP_PARAMS},
        lgb.Dataset(meta_X, meta_y, free_raw_data=False),
        num_boost_round=META_MAX_ROUNDS,
    )
    print(f"       Meta-model: trees={meta_booster.num_trees()}, features={meta_booster.num_feature()}")

    # Calibrators for backward compatibility
    all_oof_pos = oof["pos"].to_numpy()
    cal_bull = Calibrator.fit("platt", oof["p_bull_raw"].to_numpy(),
                              fr["y_bull"].to_numpy()[all_oof_pos])
    cal_bear = Calibrator.fit("platt", oof["p_bear_raw"].to_numpy(),
                              fr["y_bear"].to_numpy()[all_oof_pos])

    # --- 5. Holdout evaluation ---------------------------------------------
    print("[6/7] Holdout evaluation (Aug-Sep 2026)...")
    hbars = H.load_bars()
    htfs = {tf: hbars[tf] for tf in hz.htf}
    hfr = F.build(hbars[hz.tf], hz.tf, htfs)
    hfr = F.add_extras(hfr, hbars["1d"])
    hfr = add_v3_features(hfr, use_cache=True)
    hfr = add_labels(hfr, hz, ls)
    hfr = add_regime_features(hfr, hbars["1d"])
    hpos = H.holdout_rows(hfr)
    print(f"       Holdout rows: {len(hpos)}")

    # L1 predictions on holdout
    h_X = hfr.iloc[hpos][l1_cols]
    h_p_bull = specialists["bull"].predict(h_X)
    h_p_bear = specialists["bear"].predict(h_X)

    # Meta predictions on holdout
    h_meta_df = pd.DataFrame({
        "p_bull": h_p_bull, "p_bear": h_p_bear,
        "p_diff": h_p_bull - h_p_bear,
        "p_max": np.maximum(h_p_bull, h_p_bear),
        "p_sum": h_p_bull + h_p_bear,
    })
    for c in meta_ctx_cols:
        h_meta_df[c] = hfr[c].to_numpy()[hpos]
    h_meta_probs = meta_booster.predict(h_meta_df)
    h_pred_class = h_meta_probs.argmax(axis=1)
    h_max_prob = h_meta_probs.max(axis=1)

    # Exp A: tau/m
    h_pb_cal = cal_bull(h_p_bull)
    h_pr_cal = cal_bear(h_p_bear)
    _, best_tm = S.select_tau_m(
        fr, all_oof_pos,
        cal_bull(oof["p_bull_raw"].to_numpy()),
        cal_bear(oof["p_bear_raw"].to_numpy()),
        TAU_GRID, MARGIN_GRID, MIN_VAL_TRADES[hz.tf])
    if best_tm is not None:
        h_sig_A = S.signals(h_pb_cal, h_pr_cal, best_tm["tau"], best_tm["m"])
    else:
        h_sig_A = np.zeros(len(hpos), dtype="int8")

    # Exp B/C/D
    h_sig_B = np.where(h_pred_class == 1, 1, np.where(h_pred_class == 2, -1, 0)).astype("int8")
    h_sig_C = h_sig_B.copy(); h_sig_C[h_max_prob < 0.40] = 0
    h_sig_D = h_sig_B.copy(); h_sig_D[h_max_prob < 0.50] = 0

    holdout_results: dict[str, pd.DataFrame] = {
        "A: specialist + tau/m": S.run(hfr, hpos, h_sig_A),
        "B: meta-model": S.run(hfr, hpos, h_sig_B),
        "C: meta (conf>0.40)": S.run(hfr, hpos, h_sig_C),
        "D: meta (conf>0.50)": S.run(hfr, hpos, h_sig_D),
        "always_long": baseline_trades(hfr, hpos, "always_long"),
        "always_short": baseline_trades(hfr, hpos, "always_short"),
    }

    holdout_metrics: list[dict] = []
    for name, tr in holdout_results.items():
        holdout_metrics.append({"strategy": name, **_metrics_dict(tr)})

    print("       Holdout results:")
    for r in holdout_metrics:
        wr = r.get("win_rate")
        wr_s = f"{wr:.3f}" if wr is not None else "N/A"
        pf = r.get("profit_factor")
        pf_s = f"{pf:.3f}" if pf is not None else "N/A"
        print(f"         {r['strategy']:30s}  trades={r['trades']:4d}  WR={wr_s}  PF={pf_s}  E[R]={r.get('expectancy_r', 0):.4f}")

    # --- 6. Save artifacts -------------------------------------------------
    print("[7/7] Saving artifacts...")
    MODEL_DIR_V5.mkdir(parents=True, exist_ok=True)

    # Save boosters
    files: dict[str, dict] = {}
    for side in SIDES:
        fpath = MODEL_DIR_V5 / f"{stem}.{side}.txt"
        specialists[side].save_model(str(fpath))
        files[side] = {"file": fpath.name, "sha256_16": _sha(fpath)}

    meta_path = MODEL_DIR_V5 / f"{stem}.meta.txt"
    meta_booster.save_model(str(meta_path))
    files["meta"] = {"file": meta_path.name, "sha256_16": _sha(meta_path)}

    # Feature code hash (for reproducibility tracking)
    feat_code = Path(__file__).parent / "xau_ml" / "features.py"
    feat_sha = hashlib.sha256(feat_code.read_bytes()).hexdigest()[:16]

    # Build metadata JSON
    meta_feature_names = ["p_bull", "p_bear", "p_diff", "p_max", "p_sum"] + meta_ctx_cols

    metadata: dict = {
        "version": VERSION,
        "symbol": "XAUUSD",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "horizon": hz_name,
        "timeframe": hz.tf,
        "context_timeframes": list(hz.htf),
        "extras": True,
        "v3_features": True,
        "regime_features": True,
        "label": {
            "type": "triple_barrier",
            "entry": "open of the next bar",
            "stop_r": 1.0,
            "risk": f"{hz.sl_atr} x ATR14 (Wilder)",
            "tp_r": ls.tp_r,
            "counter_trend_tp_r": ls.ct_tp_r,
            "time_stop_bars": hz.time_stop,
            "intrabar_rule": "stop_first",
        },
        "features": l1_cols,
        "meta_features": meta_feature_names,
        "meta_context_features": meta_ctx_cols,
        "feature_groups": {
            "base": [c for c in l1_cols if c not in (V3_SESSION_COLS + V3_CALENDAR + V3_MARKET + REGIME_COLS)],
            "calendar": [c for c in l1_cols if c in V3_CALENDAR],
            "market": [c for c in l1_cols if c in V3_MARKET],
            "session": [c for c in l1_cols if c in V3_SESSION_COLS],
            "regime": [c for c in l1_cols if c in REGIME_COLS],
        },
        "feature_code_sha256_16": feat_sha,
        "training": {
            "rows_from": str(fr.iloc[all_train[0]]["date"]),
            "rows_to": str(fr.iloc[all_train[-1]]["date"]),
            "rows": len(all_train),
            "weights": "label uniqueness",
            "data_holes_excluded": True,
        },
        "evaluation": {
            "protocol": "expanding monthly walk-forward, nested meta-model + nested calibration/tau-m for baseline",
            "folds": fold_names,
            "burn_in_folds": WF2_BURN_IN,
            "note": "v0.5: stacked ensemble (specialist bull/bear + meta-model 3-class)",
        },
        "model": {
            "kind": "stacked_ensemble",
            "specialist_params": {
                **mp.params,
                "scale_pos_weight": spw_info,
                "max_rounds": mp.max_rounds,
                "early_stopping": mp.early_stopping,
            },
            "meta_params": {
                **META_MP_PARAMS,
                "max_rounds": META_MAX_ROUNDS,
            },
        },
        "signal_rule": {
            "kind": "meta_model",
            "min_conf": 0.40,
            "rule": "meta-model 3-class (FLAT/LONG/SHORT), argmax with confidence > 0.40",
        },
        "metrics": {
            "walkforward_ml": [{
                "auc_bull": mean_auc_bull,
                "auc_bear": mean_auc_bear,
                "auc_mean": (mean_auc_bull + mean_auc_bear) / 2,
                "auc_bull_min_fold": float(ml_metrics["auc_bull"].min()),
                "auc_bear_min_fold": float(ml_metrics["auc_bear"].min()),
                "logloss_bull_mean": float(ml_metrics["logloss_bull"].mean()),
                "logloss_bear_mean": float(ml_metrics["logloss_bear"].mean()),
                "features": len(l1_cols),
            }],
            "walkforward_trading": wf_trading,
            "holdout_aug_sep_2026": holdout_metrics,
        },
        "files": files,
        "calibrators": {
            "bull": cal_bull.to_json(),
            "bear": cal_bear.to_json(),
        },
    }

    json_path = MODEL_DIR_V5 / f"{stem}.json"
    json_path.write_text(json.dumps(metadata, indent=2, default=str), encoding="utf-8")
    print(f"       Saved: {json_path}")
    for k, v in files.items():
        print(f"       Saved: {MODEL_DIR_V5 / v['file']}  (sha={v['sha256_16']})")

    return metadata


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print(f"Exporting v5 stacked-ensemble models to {MODEL_DIR_V5}/")
    print(f"Version: {VERSION}")

    results = {}
    for hz_name in ("intraday", "swing"):
        results[hz_name] = export_horizon(hz_name)

    print(f"\n{'='*70}")
    print("  EXPORT COMPLETE")
    print(f"{'='*70}")
    for hz_name, meta in results.items():
        stem = f"xau_{hz_name}_ct25"
        n_files = len(meta["files"])
        print(f"\n  {hz_name}:")
        print(f"    JSON:    {MODEL_DIR_V5 / f'{stem}.json'}")
        print(f"    Files:   {n_files} boosters (bull, bear, meta)")
        wf = [r for r in meta["metrics"]["walkforward_trading"] if r["strategy"] == "C: meta (conf>0.40)"]
        if wf:
            r = wf[0]
            print(f"    WF:      trades={r['trades']}  WR={r.get('win_rate', 0):.3f}  PF={r.get('profit_factor', 0):.3f}  E[R]={r.get('expectancy_r', 0):.4f}")
        ho = [r for r in meta["metrics"]["holdout_aug_sep_2026"] if r["strategy"] == "C: meta (conf>0.40)"]
        if ho:
            r = ho[0]
            print(f"    Holdout: trades={r['trades']}  WR={r.get('win_rate', 0):.3f}  PF={r.get('profit_factor', 0):.3f}  E[R]={r.get('expectancy_r', 0):.4f}")


if __name__ == "__main__":
    main()
