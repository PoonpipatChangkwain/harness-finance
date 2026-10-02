"""Trade horizons: which bars a trade is analysed on, and the playbook for that holding period.

The horizon picked on the trade page decides the *data*, not just a line of prompt text:
the verified snapshot, ATR (hence the stop distance) and the indicators all come from the
horizon's timeframe, with the daily chart as the higher-timeframe context.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel

HorizonName = Literal["intraday", "swing", "position"]
BarInterval = Literal["15m", "1h", "1d"]

INTERVAL_MINUTES: dict[str, int] = {"15m": 15, "1h": 60, "1d": 24 * 60}
# enough history for SMA200 on each interval, within what Yahoo serves (15m ≈ 60 days)
INTERVAL_RANGE: dict[str, str] = {"15m": "1mo", "1h": "3mo", "1d": "2y"}


class HorizonSpec(BaseModel, frozen=True):
    name: HorizonName
    interval: BarInterval
    bars: str  # "15-minute"
    holding: str  # how long a trade is meant to live
    atr_multiple: tuple[float, float]  # usual stop distance, in ATR of ``interval``
    time_stop_bars: int  # default time stop when the trader gives none
    event_window_h: int  # how far ahead scheduled events matter
    event_guard_h: int  # a high-impact event this close to entry gets a policy warning

    def playbook(self) -> str:
        lo, hi = self.atr_multiple
        return (
            f"Horizon: {self.name} — analyse {self.bars} bars (daily chart = higher-timeframe context); "
            f"a trade is meant to last {self.holding}; stops usually {lo:g}-{hi:g} x ATR of the {self.bars} "
            f"bars; targets reachable within the holding period; give a time stop in {self.bars} bars "
            f"(default {self.time_stop_bars}) and an invalidation condition."
        )


HORIZONS: dict[str, HorizonSpec] = {
    "intraday": HorizonSpec(
        name="intraday",
        interval="15m",
        bars="15-minute",
        holding="a few hours, closed within the trading day",
        atr_multiple=(1.0, 2.0),
        time_stop_bars=16,
        event_window_h=24,
        event_guard_h=2,
    ),
    "swing": HorizonSpec(
        name="swing",
        interval="1h",
        bars="1-hour",
        holding="one to five trading days",
        atr_multiple=(1.5, 2.5),
        time_stop_bars=48,
        event_window_h=120,
        event_guard_h=12,
    ),
    "position": HorizonSpec(
        name="position",
        interval="1d",
        bars="daily",
        holding="weeks to months",
        atr_multiple=(2.0, 3.0),
        time_stop_bars=40,
        event_window_h=168,
        event_guard_h=24,
    ),
}


def horizon(name: str | None) -> HorizonSpec:
    return HORIZONS.get(name or "swing", HORIZONS["swing"])


def time_stop_at(start: datetime, interval: str, bars: int) -> datetime:
    """Wall-clock deadline ``bars`` bars after ``start`` (calendar time; markets that close
    overnight or at weekends reach it earlier in bars than 24/7 forex)."""
    return start + timedelta(minutes=INTERVAL_MINUTES[interval] * bars)
