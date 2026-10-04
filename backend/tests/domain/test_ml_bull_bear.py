"""The backend port of the bull/bear features + model reproduces the notebook exactly.

The fixture holds real GC=F bars and what ``notebook/xau_ml`` computed from them (features,
calibrated probabilities, signal) for the last 40 bars.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import numpy as np
import pytest

from app.domain.compute.ml import bull_bear
from app.domain.models.market import OHLCVBar

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "ml" / "xau_intraday_parity.json"
STEM = "xau_intraday_ct25"
SIGNAL = {1: "long", -1: "short", 0: "flat"}


def _bar(ts: datetime, row: dict[str, float]) -> OHLCVBar:
    return OHLCVBar(
        ts=ts,
        **{k: Decimal(str(row[k])) for k in ("open", "high", "low", "close")},
        volume=0,
    )


@pytest.fixture(scope="module")
def fx() -> dict[str, object]:
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    raw["main_bars"] = [_bar(datetime.fromisoformat(r["ts"]), r) for r in raw["main"]]
    raw["daily_bars"] = [_bar(datetime.fromisoformat(r["date"]).replace(tzinfo=UTC), r) for r in raw["daily"]]
    return raw


def test_features_match_the_notebook(fx: dict[str, object]) -> None:
    frame = bull_bear.feature_frame(STEM, fx["main_bars"], fx["daily_bars"])  # type: ignore[arg-type]
    expected = np.array(fx["expected_features"], dtype="float64")
    got = frame[fx["features"]].iloc[-len(expected) :].to_numpy("float64")  # type: ignore[index]
    np.testing.assert_allclose(got, expected, rtol=1e-9, atol=1e-9, equal_nan=True)


def test_probabilities_and_signal_match_at_every_cut(fx: dict[str, object]) -> None:
    main: list[OHLCVBar] = fx["main_bars"]  # type: ignore[assignment]
    n = len(fx["expected_p_bull"])  # type: ignore[arg-type]
    for k in (n, 20, 1):  # predict() reads the LAST bar: cut the series as a live run would
        cut = main[: len(main) - k + 1]
        out = bull_bear.predict(STEM, cut, fx["daily_bars"]).value  # type: ignore[arg-type]
        i = n - k
        assert out["p_bull"] == pytest.approx(fx["expected_p_bull"][i], abs=1e-4)  # type: ignore[index]
        assert out["p_bear"] == pytest.approx(fx["expected_p_bear"][i], abs=1e-4)  # type: ignore[index]
        assert out["signal"] == SIGNAL[fx["expected_signal"][i]]  # type: ignore[index]
        assert out["missing_features"] == []


def test_registry_serves_only_validated_models() -> None:
    assert bull_bear.model_for("XAUUSD", "intraday") == STEM
    assert bull_bear.model_for("XAUUSD", "swing") is None
    assert bull_bear.model_for("EURUSD=X", "intraday") is None


def test_too_few_bars_is_an_error_not_a_guess(fx: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="needs"):
        bull_bear.predict(STEM, fx["main_bars"][:100], fx["daily_bars"])  # type: ignore[index,arg-type]


def test_signal_rule() -> None:
    assert bull_bear._signal(0.40, 0.20, 0.375, 0.0) == "long"
    assert bull_bear._signal(0.20, 0.40, 0.375, 0.0) == "short"
    assert bull_bear._signal(0.36, 0.20, 0.375, 0.0) == "flat"
    assert bull_bear._signal(0.40, 0.38, 0.375, 0.05) == "flat"
