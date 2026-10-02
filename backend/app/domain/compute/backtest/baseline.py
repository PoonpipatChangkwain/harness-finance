"""A rule-based decision generator to compare the LLM workflow against (Phase 15 §6).

SMA fast/slow crossover: on the close of a golden cross, go long with the stop
``atr_multiple`` ATR14 below the close and one target ``reward_r`` times that risk above it;
a death cross gives the mirrored short (the simulator drops it where shorting is not
allowed). It emits the same ``SimDecision`` the trading workflow's cards become, so both
run through one simulator with one ``SimConfig`` — the gap between them is what the LLM adds.
"""

from __future__ import annotations

from decimal import Decimal

from app.domain.compute import indicators as ind
from app.domain.compute.backtest.models import Side, SimDecision
from app.domain.models.market import OHLCVBar


def sma_cross(
    symbol: str,
    bars: list[OHLCVBar],
    *,
    currency: str,
    asset_type: str = "stock",
    lot_size: Decimal = Decimal(1),
    fast: int = 20,
    slow: int = 50,
    atr_multiple: Decimal = Decimal(2),
    reward_r: Decimal = Decimal(2),
    time_stop_bars: int | None = None,
) -> list[SimDecision]:
    if fast >= slow:
        raise ValueError("fast must be shorter than slow")
    closes = [float(b.close) for b in bars]
    f = ind.sma(closes, fast).value
    s = ind.sma(closes, slow).value
    atr = ind.atr([float(b.high) for b in bars], [float(b.low) for b in bars], closes, 14).value
    out: list[SimDecision] = []
    for i in range(1, len(bars)):
        f0, s0, f1, s1, a = f[i - 1], s[i - 1], f[i], s[i], atr[i]
        if None in (f0, s0, f1, s1, a) or not a:
            continue
        assert f0 is not None and s0 is not None and f1 is not None and s1 is not None and a is not None
        side: Side
        if f0 <= s0 and f1 > s1:
            side = "long"
        elif f0 >= s0 and f1 < s1:
            side = "short"
        else:
            continue
        close = bars[i].close
        risk = Decimal(str(round(a, 8))) * atr_multiple
        sign = 1 if side == "long" else -1
        out.append(
            SimDecision(
                symbol=symbol,
                currency=currency,
                asset_type=asset_type,
                decided_on=bars[i].ts.date(),
                side=side,
                stop=close - sign * risk,
                targets=[close + sign * risk * reward_r],
                time_stop_bars=time_stop_bars,
                lot_size=lot_size,
                ref=f"baseline:sma{fast}/{slow}",
            )
        )
    return out
