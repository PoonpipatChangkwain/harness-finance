"""Vendor-neutral payload schemas, one per capability (contracts.md §3).

Every provider converts its own vendor JSON into these before returning a
``DataResult`` — nothing raw ever leaves ``domain/data`` (data-sources.md §5).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from app.domain.models.market import OHLCVFrame, Quote
from app.domain.models.symbol import Symbol

__all__ = [
    "CompanyProfile",
    "EconomicEvent",
    "FundamentalsSnapshot",
    "InsiderTxn",
    "MacroObservation",
    "MacroSeries",
    "MarketOutcome",
    "NewsItem",
    "OHLCVFrame",
    "PredictionMarket",
    "Quote",
    "SocialPost",
    "StatementSet",
    "SymbolMatch",
    "WebPage",
    "WebSearchHit",
]


class CompanyProfile(BaseModel):
    symbol: str
    name: str
    sector: str | None = None
    industry: str | None = None
    exchange: str | None = None
    country: str | None = None
    market_cap: Decimal | None = None
    description: str | None = None
    as_of: date | None = None


class FundamentalsSnapshot(BaseModel):
    symbol: str
    period: str
    metrics: dict[str, float | None] = Field(default_factory=dict)
    as_of: date | None = None


class StatementSet(BaseModel):
    symbol: str
    period_type: Literal["annual", "quarterly"]
    fiscal_period: str
    income: dict[str, float | None] = Field(default_factory=dict)
    balance: dict[str, float | None] = Field(default_factory=dict)
    cashflow: dict[str, float | None] = Field(default_factory=dict)
    fiscal_date: date | None = None
    filed_at: date | None = None


class NewsItem(BaseModel):
    id: str
    title: str
    summary: str | None = None
    url: str
    source: str
    published_at: datetime | None = None
    tickers: list[str] = Field(default_factory=list)
    sentiment: float | None = None


class SocialPost(BaseModel):
    platform: str
    id: str
    text: str
    author: str | None = None
    created_at: datetime | None = None
    url: str | None = None
    stance_label: Literal["bullish", "bearish", "neutral"] | None = None
    metrics: dict[str, int] = Field(default_factory=dict)
    kind: Literal["post", "aggregate"] = "post"


class MacroObservation(BaseModel):
    date: date
    value: float | None


class MacroSeries(BaseModel):
    series_id: str
    title: str
    units: str
    observations: list[MacroObservation]
    pct_change: float | None = None


class MarketOutcome(BaseModel):
    name: str
    prob: float


class PredictionMarket(BaseModel):
    question: str
    outcomes: list[MarketOutcome]
    volume: float | None = None
    end_date: date | None = None
    url: str | None = None


class InsiderTxn(BaseModel):
    symbol: str
    insider_name: str
    relation: str | None = None
    transaction_date: date
    transaction_type: str
    shares: int
    price: Decimal | None = None


class WebSearchHit(BaseModel):
    title: str
    url: str
    snippet: str
    published_at: datetime | None = None
    source: str


class WebPage(BaseModel):
    url: str
    title: str | None = None
    markdown: str
    fetched_at: datetime
    truncated: bool = False


EventImpact = Literal["high", "medium", "low", "holiday"]


class EconomicEvent(BaseModel):
    """One scheduled macro release / speech (ECONOMIC_CALENDAR)."""

    title: str
    currency: str  # the currency it moves: USD, GBP, JPY, EUR, … ("ALL" for global)
    at: datetime  # scheduled time, timezone-aware
    impact: EventImpact
    forecast: str | None = None
    previous: str | None = None
    actual: str | None = None


class SymbolMatch(BaseModel):
    symbol: Symbol
    name: str
    exchange: str | None = None
    score: float
