"""``PortfolioService``: analytics with a short cache, and the portfolio context trading reads.

- ``analytics(portfolio)`` runs ``domain.portfolio.analytics.analyze``, persists its
  provenance records and caches the result for ``ttl_s`` (60 s) keyed by the book's
  hash — an edit changes the hash, so it never serves numbers of an older book.
- ``trading_context(symbol, portfolio_id)`` is the ``config["portfolio"]`` port of the
  trading workflow (P10 §10.2 / P12 §12.4): the 3-state context plus equity, cash and
  the symbol's current weight, converted into the *instrument's* currency so the
  trade sizing and the policy compare like with like.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from app.domain.models.portfolio import Portfolio, portfolio_context, portfolio_hash
from app.domain.models.symbol import normalize_symbol
from app.domain.portfolio.analytics import PortfolioAnalytics, analyze
from app.domain.portfolio.market import data_source, fetch_quote, fx_pair
from app.harness.provenance.store import ProvenanceStore
from app.harness.storage import portfolio_repo

ANALYTICS_TTL_S = 60.0


class PortfolioService:
    def __init__(
        self,
        provenance: ProvenanceStore | None = None,
        *,
        ttl_s: float = ANALYTICS_TTL_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.provenance = provenance or ProvenanceStore()
        self.ttl_s = ttl_s
        self.clock = clock
        self._cache: dict[str, tuple[float, PortfolioAnalytics]] = {}

    def invalidate(self, portfolio_id: str | None = None) -> None:
        if portfolio_id is None:
            self._cache.clear()
            return
        for key in [k for k in self._cache if k.startswith(f"{portfolio_id}:")]:
            del self._cache[key]

    async def analytics(self, portfolio: Portfolio, *, fresh: bool = False) -> PortfolioAnalytics:
        key = f"{portfolio.id}:{portfolio_hash(portfolio)}"
        hit = self._cache.get(key)
        if hit is not None and not fresh and self.clock() - hit[0] < self.ttl_s:
            return hit[1]
        result = await analyze(portfolio)
        await self.provenance.add_many(result.records)
        self.invalidate(portfolio.id)
        self._cache[key] = (self.clock(), result)
        return result

    async def resolve(self, portfolio_id: str | None) -> Portfolio | None:
        """The requested book; with no id, ``pf_default`` or the only book there is."""
        if portfolio_id:
            return await portfolio_repo.get_portfolio(portfolio_id)
        default = await portfolio_repo.get_portfolio(portfolio_repo.DEFAULT_PORTFOLIO_ID)
        if default is not None:
            return default
        books = await portfolio_repo.list_portfolios()
        return books[0] if len(books) == 1 else None

    async def trading_context(self, symbol: str, portfolio_id: str | None = None) -> dict[str, Any]:
        portfolio = await self.resolve(portfolio_id)
        ctx = portfolio_context(portfolio)
        if portfolio is None:
            return {"state": "not_provided"}
        a = await self.analytics(portfolio)
        sym = normalize_symbol(symbol)
        base = a.base_currency
        rate: float | None = 1.0
        if sym.currency != base:
            fx = a.fx.get(sym.currency)
            if fx is not None:
                rate = fx.value
            else:
                quote, _ = await fetch_quote(fx_pair(sym.currency, base), data_source())
                rate = float(quote.price) if quote is not None else None
        held = next((p for p in portfolio.positions if p.symbol.canonical == sym.canonical), None)
        out: dict[str, Any] = {
            "state": ctx.state,
            "portfolio_id": portfolio.id,
            "name": portfolio.name,
            "base_currency": base,
            "currency": sym.currency,
            "fx_rate": rate,
            "symbol_weight": a.weights.get(sym.canonical, 0.0),
            "held_quantity": float(held.quantity) if held is not None else 0.0,
            "declared_only": a.weights_declared_only,
            "equity": None,
            "cash": None,
            "provenance_ids": [pid for pid in (a.weights_provenance_id, portfolio.provenance_id) if pid],
        }
        if rate:
            if a.total_value is not None and a.total_value.value > 0:
                out["equity"] = a.total_value.value / rate
                out["provenance_ids"].append(a.total_value.provenance_id)
            if a.cash is not None:
                out["cash"] = a.cash.value / rate
        return out
