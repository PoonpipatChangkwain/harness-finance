"""Yahoo Finance provider — the Tier-A default for almost every capability.

Ported from ``../TradingAgents/tradingagents/dataflows/vendors/yahoo/``
(``ohlcv.py``, ``market.py``, ``fundamentals.py``, ``news.py``) and
``../FinRobot/finrobot/data_source/yfinance_utils.py``: wraps the sync
``yfinance`` SDK with ``asyncio.to_thread``, retries transient failures
(``yf_retry``), and raises ``NoData`` on an empty frame (``raise_for_empty``)
instead of letting an empty DataFrame flow downstream as a fabricable
"no news" answer.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

import pandas as pd
import yfinance as yf

from app.domain.data.base import (
    Capability,
    DataRequest,
    DataResult,
    DataStatus,
    NoData,
    Unsupported,
)
from app.domain.data.http import to_thread_retry
from app.domain.data.schemas import CompanyProfile, FundamentalsSnapshot, NewsItem, StatementSet, SymbolMatch
from app.domain.models.market import OHLCVBar, OHLCVFrame, Quote
from app.domain.models.provenance import new_record
from app.domain.models.symbol import SPOT_METAL_PROXY, Symbol, normalize_symbol

_CAPABILITIES = frozenset(
    {
        Capability.PRICES,
        Capability.QUOTE,
        Capability.PROFILE,
        Capability.FUNDAMENTALS,
        Capability.STATEMENTS,
        Capability.NEWS,
        Capability.GLOBAL_NEWS,
        Capability.INSIDER,
        Capability.ANALYST,
        Capability.SYMBOL_SEARCH,
    }
)


def _raise_for_empty(df: pd.DataFrame | None, symbol: str) -> pd.DataFrame:
    if df is None or df.empty:
        raise NoData(f"yfinance returned no rows for {symbol}")
    return df


# calendar days a ``period=`` range spans (a little generous: holidays must not starve SMA200)
_RANGE_DAYS: dict[str, int] = {
    "1d": 4, "5d": 8, "1mo": 31, "3mo": 93, "6mo": 186, "1y": 366, "2y": 731, "5y": 1827, "10y": 3653,
}  # fmt: skip


def _bar_ts(idx: pd.Timestamp, interval: str) -> datetime:
    """UTC timestamp of a bar; a daily/weekly bar is stamped at 00:00 UTC of its *local* date.

    Yahoo indexes a daily bar at the exchange's local midnight: a SET session of 30 Sep is
    ``2026-09-30 00:00+07:00`` = ``2026-09-29 17:00 UTC``. Everything downstream dates a
    bar by ``ts.date()`` (the ``as_of`` cut, the snapshot's as-of, the chart), so the raw
    UTC instant would put every Asian bar on the previous day — and let a backtest dated
    the 29th see the 30th's prices.
    """
    local = idx.to_pydatetime()
    if interval in ("1d", "5d", "1wk", "1mo", "3mo"):
        return datetime.combine(local.date(), datetime.min.time(), tzinfo=UTC)
    return local.astimezone(UTC)


def _window_ending(as_of: date | None, range_: str, start: Any = None) -> tuple[str, str] | None:
    """``(start, end)`` for ``history()`` when ``as_of`` is in the past or ``start`` is explicit,
    else None (``period=``). Yahoo's ``end`` is exclusive, so it is the day after ``as_of``.
    """
    if start is not None:  # an explicit window (a backtest preload)
        end = as_of or date.today()
        return date.fromisoformat(str(start)).isoformat(), (end + timedelta(days=1)).isoformat()
    if as_of is None or as_of >= date.today() or range_ not in _RANGE_DAYS:
        return None
    first = as_of - timedelta(days=_RANGE_DAYS[range_])
    return first.isoformat(), (as_of + timedelta(days=1)).isoformat()


# Searching the futures ticker (GC=F) mostly returns miner stocks; a plain query is on-topic.
_METAL_NEWS_QUERY = {"XAUUSD": "gold price", "XAGUSD": "silver price"}


def _news_item(entry: dict[str, Any], canonical: str) -> NewsItem:
    """One Yahoo news entry: the ``Ticker.get_news`` shape (nested ``content``) or ``Search`` (flat)."""
    content = entry.get("content", entry)
    pub = content.get("pubDate") or content.get("providerPublishTime")
    published_at = None
    if isinstance(pub, str):
        try:
            published_at = datetime.fromisoformat(pub.replace("Z", "+00:00"))
        except ValueError:
            published_at = None
    elif isinstance(pub, int | float):
        published_at = datetime.fromtimestamp(pub, tz=UTC)
    url = (
        (content.get("canonicalUrl") or {}).get("url")
        or (content.get("clickThroughUrl") or {}).get("url")
        or content.get("link")
        or ""
    )
    source = (content.get("provider") or {}).get("displayName") or content.get("publisher") or "Yahoo Finance"
    return NewsItem(
        id=str(entry.get("id") or entry.get("uuid") or url),
        title=content.get("title", ""),
        summary=content.get("summary"),
        url=url,
        source=source,
        published_at=published_at,
        tickers=list(content.get("relatedTickers") or [canonical]),
    )


class YFinanceProvider:
    id = "yfinance"
    capabilities = _CAPABILITIES

    def is_configured(self) -> bool:
        return True

    def supports(self, cap: Capability, symbol: Symbol | None) -> bool:
        return cap in self.capabilities

    async def fetch(self, cap: Capability, req: DataRequest) -> DataResult:
        handler = {
            Capability.PRICES: self._prices,
            Capability.QUOTE: self._quote,
            Capability.PROFILE: self._profile,
            Capability.FUNDAMENTALS: self._fundamentals,
            Capability.STATEMENTS: self._statements,
            Capability.NEWS: self._news,
            Capability.GLOBAL_NEWS: self._news,
            Capability.INSIDER: self._insider,
            Capability.ANALYST: self._analyst,
            Capability.SYMBOL_SEARCH: self._symbol_search,
        }.get(cap)
        if handler is None:
            raise Unsupported(f"yfinance does not implement {cap.value}")
        return await handler(req)

    async def _prices(self, req: DataRequest) -> DataResult:
        if req.symbol is None:
            raise Unsupported("PRICES requires a symbol")
        canonical = req.symbol.canonical
        interval = str(req.params.get("interval", "1d"))
        range_ = str(req.params.get("range", "1y"))
        # a past as_of: the range counts back from as_of, not from today, so an old backtest
        # date still gets its full warm-up history (``period=`` always ends today)
        window = _window_ending(req.as_of, range_, req.params.get("start"))

        def _fetch() -> pd.DataFrame:
            ticker = yf.Ticker(canonical)
            if window is not None:
                start, end = window
                return cast(
                    "pd.DataFrame",
                    ticker.history(start=start, end=end, interval=interval, auto_adjust=False),
                )
            return cast("pd.DataFrame", ticker.history(period=range_, interval=interval, auto_adjust=False))

        df = _raise_for_empty(await to_thread_retry(_fetch), canonical)  # type: ignore[arg-type]
        bars = [
            OHLCVBar(
                ts=_bar_ts(cast("pd.Timestamp", idx), interval),
                open=Decimal(str(round(float(row["Open"]), 6))),
                high=Decimal(str(round(float(row["High"]), 6))),
                low=Decimal(str(round(float(row["Low"]), 6))),
                close=Decimal(str(round(float(row["Close"]), 6))),
                volume=int(row["Volume"]) if not pd.isna(row["Volume"]) else 0,
            )
            for idx, row in df.iterrows()
        ]
        frame = OHLCVFrame(symbol=canonical, interval=interval, bars=bars)  # type: ignore[arg-type]
        if req.as_of is not None:
            frame = frame.as_of(req.as_of)
            if not frame.bars:
                raise NoData(f"no bars on or before {req.as_of} for {canonical}")
        record = new_record(
            "data",
            None,
            source=self.id,
            tool="ohlcv",
            params={"interval": interval, "range": range_},
            as_of=req.as_of,
            url=f"https://finance.yahoo.com/quote/{canonical}",
        )
        return DataResult(
            provider=self.id, capability=Capability.PRICES, data=frame, records=[record], status="ok"
        )

    async def _quote(self, req: DataRequest) -> DataResult:
        if req.symbol is None:
            raise Unsupported("QUOTE requires a symbol")
        canonical = req.symbol.canonical

        def _fetch() -> dict[str, Any]:
            return dict(yf.Ticker(canonical).fast_info)

        info = await to_thread_retry(_fetch)
        assert isinstance(info, dict)
        price = info.get("lastPrice")
        if price is None:
            raise NoData(f"no quote for {canonical}")
        prev_close = info.get("previousClose")
        change = (Decimal(str(price)) - Decimal(str(prev_close))) if prev_close else None
        change_pct = (change / Decimal(str(prev_close))) if change is not None and prev_close else None
        quote = Quote(
            symbol=canonical,
            price=Decimal(str(price)),
            change=change,
            change_pct=change_pct,
            ts=datetime.now(UTC),
            currency=str(info.get("currency", req.symbol.currency)),
        )
        record = new_record("data", str(price), source=self.id, tool="quote", unit=quote.currency)
        return DataResult(
            provider=self.id, capability=Capability.QUOTE, data=quote, records=[record], status="ok"
        )

    async def _profile(self, req: DataRequest) -> DataResult:
        if req.symbol is None:
            raise Unsupported("PROFILE requires a symbol")
        canonical = req.symbol.canonical
        if req.as_of is not None and req.as_of < date.today():
            # Point-in-time leak guard: yfinance `.info` has no historical
            # vintage (ported rule from date_window.py::withhold_live_profile).
            raise Unsupported("yfinance profile has no point-in-time vintage")

        def _fetch() -> dict[str, Any]:
            return dict(yf.Ticker(canonical).get_info())

        info = await to_thread_retry(_fetch)
        assert isinstance(info, dict)
        if not info:
            raise NoData(f"no profile for {canonical}")
        profile = CompanyProfile(
            symbol=canonical,
            name=info.get("longName") or info.get("shortName") or canonical,
            sector=info.get("sector"),
            industry=info.get("industry"),
            exchange=info.get("exchange"),
            country=info.get("country"),
            market_cap=Decimal(str(info["marketCap"])) if info.get("marketCap") else None,
            description=info.get("longBusinessSummary"),
        )
        record = new_record("data", profile.name, source=self.id, tool="profile")
        return DataResult(
            provider=self.id, capability=Capability.PROFILE, data=profile, records=[record], status="ok"
        )

    async def _fundamentals(self, req: DataRequest) -> DataResult:
        if req.symbol is None:
            raise Unsupported("FUNDAMENTALS requires a symbol")
        canonical = req.symbol.canonical

        def _fetch() -> dict[str, Any]:
            return dict(yf.Ticker(canonical).get_info())

        info = await to_thread_retry(_fetch)
        assert isinstance(info, dict)
        if not info:
            raise NoData(f"no fundamentals for {canonical}")
        keys = [
            "trailingPE",
            "forwardPE",
            "priceToBook",
            "returnOnEquity",
            "returnOnAssets",
            "profitMargins",
            "debtToEquity",
            "currentRatio",
            "quickRatio",
            "dividendYield",
            "beta",
            "trailingEps",
            "revenueGrowth",
            "earningsGrowth",
        ]
        metrics = {k: (float(info[k]) if info.get(k) is not None else None) for k in keys}
        snapshot = FundamentalsSnapshot(symbol=canonical, period="ttm", metrics=metrics)
        record = new_record("data", metrics, source=self.id, tool="fundamentals")
        return DataResult(
            provider=self.id, capability=Capability.FUNDAMENTALS, data=snapshot, records=[record], status="ok"
        )

    async def _statements(self, req: DataRequest) -> DataResult:
        if req.symbol is None:
            raise Unsupported("STATEMENTS requires a symbol")
        canonical = req.symbol.canonical
        period_type = str(req.params.get("period_type", "annual"))

        def _fetch() -> dict[str, pd.DataFrame]:
            ticker = yf.Ticker(canonical)
            if period_type == "quarterly":
                return {
                    "income": ticker.quarterly_income_stmt,
                    "balance": ticker.quarterly_balance_sheet,
                    "cashflow": ticker.quarterly_cashflow,
                }
            return {
                "income": ticker.income_stmt,
                "balance": ticker.balance_sheet,
                "cashflow": ticker.cashflow,
            }

        frames = await to_thread_retry(_fetch)
        assert isinstance(frames, dict)
        income_df = frames["income"]
        if income_df is None or income_df.empty:
            raise NoData(f"no statements for {canonical}")
        periods = max(1, min(int(req.params.get("periods", 1) or 1), 8))

        def _col_dict(df: pd.DataFrame, col: Any) -> dict[str, float | None]:
            if df is None or df.empty or col not in df.columns:
                return {}
            series = df[col]
            return {str(k): (float(v) if pd.notna(v) else None) for k, v in series.items()}

        sets = [
            StatementSet(
                symbol=canonical,
                period_type="quarterly" if period_type == "quarterly" else "annual",
                fiscal_period=str(col.date()) if hasattr(col, "date") else str(col),
                income=_col_dict(income_df, col),
                balance=_col_dict(frames["balance"], col),
                cashflow=_col_dict(frames["cashflow"], col),
                fiscal_date=col.date() if hasattr(col, "date") else None,
            )
            for col in list(income_df.columns)[:periods]
        ]
        statement = sets[0]
        record = new_record("data", statement.fiscal_period, source=self.id, tool="statements")
        data: StatementSet | list[StatementSet] = sets if periods > 1 else statement
        return DataResult(
            provider=self.id, capability=Capability.STATEMENTS, data=data, records=[record], status="ok"
        )

    async def _news(self, req: DataRequest) -> DataResult:
        """Symbol news (``NEWS``) or topic news (``GLOBAL_NEWS`` with a ``query``).

        ``Ticker.get_news`` has come back empty for every ticker since yfinance 1.7
        (Yahoo endpoint change), so ``yf.Search`` is the fallback — and the only path
        for a topic query. Spot metals have no Yahoo ticker and search by name.
        """
        if req.symbol is None:
            raise Unsupported("NEWS requires a symbol")
        canonical = req.symbol.canonical
        limit = int(req.params.get("limit", 15) or 15)
        query = str(req.params.get("query") or "").strip()
        search_term = query or _METAL_NEWS_QUERY.get(canonical, canonical)

        def _fetch() -> list[dict[str, Any]]:
            raw: list[dict[str, Any]] = []
            if not query and canonical not in SPOT_METAL_PROXY:
                raw = list(yf.Ticker(canonical).get_news(count=limit) or [])
            if not raw:
                raw = list(yf.Search(search_term, news_count=limit, max_results=0).news or [])
            return raw

        raw = await to_thread_retry(_fetch)
        assert isinstance(raw, list)
        items = [_news_item(entry, canonical) for entry in raw]
        record = new_record(
            "data", {"count": len(items)}, source=self.id, tool="news", params={"query": search_term}
        )
        status: DataStatus = "ok" if items else "unavailable"
        return DataResult(
            provider=self.id, capability=Capability.NEWS, data=items, records=[record], status=status
        )

    async def _insider(self, req: DataRequest) -> DataResult:
        raise Unsupported("yfinance insider transactions not yet ported")

    async def _analyst(self, req: DataRequest) -> DataResult:
        if req.symbol is None:
            raise Unsupported("ANALYST requires a symbol")
        canonical = req.symbol.canonical

        def _fetch() -> dict[str, Any]:
            return dict(yf.Ticker(canonical).analyst_price_targets or {})

        targets = await to_thread_retry(_fetch)
        assert isinstance(targets, dict)
        if not targets:
            raise NoData(f"no analyst targets for {canonical}")
        record = new_record("data", targets, source=self.id, tool="analyst_price_targets")
        return DataResult(
            provider=self.id, capability=Capability.ANALYST, data=targets, records=[record], status="ok"
        )

    async def _symbol_search(self, req: DataRequest) -> DataResult:
        query = str(req.params.get("q", ""))
        if not query:
            raise Unsupported("SYMBOL_SEARCH requires q")

        def _fetch() -> list[dict[str, Any]]:
            return yf.Search(query, max_results=10).quotes  # type: ignore[no-any-return]

        quotes = await to_thread_retry(_fetch)
        assert isinstance(quotes, list)
        matches = []
        for q in quotes:
            symbol_raw = q.get("symbol")
            if not symbol_raw:
                continue
            try:
                symbol = normalize_symbol(symbol_raw)
            except ValueError:
                continue
            matches.append(
                SymbolMatch(
                    symbol=symbol,
                    name=q.get("longname") or q.get("shortname") or symbol_raw,
                    exchange=q.get("exchange"),
                    score=float(q.get("score", 0.0)),
                )
            )
        record = new_record("data", {"count": len(matches)}, source=self.id, tool="symbol_search")
        status: DataStatus = "ok" if matches else "unavailable"
        return DataResult(
            provider=self.id,
            capability=Capability.SYMBOL_SEARCH,
            data=matches,
            records=[record],
            status=status,
        )
