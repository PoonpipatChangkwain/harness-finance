"""Domain tools over a fake data router: outputs, summaries, derived provenance (P07 §7.1)."""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from app.domain.compute.ranking import rank_by
from app.domain.compute.sentiment import stance_counts
from app.domain.compute.valuation import DCFAssumptions
from app.domain.data.base import Capability, DataRequest, DataResult, NoData
from app.domain.data.router import DataRouter
from app.domain.data.schemas import (
    CompanyProfile,
    FundamentalsSnapshot,
    InsiderTxn,
    MacroObservation,
    MacroSeries,
    NewsItem,
    SocialPost,
    StatementSet,
    SymbolMatch,
    WebPage,
    WebSearchHit,
)
from app.domain.models.market import OHLCVBar, OHLCVFrame, Quote
from app.domain.models.provenance import new_record
from app.domain.models.symbol import normalize_symbol
from app.domain.tools import analysis, company, market, news
from app.domain.tools.catalog import domain_tools

Handler = Callable[[DataRequest], Any]


def frame(symbol: str, n: int = 300, base: float = 30.0, drift: float = 0.001) -> OHLCVFrame:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    bars = []
    for i in range(n):
        close = base * (1 + drift) ** i * (1 + 0.03 * math.sin(i / 7))
        bars.append(
            OHLCVBar(
                ts=start + timedelta(days=i),
                open=Decimal(str(round(close * 0.99, 4))),
                high=Decimal(str(round(close * 1.02, 4))),
                low=Decimal(str(round(close * 0.98, 4))),
                close=Decimal(str(round(close, 4))),
                volume=1000 + i,
            )
        )
    return OHLCVFrame(symbol=symbol, interval="1d", bars=bars)


class Fake:
    def __init__(self, provider_id: str, handlers: dict[Capability, Handler]) -> None:
        self.id = provider_id
        self.capabilities = frozenset(handlers)
        self.handlers = handlers

    def is_configured(self) -> bool:
        return True

    def supports(self, cap: Capability, symbol: Any) -> bool:
        return True

    async def fetch(self, cap: Capability, req: DataRequest) -> DataResult:
        data = self.handlers[cap](req)
        record = new_record("data", None, source=self.id, tool=cap.value)
        status = "unavailable" if data == [] and cap == Capability.NEWS else "ok"
        return DataResult(provider=self.id, capability=cap, data=data, records=[record], status=status)


STATEMENT = StatementSet(
    symbol="PTT.BK",
    period_type="annual",
    fiscal_period="2025-12-31",
    income={"Total Revenue": 3.0e12, "Net Income": 9.0e10},
    balance={"Total Debt": 1.0e12, "Cash And Cash Equivalents": 4.0e11, "Ordinary Shares Number": 2.8e10},
    cashflow={"Operating Cash Flow": 3.0e11, "Capital Expenditure": -1.5e11},
    fiscal_date=date(2025, 12, 31),
)


def _fund(req: DataRequest) -> FundamentalsSnapshot:
    assert req.symbol is not None
    if req.symbol.canonical == "BAD.BK":
        raise NoData("none")
    pe = {"PTT.BK": 10.0, "TOP.BK": 8.0, "BCP.BK": 12.0}.get(req.symbol.canonical, 9.0)
    return FundamentalsSnapshot(
        symbol=req.symbol.canonical,
        period="ttm",
        metrics={"trailingPE": pe, "priceToBook": 1.1, "returnOnEquity": 0.12, "debtToEquity": 80.0},
    )


def _prices(req: DataRequest) -> OHLCVFrame:
    assert req.symbol is not None
    if req.symbol.canonical == "BAD.BK":
        raise NoData("no prices")
    return frame(req.symbol.canonical, drift=0.002 if req.symbol.canonical == "PTT.BK" else 0.0005)


def _statements(req: DataRequest) -> StatementSet | list[StatementSet]:
    assert req.symbol is not None
    if req.symbol.canonical == "EMPTY.BK":
        return StatementSet(symbol="EMPTY.BK", period_type="annual", fiscal_period="2025")
    if req.params.get("period_type") == "quarterly":
        return [STATEMENT, STATEMENT.model_copy(update={"fiscal_period": "2025-09-30"})]
    return STATEMENT


def _social(req: DataRequest) -> list[SocialPost]:
    assert req.symbol is not None
    if req.symbol.canonical == "QUIET":
        return []
    return [
        SocialPost(platform="stocktwits", id="1", text="to the moon", stance_label="bullish"),
        SocialPost(platform="stocktwits", id="2", text="dump it", stance_label="bearish"),
        SocialPost(platform="reddit", id="3", text="hmm"),
        SocialPost(platform="adanos", id="4", text="agg", kind="aggregate"),
    ]


def _macro(req: DataRequest) -> MacroSeries:
    sid = str(req.params["series_id"])
    if sid == "vix":
        raise NoData("fred down")
    obs = [MacroObservation(date=date(2026, m, 1), value=4.0 + m / 10) for m in range(1, 9)]
    return MacroSeries(series_id=sid.upper(), title=sid, units="%", observations=obs, pct_change=0.02)


@pytest.fixture
def router(monkeypatch: pytest.MonkeyPatch) -> DataRouter:
    now = datetime(2026, 9, 1, tzinfo=UTC)
    providers = [
        Fake(
            "yfinance",
            {
                Capability.PRICES: _prices,
                Capability.QUOTE: lambda r: Quote(
                    symbol="PTT.BK", price=Decimal("34.5"), ts=now, currency="THB"
                ),
                Capability.PROFILE: lambda r: CompanyProfile(
                    symbol="PTT.BK",
                    name="PTT PCL",
                    sector="Energy",
                    market_cap=Decimal("9.8e11"),
                    description="x",
                ),
                Capability.FUNDAMENTALS: _fund,
                Capability.STATEMENTS: _statements,
                Capability.NEWS: lambda r: (
                    []
                    if r.symbol and r.symbol.canonical == "NONE.BK"
                    else [
                        NewsItem(
                            id="a", title="old", url="u1", source="s", published_at=now - timedelta(days=3)
                        ),
                        NewsItem(
                            id="b", title="new", url="u2", source="s", published_at=now, summary="x" * 900
                        ),
                        NewsItem(id="c", title="undated", url="u3", source="s"),
                    ]
                ),
                Capability.GLOBAL_NEWS: lambda r: [NewsItem(id="g", title="market", url="u", source="s")],
                Capability.ANALYST: lambda r: {"mean": 40.0, "high": 45.0},
                Capability.INSIDER: lambda r: [
                    InsiderTxn(
                        symbol="AAPL",
                        insider_name="Tim",
                        transaction_date=date(2026, 8, 1),
                        transaction_type="sell",
                        shares=1000,
                        price=Decimal("200"),
                    )
                ],
            },
        ),
        Fake(
            "financial_datasets", {Capability.INSTITUTIONAL: lambda r: [{"holder": "Vanguard", "shares": 5}]}
        ),
        Fake(
            "sec_edgar",
            {
                Capability.FILINGS: lambda r: (
                    {"item": r.params["item"], "text": "Risk factors…", "url": "u"}
                    if r.params.get("filing_url")
                    else [{"form": "10-K", "filed": "2026-02-01"}]
                )
            },
        ),
        Fake("fred", {Capability.MACRO: _macro}),
        Fake("polymarket", {Capability.PREDICTION_MARKETS: lambda r: [{"question": "Fed cut?", "yes": 0.6}]}),
        Fake("stocktwits", {Capability.SOCIAL: _social}),
        Fake(
            "exa",
            {
                Capability.WEB_SEARCH: lambda r: [
                    WebSearchHit(title="t", url="https://a", snippet="s", source="exa")
                ]
            },
        ),
        Fake(
            "web_fetch",
            {
                Capability.WEB_FETCH: lambda r: WebPage(
                    url=r.params["url"], title="T", markdown="# hi", fetched_at=now
                )
            },
        ),
        Fake(
            "static",
            {
                Capability.SYMBOL_SEARCH: lambda r: [
                    SymbolMatch(symbol=normalize_symbol("PTT.BK"), name="PTT", exchange="SET", score=0.9)
                ]
            },
        ),
    ]
    fake_router = DataRouter({p.id: p for p in providers})  # type: ignore[misc]
    monkeypatch.setattr("app.domain.tools.common.get_router", lambda: fake_router)
    return fake_router


def ids(out: Any) -> set[str]:
    return {r.id for r in out.records}


pytestmark = pytest.mark.usefixtures("router")


async def test_catalog_names_unique_and_described() -> None:
    names = [t.name for t in domain_tools()]
    assert len(names) == len(set(names)) >= 25
    assert all(len(t.description) > 40 for t in domain_tools())


async def test_prices_quote_snapshot_indicators() -> None:
    out = await market.get_prices(market.PricesArgs(symbol="PTT"), None)  # type: ignore[arg-type]
    assert out.summary.startswith("PTT.BK · 300 bars")
    derived = out.records[-1]
    assert derived.value["last_close"] == out.data["bars"][-1]["close"] and derived.inputs == [
        out.records[0].id
    ]

    q = await market.get_quote(market.SymbolArgs(symbol="PTT"), None)  # type: ignore[arg-type]
    assert q.summary == "PTT.BK 34.5 THB" and q.records[-1].unit == "THB"

    snap = await market.get_market_snapshot(market.SnapshotArgs(symbol="PTT"), None)  # type: ignore[arg-type]
    assert "RSI14" in snap.summary and snap.data["currency"] == "THB"
    assert "indicators" in snap.records[-1].value

    default = await market.compute_indicators(market.IndicatorArgs(symbol="PTT"), None)  # type: ignore[arg-type]
    assert "rsi14" in default.data["indicators"]
    every = await market.compute_indicators(
        market.IndicatorArgs(
            symbol="PTT",
            indicators=["sma", "ema", "rsi", "macd", "bollinger", "atr", "vwma", "stoch", "obv"],
            period=10,
        ),
        None,  # type: ignore[arg-type]
    )
    assert set(every.data["values"]) == {
        "sma10", "ema10", "rsi14", "macd", "bollinger10", "atr14", "vwma10", "stoch", "obv"
    }  # fmt: skip
    assert len(every.data["values"]["sma10"]) == 5
    early = await market.compute_indicators(
        market.IndicatorArgs(symbol="PTT", indicators=["rsi"], as_of=date(2020, 1, 1)),
        None,  # type: ignore[arg-type]
    )
    assert early.status == "unavailable"


async def test_search_and_sector_lists() -> None:
    found = await market.search_symbols(market.SearchArgs(query="ptt"), None)  # type: ignore[arg-type]
    assert found.data[0]["symbol"] == "PTT.BK"
    sectors = await market.list_sector_symbols(market.SectorArgs(market="TH"), None)  # type: ignore[arg-type]
    assert "Energy" in sectors.data["sectors"]
    energy = await market.list_sector_symbols(market.SectorArgs(market="TH", sector="energy"), None)  # type: ignore[arg-type]
    assert all(s["symbol"].endswith(".BK") for s in energy.data["symbols"]) and energy.data["symbols"]
    us = await market.list_sector_symbols(market.SectorArgs(market="US", sector="Technology"), None)  # type: ignore[arg-type]
    assert any(s["symbol"] == "AAPL" for s in us.data["symbols"])
    none = await market.list_sector_symbols(market.SectorArgs(market="US", sector="Unicorns"), None)  # type: ignore[arg-type]
    assert none.status == "partial" and none.data["available_sectors"]


async def test_company_tools() -> None:
    args = company.SymbolArgs(symbol="PTT")
    prof = await company.get_company_profile(args, None)  # type: ignore[arg-type]
    assert prof.summary.startswith("PTT PCL · Energy") and "description" not in prof.records[-1].value
    fund = await company.get_fundamentals(args, None)  # type: ignore[arg-type]
    assert fund.records[-1].value["trailingPE"] == 10.0
    st = await company.get_statements(company.StatementArgs(symbol="PTT", period="quarterly"), None)  # type: ignore[arg-type]
    assert "| item | 2025-12-31 | 2025-09-30 |" in st.data and "| Net Income | 90,000,000,000 |" in st.data
    derived = [r for r in st.records if r.tool == "get_statements"]
    assert derived[-1].value["income"]["Net Income"] == 9.0e10
    assert "### margins (computed)" in st.data and "| net_margin | 3.00% | 3.00% |" in st.data
    assert "% change vs previous period" in st.data
    est = await company.get_analyst_estimates(args, None)  # type: ignore[arg-type]
    assert est.records[-1].value == {"mean": 40.0, "high": 45.0}
    assert "PRICE TARGETS" in est.data["note"] and est.data["price_targets"]["mean"] == 40.0
    ins = await company.get_insider_activity(company.ListArgs(symbol="AAPL"), None)  # type: ignore[arg-type]
    assert ins.data["transactions"][0]["shares"] == 1000
    hold = await company.get_institutional_holdings(company.ListArgs(symbol="AAPL"), None)  # type: ignore[arg-type]
    assert hold.data["holders"][0]["holder"] == "Vanguard"
    listing = await company.read_filing(company.FilingArgs(symbol="AAPL"), None)  # type: ignore[arg-type]
    assert "1 filings" in listing.summary
    section = await company.read_filing(company.FilingArgs(symbol="AAPL", filing_url="https://sec/x"), None)  # type: ignore[arg-type]
    assert "item 1A" in section.summary


async def test_news_social_macro_web() -> None:
    n = await news.get_news(news.NewsArgs(symbol="PTT", limit=2), None)  # type: ignore[arg-type]
    assert [i["title"] for i in n.data["items"]] == ["new", "old"] and len(
        n.data["items"][0]["summary"]
    ) == 400
    none = await news.get_news(news.NewsArgs(symbol="NONE.BK"), None)  # type: ignore[arg-type]
    assert none.status == "ok" and none.data["items"] == []  # answered, nothing to report
    g = await news.get_global_news(news.GlobalNewsArgs(market="TH"), None)  # type: ignore[arg-type]
    assert g.data["items"][0]["title"] == "market"

    s = await news.get_social_sentiment(news.SocialArgs(symbol="PTT"), None)  # type: ignore[arg-type]
    counts = s.data["stance_counts"]
    assert (counts["bullish"], counts["bearish"], counts["unlabeled"], counts["total"]) == (1, 1, 1, 3)
    assert len(s.data["aggregates"]) == 1 and "bull 1 / bear 1" in s.summary
    quiet = await news.get_social_sentiment(news.SocialArgs(symbol="QUIET"), None)  # type: ignore[arg-type]
    assert quiet.status == "ok" and quiet.data["stance_counts"]["total"] == 0

    m = await news.get_macro(news.MacroArgs(preset="risk", series=["cpi"], observations=3), None)  # type: ignore[arg-type]
    assert (
        m.status == "partial"
        and m.data["failed"] == ["vix (unavailable)"]
        and len(m.data["series"]["cpi"]["observations"]) == 3
    )
    default = await news.get_macro(news.MacroArgs(), None)  # type: ignore[arg-type]
    assert len(default.data["series"]) == 4
    only_bad = await news.get_macro(news.MacroArgs(series=["vix"]), None)  # type: ignore[arg-type]
    assert only_bad.status == "unavailable"

    pm = await news.get_prediction_markets(news.QueryArgs(query="Fed cut"), None)  # type: ignore[arg-type]
    assert pm.records[-1].value[0]["yes"] == 0.6
    ws = await news.web_search(news.QueryArgs(query="PTT news"), None)  # type: ignore[arg-type]
    assert ws.data["results"][0]["url"] == "https://a"
    wf = await news.web_fetch(news.FetchArgs(url="https://example.com"), None)  # type: ignore[arg-type]
    assert wf.data["markdown"] == "# hi"


async def test_returns_risk_with_and_without_benchmark() -> None:
    out = await analysis.compute_returns_risk(analysis.ReturnsRiskArgs(symbol="PTT"), None)  # type: ignore[arg-type]
    assert out.data["benchmark"] == "^SET.BK" and isinstance(out.data["beta"], float)
    assert out.data["cumulative_return"] > 0 and "MDD" in out.summary
    assert all(r.inputs for r in out.records if r.kind == "compute")
    bad_bench = await analysis.compute_returns_risk(
        analysis.ReturnsRiskArgs(symbol="PTT", benchmark="BAD.BK"),
        None,  # type: ignore[arg-type]
    )
    assert bad_bench.status == "partial" and bad_bench.data["notes"]
    fx = await analysis.compute_returns_risk(analysis.ReturnsRiskArgs(symbol="EURUSD"), None)  # type: ignore[arg-type]
    assert "beta" not in fx.data


async def test_dcf_from_statements_and_missing_inputs() -> None:
    a = DCFAssumptions(wacc=0.09, terminal_growth=0.02, years=5, growth_rates=[0.05, 0.05, 0.04, 0.04, 0.03])
    out = await analysis.compute_dcf(analysis.DCFArgs(symbol="PTT", assumptions=a), None)  # type: ignore[arg-type]
    assert out.data["inputs"] == {"fcf0": 1.5e11, "net_debt": 6.0e11, "shares": 2.8e10}
    assert len(out.data["sensitivity"]["per_share"]) == 3 and 0 < out.data["terminal_value_share_of_ev"] < 1
    kinds = {r.kind for r in out.records}
    assert {"data", "compute", "llm_assumption"} <= kinds
    assert "THB/share" in out.summary

    missing = await analysis.compute_dcf(analysis.DCFArgs(symbol="EMPTY.BK", assumptions=a), None)  # type: ignore[arg-type]
    assert missing.status == "partial" and set(missing.data["missing"]) == {"fcf0", "net_debt", "shares"}
    forced = await analysis.compute_dcf(
        analysis.DCFArgs(symbol="EMPTY.BK", assumptions=a, fcf0=100.0, net_debt=10.0, shares=10.0),
        None,  # type: ignore[arg-type]
    )
    assert forced.status == "ok" and sum(r.kind == "llm_assumption" for r in forced.records) == 4


async def test_comps_and_comparisons() -> None:
    c = await analysis.compute_comps(
        analysis.CompsArgs(symbol="PTT", peers=["TOP", "BCP", "BAD"], market="TH"),
        None,  # type: ignore[arg-type]
    )
    assert c.data["peer_stats"]["median"] == 10.0 and c.data["premium_vs_median"] == 0.0
    assert c.status == "partial" and c.data["unavailable"] == ["BAD.BK"]

    perf = await analysis.compare_performance(
        analysis.PerformanceArgs(symbols=["PTT", "TOP", "BAD"], market="TH"),
        None,  # type: ignore[arg-type]
    )
    assert perf.data["ranking"][0]["symbol"] == "PTT.BK" and perf.data["ranking"][-1]["rank"] is None
    assert perf.status == "partial"

    fc = await analysis.compare_fundamentals(
        analysis.FundamentalsCompareArgs(
            symbols=["PTT", "TOP", "BAD"], market="TH", metrics=["pe", "roe", "zzz"]
        ),
        None,  # type: ignore[arg-type]
    )
    assert fc.data["metrics"] == ["pe", "roe"]
    assert fc.data["rankings"]["pe"][0]["symbol"] == "TOP.BK"  # lower P/E ranks first
    assert fc.status == "partial" and "x 2 metrics" in fc.summary


def test_stance_counts_and_ranking_compute() -> None:
    posts = [SocialPost(platform="x", id=str(i), text="t", stance_label="bullish") for i in range(3)]
    res = stance_counts(posts)
    assert (
        res.value["bull_ratio"] == 1.0 and res.records[0].calculation == "compute.sentiment.stance_counts@v1"
    )
    assert stance_counts([]).value["bull_ratio"] is None
    ranked = rank_by({"A": 1.0, "B": None, "C": 3.0}, metric="r", descending=False)
    assert [r["symbol"] for r in ranked.value] == ["A", "C", "B"] and ranked.value[-1]["rank"] is None


def test_dcf_inputs_from_sec_field_names() -> None:
    st = StatementSet(
        symbol="AAPL",
        period_type="annual",
        fiscal_period="2025",
        income={"diluted_shares": 1.5e10},
        balance={"long_term_debt": 8.0e10, "short_term_debt": 2.0e10, "cash": 3.0e10},
        cashflow={"operating_cash_flow": 1.1e11, "capex": 1.0e10},
    )
    found = analysis._dcf_inputs(st)
    assert found == {
        "fcf0": 1.0e11,
        "net_debt": 7.0e10,
        "shares": 1.5e10,
        "total_debt": 1.0e11,
        "cash": 3.0e10,
    }
