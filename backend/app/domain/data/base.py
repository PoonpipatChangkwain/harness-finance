"""Data provider interface, error taxonomy, request/result envelope (F4).

Ported concept from ``../TradingAgents/tradingagents/dataflows/router.py``,
``errors.py``: the router reacts to *behavior* (retryable? configured?
core capability?), not to which vendor raised, so a new provider needs no
new except clause anywhere else in the codebase. See docs/contracts.md §3.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from app.domain.models.provenance import ProvenanceRecord
from app.domain.models.symbol import Symbol


class Capability(StrEnum):
    PRICES = "prices"
    QUOTE = "quote"
    PROFILE = "profile"
    FUNDAMENTALS = "fundamentals"
    STATEMENTS = "statements"
    NEWS = "news"
    GLOBAL_NEWS = "global_news"
    MACRO = "macro"
    PREDICTION_MARKETS = "prediction_markets"
    SOCIAL = "social"
    INSIDER = "insider"
    INSTITUTIONAL = "institutional"
    FILINGS = "filings"
    ANALYST = "analyst"
    WEB_SEARCH = "web_search"
    WEB_FETCH = "web_fetch"
    SYMBOL_SEARCH = "symbol_search"
    ECONOMIC_CALENDAR = "economic_calendar"


# Capabilities where an all-providers failure must still let the caller
# proceed with a degraded answer rather than blow up the whole run.
OPTIONAL_CAPABILITIES = frozenset(
    {
        Capability.MACRO,
        Capability.PREDICTION_MARKETS,
        Capability.SOCIAL,
        Capability.INSIDER,
        Capability.INSTITUTIONAL,
        Capability.ANALYST,
        Capability.GLOBAL_NEWS,
        Capability.ECONOMIC_CALENDAR,
    }
)


class DataError(Exception):
    retryable: bool = False


class NotConfigured(DataError):
    """No API key / config for this provider; skip it, it is not a failure."""


class RateLimited(DataError):
    retryable = True

    def __init__(self, message: str = "rate limited", *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class Transient(DataError):
    """Timeout, 5xx, connection error — worth retrying / trying the next provider."""

    retryable = True


class NoData(DataError):
    """The provider was reachable and configured, but has no data for this request."""


class Unsupported(DataError):
    """This provider does not support this symbol/market/capability combination."""


class BadRequest(DataError):
    """The request itself is invalid; failing over to another provider won't help."""


@runtime_checkable
class DataProvider(Protocol):
    id: str
    capabilities: frozenset[Capability]

    def is_configured(self) -> bool: ...

    def supports(self, cap: Capability, symbol: Symbol | None) -> bool: ...

    async def fetch(self, cap: Capability, req: DataRequest) -> DataResult: ...


class DataRequest(BaseModel):
    symbol: Symbol | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    as_of: date | None = None


DataStatus = Literal["ok", "partial", "unavailable"]


class DataResult(BaseModel):
    provider: str
    capability: Capability
    data: Any
    records: list[ProvenanceRecord] = Field(default_factory=list)
    status: DataStatus
    notes: list[str] = Field(default_factory=list)
