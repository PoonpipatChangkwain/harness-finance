"""The sweep-continuation setup (compute/smc_setup.py): checks, freshness, momentum rule."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from app.domain.compute import smc_setup
from app.domain.compute.smc_setup import momentum, sweep_continuation_setup
from app.domain.models.market import OHLCVBar

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _bars(closes: list[float]) -> list[OHLCVBar]:
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        out.append(
            OHLCVBar(
                ts=T0 + timedelta(minutes=15 * i),
                open=Decimal(str(prev)),
                high=Decimal(str(round(max(prev, c) + 0.2, 4))),
                low=Decimal(str(round(min(prev, c) - 0.2, 4))),
                close=Decimal(str(c)),
                volume=0,
            )
        )
        prev = c
    return out


# 61 bars drifting up from 100 to 112
CLOSES = [round(100.0 + 0.2 * k, 4) for k in range(61)]


def _smc(trend: str, *sweeps: dict[str, Any]) -> dict[str, Any]:
    return {"swing": {"trend": trend}, "sweeps": list(sweeps)}


def _sweep(
    side: str = "long", ago: int = 3, kind: str = "continuation", trend: str = "bullish", low: float = 110.5
) -> dict[str, Any]:
    return {
        "type": "EQH" if side == "long" else "EQL",
        "level": 111.0,
        "kind": kind,
        "side": side,
        "bars_ago": ago,
        "swing_trend": trend,
        "bar_high": 111.6,
        "bar_low": low,
    }


@pytest.fixture
def momentum_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(smc_setup, "momentum", lambda side, i, **kw: {"ok": True})


@pytest.mark.usefixtures("momentum_ok")
def test_fresh_continuation_with_the_trend_is_active() -> None:
    v = sweep_continuation_setup(_bars(CLOSES), _smc("bullish", _sweep())).value
    assert v["active"] and v["side"] == "long" and v["bars_ago"] == 3
    assert v["structure_stop"] == 110.5
    assert v["reason"].startswith("long setup: close through the EQH 111")


@pytest.mark.usefixtures("momentum_ok")
@pytest.mark.parametrize(
    ("trend_now", "sweep", "failed"),
    [
        ("bullish", _sweep(trend="bearish"), "with_trend"),  # against the swing trend at the time
        ("bearish", _sweep(), "trend_unchanged"),  # the trend flipped since
        ("bullish", _sweep(side="short", trend="bullish"), "with_trend"),
    ],
)
def test_trend_checks(trend_now: str, sweep: dict[str, Any], failed: str) -> None:
    v = sweep_continuation_setup(_bars(CLOSES), _smc(trend_now, sweep)).value
    assert not v["active"] and failed in v["reason"]


@pytest.mark.usefixtures("momentum_ok")
def test_stale_reversal_and_invalidated_sweeps_are_not_setups() -> None:
    bars = _bars(CLOSES)
    old = _sweep(ago=17, low=108.0)  # the close 17 bars ago is 108.6
    assert not sweep_continuation_setup(bars, _smc("bullish", old)).value["active"]
    assert sweep_continuation_setup(bars, _smc("bullish", old), max_age=20).value["active"]
    rev = sweep_continuation_setup(bars, _smc("bullish", _sweep(kind="reversal"))).value
    assert not rev["active"] and rev["candidates"] == [] and "no continuation sweep" in rev["reason"]
    # a close below the sweeping bar's low (110.5) after the sweep invalidates it
    dip = _bars([*CLOSES[:-2], 110.0, 112.0])
    v = sweep_continuation_setup(dip, _smc("bullish", _sweep(ago=3))).value
    assert not v["active"] and "not_invalidated" in v["reason"]


def test_momentum_needs_macd_and_rsi() -> None:
    kw: dict[str, Any] = {
        "hist": [0.1, 0.3],
        "macd_line": [1.0, 1.2],
        "signal_line": [0.9, 0.9],
        "rsi_values": [55.0, 60.0],
        "rsi_avg": [50.0, 52.0],
        "divergences": [],
    }
    assert momentum("long", 1, **kw)["ok"]  # histogram rising above zero, RSI over its MA
    assert not momentum("short", 1, **kw)["ok"]
    below = {**kw, "rsi_values": [55.0, 48.0]}
    assert not momentum("long", 1, **below)["ok"]  # RSI under its MA, no divergence
    div = {**below, "divergences": [{"kind": "bullish", "known_at": 0}]}
    assert momentum("long", 1, **div)["ok"]  # a recent bullish divergence stands in for RSI
    falling = {**kw, "hist": [0.3, 0.1]}
    assert not momentum("long", 1, **falling)["ok"]  # histogram fading, no cross


def test_real_momentum_runs_on_bars_and_records_provenance() -> None:
    res = sweep_continuation_setup(_bars(CLOSES), _smc("bullish", _sweep()), inputs=["prov_x"])
    (rec,) = res.records
    assert rec.calculation == "compute.smc_setup.sweep_continuation@v1" and rec.inputs == ["prov_x"]
    (cand,) = res.value["candidates"]
    assert set(cand["momentum"]) >= {"macd_hist_state", "rsi", "rsi_ma", "ok"}
