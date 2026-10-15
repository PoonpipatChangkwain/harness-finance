"""LLM veto gate on the WALK-FORWARD (out-of-fold) trades of 2025-06 .. 2026-01.

Same gate, prompt and simulator as ``llm_filter_experiment.py`` (Aug-Sep 2026 holdout), but
on the nested walk-forward signals of experiment C (meta-model, conf > 0.40): ~760 trades,
about 4x the holdout's statistical power. Signals are out-of-fold, so the ML side is not
in-sample; the LLM side runs in ``anon`` mode (no date, no price level) to limit what it can
recall about 2025. Includes the random-veto control (skip with the LLM's own skip rate).

Run:  %USERPROFILE%\\.venvs\\xau-ml\\Scripts\\python llm_filter_walkforward.py [--provider ... --model ...]
The walk-forward OOF predictions are cached in results/llm_filter/wf_oof_intraday.csv.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning, module="lightgbm")

import httpx
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from llm_filter_experiment import (  # noqa: E402
    EVENT_AFTER_H,
    EVENT_BEFORE_H,
    OUT_DIR,
    Calendar,
    LLMGate,
    by_month,
    context_packet,
    metrics_row,
    run_gated,
)
from export_v5 import (  # noqa: E402
    add_regime_features,
    build_l1_cols,
    build_meta_context_cols,
    build_meta_features,
    build_meta_labels,
    nested_ensemble_trading,
    run_specialists_oof,
)
from xau_ml import features as F  # noqa: E402
from xau_ml import sim as S  # noqa: E402
from xau_ml.config import HORIZONS, LABEL_SETS, TRAIN, WF2_BURN_IN, WF2_FIRST_FOLD, WF2_LAST_FOLD, ModelParams  # noqa: E402
from xau_ml.cv import assert_folds, monthly_folds  # noqa: E402
from xau_ml.data import load as load_tf  # noqa: E402
from xau_ml.labels import add_labels  # noqa: E402
from xau_ml.v3 import add_v3_features  # noqa: E402

OOF_CACHE = OUT_DIR / "wf_oof_intraday.csv"
MIN_CONF = 0.40
N_RANDOM = 300


def build_training_frame() -> tuple[pd.DataFrame, list]:
    hz, ls = HORIZONS["intraday"], LABEL_SETS["ct25"]
    bars = {tf: load_tf(tf) for tf in (hz.tf, *hz.htf)}
    fr = F.build(bars[hz.tf], hz.tf, {tf: bars[tf] for tf in hz.htf})
    fr = F.add_extras(fr, bars["1d"])
    fr = add_v3_features(fr)
    fr = add_labels(fr, hz, ls)
    fr = add_regime_features(fr, bars["1d"])
    folds = monthly_folds(fr, hz, WF2_FIRST_FOLD, WF2_LAST_FOLD, train_from=TRAIN[0])
    assert_folds(fr, folds, hz)
    return fr, folds


def walkforward_signals(fr: pd.DataFrame, folds: list) -> tuple[pd.DataFrame, np.ndarray, np.ndarray, np.ndarray]:
    """OOF specialist probs (cached) -> nested meta-model signals of experiment C."""
    hz = HORIZONS["intraday"]
    l1_cols = build_l1_cols(fr)
    if OOF_CACHE.exists():
        oof = pd.read_csv(OOF_CACHE)
        print(f"      OOF from cache ({len(oof)} rows)")
    else:
        print("      walk-forward specialists (10 folds x 2 sides) ...")
        oof, _ = run_specialists_oof(fr, folds, l1_cols, hz, ModelParams())
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        oof.to_csv(OOF_CACHE, index=False)
    ctx_cols = build_meta_context_cols(fr)
    meta_X = build_meta_features(oof, fr, ctx_cols)
    meta_y = build_meta_labels(fr, oof["pos"].to_numpy())
    fold_names = [f.name for f in folds]
    trades_C, _, sig, scored = nested_ensemble_trading(fr, oof, meta_X, meta_y, fold_names, WF2_BURN_IN, min_conf=MIN_CONF)
    pos = oof["pos"].to_numpy()[scored]
    return trades_C, pos, sig[scored], oof


async def main_async(args: argparse.Namespace) -> None:
    t0 = time.time()
    print("[1/5] training frame + folds ...")
    fr, folds = build_training_frame()
    print(f"      frame {len(fr)} bars {fr['date'].min()} .. {fr['date'].max()}")

    print("[2/5] walk-forward signals (experiment C) ...")
    trades_C, pos, sig, oof = walkforward_signals(fr, folds)
    mC = S.metrics(trades_C)
    print(f"      reference C: trades {mC['trades']}  WR {mC['win_rate']:.3f}  PF {mC['profit_factor']:.3f}  total {mC['total_r']:+.1f}R")

    # specialist probs per scored row (for the packet)
    oof_scored = oof.iloc[np.flatnonzero(np.isin(oof['pos'].to_numpy(), pos))].set_index("pos")
    pb_of = oof_scored["p_bull_raw"].to_dict()
    pr_of = oof_scored["p_bear_raw"].to_dict()

    async def take_gate(t, s):
        return "take"

    ml_all, ml_taken = await run_gated(fr, pos, sig, take_gate)
    print(f"      ml_only (same simulator): trades {len(ml_taken)}  WR {ml_taken['r'].gt(0).mean():.3f}  total {ml_taken['r'].sum():+.1f}R")

    h_to, h_since = fr["cal_hours_to_next"].to_numpy(), fr["cal_hours_since_last"].to_numpy()

    async def cal_gate(t, s):
        return "skip" if (h_to[t] <= EVENT_BEFORE_H or h_since[t] <= EVENT_AFTER_H) else "take"

    cal_all, cal_taken = await run_gated(fr, pos, sig, cal_gate)

    print(f"[3/5] LLM answers ({args.model} via {args.provider}, anon) ...")
    cal = Calendar()
    gate = LLMGate(args.provider, args.model, "anon", args.concurrency, args.dry_run, cache_dir=OUT_DIR / "walkforward")
    decisions: dict[tuple[int, int], dict] = {}
    date_arr = fr["date"].to_numpy()
    pos_of_date = {d: i for i, d in enumerate(date_arr)}
    # the meta-model has no scalar confidence in nested_ensemble_trading's return; use |p_bull-p_bear| as a proxy
    async with httpx.AsyncClient() as client:
        async def ask(t: int, s: int) -> dict:
            if (t, s) not in decisions:
                pb, pr = float(pb_of[t]), float(pr_of[t])
                pkt = context_packet(fr, t, s, abs(pb - pr), pb, pr, cal, "anon")
                decisions[(t, s)] = await gate.decide(client, str(fr["date"].iat[t]), s, pkt)
            return decisions[(t, s)]

        pre = [(pos_of_date[d], 1 if sd == "long" else -1) for d, sd in zip(ml_taken["decided"], ml_taken["side"])]
        if args.max_calls:
            pre = pre[: args.max_calls]
        done = 0
        for fut in asyncio.as_completed([ask(t, s) for t, s in pre]):
            await fut
            done += 1
            if done % 100 == 0 or done == len(pre):
                print(f"      prefetch {done}/{len(pre)} (new calls {gate.calls}, parse failures {gate.parse_failures})")

        async def llm_gate(t, s):
            if args.max_calls and (t, s) not in decisions:
                return "take"
            return (await ask(t, s))["action"]

        async def llm_binary_gate(t, s):
            a = await llm_gate(t, s)
            return "take" if a == "half" else a

        llm_all, llm_taken = await run_gated(fr, pos, sig, llm_gate)
        bin_all, bin_taken = await run_gated(fr, pos, sig, llm_binary_gate)
        print(f"      LLM consulted on {len(decisions)} candidates (new calls {gate.calls}, parse failures {gate.parse_failures})")

    print("[4/5] results")
    arms = {"ml_only": ml_taken, "calendar_rule": cal_taken, "llm_veto(half=0.5R)": llm_taken, "llm_binary(half=take)": bin_taken}
    table = pd.DataFrame([metrics_row(k, v) for k, v in arms.items()]).set_index("arm")
    cols = ["trades", "win_rate", "profit_factor", "expectancy_r", "total_r", "max_dd_pct", "longs", "shorts"]
    print("\n=== Walk-forward 2025-06 .. 2026-01, intraday 15m, experiment C signals ===")
    print(table[cols].to_string(float_format=lambda x: f"{x:.3f}"))
    for name in ("ml_only", "llm_binary(half=take)"):
        print(f"\n--- by month: {name}")
        print(by_month(arms[name]).to_string(float_format=lambda x: f"{x:.3f}"))

    ml_set = ml_taken[["decided", "side", "r"]].copy()
    ml_set["t"] = [pos_of_date[d] for d in ml_set["decided"]]
    ml_set["action"] = [decisions.get((t, 1 if sd == "long" else -1), {}).get("action", "take")
                        for t, sd in zip(ml_set["t"], ml_set["side"])]
    ml_set["win"] = ml_set["r"] > 0
    diag = ml_set.groupby("action").agg(n=("r", "size"), win_rate=("win", "mean"), mean_r=("r", "mean"), total_r=("r", "sum"))
    print("\n=== LLM verdict on the 1:1 ML-only trade set ===")
    print(diag.to_string(float_format=lambda x: f"{x:.3f}"))
    actions = pd.Series([r["action"] for r in decisions.values()]).value_counts()
    print("\nactions over all candidates: " + ", ".join(f"{k}={v}" for k, v in actions.items()))

    # random-veto control with the LLM's skip (and half) rates
    n_c = max(len(decisions), 1)
    p_skip, p_half = actions.get("skip", 0) / n_c, actions.get("half", 0) / n_c
    rng = np.random.default_rng(7)
    rows_bin, rows_half = [], []
    for _ in range(N_RANDOM):
        async def g_bin(t, s, r=rng):
            return "skip" if r.random() < p_skip else "take"

        async def g_half(t, s, r=rng):
            u = r.random()
            return "skip" if u < p_skip else ("half" if u < p_skip + p_half else "take")

        _, tk = await run_gated(fr, pos, sig, g_bin)
        rows_bin.append(S.metrics(tk))
        _, tk = await run_gated(fr, pos, sig, g_half)
        rows_half.append(S.metrics(tk))
    rb, rh = pd.DataFrame(rows_bin), pd.DataFrame(rows_half)
    print(f"\n=== random veto x{N_RANDOM} (skip p={p_skip:.3f}; half p={p_half:.3f}) ===")
    ctrl = {}
    for metric in ("win_rate", "profit_factor", "total_r"):
        v_bin = table.loc["llm_binary(half=take)", metric]
        v_half = table.loc["llm_veto(half=0.5R)", metric]
        pct_bin = float((rb[metric] < v_bin).mean() * 100)
        pct_half = float((rh[metric] < v_half).mean() * 100)
        ctrl[metric] = {"random_bin_5_95": [float(rb[metric].quantile(.05)), float(rb[metric].quantile(.95))],
                        "llm_binary": float(v_bin), "llm_binary_pct": pct_bin,
                        "random_half_5_95": [float(rh[metric].quantile(.05)), float(rh[metric].quantile(.95))],
                        "llm_half": float(v_half), "llm_half_pct": pct_half}
        print(f"  {metric:14s} random-bin 5-95% [{rb[metric].quantile(.05):.3f}, {rb[metric].quantile(.95):.3f}]  "
              f"llm_binary {v_bin:.3f} -> pct {pct_bin:.0f} | random-half [{rh[metric].quantile(.05):.3f}, {rh[metric].quantile(.95):.3f}]  "
              f"llm_half {v_half:.3f} -> pct {pct_half:.0f}")

    print("[5/5] saving")
    tag = f"walkforward_anon_{args.model.replace('/', '_').replace(':', '_')}"
    for name, df in (("ml_only", ml_all), ("calendar_rule", cal_all), ("llm_veto", llm_all)):
        df.to_csv(OUT_DIR / f"trades_{name}_{tag}.csv", index=False)
    ml_set.to_csv(OUT_DIR / f"diag_mlset_{tag}.csv", index=False)
    summary = {
        "generated_utc": pd.Timestamp.now("UTC").isoformat(), "window": "walk-forward OOF 2025-06..2026-01 (exp C)",
        "llm_model": args.model, "llm_provider": args.provider, "mode": "anon",
        "reference_C_from_notebook_pipeline": {k: (None if isinstance(v, float) and np.isnan(v) else v) for k, v in mC.items()},
        "llm_calls_new": gate.calls, "llm_parse_failures": gate.parse_failures, "candidates": len(decisions),
        "arms": table.reset_index().to_dict(orient="records"),
        "llm_verdict_on_ml_trades": diag.reset_index().to_dict(orient="records"),
        "actions": actions.to_dict(), "random_control": ctrl,
    }
    (OUT_DIR / f"summary_{tag}.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(f"      {OUT_DIR / f'summary_{tag}.json'}  ({time.time() - t0:.0f}s)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--provider", default="ollama_cloud")
    ap.add_argument("--model", default="gpt-oss:120b")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--max-calls", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    main()
