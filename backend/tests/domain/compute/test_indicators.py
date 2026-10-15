from __future__ import annotations

import csv
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.domain.compute.indicators import (
    atr,
    bollinger,
    ema,
    macd,
    macd_cross,
    macd_hist_state,
    rsi,
    rsi_divergence,
    rsi_ma,
    sma,
    verified_snapshot,
)
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
    assert result["signal"][:8] == [None] * 8  # SMA9 of the MACD line needs 9 values
    for m, s, h in zip(result["macd"][8:], result["signal"][8:], result["hist"][8:], strict=True):
        assert h == pytest.approx(m - s, abs=1e-9)


def test_macd_signal_is_an_sma_of_the_macd_line_by_default() -> None:
    closes = [float(x) for x in range(1, 61)]
    closes[30:] = [60.0 - 0.5 * k for k in range(30)]
    v = macd(closes).value
    line = [x for x in v["macd"] if x is not None]
    assert v["signal"][-1] == pytest.approx(sum(line[-9:]) / 9)
    classic = macd(closes, signal_ma="ema").value
    assert classic["signal"][-1] != pytest.approx(v["signal"][-1])


def test_macd_hist_state_and_cross() -> None:
    assert macd_hist_state([None, 0.5]) is None
    assert macd_hist_state([0.2, 0.5]) == "rising_above"
    assert macd_hist_state([0.6, 0.5]) == "falling_above"
    assert macd_hist_state([-0.2, -0.5]) == "falling_below"
    assert macd_hist_state([-0.6, -0.5]) == "rising_below"
    assert macd_cross([1.0, 1.2], [1.1, 1.1]) == "bullish"
    assert macd_cross([1.2, 1.0], [1.1, 1.1]) == "bearish"
    assert macd_cross([1.2, 1.3], [1.1, 1.1]) is None


def test_rsi_ma_is_an_sma_of_rsi() -> None:
    r = [None, 40.0, 50.0, 60.0]
    assert rsi_ma(r, 3) == [None, None, None, pytest.approx(50.0)]


def _bars_from_closes(closes: list[float], wick: float = 0.3) -> tuple[list[float], list[float]]:
    return [c + wick for c in closes], [c - wick for c in closes]


def test_rsi_divergence_bullish_on_lower_low_with_higher_rsi_low() -> None:
    # a steep fall to 88.4, a bounce to 96.8, then a slow drift to a lower low at 87.2: the second
    # RSI pivot low (24) is above the first (9) -> regular bullish divergence, confirmed 5 bars later
    closes = [100.0, 100.28, 100.49, 100.43, 100.24, 100.25, 100.17, 100.4, 100.24, 100.22, 100.29, 100.62,
              100.62, 100.45, 100.65, 100.74, 100.54, 100.87, 101.26, 101.51, 101.83, 101.68, 101.86, 102.18,
              102.33, 102.31, 101.99, 101.94, 102.03, 102.36, 102.73, 100.92, 99.34, 97.4, 95.78, 94.01,
              91.92,
              90.25, 88.39, 89.78, 91.08, 91.98, 93.18, 94.6, 95.65, 96.75, 96.1, 95.18, 94.41, 93.51, 92.9,
              92.22, 91.4, 90.43, 89.56, 88.76, 88.13, 87.17, 87.79, 88.47, 89.09, 89.82, 90.44, 91.23, 91.87,
              92.51]  # fmt: skip
    highs, lows = _bars_from_closes(closes)
    v = rsi_divergence(closes, highs, lows, 14).value
    assert v["bullish"] is not None and v["bearish"] is None
    rp, rc = v["bullish"]["rsi"]
    lp, lc = v["bullish"]["price"]
    assert lc < lp and rc > rp
    assert v["bullish"]["pivot_bars_ago"] == 8
    # not confirmed until 5 bars after the pivot: cut the series 3 bars after it and nothing fires
    short = closes[: len(closes) - 6]
    highs, lows = _bars_from_closes(short)
    assert rsi_divergence(short, highs, lows, 14).value["bullish"] is None


def test_rsi_divergence_none_on_flat_series() -> None:
    closes = [100.0 + (k % 3) * 0.1 for k in range(80)]
    highs, lows = _bars_from_closes(closes)
    v = rsi_divergence(closes, highs, lows, 14).value
    assert v == {"bullish": None, "bearish": None}


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
