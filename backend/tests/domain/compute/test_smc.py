"""Smart Money Concepts port (compute/smc.py) on hand-built bar series."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.domain.compute.smc import smart_money_concepts
from app.domain.models.market import OHLCVBar

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _bar(i: int, o: float, h: float, low: float, c: float) -> OHLCVBar:
    return OHLCVBar(
        ts=T0 + timedelta(hours=i),
        open=Decimal(str(o)),
        high=Decimal(str(h)),
        low=Decimal(str(low)),
        close=Decimal(str(c)),
        volume=0,
    )


def _from_closes(closes: list[float], wick: float = 0.2) -> list[OHLCVBar]:
    bars = []
    prev = closes[0]
    for i, c in enumerate(closes):
        bars.append(_bar(i, prev, max(prev, c) + wick, min(prev, c) - wick, c))
        prev = c
    return bars


def _line(a: float, b: float, steps: int) -> list[float]:
    """``steps`` closes moving from ``a`` (exclusive) to ``b`` (inclusive)."""
    return [round(a + (b - a) * k / steps, 4) for k in range(1, steps + 1)]


# 100 → 110 (peak bar 10) → 104 (trough bar 16) → 115 (peak bar 27) → 100
ZIGZAG = [100.0, *_line(100, 110, 10), *_line(110, 104, 6), *_line(104, 115, 11), *_line(115, 100, 15)]


def test_too_few_bars_is_an_error() -> None:
    with pytest.raises(ValueError, match="at least"):
        smart_money_concepts(_from_closes([100.0, 101.0, 102.0]))


def test_bos_then_choch_on_swing_structure() -> None:
    v = smart_money_concepts(_from_closes(ZIGZAG), swing_length=5).value
    breaks = v["swing"]["breaks"]
    # newest first: the drop through the 103.8 low flips the trend; the rally through 110.2 continued it
    assert [(b["bias"], b["tag"]) for b in breaks] == [("bearish", "CHoCH"), ("bullish", "BOS")]
    assert breaks[0]["level"] == pytest.approx(103.8)
    assert breaks[1]["level"] == pytest.approx(110.2)
    assert v["swing"]["trend"] == "bearish"


def test_order_blocks_mitigated_and_kept() -> None:
    v = smart_money_concepts(_from_closes(ZIGZAG), swing_length=5).value
    # the bullish block (trough candle at 103.8) was traded through on the way down → gone;
    # the bearish block is the top candle before the CHoCH, still above price
    (ob,) = v["order_blocks"]["swing"]
    assert ob["bias"] == "bearish"
    assert ob["high"] == pytest.approx(115.2)
    assert ob["distance_atr"] is not None and ob["distance_atr"] > 0


def test_premium_discount_and_strong_weak() -> None:
    v = smart_money_concepts(_from_closes(ZIGZAG), swing_length=5).value
    pd_ = v["premium_discount"]
    assert pd_["top"] == pytest.approx(115.2)
    assert pd_["bottom"] == pytest.approx(min(b - 0.2 for b in ZIGZAG))  # trailing low follows price
    assert pd_["zone"] == "discount" and pd_["position"] < 0.1
    # bearish swing trend: the high that started the drop is protected, the low is not
    assert v["strong_weak"]["high"]["strength"] == "strong"
    assert v["strong_weak"]["low"]["strength"] == "weak"


def test_equal_highs() -> None:
    closes = [100.0, *_line(100, 110, 5), *_line(110, 105, 5), *_line(105, 110, 5), *_line(110, 104, 5)]
    v = smart_money_concepts(_from_closes(closes), swing_length=5).value
    eqh = [e for e in v["equal_levels"] if e["type"] == "EQH"]
    assert eqh and eqh[0]["level"] == pytest.approx(110.2)
    assert eqh[0]["first_level"] == pytest.approx(110.2)


def _flat(n: int, price: float = 100.0) -> list[OHLCVBar]:
    return [_bar(i, price, price + 0.1, price - 0.1, price) for i in range(n)]


def test_bullish_fvg_until_price_trades_below_it() -> None:
    bars = _flat(20)
    bars += [_bar(20, 100, 105.5, 99.9, 105), _bar(21, 105, 106, 103, 105.5)]  # gap 100.1 → 103
    (gap,) = smart_money_concepts(bars).value["fair_value_gaps"]
    assert (gap["bias"], gap["low"], gap["high"]) == ("bullish", pytest.approx(100.1), pytest.approx(103))
    assert gap["distance_atr"] < 0  # below price

    # a dip into the gap that holds above its floor keeps it; trading below 100.1 removes it
    held = [*bars, _bar(22, 105.5, 105.6, 101, 104)]
    assert smart_money_concepts(held).value["fair_value_gaps"]
    filled = [*held, _bar(23, 104, 104.1, 100.0, 100.5)]
    assert smart_money_concepts(filled).value["fair_value_gaps"] == []


def test_bearish_fvg_removed_on_first_touch_like_the_script() -> None:
    bars = _flat(10)
    bars += [_bar(10, 100, 100.1, 94.5, 95), _bar(11, 95, 97, 94, 94.5)]  # gap 97 → 99.9
    (gap,) = smart_money_concepts(bars).value["fair_value_gaps"]
    assert (gap["bias"], gap["low"], gap["high"]) == ("bearish", pytest.approx(97), pytest.approx(99.9))
    touched = [*bars, _bar(12, 94.5, 97.5, 94.4, 95)]  # trades up into the gap's lower edge
    assert smart_money_concepts(touched).value["fair_value_gaps"] == []


def test_provenance_record() -> None:
    res = smart_money_concepts(_from_closes(ZIGZAG), swing_length=5, inputs=["prov_x"])
    (rec,) = res.records
    assert rec.calculation == "compute.smc.smart_money_concepts@v2"
    assert rec.inputs == ["prov_x"]


# two equal highs at 110.2 (bars 8 and 18), then a pullback to 106
_EQH_BASE = [100.0, *_line(100, 110, 8), *_line(110, 105, 5), *_line(105, 110, 5), *_line(110, 106, 4)]


def test_close_through_equal_highs_is_a_continuation_sweep_used_once() -> None:
    closes = [*_EQH_BASE, *_line(106, 109.5, 4), 111.0, 111.5]
    v = smart_money_concepts(_from_closes(closes)).value
    assert v["equal_levels"][0]["type"] == "EQH" and v["equal_levels"][0]["level"] == pytest.approx(110.2)
    (sw,) = v["sweeps"]
    assert (sw["kind"], sw["side"], sw["bars_ago"]) == ("continuation", "long", 1)
    assert sw["level"] == pytest.approx(110.2) and sw["bar_low"] == pytest.approx(109.3)
    # back below and through again: the level was already used
    again = smart_money_concepts(_from_closes([*closes, 109.0, 110.8])).value
    assert len(again["sweeps"]) == 1 and again["sweeps"][0]["bars_ago"] == 3


def _walk(n: int, seed: int = 3) -> list[OHLCVBar]:
    import random

    rnd = random.Random(seed)
    closes, x = [], 2000.0
    for _ in range(n):
        x += rnd.gauss(0, 2.0)
        closes.append(round(x, 2))
    return _from_closes(closes, wick=0.8)


def test_feature_series_is_point_in_time() -> None:
    from math import isnan

    from app.domain.compute.smc import smc_feature_series

    bars = _walk(700)
    full = smc_feature_series(bars).value
    assert len(full) == len(bars)
    assert set(full[400]) >= {"smc_swing_trend", "smc_pd_pos", "smc_cont_sweep_since"}
    for i in (260, 433, 699):  # past the ATR200 warm-up, where the volatility measure settles
        cut = smc_feature_series(bars[: i + 1]).value[-1]
        for k, v in full[i].items():
            assert (isnan(v) and isnan(cut[k])) or v == pytest.approx(cut[k]), (i, k)
    # the last row agrees with the snapshot the desk reads
    snap = smart_money_concepts(bars).value
    trend = {"bullish": 1.0, "bearish": -1.0, "none": 0.0}[snap["swing"]["trend"]]
    assert full[-1]["smc_swing_trend"] == trend


def test_wick_through_equal_highs_closing_back_is_a_reversal_sweep() -> None:
    bars = _from_closes([*_EQH_BASE, *_line(106, 109.5, 4)])
    n = len(bars)
    bars += [_bar(n, 109.5, 111.0, 109.0, 109.3), _bar(n + 1, 109.3, 109.5, 108.0, 108.2)]
    (sw,) = smart_money_concepts(bars).value["sweeps"]
    assert (sw["kind"], sw["side"], sw["bar_high"]) == ("reversal", "short", 111.0)
