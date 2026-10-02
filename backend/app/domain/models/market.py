"""OHLCV bar/frame and quote models shared by every data provider and compute fn."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

Interval = Literal["1m", "5m", "15m", "1h", "1d", "1wk"]


class OHLCVBar(BaseModel):
    ts: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


class OHLCVFrame(BaseModel):
    symbol: str
    interval: Interval
    tz: str = "UTC"
    adjusted: bool = False
    bars: list[OHLCVBar]

    def to_closes(self) -> list[Decimal]:
        return [b.close for b in self.bars]

    def as_of(self, cutoff: date) -> OHLCVFrame:
        """Bars on or before ``cutoff`` (look-ahead guard)."""
        kept = [b for b in self.bars if b.ts.date() <= cutoff]
        return self.model_copy(update={"bars": kept})


class Quote(BaseModel):
    symbol: str
    price: Decimal
    change: Decimal | None = None
    change_pct: Decimal | None = None
    ts: datetime
    currency: str
