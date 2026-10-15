"""Economic calendar features for XAUUSD — high-impact USD events that drive gold.

Every feature is deterministic from the event schedule (no LLM, no API call at training
time).  The calendar is a static CSV of known events: date, time (UTC), event name,
impact level, forecast, actual, previous.  For live use the backend can fetch this from
investing.com or forexfactory.

Features returned per bar:
  - ``cal_high_today``      — 1 if any high-impact USD event is scheduled today (server day)
  - ``cal_hours_to_next``   — hours until the next high-impact event (capped at 48)
  - ``cal_hours_since_last``— hours since the last high-impact event (capped at 48)
  - ``cal_is_nfp_day``      — 1 if today is NFP (first Friday of the month)
  - ``cal_is_fomc_day``     — 1 if today is an FOMC day (rate decision or minutes)
  - ``cal_event_count``     — number of high-impact USD events today (0–N)
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# --- built-in calendar: first Fridays (NFP) and known FOMC dates --------------------------

def first_fridays(start: str, end: str) -> list[pd.Timestamp]:
    """Non-Farm Payrolls: first Friday of each month, 08:30 ET → ~15:30 server."""
    days = pd.date_range(start, end, freq="D")
    return [d for d in days if d.dayofweek == 4 and d.day <= 7]


# FOMC meeting dates (rate decision days): 2024-01 through 2026-12.
# Source: federalreserve.gov/monetarypolicy/fomccalendars.htm
FOMC_DATES = [
    # 2024
    "2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12", "2024-07-31",
    "2024-09-18", "2024-11-07", "2024-12-18",
    # 2025
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18", "2025-07-30",
    "2025-09-17", "2025-10-29", "2025-12-17",
    # 2026
    "2026-01-28", "2026-03-18", "2026-05-06", "2026-06-17", "2026-07-29",
    "2026-09-16", "2026-10-28", "2026-12-16",
]

# CPI release dates (approximate: usually second week Tuesday/Wednesday).
# Source: bls.gov/schedule
CPI_DATES = [
    # 2024
    "2024-01-11", "2024-02-13", "2024-03-12", "2024-04-10", "2024-05-15",
    "2024-06-12", "2024-07-11", "2024-08-14", "2024-09-11", "2024-10-10",
    "2024-11-13", "2024-12-11",
    # 2025
    "2025-01-15", "2025-02-12", "2025-03-12", "2025-04-10", "2025-05-13",
    "2025-06-11", "2025-07-15", "2025-08-12", "2025-09-10", "2025-10-14",
    "2025-11-12", "2025-12-10",
    # 2026
    "2026-01-13", "2026-02-11", "2026-03-11", "2026-04-14", "2026-05-12",
    "2026-06-10", "2026-07-14", "2026-08-12", "2026-09-11", "2026-10-13",
    "2026-11-10", "2026-12-09",
]


def build_event_schedule(start: str, end: str) -> pd.DataFrame:
    """Deterministic calendar of high-impact USD events.

    Server times: NFP 15:30, FOMC 21:00, CPI 15:30 (all approximate; what matters is
    the DATE, not the exact minute — features like ``cal_high_today`` use the server day).
    """
    rows = []

    for d in first_fridays(start, end):
        rows.append({"date": d, "server_hour": 15.5, "event": "NFP", "impact": "high"})

    for d in FOMC_DATES:
        ts = pd.Timestamp(d)
        if pd.Timestamp(start) <= ts <= pd.Timestamp(end):
            rows.append({"date": ts, "server_hour": 21.0, "event": "FOMC", "impact": "high"})

    for d in CPI_DATES:
        ts = pd.Timestamp(d)
        if pd.Timestamp(start) <= ts <= pd.Timestamp(end):
            rows.append({"date": ts, "server_hour": 15.5, "event": "CPI", "impact": "high"})

    df = pd.DataFrame(rows)
    if df.empty:
        df = pd.DataFrame(columns=["date", "server_hour", "event", "impact"])
    df["date"] = pd.to_datetime(df["date"])
    df["event_time"] = df["date"] + pd.to_timedelta(df["server_hour"], unit="h")
    return df.sort_values("event_time").reset_index(drop=True)


def add_calendar_features(bars: pd.DataFrame, start: str = "2023-01-01",
                          end: str = "2027-01-01") -> pd.DataFrame:
    """Add calendar features to a bar DataFrame with ``date`` = bar open time (server clock).

    Every feature is known at bar close (the decision time) — no look-ahead.
    """
    out = bars.copy()
    cal = build_event_schedule(start, end)
    bar_day = out["date"].dt.normalize()
    bar_time = out["date"]  # server clock

    # 1) High-impact event today
    event_days = set(cal["date"].dt.normalize().unique())
    out["cal_high_today"] = bar_day.isin(event_days).astype("float64")

    # 2) Event count today
    day_counts = cal.groupby(cal["date"].dt.normalize()).size()
    out["cal_event_count"] = bar_day.map(day_counts).fillna(0).astype("float64")

    # 3) NFP day
    nfp_days = set(cal[cal["event"] == "NFP"]["date"].dt.normalize().unique())
    out["cal_is_nfp_day"] = bar_day.isin(nfp_days).astype("float64")

    # 4) FOMC day
    fomc_days = set(cal[cal["event"] == "FOMC"]["date"].dt.normalize().unique())
    out["cal_is_fomc_day"] = bar_day.isin(fomc_days).astype("float64")

    # 5) Hours to next event / hours since last (capped at 48)
    event_times = cal["event_time"].to_numpy().astype("datetime64[ns]")
    bar_times = bar_time.to_numpy().astype("datetime64[ns]")
    hours_to = np.full(len(bars), 48.0)
    hours_since = np.full(len(bars), 48.0)

    for i, bt in enumerate(bar_times):
        future = event_times[event_times > bt]
        if len(future):
            h = (future[0] - bt) / np.timedelta64(1, "h")
            hours_to[i] = min(float(h), 48.0)
        past = event_times[event_times <= bt]
        if len(past):
            h = (bt - past[-1]) / np.timedelta64(1, "h")
            hours_since[i] = min(float(h), 48.0)

    out["cal_hours_to_next"] = hours_to
    out["cal_hours_since_last"] = hours_since

    return out


CALENDAR_COLS = [
    "cal_high_today", "cal_event_count", "cal_is_nfp_day", "cal_is_fomc_day",
    "cal_hours_to_next", "cal_hours_since_last",
]
