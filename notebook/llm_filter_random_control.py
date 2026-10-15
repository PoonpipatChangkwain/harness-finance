"""Control for llm_filter_experiment: how good does a *random* veto look on this holdout?

Skips each candidate with the same probability the LLM skipped (anon: 15 of 218), many
times, on the same simulator. If the LLM's PF / total R sit inside the bulk of this
distribution, the LLM did nothing a coin could not.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from llm_filter_experiment import build_frame, load_bars, load_model, ml_signals, run_gated  # noqa: E402
from xau_ml import sim as S  # noqa: E402

N_SIM = 500
P_SKIP = 15 / 218  # anon LLM: 15 skips over 218 consulted candidates
LLM_ANON = {"profit_factor": 1.202, "total_r": 20.567, "win_rate": 0.459}
LLM_HALF = {"profit_factor": 1.245}


async def main() -> None:
    bars = load_bars(False)
    fr, pos = build_frame(bars, "intraday")
    meta, boosters = load_model("xau_intraday_ct25")
    sig = ml_signals(meta, boosters, fr, pos)["signal"].to_numpy()

    async def take(t, s):
        return "take"

    _, base = await run_gated(fr, pos, sig, take)
    print("ML only:", {k: round(v, 3) for k, v in S.metrics(base).items() if k in ("trades", "win_rate", "profit_factor", "total_r")})

    rng = np.random.default_rng(1)
    rows = []
    for i in range(N_SIM):
        async def gate(t, s, r=rng):
            return "skip" if r.random() < P_SKIP else "take"

        _, tk = await run_gated(fr, pos, sig, gate)
        m = S.metrics(tk)
        rows.append({k: m[k] for k in ("trades", "win_rate", "profit_factor", "total_r", "max_dd_pct")})
    df = pd.DataFrame(rows)
    q = df.quantile([0.05, 0.25, 0.5, 0.75, 0.95]).round(3)
    print(f"\nrandom veto x{N_SIM} (skip prob {P_SKIP:.3f}):")
    print(q.to_string())
    for k, v in LLM_ANON.items():
        pct = (df[k] < v).mean() * 100
        print(f"LLM anon binary {k}={v}: beats {pct:.0f}% of random vetoes")

    # second control: random *halving* with the LLM's half rate (139/218) on top of random skips
    P_HALF = 139 / 218
    rows2 = []
    for i in range(N_SIM):
        async def gate2(t, s, r=rng):
            u = r.random()
            return "skip" if u < P_SKIP else ("half" if u < P_SKIP + P_HALF else "take")

        _, tk = await run_gated(fr, pos, sig, gate2)
        rows2.append(S.metrics(tk)["profit_factor"])
    pf2 = pd.Series(rows2)
    print(f"\nrandom skip+half x{N_SIM}: PF median {pf2.median():.3f}, 5-95% [{pf2.quantile(.05):.3f}, {pf2.quantile(.95):.3f}]")
    print(f"LLM anon half=0.5R PF={LLM_HALF['profit_factor']}: beats {(pf2 < LLM_HALF['profit_factor']).mean()*100:.0f}% of random sizings")

    # bootstrap CI of ML-only PF itself (sampling error of the reference)
    r = base["r"].to_numpy()
    boots = []
    for _ in range(5000):
        x = rng.choice(r, len(r), replace=True)
        boots.append(x[x > 0].sum() / -x[x < 0].sum())
    b = np.array(boots)
    print(f"\nML-only PF bootstrap 90% CI: [{np.percentile(b, 5):.2f}, {np.percentile(b, 95):.2f}]  (point 1.146)")


if __name__ == "__main__":
    asyncio.run(main())
