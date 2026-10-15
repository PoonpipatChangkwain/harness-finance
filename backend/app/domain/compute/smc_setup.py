"""The SMC sweep-continuation setup the LLM-only desk trades (F1, our own rules).

Rule "B + trend + momentum" from the rule-based SMC study (2026-10-05, GC=F 15m 60d and 1h
730d, scratchpad ``smc_rules.py``): of order-block retests (A), continuation sweeps (B) and
reversal sweeps (C), only B with the swing trend and a momentum filter stayed positive on
two years of 1h bars (+0.23R/trade, PF 1.5, 36 trades — thin, and positive only in 2026).

A setup is **active** when, within the last ``max_age`` bars:

1. a *continuation* sweep printed (``compute/smc.py`` ``sweeps``: a close through an equal
   high → long, through an equal low → short);
2. it went **with** the swing trend at that bar, and the swing trend has not flipped since;
3. momentum agreed on the sweep bar (TradingView RSI + ChrisMoody MACD, as in the study):
   long = MACD histogram rising (above or below zero) or a bullish MACD cross, **and** RSI
   above its SMA-14 or a bullish regular RSI divergence confirmed within ``DIV_RECENT``
   bars — the mirror for a short;
4. no close since then beyond the sweeping bar's extreme (the structural stop).

Every candidate sweep is returned with the checks it passed, so the desk (and a reviewer)
sees why there is or is not a trade.
"""

from __future__ import annotations

from typing import Any

from app.domain.compute import indicators as ind
from app.domain.compute.base import ComputeResult, compute_record
from app.domain.models.market import OHLCVBar

SETUP_VERSION = "v1"
MAX_AGE = 16  # bars: a sweep older than this is stale (the horizon's default time stop)
DIV_RECENT = 10  # bars: an RSI divergence this recent counts as momentum


def momentum(
    side: str,
    i: int,
    *,
    hist: list[float | None],
    macd_line: list[float | None],
    signal_line: list[float | None],
    rsi_values: list[float | None],
    rsi_avg: list[float | None],
    divergences: list[dict[str, Any]],
) -> dict[str, Any]:
    """The momentum check on bar ``i`` (point in time: only data up to ``i``)."""
    state = ind.macd_hist_state(hist[: i + 1])
    cross = ind.macd_cross(macd_line[: i + 1], signal_line[: i + 1])
    r, rma = rsi_values[i], rsi_avg[i]
    want = "bullish" if side == "long" else "bearish"
    div = any(e["kind"] == want and 0 <= i - e["known_at"] <= DIV_RECENT for e in divergences)
    if side == "long":
        macd_ok = state in ("rising_above", "rising_below") or cross == "bullish"
        rsi_ok = (r is not None and rma is not None and r > rma) or div
    else:
        macd_ok = state in ("falling_above", "falling_below") or cross == "bearish"
        rsi_ok = (r is not None and rma is not None and r < rma) or div
    return {
        "ok": bool(macd_ok and rsi_ok),
        "macd_hist_state": state,
        "macd_cross": cross,
        "rsi": r,
        "rsi_ma": rma,
        "rsi_divergence": div,
        "macd_ok": bool(macd_ok),
        "rsi_ok": bool(rsi_ok),
    }


def sweep_continuation_setup(
    bars: list[OHLCVBar],
    smc_value: dict[str, Any],
    *,
    max_age: int = MAX_AGE,
    inputs: list[str] | None = None,
) -> ComputeResult[dict[str, Any]]:
    """The setup state at the last of ``bars`` — the same bars ``smc_value`` was computed on."""
    closes = [float(b.close) for b in bars]
    highs = [float(b.high) for b in bars]
    lows = [float(b.low) for b in bars]
    last = len(bars) - 1
    r = ind.rsi(closes, 14).value
    rma = ind.rsi_ma(r, 14)
    m = ind.macd(closes).value
    divs = ind.divergence_events(r, highs, lows)
    trend_now = (smc_value.get("swing") or {}).get("trend")

    candidates: list[dict[str, Any]] = []
    for sw in smc_value.get("sweeps") or []:
        if sw["kind"] != "continuation" or sw["bars_ago"] > max_age:
            continue
        i = last - sw["bars_ago"]
        side = sw["side"]
        want = "bullish" if side == "long" else "bearish"
        stop = sw["bar_low"] if side == "long" else sw["bar_high"]
        after = closes[i + 1 :]
        broken = any(x < stop for x in after) if side == "long" else any(x > stop for x in after)
        mom = momentum(
            side,
            i,
            hist=m["hist"],
            macd_line=m["macd"],
            signal_line=m["signal"],
            rsi_values=r,
            rsi_avg=rma,
            divergences=divs,
        )
        checks = {
            "with_trend": sw["swing_trend"] == want,
            "trend_unchanged": trend_now == want,
            "momentum": mom["ok"],
            "not_invalidated": not broken,
        }
        candidates.append(
            {
                "side": side,
                "type": sw["type"],
                "level": sw["level"],
                "bars_ago": sw["bars_ago"],
                "structure_stop": stop,
                "checks": checks,
                "momentum": mom,
                "active": all(checks.values()),
            }
        )
    active = next((c for c in candidates if c["active"]), None)  # newest first
    if active:
        reason = (
            f"{active['side']} setup: close through the {active['type']} {active['level']:.6g} "
            f"{active['bars_ago']} bars ago with the {trend_now} swing trend and momentum"
        )
    elif candidates:
        failed = [k for k, ok in candidates[0]["checks"].items() if not ok]
        reason = f"newest continuation sweep fails: {', '.join(failed)}"
    else:
        reason = f"no continuation sweep of an equal high/low in the last {max_age} bars"
    value: dict[str, Any] = {
        "active": active is not None,
        "side": active["side"] if active else None,
        "level": active["level"] if active else None,
        "bars_ago": active["bars_ago"] if active else None,
        "structure_stop": active["structure_stop"] if active else None,
        "swing_trend": trend_now,
        "max_age": max_age,
        "reason": reason,
        "candidates": candidates,
    }
    record = compute_record(
        f"compute.smc_setup.sweep_continuation@{SETUP_VERSION}",
        {k: value[k] for k in ("active", "side", "level", "bars_ago", "structure_stop")},
        params={"max_age": max_age, "div_recent": DIV_RECENT, "rsi": 14, "macd": "12/26/9 sma"},
        inputs=inputs,
    )
    return ComputeResult(value=value, records=[record])
