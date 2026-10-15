"""The ``session`` feature is the one the models were trained on (notebook ``add_extras``).

The parity fixture's last bars only cover server hours 1-6 and 20-23, so a wrong mapping at
hours 0, 9 and 15 went unnoticed until 2026-10-05. This pins all 24 hours.
"""

from __future__ import annotations

import pandas as pd

from app.domain.compute.ml.features import add_packet_context, add_session, atr, calendar_around

# notebook/xau_ml/features.py: hour 1-9 -> 0 (asia), 10-14 -> 1 (london), everything else -> 2
EXPECTED = {
    **{h: 0.0 for h in range(1, 10)},
    **{h: 1.0 for h in range(10, 15)},
    0: 2.0,
    **{h: 2.0 for h in range(15, 24)},
}


def test_session_matches_the_trained_feature_for_every_server_hour() -> None:
    frame = pd.DataFrame({"date": pd.date_range("2026-10-05 00:00", periods=24, freq="h")})
    got = add_session(frame)["session"].tolist()
    assert got == [EXPECTED[h] for h in range(24)]


def test_calendar_around_names_the_neighbouring_releases() -> None:
    # NFP Friday 2026-10-02 15:30 server, CPI Tuesday 2026-10-13 15:30 server
    near = calendar_around(pd.Timestamp("2026-10-02 14:00"))
    assert near["next_event"] == "NFP" and near["hours_to_next"] == 1.5
    after = calendar_around(pd.Timestamp("2026-10-02 16:00"))
    assert after["last_event"] == "NFP" and after["hours_since_last"] == 0.5


def test_packet_context_columns_exist_and_are_finite_on_enough_bars() -> None:
    n = 400
    idx = pd.date_range("2026-08-03 01:00", periods=n, freq="15min")
    price = 4000 + pd.Series(range(n)).mul(0.3) + pd.Series(range(n)).mod(7)
    main = pd.DataFrame(
        {"date": idx, "open": price, "high": price + 2, "low": price - 2, "close": price + 0.5}
    )
    main["t_close"] = main["date"] + pd.Timedelta(minutes=15)
    days = pd.date_range("2026-07-01", "2026-08-10", freq="B")
    d = pd.DataFrame({"date": days, "open": 3900.0, "high": 4100.0, "low": 3850.0, "close": 4000.0})
    d["t_close"] = d["date"] + pd.Timedelta(days=1)
    out = add_packet_context(main, d)
    last = out.iloc[-1]
    for col in (
        "rsi14",
        "ret_atr_4",
        "ret_atr_16",
        "ret_atr_48",
        "range_pos_96",
        "day_move_atr",
        "day_range_atr",
    ):
        assert pd.notna(last[col]), col
    assert 0.0 <= last["range_pos_96"] <= 1.0
    # distance to the previous CLOSED day's high / low, in ATR14 of the 15m bars
    a14 = atr(out["high"].to_numpy(), out["low"].to_numpy(), out["close"].to_numpy(), 14)[-1]
    assert abs(last["dist_pdh_atr"] - (last["close"] - 4100.0) / a14) < 1e-9
    assert abs(last["dist_pdl_atr"] - (last["close"] - 3850.0) / a14) < 1e-9
