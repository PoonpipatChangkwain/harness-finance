"""LLM veto gate on top of the v0.5 meta-model — Aug-Sep 2026 holdout experiment.

Question: if an LLM may only *skip / halve / take* each trade the ML proposes (never change
its side, stop or target), does the holdout win rate / expectancy improve?

Arms, all on the same rows, labels and simulator (``xau_ml.sim.run`` semantics):
  ml_only        meta-model C (conf > 0.40) — the reference
  calendar_rule  ml_only, but skip inside the event window (<= 2h before / <= 1h after
                 NFP / FOMC / CPI) — the cheap deterministic version of "event veto"
  llm_veto       ml_only, LLM decides take / half / skip per candidate
  llm_binary     same LLM answers, but half counts as take (pure veto)

Leakage control: in ``--mode anon`` (default) the LLM never sees the calendar date or the
price level — only weekday, server hour, session, hours to/since the next scheduled
release and every feature in ATR / ratio units. ``--mode dated`` adds the date and price
so the difference between the two modes measures how much the model "remembers" of 2026.

Run:  cd notebook && %USERPROFILE%\\.venvs\\xau-ml\\Scripts\\python llm_filter_experiment.py
        [--mode anon|dated] [--provider openrouter|groq|openai|...] [--model ...]
The provider's key comes from the env or ../backend/.env. LLM answers are cached per
(mode, model, bar_ts, side) in results/llm_filter/, so re-runs are free. (Groq caps
gpt-oss-120b at 8k tokens/min, ~5 calls/min — hence OpenRouter by default.)
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning, module="lightgbm")

import httpx
import lightgbm as lgb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from xau_ml import features as F  # noqa: E402
from xau_ml import holdout as H  # noqa: E402
from xau_ml import sim as S  # noqa: E402
from xau_ml.calendar import build_event_schedule  # noqa: E402
from xau_ml.config import COST_PRICE, HORIZONS, LABEL_SETS, RISK_PCT, TF_DELTA  # noqa: E402
from xau_ml.labels import add_labels  # noqa: E402
from xau_ml.v3 import add_v3_features  # noqa: E402
from export_v5 import MODEL_DIR_V5, add_regime_features  # noqa: E402

BAR_CACHE = ROOT / "data" / "holdout_cache"
OUT_DIR = ROOT / "results" / "llm_filter"
# OpenAI-compatible chat endpoints; the key is read from the env var or ../backend/.env
PROVIDERS = {
    "groq": ("https://api.groq.com/openai/v1/chat/completions", "GROQ_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1/chat/completions", "OPENROUTER_API_KEY"),
    "openai": ("https://api.openai.com/v1/chat/completions", "OPENAI_API_KEY"),
    "deepseek": ("https://api.deepseek.com/v1/chat/completions", "DEEPSEEK_API_KEY"),
    "xai": ("https://api.x.ai/v1/chat/completions", "XAI_API_KEY"),
    "ollama_cloud": ("https://ollama.com/v1/chat/completions", "OLLAMA_API_KEY"),
}
MIN_CONF = 0.40
EVENT_BEFORE_H, EVENT_AFTER_H = 2.0, 1.0
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
SESSIONS = {0.0: "asia", 1.0: "london", 2.0: "new_york"}
REGIMES = {1.0: "UP", 0.0: "SIDE", -1.0: "DOWN"}
TRENDS = {1.0: "up", -1.0: "down", 0.0: "mixed"}

SYSTEM_PROMPT = """You are the risk gate of a systematic XAUUSD (gold) intraday strategy.
A machine-learning model has proposed a trade. You cannot change its direction, stop,
target or timing. You decide only how much of it to take:
  "take" = full size, "half" = half size, "skip" = do not trade.

The model already has a measured statistical edge on the features it sees. Do not
second-guess its probabilities. Skip or halve ONLY for a specific, nameable reason the
model cannot weigh well, for example:
  - a high-impact release (NFP, FOMC, CPI) within about 2 hours ahead or 1 hour behind,
    so the stop would be hit by a spike rather than by the trade being wrong;
  - an entry so late in the session or in such thin liquidity that the target is
    unlikely to be reached before the 4-hour time stop;
  - the intraday move is already exhausted in the trade's direction (no room left to the
    target), or the daily regime clearly contradicts the trade.
The default answer is "take". Expect to take most trades.

Answer with JSON only, no prose:
{"action": "take" | "half" | "skip", "reason": "<one sentence>", "risk_flags": ["..."]}"""


# --------------------------------------------------------------------------------------
# data / model
# --------------------------------------------------------------------------------------

def load_bars(refetch: bool) -> dict[str, pd.DataFrame]:
    """Holdout bars from yfinance, cached to CSV so the experiment is reproducible after
    yfinance's 60-day 15m window has rolled past August."""
    BAR_CACHE.mkdir(parents=True, exist_ok=True)
    paths = {tf: BAR_CACHE / f"GCF_{tf}.csv" for tf in ("15m", "1h", "1d")}
    if not refetch and all(p.exists() for p in paths.values()):
        out = {}
        for tf, p in paths.items():
            df = pd.read_csv(p, parse_dates=["date", "t_close"])
            out[tf] = df
        return out
    bars = H.load_bars()
    for tf, df in bars.items():
        df.to_csv(paths[tf], index=False)
    return bars


def build_frame(bars: dict[str, pd.DataFrame], hz_name: str) -> tuple[pd.DataFrame, np.ndarray]:
    hz = HORIZONS[hz_name]
    ls = LABEL_SETS["ct25"]
    htfs = {tf: bars[tf] for tf in hz.htf}
    fr = F.build(bars[hz.tf], hz.tf, htfs)
    fr = F.add_extras(fr, bars["1d"])
    fr = add_v3_features(fr, use_cache=True)
    fr = add_labels(fr, hz, ls)
    fr = add_regime_features(fr, bars["1d"])
    return fr, H.holdout_rows(fr)


def load_model(stem: str) -> tuple[dict, dict[str, lgb.Booster]]:
    meta = json.loads((MODEL_DIR_V5 / f"{stem}.json").read_text(encoding="utf-8"))
    boosters = {k: lgb.Booster(model_file=str(MODEL_DIR_V5 / v["file"])) for k, v in meta["files"].items()}
    return meta, boosters


def ml_signals(meta: dict, boosters: dict[str, lgb.Booster], fr: pd.DataFrame, pos: np.ndarray) -> pd.DataFrame:
    X = fr.iloc[pos][meta["features"]]
    pb = boosters["bull"].predict(X)
    pr = boosters["bear"].predict(X)
    m = pd.DataFrame({"p_bull": pb, "p_bear": pr, "p_diff": pb - pr, "p_max": np.maximum(pb, pr), "p_sum": pb + pr})
    for c in meta["meta_context_features"]:
        m[c] = fr[c].to_numpy()[pos]
    probs = boosters["meta"].predict(m[meta["meta_features"]])
    cls = probs.argmax(axis=1)
    conf = probs.max(axis=1)
    sig = np.where(cls == 1, 1, np.where(cls == 2, -1, 0)).astype("int8")
    sig[conf < MIN_CONF] = 0
    return pd.DataFrame({"pos": pos, "p_bull": pb, "p_bear": pr, "conf": conf, "signal": sig})


# --------------------------------------------------------------------------------------
# context packet for the LLM
# --------------------------------------------------------------------------------------

def _f(x, nd: int = 2):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if np.isnan(v) else round(v, nd)


class Calendar:
    def __init__(self, start: str = "2024-01-01", end: str = "2026-12-31") -> None:
        cal = build_event_schedule(start, end)
        self.times = cal["event_time"].to_numpy().astype("datetime64[ns]")
        self.names = cal["event"].tolist()

    def around(self, bt: pd.Timestamp) -> dict:
        t = np.datetime64(bt, "ns")
        fut = np.flatnonzero(self.times > t)
        past = np.flatnonzero(self.times <= t)
        out: dict = {}
        if len(fut):
            i = fut[0]
            out["next_event"] = self.names[i]
            out["hours_to_next"] = round(float((self.times[i] - t) / np.timedelta64(1, "h")), 1)
        if len(past):
            i = past[-1]
            out["last_event"] = self.names[i]
            out["hours_since_last"] = round(float((t - self.times[i]) / np.timedelta64(1, "h")), 1)
        return out


def context_packet(fr: pd.DataFrame, t: int, side: int, conf: float, pb: float, pr: float,
                   cal: Calendar, mode: str) -> dict:
    row = fr.iloc[t]
    bt: pd.Timestamp = row["date"]
    trend = float(row["d1_trend"]) if not pd.isna(row["d1_trend"]) else 0.0
    tp_r = float(row["tp_bull"] if side == 1 else row["tp_bear"])
    pkt: dict = {
        "instrument": "XAUUSD spot gold, 15-minute bars",
        "proposed_trade": {
            "side": "long" if side == 1 else "short",
            "entry": "open of the next 15m bar",
            "stop": "1.5 x ATR14 (1R)",
            "take_profit_r": tp_r,
            "time_stop": "16 bars (4 hours)",
            "counter_trend": bool(trend == -side),
        },
        "model": {"confidence": _f(conf, 3), "p_bull": _f(pb, 3), "p_bear": _f(pr, 3)},
        "time": {
            "weekday": WEEKDAYS[bt.dayofweek],
            "server_hour": int(bt.hour),
            "server_clock": "New York + 7h (00:00 = 17:00 NY, the daily break)",
            "session": SESSIONS.get(float(row["session"]), "unknown"),
        },
        "daily_context": {
            "daily_trend_vs_sma50_sma200": TRENDS.get(trend, "mixed"),
            "regime": REGIMES.get(float(row["regime"]), "unknown"),
            "price_vs_sma50_daily_in_atr": _f(row["regime_trend_str"]),
            "sma50_slope_5d_in_atr": _f(row["regime_sma50_slope"]),
            "price_vs_sma200_daily_in_atr": _f(row["d1_dist_sma200_atr"]),
            "daily_rsi14": _f(row["d1_rsi14"], 1),
            "daily_adx14": _f(row["d1_adx14"], 1),
            "position_in_20d_range_0_1": _f(row["d1_range_pos_20"]),
            "ret_5d_in_atr": _f(row["d1_ret_atr_5"]),
        },
        "intraday_context": {
            "adx14": _f(row["adx14"], 1),
            "rsi14": _f(row["rsi14"], 1),
            "atr_vs_its_96bar_avg": _f(row["atr_regime"]),
            "bb_width": _f(row["bb_width"], 3),
            "ret_last_1h_in_atr": _f(row["ret_atr_4"]),
            "ret_last_4h_in_atr": _f(row["ret_atr_16"]),
            "ret_last_12h_in_atr": _f(row["ret_atr_48"]),
            "position_in_24h_range_0_1": _f(row["range_pos_96"]),
            "today_move_from_open_in_atr": _f(row["day_move_atr"]),
            "today_range_in_atr": _f(row["day_range_atr"]),
            "dist_to_prev_day_high_in_atr": _f(row["dist_pdh_atr"]),
            "dist_to_prev_day_low_in_atr": _f(row["dist_pdl_atr"]),
        },
        "macro_context_daily": {
            "vix": _f(row["vix"], 1),
            "vix_vs_sma20": _f(row["vix_level"]),
            "vix_ret_5d": _f(row["vix_ret_5"], 3),
            "dxy_zscore_vs_sma20": _f(row["dxy_dist_sma20"]),
            "dxy_ret_5d": _f(row["dxy_ret_5"], 4),
            "us10y_yield_pct": _f(row["us10y"]),
            "us10y_chg_5d_bps": _f(float(row["us10y_chg_5"]) * 100 if not pd.isna(row["us10y_chg_5"]) else None, 0),
        },
        "scheduled_releases": {
            **cal.around(bt),
            "is_nfp_day": bool(row["cal_is_nfp_day"] > 0.5),
            "is_fomc_day": bool(row["cal_is_fomc_day"] > 0.5),
        },
    }
    if mode == "dated":
        pkt["time"]["server_datetime"] = bt.strftime("%Y-%m-%d %H:%M")
        pkt["price"] = {"close": _f(row["close"]), "atr14": _f(row["atr14"])}
    return pkt


# --------------------------------------------------------------------------------------
# LLM client with cache
# --------------------------------------------------------------------------------------

def _api_key(env_name: str) -> str:
    key = os.environ.get(env_name)
    if key:
        return key
    env = ROOT.parent / "backend" / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{env_name}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit(f"{env_name} not found (env or backend/.env)")


class LLMGate:
    def __init__(self, provider: str, model: str, mode: str, concurrency: int, dry_run: bool,
                 cache_dir: Path | None = None) -> None:
        self.model, self.mode, self.dry_run = model, mode, dry_run
        self.url, key_env = PROVIDERS[provider]
        slug = model.replace("/", "_").replace(":", "_")
        cache_dir = cache_dir or OUT_DIR
        cache_dir.mkdir(parents=True, exist_ok=True)
        # cache key = (mode, model, bar, side): the same model through another provider reuses it
        self.cache_path = cache_dir / f"llm_cache_{mode}_{slug}.jsonl"
        self.cache: dict[str, dict] = {}
        if self.cache_path.exists():
            for line in self.cache_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    rec = json.loads(line)
                    self.cache[rec["key"]] = rec
        self.sem = asyncio.Semaphore(concurrency)
        self.key = None if dry_run else _api_key(key_env)
        self.calls = 0
        self.parse_failures = 0

    def _key(self, bar_ts: str, side: int) -> str:
        return hashlib.sha256(f"{self.mode}|{self.model}|{bar_ts}|{side}".encode()).hexdigest()[:24]

    async def decide(self, client: httpx.AsyncClient, bar_ts: str, side: int, packet: dict) -> dict:
        k = self._key(bar_ts, side)
        if k in self.cache:
            return self.cache[k]
        if self.dry_run:
            return {"key": k, "action": "take", "reason": "dry-run", "risk_flags": []}
        body = {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(packet, ensure_ascii=False)},
            ],
        }
        text, usage = await self._post(client, body)
        self.calls += 1
        parsed = self._parse(text)
        if parsed is None:
            self.parse_failures += 1
            parsed = {"action": "take", "reason": "unparseable answer -> fail-open", "risk_flags": ["parse_error"]}
        rec = {"key": k, "bar_ts": bar_ts, "side": side, **parsed, "raw": text, "usage": usage}
        self.cache[k] = rec
        with self.cache_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    async def _post(self, client: httpx.AsyncClient, body: dict) -> tuple[str, dict]:
        delay = 1.0
        for attempt in range(6):
            async with self.sem:
                try:
                    r = await client.post(self.url, json=body, headers={"Authorization": f"Bearer {self.key}"}, timeout=90)
                except (httpx.TimeoutException, httpx.TransportError):
                    r = None
            if r is not None and r.status_code == 200:
                data = r.json()
                return data["choices"][0]["message"]["content"], data.get("usage", {})
            if r is not None and r.status_code not in (408, 429, 500, 502, 503, 504):
                raise RuntimeError(f"LLM HTTP {r.status_code}: {r.text[:300]}")
            retry_after = float(r.headers.get("retry-after", 0)) if r is not None else 0
            await asyncio.sleep(max(delay, retry_after))
            delay = min(delay * 2, 20)
        raise RuntimeError("LLM: gave up after retries")

    @staticmethod
    def _parse(text: str) -> dict | None:
        try:
            s = text.strip()
            if s.startswith("```"):
                s = s.strip("`")
                s = s[s.find("{"):]
            obj = json.loads(s[s.find("{"): s.rfind("}") + 1])
        except (ValueError, TypeError):
            return None
        action = str(obj.get("action", "")).lower().strip()
        if action not in ("take", "half", "skip"):
            return None
        flags = obj.get("risk_flags") or []
        return {"action": action, "reason": str(obj.get("reason", ""))[:300],
                "risk_flags": [str(x) for x in flags][:6]}


# --------------------------------------------------------------------------------------
# simulators
# --------------------------------------------------------------------------------------

WEIGHT = {"take": 1.0, "half": 0.5, "skip": 0.0}


async def run_gated(fr: pd.DataFrame, rows_idx: np.ndarray, sig: np.ndarray, gate, weights: dict[str, float] = WEIGHT):
    """``S.run`` with a per-candidate gate: ``await gate(t, side) -> action``. A skipped
    candidate leaves the slot free, exactly as if the model had said FLAT. The gate is only
    consulted for candidates that actually reach it (no open position), so the LLM is asked
    about ~200-300 rows, not every signal row."""
    r = {1: fr["r_bull"].to_numpy(), -1: fr["r_bear"].to_numpy()}
    off = {1: fr["exit_bull"].to_numpy(), -1: fr["exit_bear"].to_numpy()}
    risk = fr["risk"].to_numpy()
    dates = fr["date"].to_numpy()
    rows: list[dict] = []
    free_from = 0
    for t, s in zip(np.asarray(rows_idx), np.asarray(sig)):
        s = int(s)
        if s == 0 or t < free_from or t + 1 >= len(fr) or np.isnan(r[s][t]):
            continue
        action = await gate(int(t), s)
        w = weights[action]
        r_full = r[s][t] - COST_PRICE / risk[t]
        if w == 0.0:
            rows.append({"decided": dates[t], "side": "long" if s == 1 else "short", "action": action,
                         "r_full": r_full, "r": 0.0, "weight": 0.0, "bars": int(off[s][t]), "taken": False})
            continue
        exit_idx = t + 1 + int(off[s][t])
        rows.append({"decided": dates[t], "entry": dates[t + 1], "exit": dates[min(exit_idx, len(fr) - 1)],
                     "side": "long" if s == 1 else "short", "action": action, "r_full": r_full,
                     "r": w * r_full, "weight": w, "bars": int(off[s][t]), "taken": True})
        free_from = exit_idx
    df = pd.DataFrame(rows)
    taken = df[df["taken"]].copy() if len(df) else df
    if len(taken):
        taken["equity"] = np.cumprod(1 + RISK_PCT * taken["r"].to_numpy())
    return df, taken


def metrics_row(name: str, taken: pd.DataFrame) -> dict:
    m = S.metrics(taken) if len(taken) else S.metrics(pd.DataFrame(columns=["r", "side", "equity"]))
    return {"arm": name, **{k: (None if isinstance(v, float) and (np.isnan(v) or np.isinf(v)) else v) for k, v in m.items()}}


def by_month(taken: pd.DataFrame) -> pd.DataFrame:
    if taken.empty:
        return pd.DataFrame()
    g = taken.groupby(pd.to_datetime(taken["decided"]).dt.to_period("M"))
    return pd.DataFrame({str(k): S.metrics(t.assign(equity=np.cumprod(1 + RISK_PCT * t["r"].to_numpy()))) for k, t in g}).T[
        ["trades", "win_rate", "profit_factor", "expectancy_r", "total_r"]]


# --------------------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------------------

async def main_async(args: argparse.Namespace) -> None:
    t0 = time.time()
    hz_name = args.horizon
    stem = f"xau_{hz_name}_ct25"
    print(f"[1/5] bars ({'refetch' if args.refetch else 'cache if present'}) ...")
    bars = load_bars(args.refetch)
    print(f"      15m {bars['15m']['date'].min()} .. {bars['15m']['date'].max()} ({len(bars['15m'])} bars)")

    print("[2/5] frame + labels + v5 model ...")
    fr, pos = build_frame(bars, hz_name)
    meta, boosters = load_model(stem)
    sigdf = ml_signals(meta, boosters, fr, pos)
    sig = sigdf["signal"].to_numpy()
    conf = sigdf["conf"].to_numpy()
    print(f"      holdout rows {len(pos)}, signals {(sig != 0).sum()} (long {(sig == 1).sum()}, short {(sig == -1).sum()})")

    # --- reference arm: ML only --------------------------------------------------------
    async def take_gate(t: int, s: int) -> str:
        return "take"

    ml_all, ml_taken = await run_gated(fr, pos, sig, take_gate)
    print(f"      ml_only trades {len(ml_taken)}  win {ml_taken['r'].gt(0).mean():.3f}  total {ml_taken['r'].sum():+.1f}R")

    # --- deterministic calendar window -------------------------------------------------
    h_to = fr["cal_hours_to_next"].to_numpy()
    h_since = fr["cal_hours_since_last"].to_numpy()

    async def cal_gate(t: int, s: int) -> str:
        return "skip" if (h_to[t] <= EVENT_BEFORE_H or h_since[t] <= EVENT_AFTER_H) else "take"

    cal_all, cal_taken = await run_gated(fr, pos, sig, cal_gate)

    # --- LLM gate: prefetch (in parallel) the candidates ML-only reached — the LLM path reaches
    #     the same ones until its first skip — then answer lazily whatever else the loop reaches
    print(f"[3/5] LLM answers ({args.model}, mode={args.mode}) ...")
    cal = Calendar()
    gate = LLMGate(args.provider, args.model, args.mode, args.concurrency, args.dry_run)
    by_pos = {int(t): (float(c), float(pb), float(pr)) for t, c, pb, pr in
              zip(sigdf["pos"], conf, sigdf["p_bull"], sigdf["p_bear"])}
    decisions: dict[tuple[int, int], dict] = {}
    date_arr = fr["date"].to_numpy()
    pos_of_date = {d: i for i, d in enumerate(date_arr)}

    async with httpx.AsyncClient() as client:
        async def ask(t: int, s: int) -> dict:
            if (t, s) not in decisions:
                c, pb, pr = by_pos[t]
                pkt = context_packet(fr, t, s, c, pb, pr, cal, args.mode)
                decisions[(t, s)] = await gate.decide(client, str(fr["date"].iat[t]), s, pkt)
            return decisions[(t, s)]

        pre = [(pos_of_date[d], 1 if sd == "long" else -1) for d, sd in zip(ml_taken["decided"], ml_taken["side"])]
        if args.max_calls:
            pre = pre[: args.max_calls]
        done = 0
        for fut in asyncio.as_completed([ask(t, s) for t, s in pre]):
            await fut
            done += 1
            if done % 25 == 0 or done == len(pre):
                print(f"      prefetch {done}/{len(pre)} (new calls {gate.calls}, parse failures {gate.parse_failures})")

        async def llm_gate(t: int, s: int) -> str:
            if args.max_calls and (t, s) not in decisions:
                return "take"
            return (await ask(t, s))["action"]

        async def llm_binary_gate(t: int, s: int) -> str:
            a = await llm_gate(t, s)
            return "take" if a == "half" else a

        llm_all, llm_taken = await run_gated(fr, pos, sig, llm_gate)
        bin_all, bin_taken = await run_gated(fr, pos, sig, llm_binary_gate)
        print(f"      LLM consulted on {len(decisions)} candidates (new calls {gate.calls}, parse failures {gate.parse_failures})")

    def llm_action(t: int, s: int) -> str:
        rec = decisions.get((t, s))
        return rec["action"] if rec else "take"

    # --- results -------------------------------------------------------------------------
    print("[4/5] results")
    arms = {"ml_only": ml_taken, "calendar_rule": cal_taken, "llm_veto(half=0.5R)": llm_taken, "llm_binary(half=take)": bin_taken}
    table = pd.DataFrame([metrics_row(k, v) for k, v in arms.items()]).set_index("arm")
    cols = ["trades", "win_rate", "profit_factor", "expectancy_r", "total_r", "max_dd_pct", "longs", "shorts"]
    print("\n=== Holdout Aug-Sep 2026, intraday 15m ===")
    print(table[cols].to_string(float_format=lambda x: f"{x:.3f}"))

    for name, tk in arms.items():
        print(f"\n--- by month: {name}")
        print(by_month(tk).to_string(float_format=lambda x: f"{x:.3f}"))

    # diagnostic: what did the LLM say about the exact trades ML-only took, and how did they end?
    ml_set = ml_taken[["decided", "side"]].copy()
    ml_set["t"] = [pos_of_date[d] for d in ml_set["decided"]]
    ml_set["action"] = [llm_action(t, 1 if sd == "long" else -1) for t, sd in zip(ml_set["t"], ml_set["side"])]
    ml_set["r"] = ml_taken["r"].to_numpy()
    ml_set["win"] = ml_set["r"] > 0
    diag = ml_set.groupby("action").agg(n=("r", "size"), win_rate=("win", "mean"), mean_r=("r", "mean"), total_r=("r", "sum"))
    print("\n=== LLM verdict on the 1:1 ML-only trade set (realised outcome of each group) ===")
    print(diag.to_string(float_format=lambda x: f"{x:.3f}"))

    flags = pd.Series([f for rec in decisions.values() for f in rec.get("risk_flags", [])]).value_counts().head(12)
    print("\n=== most common risk_flags ===")
    print(flags.to_string() if len(flags) else "(none)")
    print(f"\nactions over all {len(decisions)} candidates: " +
          ", ".join(f"{k}={v}" for k, v in pd.Series([r['action'] for r in decisions.values()]).value_counts().items()))

    # --- persist ---------------------------------------------------------------------------
    print("[5/5] saving")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tag = f"{hz_name}_{args.mode}_{args.model.replace('/', '_').replace(':', '_')}"
    for name, df in (("ml_only", ml_all), ("calendar_rule", cal_all), ("llm_veto", llm_all)):
        df.to_csv(OUT_DIR / f"trades_{name}_{tag}.csv", index=False)
    ml_set.to_csv(OUT_DIR / f"diag_mlset_{tag}.csv", index=False)
    summary = {
        "generated_utc": pd.Timestamp.now("UTC").isoformat(),
        "horizon": hz_name, "model_stem": stem, "model_version": meta["version"],
        "llm_model": args.model, "llm_provider": args.provider, "mode": args.mode, "min_conf": MIN_CONF,
        "event_window_h": {"before": EVENT_BEFORE_H, "after": EVENT_AFTER_H},
        "bars": {tf: [str(bars[tf]["date"].min()), str(bars[tf]["date"].max()), len(bars[tf])] for tf in bars},
        "holdout_rows": int(len(pos)), "signals": int((sig != 0).sum()),
        "llm_calls_new": gate.calls, "llm_parse_failures": gate.parse_failures,
        "arms": table.reset_index().to_dict(orient="records"),
        "by_month": {k: by_month(v).reset_index().rename(columns={"index": "month"}).to_dict(orient="records") for k, v in arms.items()},
        "llm_verdict_on_ml_trades": diag.reset_index().to_dict(orient="records"),
        "risk_flags_top": flags.to_dict(),
    }
    (OUT_DIR / f"summary_{tag}.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(f"      {OUT_DIR / f'summary_{tag}.json'}  ({time.time() - t0:.0f}s)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--horizon", default="intraday", choices=["intraday", "swing"])
    ap.add_argument("--mode", default="anon", choices=["anon", "dated"])
    ap.add_argument("--provider", default="openrouter", choices=sorted(PROVIDERS))
    ap.add_argument("--model", default="openai/gpt-oss-120b")
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--max-calls", type=int, default=0, help="limit candidates (smoke test)")
    ap.add_argument("--refetch", action="store_true", help="ignore the bar cache")
    ap.add_argument("--dry-run", action="store_true", help="no LLM calls; every answer = take")
    asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    main()
