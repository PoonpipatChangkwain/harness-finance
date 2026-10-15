"""Decision table: which gate configuration is good enough to enable now?

Reads the per-arm trade lists written by llm_v6_trend.py and, per window and pooled, reports
mean R per trade with a bootstrap 90% CI, P(mean R > 0), and R per unit of risk for the half-size
arms. Bootstrap resamples trades (positions are one-at-a-time so trades do not overlap).

Criteria for "use now" (set before looking at the table):
  1. mean R > 0 in BOTH windows, and
  2. pooled P(mean R > 0) >= 0.90, and
  3. the gate is not fitted on the evaluation window (rule/LLM chosen a priori), and
  4. max drawdown < 25% in both windows.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from xau_ml import sim as S  # noqa: E402

D = ROOT / "results" / "llm_filter" / "v6"
ARMS = ["ml_only", "trend_rule", "llm_binary", "trend_llm_binary", "trend_llm_half"]
FITTED_ON_WF = {"trend_rule": True, "trend_llm_binary": True, "trend_llm_half": True}
rng = np.random.default_rng(11)


def stats(r: np.ndarray, w: np.ndarray) -> dict:
    n = len(r)
    boots = np.array([r[i].sum() / w[i].sum() for i in (rng.integers(0, n, n) for _ in range(4000))])
    per_unit = r.sum() / w.sum()
    return {"n": n, "R/unit_risk": per_unit, "p5": np.percentile(boots, 5), "p95": np.percentile(boots, 95),
            "P(>0)": float((boots > 0).mean())}


def dd(r: np.ndarray) -> float:
    eq = np.r_[1.0, np.cumprod(1 + 0.01 * r)]
    return float(100 * (1 - eq / np.maximum.accumulate(eq)).max())


rows = []
for arm in ARMS:
    wf = pd.read_csv(D / f"wf_{arm}.csv")
    ho = pd.read_csv(D / f"ho_{arm}.csv")
    out = {"arm": arm}
    for tag, df in (("WF", wf), ("HO", ho), ("pooled", pd.concat([wf, ho]))):
        s = stats(df["r"].to_numpy(), df["weight"].to_numpy())
        out.update({f"{tag}_n": s["n"], f"{tag}_R": round(s["R/unit_risk"], 3),
                    f"{tag}_CI90": f"[{s['p5']:+.2f},{s['p95']:+.2f}]", f"{tag}_P>0": round(s["P(>0)"], 2)})
        if tag != "pooled":
            out[f"{tag}_DD"] = round(dd(df["r"].to_numpy()), 1)
    c1 = out["WF_R"] > 0 and out["HO_R"] > 0
    c2 = out["pooled_P>0"] >= 0.90
    c3 = not FITTED_ON_WF.get(arm, False)
    c4 = out["WF_DD"] < 25 and out["HO_DD"] < 25
    out["crit"] = f"{'1' if c1 else '-'}{'2' if c2 else '-'}{'3' if c3 else '-'}{'4' if c4 else '-'}"
    rows.append(out)
pd.set_option("display.width", 250)
print(pd.DataFrame(rows).set_index("arm").T.to_string())
