from __future__ import annotations

import csv
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.domain.compute.indicators import atr, bollinger, ema, macd, rsi, sma, verified_snapshot
from app.domain.models.market import OHLCVBar, OHLCVFrame

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "ohlcv_ptt.csv"


def _load_frame() -> OHLCVFrame:
    bars = []
    with FIXTURE.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            bars.append(
                OHLCVBar(
                    ts=datetime.combine(date.fromisoformat(row["date"]), datetime.min.time(), tzinfo=UTC),
                    open=Decimal(row["open"]),
                    high=Decimal(row["high"]),
                    low=Decimal(row["low"]),
                    close=Decimal(row["close"]),
                    volume=int(row["volume"]),
                )
            )
    return OHLCVFrame(symbol="PTT.BK", interval="1d", bars=bars)


def test_sma_of_constant_series_equals_constant() -> None:
    result = sma([10.0] * 25, n=20)
    assert result.value[-1] == pytest.approx(10.0)


def test_ema_converges_toward_constant_series() -> None:
    result = ema([5.0] * 30, n=10)
    assert result.value[-1] == pytest.approx(5.0, abs=1e-6)


def test_rsi_is_100_for_monotonic_increase() -> None:
    closes = [float(i) for i in range(1, 30)]
    result = rsi(closes, n=14)
    assert result.value[-1] == pytest.approx(100.0)


def test_rsi_is_0_for_monotonic_decrease() -> None:
    closes = [float(i) for i in range(30, 1, -1)]
    result = rsi(closes, n=14)
    assert result.value[-1] == pytest.approx(0.0)


def test_rsi_bounded_0_100_on_fixture() -> None:
    frame = _load_frame()
    closes = [float(b.close) for b in frame.bars]
    values = [v for v in rsi(closes, 14).value if v is not None]
    assert all(0.0 <= v <= 100.0 for v in values)


def test_bollinger_band_ordering() -> None:
    frame = _load_frame()
    closes = [float(b.close) for b in frame.bars]
    boll = bollinger(closes).value
    for upper, mid, lower in zip(boll["upper"], boll["mid"], boll["lower"], strict=True):
        if upper is None:
            continue
        assert lower <= mid <= upper


def test_macd_hist_equals_macd_minus_signal() -> None:
    frame = _load_frame()
    closes = [float(b.close) for b in frame.bars]
    result = macd(closes).value
    for m, s, h in zip(result["macd"], result["signal"], result["hist"], strict=True):
        assert h == pytest.approx(m - s, abs=1e-9)


def test_atr_is_positive() -> None:
    frame = _load_frame()
    highs = [float(b.high) for b in frame.bars]
    lows = [float(b.low) for b in frame.bars]
    closes = [float(b.close) for b in frame.bars]
    values = [v for v in atr(highs, lows, closes, 14).value if v is not None]
    assert all(v > 0 for v in values)


def test_verified_snapshot_shape() -> None:
    frame = _load_frame()
    as_of = frame.bars[-1].ts.date()
    result = verified_snapshot(frame, as_of)
    value = result.value
    assert value["symbol"] == "PTT.BK"
    assert value["indicators"]["sma20"] is not None
    assert value["indicators"]["sma200"] is not None
    assert len(value["recent_closes"]) == 10
    assert value["high_52w"] >= value["low_52w"]
    assert value["change_pct"]["1d"] is not None


def test_verified_snapshot_respects_as_of_cutoff() -> None:
    frame = _load_frame()
    cutoff = frame.bars[50].ts.date()
    result = verified_snapshot(frame, cutoff)
    assert result.value["latest_bar"]["ts"].startswith(cutoff.isoformat())
