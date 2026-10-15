"""Smart Money Concepts: market structure, order blocks, liquidity and imbalances (F1).

A port of LuxAlgo's "Smart Money Concepts" Pine Script v5 indicator (© LuxAlgo, licensed
CC BY-NC-SA 4.0 — https://creativecommons.org/licenses/by-nc-sa/4.0/; see
``agents/prompts/THIRD_PARTY.md``). This module is a modified version: only the
analysis is ported (no drawing), with the indicator's default inputs, and it returns the
state at the last bar instead of chart objects.

Ported, bar by bar in the same order as the script:

- legs/pivots (``leg``, ``getCurrentStructure``): swing (50 bars), internal (5) and
  equal-high/low (3) pivots, confirmed ``size`` bars after the extreme — no repainting
- structure breaks (``displayStructure``): a close crossing the last pivot is a BOS when it
  continues the trend and a CHoCH when it flips it; internal breaks are ignored while the
  internal pivot equals the swing pivot (the script's ``extraCondition``)
- order blocks (``storeOrdeBlock``/``deleteOrderBlocks``): the extreme candle between the
  broken pivot and the break, high-volatility bars (range ≥ 2 x ATR200) inverted, removed
  once price trades through them (High/Low mitigation)
- equal highs/lows: consecutive 3-bar pivots within 0.1 x ATR200
- fair value gaps with the auto threshold (on the bars' own timeframe), removed the way the
  script removes them: a bullish gap when price trades below it, a bearish gap as soon as
  price trades up into it
- trailing swing extremes (strong/weak high/low) and premium/discount/equilibrium zones

Added (not in the script): **sweeps** of the newest equal highs/lows, each level used once.
A close through the level from the other side is a *continuation* sweep (EQH → long, EQL →
short; the side Mahadzva 2026 found swept levels predict); a wick through it that closes back
inside is a *reversal* sweep (the textbook reading). Each keeps the swing trend at that bar.

Deviations: when there is too little history for ATR200 the volatility measure falls back
to the script's own "cumulative mean range" option; swing order blocks and fair value gaps
(off by default on the chart) are always computed; multi-timeframe daily/weekly/monthly
levels are not ported (the workflow already reads the daily chart).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.domain.compute import indicators as ind
from app.domain.compute.base import ComputeResult, compute_record
from app.domain.models.market import OHLCVBar

_VERSION = "v2"  # v2: equal-high/low sweeps (``sweeps``)

BULLISH = 1
BEARISH = -1
SWING_LENGTH = 50
INTERNAL_LENGTH = 5
EQUAL_LENGTH = 3
EQUAL_THRESHOLD = 0.1  # x ATR200
ATR_LENGTH = 200
HIGH_VOLATILITY_RANGE = 2.0  # x ATR200: such a bar's high/low are swapped for order blocks
MAX_ORDER_BLOCKS = 100
MIN_BARS = INTERNAL_LENGTH + 3
SWEEP_LEVELS = 10  # the newest equal highs/lows watched for a sweep
SWEEP_REPORT_BARS = 50  # sweeps reported when they printed this recently


def _bias_name(bias: int) -> str:
    return {BULLISH: "bullish", BEARISH: "bearish"}.get(bias, "none")


@dataclass
class _Pivot:
    current: float | None = None
    last: float | None = None
    crossed: bool = False
    index: int = 0
    label: str | None = None  # HH/LH for highs, HL/LL for lows


class _Leg:
    """Pine ``leg(size)``: 0 = bearish leg (a new high was confirmed), 1 = bullish leg."""

    def __init__(self, size: int) -> None:
        self.size = size
        self.leg = 0
        self.prev: int | None = None

    def step(self, i: int, highs: list[float], lows: list[float]) -> int:
        size = self.size
        if i >= size:
            if highs[i - size] > max(highs[i - size + 1 : i + 1]):
                self.leg = 0
            elif lows[i - size] < min(lows[i - size + 1 : i + 1]):
                self.leg = 1
        change = 0 if self.prev is None else self.leg - self.prev
        self.prev = self.leg
        return change


def _volatility(highs: list[float], lows: list[float], closes: list[float]) -> list[float | None]:
    atr200 = ind.atr(highs, lows, closes, ATR_LENGTH).value
    if any(v is not None for v in atr200):
        return atr200
    out: list[float | None] = [None]
    cum = 0.0
    for i in range(1, len(closes)):
        cum += max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        out.append(cum / i)
    return out


def smart_money_concepts(
    bars: list[OHLCVBar],
    *,
    swing_length: int = SWING_LENGTH,
    internal_length: int = INTERNAL_LENGTH,
    equal_length: int = EQUAL_LENGTH,
    max_items: int = 3,
    inputs: list[str] | None = None,
    on_bar: Callable[[int, dict[str, Any]], None] | None = None,
) -> ComputeResult[dict[str, Any]]:
    """SMC state at the last of ``bars`` (closed bars, oldest first).

    ``on_bar(i, state)``, when given, is called after every bar with the live (internal,
    read-only) state — what a point-in-time reader would know at bar ``i``'s close. It is
    how ``smc_feature_series`` gets every bar from one pass instead of one pass per bar.
    """
    n = len(bars)
    min_bars = max(MIN_BARS, internal_length + 3)
    if n < min_bars:
        raise ValueError(f"need at least {min_bars} bars for SMC, got {n}")
    o = [float(b.open) for b in bars]
    h = [float(b.high) for b in bars]
    lo = [float(b.low) for b in bars]
    c = [float(b.close) for b in bars]
    vol = _volatility(h, lo, c)
    atr14 = ind.atr(h, lo, c, 14).value[-1]

    parsed_h: list[float] = []
    parsed_l: list[float] = []

    swing_high, swing_low = _Pivot(), _Pivot()
    int_high, int_low = _Pivot(), _Pivot()
    eq_high, eq_low = _Pivot(), _Pivot()
    trend = {"swing": 0, "internal": 0}
    legs = {"swing": _Leg(swing_length), "internal": _Leg(internal_length), "equal": _Leg(equal_length)}
    trail: dict[str, Any] = {"top": None, "bottom": None, "top_index": 0, "bottom_index": 0}
    order_blocks: dict[str, list[dict[str, Any]]] = {"swing": [], "internal": []}
    breaks: dict[str, list[dict[str, Any]]] = {"swing": [], "internal": []}
    equal_events: list[dict[str, Any]] = []
    fvgs: list[dict[str, Any]] = []
    sweeps: list[dict[str, Any]] = []
    swept: set[tuple[str, float]] = set()
    cum_delta = 0.0

    def detect_sweeps(i: int) -> None:
        for e in equal_events[-SWEEP_LEVELS:][::-1]:
            key = (e["type"], e["level"])
            if key in swept or e["index"] >= i:
                continue
            lvl = e["level"]
            kind = side = None
            if e["type"] == "EQH":
                if c[i - 1] <= lvl < c[i]:
                    kind, side = "continuation", "long"
                elif h[i] > lvl > c[i]:
                    kind, side = "reversal", "short"
            elif c[i - 1] >= lvl > c[i]:
                kind, side = "continuation", "short"
            elif lo[i] < lvl < c[i]:
                kind, side = "reversal", "long"
            if kind is None:
                continue
            swept.add(key)
            sweeps.append(
                {
                    "type": e["type"],
                    "level": lvl,
                    "kind": kind,
                    "side": side,
                    "index": i,
                    "swing_trend": _bias_name(trend["swing"]),
                    "bar_high": h[i],
                    "bar_low": lo[i],
                }
            )

    def structure(i: int, kind: str) -> None:
        size = legs[kind].size
        change = legs[kind].step(i, h, lo)
        if change == 0:
            return
        k = i - size
        if change == 1:  # start of a bullish leg → pivot low
            piv = {"equal": eq_low, "internal": int_low, "swing": swing_low}[kind]
            level = lo[k]
            if (
                kind == "equal"
                and piv.current is not None
                and vol[i] is not None
                and abs(piv.current - level) < EQUAL_THRESHOLD * vol[i]  # type: ignore[operator]
            ):
                equal_events.append({"type": "EQL", "level": level, "first_level": piv.current, "index": k})
            label = None if piv.current is None else ("LL" if level < piv.current else "HL")
            if kind == "swing":
                trail["bottom"], trail["bottom_index"] = level, k
        else:  # start of a bearish leg → pivot high
            piv = {"equal": eq_high, "internal": int_high, "swing": swing_high}[kind]
            level = h[k]
            if (
                kind == "equal"
                and piv.current is not None
                and vol[i] is not None
                and abs(piv.current - level) < EQUAL_THRESHOLD * vol[i]  # type: ignore[operator]
            ):
                equal_events.append({"type": "EQH", "level": level, "first_level": piv.current, "index": k})
            label = None if piv.current is None else ("HH" if level > piv.current else "LH")
            if kind == "swing":
                trail["top"], trail["top_index"] = level, k
        piv.last, piv.current, piv.crossed, piv.index, piv.label = piv.current, level, False, k, label

    def store_order_block(i: int, piv: _Pivot, kind: str, bias: int) -> None:
        if bias == BEARISH:
            window = parsed_h[piv.index : i]
            idx = piv.index + window.index(max(window))
        else:
            window = parsed_l[piv.index : i]
            idx = piv.index + window.index(min(window))
        blocks = order_blocks[kind]
        if len(blocks) >= MAX_ORDER_BLOCKS:
            blocks.pop()
        blocks.insert(0, {"bias": bias, "high": parsed_h[idx], "low": parsed_l[idx], "index": idx})

    def display_structure(i: int, kind: str) -> None:
        internal = kind == "internal"
        piv = int_high if internal else swing_high
        extra = (
            piv.current is not None and swing_high.current is not None and piv.current != swing_high.current
            if internal
            else True
        )
        lvl = piv.current
        if lvl is not None and c[i] > lvl and c[i - 1] <= lvl and not piv.crossed and extra:
            tag = "CHoCH" if trend[kind] == BEARISH else "BOS"
            piv.crossed = True
            trend[kind] = BULLISH
            breaks[kind].append({"tag": tag, "bias": BULLISH, "level": lvl, "index": i})
            store_order_block(i, piv, kind, BULLISH)

        piv = int_low if internal else swing_low
        extra = (
            piv.current is not None and swing_low.current is not None and piv.current != swing_low.current
            if internal
            else True
        )
        lvl = piv.current
        if lvl is not None and c[i] < lvl and c[i - 1] >= lvl and not piv.crossed and extra:
            tag = "CHoCH" if trend[kind] == BULLISH else "BOS"
            piv.crossed = True
            trend[kind] = BEARISH
            breaks[kind].append({"tag": tag, "bias": BEARISH, "level": lvl, "index": i})
            store_order_block(i, piv, kind, BEARISH)

    for i in range(n):
        v = vol[i]
        high_vol = v is not None and (h[i] - lo[i]) >= HIGH_VOLATILITY_RANGE * v
        parsed_h.append(lo[i] if high_vol else h[i])
        parsed_l.append(h[i] if high_vol else lo[i])

        if trail["top"] is not None and h[i] >= trail["top"]:
            trail["top"], trail["top_index"] = h[i], i
        if trail["bottom"] is not None and lo[i] <= trail["bottom"]:
            trail["bottom"], trail["bottom_index"] = lo[i], i

        fvgs[:] = [
            g
            for g in fvgs
            if not (
                (g["bias"] == BULLISH and lo[i] < g["bottom"]) or (g["bias"] == BEARISH and h[i] > g["top"])
            )
        ]

        structure(i, "swing")
        structure(i, "internal")
        structure(i, "equal")
        if i > 0:
            display_structure(i, "internal")
            display_structure(i, "swing")
            detect_sweeps(i)

        for kind in ("internal", "swing"):
            order_blocks[kind] = [
                b
                for b in order_blocks[kind]
                if not (
                    (b["bias"] == BEARISH and h[i] > b["high"]) or (b["bias"] == BULLISH and lo[i] < b["low"])
                )
            ]

        if i >= 1:
            delta = (c[i - 1] - o[i - 1]) / (o[i - 1] * 100) if o[i - 1] else 0.0
            cum_delta += abs(delta)
            threshold = cum_delta / i * 2
            if i >= 2:
                if lo[i] > h[i - 2] and c[i - 1] > h[i - 2] and delta > threshold:
                    fvgs.insert(0, {"bias": BULLISH, "top": lo[i], "bottom": h[i - 2], "index": i - 1})
                if h[i] < lo[i - 2] and c[i - 1] < lo[i - 2] and -delta > threshold:
                    # the script stores a bearish gap with top = current high (its lower edge)
                    fvgs.insert(0, {"bias": BEARISH, "top": h[i], "bottom": lo[i - 2], "index": i - 1})

        if on_bar is not None:
            on_bar(
                i,
                {
                    "trend": trend,
                    "trail": trail,
                    "order_blocks": order_blocks,
                    "fvgs": fvgs,
                    "breaks": breaks,
                    "sweeps": sweeps,
                },
            )

    last = n - 1
    close = c[last]

    def ago(index: int) -> int:
        return last - index

    def distance_atr(zone_low: float, zone_high: float) -> float | None:
        if not atr14:
            return None
        if zone_low <= close <= zone_high:
            return 0.0
        edge = zone_high if close > zone_high else zone_low
        return (edge - close) / atr14

    def zone(item: dict[str, Any], top: str, bottom: str) -> dict[str, Any]:
        z_low, z_high = sorted((item[top], item[bottom]))
        return {
            "bias": _bias_name(item["bias"]),
            "low": z_low,
            "high": z_high,
            "bars_ago": ago(item["index"]),
            "distance_atr": distance_atr(z_low, z_high),
        }

    def pivot_view(piv: _Pivot) -> dict[str, Any] | None:
        if piv.current is None:
            return None
        return {"level": piv.current, "label": piv.label, "bars_ago": ago(piv.index), "broken": piv.crossed}

    def structure_view(kind: str) -> dict[str, Any]:
        recent = breaks[kind][-max_items:][::-1]
        return {
            "trend": _bias_name(trend[kind]),
            "breaks": [
                {
                    "tag": b["tag"],
                    "bias": _bias_name(b["bias"]),
                    "level": b["level"],
                    "bars_ago": ago(b["index"]),
                }
                for b in recent
            ],
        }

    top, bottom = trail["top"], trail["bottom"]
    premium_discount: dict[str, Any] | None = None
    strong_weak: dict[str, Any] | None = None
    if top is not None and bottom is not None and top > bottom:
        span = top - bottom
        position = (close - bottom) / span
        premium_discount = {
            "top": top,
            "bottom": bottom,
            "equilibrium": (top + bottom) / 2,
            "premium_from": 0.95 * top + 0.05 * bottom,
            "discount_to": 0.95 * bottom + 0.05 * top,
            "position": position,  # 0 = swing low, 1 = swing high
            "zone": "premium" if position > 0.525 else "discount" if position < 0.475 else "equilibrium",
        }
        strong_weak = {
            "high": {
                "level": top,
                "strength": "strong" if trend["swing"] == BEARISH else "weak",
                "bars_ago": ago(trail["top_index"]),
            },
            "low": {
                "level": bottom,
                "strength": "strong" if trend["swing"] == BULLISH else "weak",
                "bars_ago": ago(trail["bottom_index"]),
            },
        }

    value: dict[str, Any] = {
        "bars": n,
        "bar_ts": bars[last].ts.isoformat(),
        "close": close,
        "atr14": atr14,
        "swing": structure_view("swing"),
        "internal": structure_view("internal"),
        "pivots": {
            "swing_high": pivot_view(swing_high),
            "swing_low": pivot_view(swing_low),
            "internal_high": pivot_view(int_high),
            "internal_low": pivot_view(int_low),
        },
        "strong_weak": strong_weak,
        "premium_discount": premium_discount,
        "order_blocks": {
            kind: [zone(b, "high", "low") for b in order_blocks[kind][:max_items]]
            for kind in ("internal", "swing")
        },
        "equal_levels": [
            {
                "type": e["type"],
                "level": e["level"],
                "first_level": e["first_level"],
                "bars_ago": ago(e["index"]),
            }
            for e in equal_events[-max_items:][::-1]
        ],
        "fair_value_gaps": [zone(g, "top", "bottom") for g in fvgs[:max_items]],
        # newest first; `bar_high`/`bar_low` = the sweeping bar (the structural stop)
        "sweeps": [
            {**{k: v for k, v in sw.items() if k != "index"}, "bars_ago": ago(sw["index"])}
            for sw in sweeps[::-1]
            if ago(sw["index"]) <= SWEEP_REPORT_BARS
        ][:SWEEP_LEVELS],
    }
    params: dict[str, object] = {
        "swing_length": swing_length,
        "internal_length": internal_length,
        "equal_length": equal_length,
        "equal_threshold": EQUAL_THRESHOLD,
        "atr_length": ATR_LENGTH,
    }
    record = compute_record(
        f"compute.smc.smart_money_concepts@{_VERSION}", value, params=params, inputs=inputs
    )
    return ComputeResult(value=value, records=[record])


FEATURES_VERSION = "v1"
_CAP = 200.0  # bars-since features are capped (``nan`` = never)


def smc_feature_series(
    bars: list[OHLCVBar], *, inputs: list[str] | None = None
) -> ComputeResult[list[dict[str, float]]]:
    """Point-in-time SMC features for EVERY bar (one pass; row ``i`` uses bars ``<= i``).

    Numeric encodings for a model: trends as +1/-1/0, distances in ATR14 (signed: zone minus
    close, so a zone below price is negative), bars-since counters capped at 200 (``nan`` =
    none yet), the last continuation sweep's side as +1/-1.
    """
    h = [float(b.high) for b in bars]
    lo = [float(b.low) for b in bars]
    c = [float(b.close) for b in bars]
    atr14 = ind.atr(h, lo, c, 14).value
    nan = float("nan")
    rows: list[dict[str, float]] = []

    def nearest(
        zones: list[dict[str, Any]], bias: int, top: str, bottom: str, close: float, a: float
    ) -> float:
        best = nan
        for z in zones:
            if z["bias"] != bias:
                continue
            z_lo, z_hi = sorted((z[top], z[bottom]))
            d = 0.0 if z_lo <= close <= z_hi else ((z_hi if close > z_hi else z_lo) - close) / a
            if best != best or abs(d) < abs(best):
                best = d
        return best

    def since(i: int, idx: int | None) -> float:
        return nan if idx is None else float(min(i - idx, _CAP))

    def hook(i: int, st: dict[str, Any]) -> None:
        a = atr14[i]
        close = c[i]
        tr = st["trail"]
        if not a:
            rows.append({})
            return
        pos = nan
        if tr["top"] is not None and tr["bottom"] is not None and tr["top"] > tr["bottom"]:
            pos = (close - tr["bottom"]) / (tr["top"] - tr["bottom"])
        sb = st["breaks"]["swing"][-1] if st["breaks"]["swing"] else None
        ib = st["breaks"]["internal"][-1] if st["breaks"]["internal"] else None
        cont = next((x for x in reversed(st["sweeps"]) if x["kind"] == "continuation"), None)
        rev = next((x for x in reversed(st["sweeps"]) if x["kind"] == "reversal"), None)
        obs = st["order_blocks"]["internal"]
        rows.append(
            {
                "smc_swing_trend": float(st["trend"]["swing"]),
                "smc_internal_trend": float(st["trend"]["internal"]),
                "smc_pd_pos": pos,
                "smc_bull_ob_dist": nearest(obs, BULLISH, "high", "low", close, a),
                "smc_bear_ob_dist": nearest(obs, BEARISH, "high", "low", close, a),
                "smc_bull_fvg_dist": nearest(st["fvgs"], BULLISH, "top", "bottom", close, a),
                "smc_bear_fvg_dist": nearest(st["fvgs"], BEARISH, "top", "bottom", close, a),
                "smc_swing_break_since": since(i, sb["index"] if sb else None),
                "smc_swing_break_bias": float(sb["bias"]) if sb else 0.0,
                "smc_swing_break_choch": float(sb["tag"] == "CHoCH") if sb else 0.0,
                "smc_internal_break_since": since(i, ib["index"] if ib else None),
                "smc_internal_break_bias": float(ib["bias"]) if ib else 0.0,
                "smc_cont_sweep_since": since(i, cont["index"] if cont else None),
                "smc_cont_sweep_side": (1.0 if cont["side"] == "long" else -1.0) if cont else 0.0,
                "smc_cont_sweep_with_trend": float(
                    cont["swing_trend"] == ("bullish" if cont["side"] == "long" else "bearish")
                )
                if cont
                else 0.0,
                "smc_rev_sweep_since": since(i, rev["index"] if rev else None),
                "smc_rev_sweep_side": (1.0 if rev["side"] == "long" else -1.0) if rev else 0.0,
            }
        )

    smart_money_concepts(bars, on_bar=hook)
    record = compute_record(
        f"compute.smc.smc_feature_series@{FEATURES_VERSION}",
        {"rows": len(rows)},
        params={"swing_length": SWING_LENGTH, "internal_length": INTERNAL_LENGTH, "cap": _CAP},
        inputs=inputs,
    )
    return ComputeResult(value=rows, records=[record])
