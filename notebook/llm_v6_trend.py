"""v0.6 candidate: deterministic daily-trend filter on the v0.5 meta-model signals.

Finding from llm_filter_walkforward.py: the biggest loser group is trades that fade the daily
trend (close on the trend's side of BOTH daily SMA50 and SMA200 — d1_trend == -side), and the
meta-model gives d1_trend ~0 importance because 87% of its training rows are an uptrend. Retraining
cannot fix a regime it never saw, so the filter is a rule on the signal, not a feature.

Evaluated on two windows with the SAME simulator:
  WF       walk-forward OOF 2025-06..2026-01 (758 ML trades). The rule was chosen after seeing
           this window, so this is in-sample for the RULE (one binary rule, no tuned parameter).
  HOLDOUT  Aug-Sep 2026 — already seen by earlier experiments, but not used to pick this rule.
The only clean test is the forward A/B (arm ``trend_rule``).

Arms: ml_only | trend_rule | llm_binary | trend+llm_binary | trend+llm_half
(LLM = gpt-oss 120B, anon, answers read from the caches of the two earlier experiments; any
candidate not cached yet — reachable only because the filter freed a slot — is asked lazily.)
Plus: does the LLM add anything ON TOP of the rule? (take vs rest among trend-aligned trades).

Run: %USERPROFILE%\\.venvs\\xau-ml\\Scripts\\python llm_v6_trend.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

import httpx
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from llm_filter_experiment import (  # noqa: E402
    OUT_DIR, Calendar, LLMGate, build_frame as holdout_frame, context_packet,
    load_bars, load_model, metrics_row, ml_signals, run_gated, by_month,
)
from llm_filter_walkforward import build_training_frame, walkforward_signals  # noqa: E402

MODEL, PROVIDER = "gpt-oss:120b", "ollama_cloud"


def perm_p(a: np.ndarray, b: np.ndarray, f, n: int = 5000, seed: int = 0) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    obs = f(a) - f(b)
    pool, k, c = np.r_[a, b], len(a), 0
    for _ in range(n):
        p = rng.permutation(pool)
        c += abs(f(p[:k]) - f(p[k:])) >= abs(obs)
    return float(obs), c / n


async def run_window(name: str, fr: pd.DataFrame, pos: np.ndarray, sig: np.ndarray,
                     pb: dict, pr: dict, cache_dir: Path | None) -> dict:
    d1 = fr["d1_trend"].fillna(0).to_numpy()
    cal = Calendar()
    gate = LLMGate(PROVIDER, MODEL, "anon", 6, False, cache_dir=cache_dir)
    decisions: dict[tuple[int, int], str] = {}

    async with httpx.AsyncClient() as client:
        async def llm(t: int, s: int) -> str:
            if (t, s) not in decisions:
                p_b, p_r = float(pb[t]), float(pr[t])
                pkt = context_packet(fr, t, s, abs(p_b - p_r), p_b, p_r, cal, "anon")
                decisions[(t, s)] = (await gate.decide(client, str(fr["date"].iat[t]), s, pkt))["action"]
            return decisions[(t, s)]

        async def g_ml(t, s): return "take"
        async def g_trend(t, s): return "skip" if d1[t] == -s else "take"
        async def g_llm_bin(t, s):
            a = await llm(t, s); return "take" if a == "half" else a
        async def g_trend_bin(t, s):
            return "skip" if d1[t] == -s else await g_llm_bin(t, s)
        async def g_trend_half(t, s):
            return "skip" if d1[t] == -s else await llm(t, s)

        res = {}
        for arm, g in (("ml_only", g_ml), ("trend_rule", g_trend), ("llm_binary", g_llm_bin),
                       ("trend+llm_binary", g_trend_bin), ("trend+llm_half", g_trend_half)):
            res[arm] = (await run_gated(fr, pos, sig, g))[1]
        print(f"  [{name}] LLM answers read/asked: {len(decisions)} (new API calls {gate.calls})")

    (OUT_DIR / "v6").mkdir(parents=True, exist_ok=True)
    tag = "wf" if name.startswith("WALK") else "ho"
    for k, v in res.items():
        v.to_csv(OUT_DIR / "v6" / f"{tag}_{k.replace('+', '_')}.csv", index=False)
    table = pd.DataFrame([metrics_row(k, v) for k, v in res.items()]).set_index("arm")
    cols = ["trades", "win_rate", "profit_factor", "expectancy_r", "total_r", "max_dd_pct", "longs", "shorts"]
    print(f"\n=== {name} ===")
    print(table[cols].to_string(float_format=lambda x: f"{x:.3f}"))
    print("--- by month: ml_only vs trend_rule vs trend+llm_binary (total R)")
    bm = pd.DataFrame({k: by_month(res[k])["total_r"] for k in ("ml_only", "trend_rule", "trend+llm_binary")})
    print(bm.round(1).to_string())

    # trade-level: ML-only set, split by trend alignment and LLM verdict
    ml = res["ml_only"][["decided", "side", "r"]].copy()
    idx = {d: i for i, d in enumerate(fr["date"].to_numpy())}
    ml["t"] = [idx[d] for d in ml["decided"]]
    ml["s"] = np.where(ml.side == "long", 1, -1)
    ml["counter"] = d1[ml["t"].to_numpy()] == -ml["s"].to_numpy()
    ml["llm"] = [decisions.get((t, s), "n/a") for t, s in zip(ml["t"], ml["s"])]
    for lab, sub in (("counter-trend", ml[ml.counter]), ("trend-aligned", ml[~ml.counter])):
        print(f"  {lab:14s} n={len(sub):4d} win {(sub.r > 0).mean():.3f} meanR {sub.r.mean():+.3f} totalR {sub.r.sum():+.1f}")
    al = ml[~ml.counter & (ml.llm != "n/a")]
    take, rest = al[al.llm == "take"].r.to_numpy(), al[al.llm != "take"].r.to_numpy()
    out = {"table": table.reset_index().to_dict(orient="records"),
           "counter_n": int(ml.counter.sum()), "aligned_n": int((~ml.counter).sum())}
    if len(take) > 5 and len(rest) > 5:
        dw, pw = perm_p(take, rest, lambda x: (x > 0).mean())
        dr, pr_ = perm_p(take, rest, np.mean)
        print(f"  among trend-aligned: LLM take n={len(take)} win {(take>0).mean():.3f} meanR {take.mean():+.3f} | "
              f"rest n={len(rest)} win {(rest>0).mean():.3f} meanR {rest.mean():+.3f}")
        print(f"  take vs rest: dWin {dw:+.3f} p={pw:.3f} | dMeanR {dr:+.3f} p={pr_:.3f}")
        out["llm_on_top"] = {"take_n": len(take), "rest_n": len(rest), "dwin": dw, "p_win": pw, "dmeanr": dr, "p_meanr": pr_}
    return out


async def main() -> None:
    print("[1/2] walk-forward window ...")
    fr, folds = build_training_frame()
    _, pos, sig, oof = walkforward_signals(fr, folds)
    sc = oof.set_index("pos")
    wf = await run_window("WALK-FORWARD 2025-06..2026-01 (rule chosen on this window)", fr, pos, sig,
                          sc["p_bull_raw"].to_dict(), sc["p_bear_raw"].to_dict(), OUT_DIR / "walkforward")

    print("\n[2/2] Aug-Sep 2026 holdout ...")
    bars = load_bars(False)
    hfr, hpos = holdout_frame(bars, "intraday")
    meta, boosters = load_model("xau_intraday_ct25")
    sd = ml_signals(meta, boosters, hfr, hpos)
    ho = await run_window("HOLDOUT Aug-Sep 2026", hfr, hpos, sd["signal"].to_numpy(),
                          dict(zip(sd["pos"], sd["p_bull"])), dict(zip(sd["pos"], sd["p_bear"])), None)
    (OUT_DIR / "summary_v6_trend.json").write_text(json.dumps({"walkforward": wf, "holdout": ho}, indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
