"""Forward paper-trade A/B: v0.5 meta-model alone vs calendar rule vs LLM veto, on LIVE bars.

Why: the Aug-Sep 2026 holdout could not separate the LLM gate from a random veto. The only
clean test left is pre-registration — the LLM must answer BEFORE the trade's outcome exists.
This script does exactly that, one run at a time, and scores trades once their 4h window
has closed. Nothing here trades; it only logs.

Each run (idempotent, safe to run hourly):
  1. refresh bars  — GC=F 15m/1h from yfinance, merged into data/forward_cache/ (yfinance
                     keeps only 60 days of 15m, the store keeps everything); VIX/DXY/US10Y
                     daily cache refreshed; bars still in progress are dropped
  2. signals       — same feature pipeline + v5 boosters as the holdout experiment
  3. candidates    — every CLOSED signal bar since START not yet logged gets an LLM answer
                     (anonymised packet: no date, no price level) and the calendar verdict,
                     appended to results/forward_ab/candidates.jsonl with `decided_at`
  4. replay        — each arm replays the candidate log with one-position-at-a-time rules
                     (identical to the holdout simulator); a trade whose window has not
                     closed is `pending` and blocks that arm until it resolves
  5. report        — results/forward_ab/report.md + trades_<arm>.csv

Arms: ml_only · calendar_rule · llm_veto (half = 0.5R) · llm_binary (half = take)

Run:   %USERPROFILE%\\.venvs\\xau-ml\\Scripts\\python forward_ab.py [--provider ollama_cloud --model gpt-oss:120b]
Hourly (Windows Task Scheduler, run once from a shell):
  schtasks /Create /SC HOURLY /TN "XAU forward AB" /TR "\"%USERPROFILE%\\.venvs\\xau-ml\\Scripts\\python.exe\" \"<repo>\\Product\\notebook\\forward_ab.py\"" /F
Judge after ~100 resolved trades per arm, not before.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from llm_filter_experiment import (  # noqa: E402
    EVENT_AFTER_H,
    EVENT_BEFORE_H,
    Calendar,
    LLMGate,
    context_packet,
    load_model,
    ml_signals,
)
from xau_ml import features as F  # noqa: E402
from xau_ml import holdout as H  # noqa: E402
from xau_ml import sim as S  # noqa: E402
from xau_ml.config import COST_PRICE, HORIZONS, LABEL_SETS, RISK_PCT  # noqa: E402
from xau_ml.labels import add_labels  # noqa: E402
from xau_ml.market_data import fetch_daily  # noqa: E402
from xau_ml.v3 import add_v3_features  # noqa: E402
from export_v5 import add_regime_features  # noqa: E402

STORE = ROOT / "data" / "forward_cache"
SEED_15M = ROOT / "data" / "holdout_cache" / "GCF_15m.csv"  # history before the 60-day window
OUT = ROOT / "results" / "forward_ab"
STATE = OUT / "state.json"
CANDIDATES = OUT / "candidates.jsonl"
DEFAULT_START = "2026-10-05 00:00"  # server clock; fixed on the first run and never moved
ARMS = ("ml_only", "calendar_rule", "trend_rule", "llm_veto", "llm_binary")
WEIGHT = {"take": 1.0, "half": 0.5, "skip": 0.0}


# --------------------------------------------------------------------------------------
# bars
# --------------------------------------------------------------------------------------

def _now_server() -> pd.Timestamp:
    return pd.Timestamp.now(tz="America/New_York").tz_localize(None) + H.SERVER_OFFSET


def _merge(old: pd.DataFrame | None, new: pd.DataFrame) -> pd.DataFrame:
    df = new if old is None else pd.concat([old, new], ignore_index=True)
    df = df.drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)
    return df


def refresh_bars() -> dict[str, pd.DataFrame]:
    STORE.mkdir(parents=True, exist_ok=True)
    now = _now_server()
    out: dict[str, pd.DataFrame] = {}
    for tf, kw in (("15m", {"period": "60d"}), ("1h", {"start": "2025-01-01"})):
        p = STORE / f"GCF_{tf}.csv"
        old = pd.read_csv(p, parse_dates=["date", "t_close"]) if p.exists() else None
        if old is None and tf == "15m" and SEED_15M.exists():
            old = pd.read_csv(SEED_15M, parse_dates=["date", "t_close"])
        try:
            new = H.fetch(tf, **kw)
        except Exception as exc:  # noqa: BLE001 — a feed hiccup must not lose the store
            print(f"      WARN {tf} fetch failed ({exc!r}); using the store as is")
            new = old.iloc[0:0] if old is not None else None
        if new is None:
            raise SystemExit(f"no {tf} bars available")
        df = _merge(old, new)
        df = df[df["t_close"] <= now].reset_index(drop=True)  # never a bar still forming
        df.to_csv(p, index=False)
        out[tf] = df
    out["1d"] = H.daily_from_hourly(out["1h"])
    for sym in ("^VIX", "DX-Y.NYB", "^TNX"):
        try:
            fetch_daily(sym, use_cache=False)
        except Exception as exc:  # noqa: BLE001
            print(f"      WARN {sym} refresh failed ({exc!r}); stale cache used")
    return out


def build_frame(bars: dict[str, pd.DataFrame]) -> pd.DataFrame:
    hz, ls = HORIZONS["intraday"], LABEL_SETS["ct25"]
    fr = F.build(bars["15m"], "15m", {tf: bars[tf] for tf in hz.htf})
    fr = F.add_extras(fr, bars["1d"])
    fr = add_v3_features(fr, use_cache=True)
    fr = add_labels(fr, hz, ls)  # NaN where the 4h window has not closed yet
    return add_regime_features(fr, bars["1d"])


# --------------------------------------------------------------------------------------
# state / candidate log
# --------------------------------------------------------------------------------------

def load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"start": DEFAULT_START, "runs": 0}


def save_state(st: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, indent=2, default=str), encoding="utf-8")


def load_candidates() -> list[dict]:
    if not CANDIDATES.exists():
        return []
    return [json.loads(l) for l in CANDIDATES.read_text(encoding="utf-8").splitlines() if l.strip()]


def append_candidates(recs: list[dict]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with CANDIDATES.open("a", encoding="utf-8") as fh:
        for r in recs:
            fh.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")


# --------------------------------------------------------------------------------------
# replay
# --------------------------------------------------------------------------------------

def _arm_action(arm: str, c: dict) -> str:
    if arm == "ml_only":
        return "take"
    if arm == "calendar_rule":
        return c["calendar"]
    if arm == "trend_rule":  # skip trades that fade the daily trend (their TP is the counter-trend 2.5R)
        return "skip" if c["tp_r"] > 1.5 else "take"
    a = c["llm"]["action"]
    if arm == "llm_binary" and a == "half":
        return "take"
    return a


def replay(arm: str, cands: list[dict], fr: pd.DataFrame) -> pd.DataFrame:
    """One position at a time. Pending (unresolved) trade blocks the arm; later candidates
    are left for a future run — their LLM answer is already committed in the log."""
    idx = {str(pd.Timestamp(d)): i for i, d in enumerate(fr["date"].to_numpy())}  # same str() as the log
    r_col = {"long": fr["r_bull"].to_numpy(), "short": fr["r_bear"].to_numpy()}
    off_col = {"long": fr["exit_bull"].to_numpy(), "short": fr["exit_bear"].to_numpy()}
    risk = fr["risk"].to_numpy()
    dates = fr["date"].to_numpy()
    rows: list[dict] = []
    free_from: int = -1
    for c in sorted(cands, key=lambda x: x["bar_ts"]):
        t = idx.get(c["bar_ts"])
        if t is None or t < free_from:
            continue
        action = _arm_action(arm, c)
        w = WEIGHT[action]
        base = {"decided": c["bar_ts"], "side": c["side"], "action": action, "weight": w,
                "conf": c["conf"], "decided_at": c["decided_at"]}
        if w == 0.0:
            rows.append({**base, "status": "skipped", "r": 0.0, "r_full": np.nan})
            continue
        rr = r_col[c["side"]][t]
        if np.isnan(rr) or t + 1 >= len(fr):
            rows.append({**base, "status": "pending", "r": np.nan, "r_full": np.nan})
            break  # blocks the arm until the window closes
        r_full = float(rr - COST_PRICE / risk[t])
        exit_idx = t + 1 + int(off_col[c["side"]][t])
        rows.append({**base, "status": "resolved", "r_full": r_full, "r": w * r_full,
                     "entry": str(dates[t + 1]), "exit": str(dates[min(exit_idx, len(fr) - 1)]),
                     "bars": int(off_col[c["side"]][t])})
        free_from = exit_idx
    return pd.DataFrame(rows)


def arm_metrics(tr: pd.DataFrame) -> dict:
    res = tr[tr["status"] == "resolved"].copy() if len(tr) else tr
    out = {"resolved": int(len(res)), "pending": int((tr["status"] == "pending").sum()) if len(tr) else 0,
           "skipped": int((tr["status"] == "skipped").sum()) if len(tr) else 0}
    if len(res):
        res["equity"] = np.cumprod(1 + RISK_PCT * res["r"].to_numpy())
        m = S.metrics(res)
        out.update({k: (None if isinstance(v, float) and (np.isnan(v) or np.isinf(v)) else round(v, 3))
                    for k, v in m.items() if k in ("win_rate", "profit_factor", "expectancy_r", "total_r", "max_dd_pct")})
        out["r_per_unit_risk"] = round(float(res["r"].sum() / res["weight"].sum()), 3) if res["weight"].sum() else None
    return out


# --------------------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------------------

async def main_async(args: argparse.Namespace) -> None:
    t0 = time.time()
    st = load_state()
    start = pd.Timestamp(st["start"])
    now = _now_server()
    print(f"[1/5] bars (server now {now:%Y-%m-%d %H:%M}, start {start:%Y-%m-%d %H:%M}) ...")
    bars = refresh_bars()
    print(f"      15m {bars['15m']['date'].min()} .. {bars['15m']['date'].max()} ({len(bars['15m'])} bars)")

    print("[2/5] frame + v5 signals ...")
    fr = build_frame(bars)
    meta, boosters = load_model("xau_intraday_ct25")
    rows = np.flatnonzero(((fr["date"] >= start) & (fr["no_entry"] == 0)).to_numpy())
    sigdf = ml_signals(meta, boosters, fr, rows) if len(rows) else pd.DataFrame(columns=["pos", "p_bull", "p_bear", "conf", "signal"])
    sig_rows = sigdf[sigdf["signal"] != 0]
    print(f"      decision rows since start {len(rows)}, signal rows {len(sig_rows)}")

    print(f"[3/5] candidates ({args.model} via {args.provider}, anon) ...")
    cands = load_candidates()
    known = {(c["bar_ts"], c["side"]) for c in cands}
    cal = Calendar()
    gate = LLMGate(args.provider, args.model, "anon", args.concurrency, args.dry_run, cache_dir=OUT)
    h_to, h_since = fr["cal_hours_to_next"].to_numpy(), fr["cal_hours_since_last"].to_numpy()
    new: list[dict] = []

    async with httpx.AsyncClient() as client:
        async def one(t: int, s: int, conf: float, pb: float, pr: float) -> None:
            row = fr.iloc[t]
            bar_ts = str(row["date"])
            side = "long" if s == 1 else "short"
            if (bar_ts, side) in known:
                return
            pkt = context_packet(fr, t, s, conf, pb, pr, cal, "anon")
            rec = await gate.decide(client, bar_ts, s, pkt)
            decided_at = _now_server()
            new.append({
                "bar_ts": bar_ts, "t_close": str(row["t_close"]), "side": side,
                "conf": round(conf, 4), "p_bull": round(pb, 4), "p_bear": round(pr, 4),
                "tp_r": float(row["tp_bull"] if s == 1 else row["tp_bear"]),
                "risk": round(float(row["risk"]), 3), "close": float(row["close"]),
                "decided_at": str(decided_at.floor("s")),
                "late_minutes": int((decided_at - row["t_close"]).total_seconds() // 60),
                "calendar": "skip" if (h_to[t] <= EVENT_BEFORE_H or h_since[t] <= EVENT_AFTER_H) else "take",
                "llm": {"model": args.model, "action": rec["action"], "reason": rec.get("reason", ""),
                        "risk_flags": rec.get("risk_flags", [])},
            })

        await asyncio.gather(*[one(int(t), int(s), float(c), float(pb), float(pr)) for t, s, c, pb, pr in
                               zip(sig_rows["pos"], sig_rows["signal"], sig_rows["conf"], sig_rows["p_bull"], sig_rows["p_bear"])])
    new.sort(key=lambda x: x["bar_ts"])
    if args.dry_run:
        print("      dry-run: candidates NOT written (answers are not real LLM verdicts)")
    else:
        append_candidates(new)
    cands += new
    late = [c["late_minutes"] for c in new]
    print(f"      new {len(new)} (LLM calls {gate.calls}, parse failures {gate.parse_failures}); total {len(cands)}"
          + (f"; decided {min(late)}..{max(late)} min after bar close" if late else ""))

    print("[4/5] replay")
    OUT.mkdir(parents=True, exist_ok=True)
    table: dict[str, dict] = {}
    for arm in ARMS:
        tr = replay(arm, cands, fr)
        if not args.dry_run:
            tr.to_csv(OUT / f"trades_{arm}.csv", index=False)
        table[arm] = arm_metrics(tr)
    df = pd.DataFrame(table).T
    print(df.to_string())
    actions = pd.Series([c["llm"]["action"] for c in cands]).value_counts().to_dict() if cands else {}

    print("[5/5] report")
    lines = [
        "# Forward A/B — XAUUSD intraday v0.5 vs gates (paper, no orders)", "",
        f"- start (server clock, pre-registered): **{start:%Y-%m-%d %H:%M}**",
        f"- last run: {now:%Y-%m-%d %H:%M} server · runs: {st['runs'] + 1}",
        f"- bars: 15m {bars['15m']['date'].min():%Y-%m-%d} .. {bars['15m']['date'].max():%Y-%m-%d %H:%M}",
        f"- candidates logged: {len(cands)} · LLM actions: {actions}",
        f"- LLM: {args.model} via {args.provider}, anonymised packet (no date / price), temperature 0", "",
        "LLM answers are committed when the bar closes (see `decided_at` / `late_minutes` in "
        "`candidates.jsonl`); outcomes are scored only after the 16-bar window closes.", "",
        "| arm | resolved | pending | skipped | win% | PF | exp R | total R | R / unit risk | max DD % |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for arm, m in table.items():
        wr = m.get("win_rate")
        lines.append(f"| {arm} | {m['resolved']} | {m['pending']} | {m['skipped']} | "
                     f"{'' if wr is None else f'{100 * wr:.1f}'} | {m.get('profit_factor', '')} | {m.get('expectancy_r', '')} | "
                     f"{m.get('total_r', '')} | {m.get('r_per_unit_risk', '')} | {m.get('max_dd_pct', '')} |")
    lines += ["", "Judge after ~100 resolved trades per arm. Win-rate SE at n=100 is about ±5 pp; "
              "differences inside that band are noise.", ""]
    if args.dry_run:
        print("      dry-run: report/state NOT written")
    else:
        (OUT / "report.md").write_text("\n".join(lines), encoding="utf-8")
        st.update({"runs": st["runs"] + 1, "last_run_server": str(now.floor("s")), "candidates": len(cands),
                   "llm_model": args.model, "llm_provider": args.provider})
        save_state(st)
    print(f"      {OUT / 'report.md'}  ({time.time() - t0:.0f}s)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--provider", default="ollama_cloud")
    ap.add_argument("--model", default="gpt-oss:120b")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--dry-run", action="store_true", help="no LLM calls (every answer = take); for plumbing tests only")
    asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    main()
