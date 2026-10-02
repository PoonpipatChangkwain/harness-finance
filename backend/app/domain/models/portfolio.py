"""Portfolio models: positions, the book, and its 3-state context.

Ported concept from ``../TradingAgents/tradingagents/portfolio.py``: a
position, a flat book, and "not provided" are distinct facts and must never
be conflated (treating "not provided" as "flat" invents a fact about the
user's account). See docs/contracts.md §5.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from app.domain.models.symbol import Symbol


class Position(BaseModel):
    symbol: Symbol
    quantity: Decimal
    avg_cost: Decimal | None = None
    target_weight: Decimal | None = None
    declared_weight: Decimal | None = None
    note: str | None = None


class Portfolio(BaseModel):
    id: str
    name: str
    base_currency: str = "THB"
    cash: Decimal | None = None
    positions: list[Position]
    updated_at: datetime
    # (P12) display + audit: icon/color for the multi-portfolio view, and the
    # ``kind=user_input`` provenance record of the revision these values came from
    icon: str | None = None
    color: str | None = None
    provenance_id: str | None = None


def portfolio_hash(portfolio: Portfolio) -> str:
    """Stable hash of what the book *holds* (port of TradingAgents ``PortfolioContext`` hashing).

    Used in the advisor's graph signature (T3) and the analytics cache key: two
    portfolios with the same positions, cash and base currency hash the same, whatever
    their name, icon or timestamps.
    """
    payload = {
        "base": portfolio.base_currency,
        "cash": None if portfolio.cash is None else str(portfolio.cash.normalize()),
        "positions": sorted(
            [
                p.symbol.canonical,
                str(p.quantity.normalize()),
                None if p.avg_cost is None else str(p.avg_cost.normalize()),
                None if p.declared_weight is None else str(p.declared_weight.normalize()),
                None if p.target_weight is None else str(p.target_weight.normalize()),
            ]
            for p in portfolio.positions
        ),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:16]


PortfolioState = Literal["positions", "flat", "not_provided"]


class PortfolioContext(BaseModel):
    state: PortfolioState
    portfolio: Portfolio | None = None


def portfolio_context(portfolio: Portfolio | None) -> PortfolioContext:
    """Classify a (possibly absent) portfolio into its 3-state context.

    ``None`` -> not_provided; zero positions and no cash -> flat; else
    positions. A portfolio with only cash and no positions is still
    "flat" (an empty book, not "no information").
    """
    if portfolio is None:
        return PortfolioContext(state="not_provided", portfolio=None)
    if not portfolio.positions:
        return PortfolioContext(state="flat", portfolio=portfolio)
    return PortfolioContext(state="positions", portfolio=portfolio)
