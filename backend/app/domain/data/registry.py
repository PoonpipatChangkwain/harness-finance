"""Provider registry: wires every implemented provider into a router singleton."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache

from app.domain.data.providers.adanos_provider import AdanosProvider
from app.domain.data.providers.alpha_vantage_provider import AlphaVantageProvider
from app.domain.data.providers.crypto_news_provider import CryptoNewsProvider
from app.domain.data.providers.financial_datasets_provider import FinancialDatasetsProvider
from app.domain.data.providers.finnhub_provider import FinnhubProvider
from app.domain.data.providers.fmp_provider import FmpProvider
from app.domain.data.providers.forexfactory_provider import ForexFactoryProvider
from app.domain.data.providers.fred_provider import FredProvider
from app.domain.data.providers.polymarket_provider import PolymarketProvider
from app.domain.data.providers.reddit_api_provider import RedditApiProvider
from app.domain.data.providers.reddit_rss_provider import RedditRssProvider
from app.domain.data.providers.search.exa import ExaProvider
from app.domain.data.providers.search.langsearch import LangSearchProvider
from app.domain.data.providers.search.perplexity import PerplexityProvider
from app.domain.data.providers.search.tavily import TavilyProvider
from app.domain.data.providers.sec_api_provider import SecApiProvider
from app.domain.data.providers.sec_edgar_provider import SecEdgarProvider
from app.domain.data.providers.spot_metals_provider import SpotMetalsProvider
from app.domain.data.providers.static_symbol_provider import StaticSymbolProvider
from app.domain.data.providers.stocktwits_provider import StocktwitsProvider
from app.domain.data.providers.web_fetch_provider import WebFetchProvider
from app.domain.data.providers.x_provider import XProvider
from app.domain.data.providers.yfinance_provider import YFinanceProvider
from app.domain.data.router import DataRouter


def _build_router() -> DataRouter:
    router = DataRouter()
    for provider in (
        # Tier A (keyless)
        SpotMetalsProvider(),
        YFinanceProvider(),
        SecEdgarProvider(),
        PolymarketProvider(),
        ForexFactoryProvider(),
        StocktwitsProvider(),
        CryptoNewsProvider(),
        RedditRssProvider(),
        WebFetchProvider(),
        StaticSymbolProvider(),
        # Tier B (free key)
        FredProvider(),
        AlphaVantageProvider(),
        FinnhubProvider(),
        FmpProvider(),
        # Tier C (paid / optional)
        FinancialDatasetsProvider(),
        XProvider(),
        AdanosProvider(),
        ExaProvider(),
        PerplexityProvider(),
        TavilyProvider(),
        LangSearchProvider(),
        RedditApiProvider(),
        SecApiProvider(),
    ):
        router.register(provider)
    return router


@lru_cache
def _default_router() -> DataRouter:
    return _build_router()


# a backtest cell swaps in its point-in-time router for its own task (``use_router``)
_override: ContextVar[DataRouter | None] = ContextVar("data_router_override", default=None)


def get_router() -> DataRouter:
    return _override.get() or _default_router()


def default_router() -> DataRouter:
    """The real router, even inside ``use_router`` (to wrap it, or to preload a backtest)."""
    return _default_router()


@contextmanager
def use_router(router: DataRouter) -> Iterator[DataRouter]:
    """Route every ``get_router()`` in this task (and tasks it spawns) to ``router``."""
    token = _override.set(router)
    try:
        yield router
    finally:
        _override.reset(token)
