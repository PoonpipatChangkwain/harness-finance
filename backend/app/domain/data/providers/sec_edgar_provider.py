"""SEC EDGAR — point-in-time company statements and filings, no API key.

Ported from ``../TradingAgents/tradingagents/dataflows/vendors/sec_edgar.py``:
every fact carries the date it was *filed*, not just the period it covers,
so a run dated in the past sees only what was on file by then (restatements
included at the vintage that was current). Requires a ``User-Agent`` that
identifies the caller (SEC returns 403 without one) but no account or key.
"""

from __future__ import annotations

import json
import time
from datetime import date
from typing import Any

import httpx

from app.domain.data.base import (
    Capability,
    DataProvider,
    DataRequest,
    DataResult,
    NoData,
    NotConfigured,
    Transient,
    Unsupported,
)
from app.domain.data.http import request_with_retry
from app.domain.data.schemas import StatementSet
from app.domain.models.provenance import new_record
from app.domain.models.symbol import Market, Symbol
from app.settings import get_settings

_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
_CACHE_TTL = 24 * 3600

# tag, best-match-first; a period keeps the first tag that reports it so
# values are never summed across renamed tags.
_TAGS: dict[str, tuple[str, ...]] = {
    "revenue": ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"),
    "cost_of_revenue": ("CostOfRevenue", "CostOfGoodsAndServicesSold"),
    "gross_profit": ("GrossProfit",),
    "operating_income": ("OperatingIncomeLoss",),
    "net_income": ("NetIncomeLoss",),
    "eps_diluted": ("EarningsPerShareDiluted",),
    "total_assets": ("Assets",),
    "current_assets": ("AssetsCurrent",),
    "cash": ("CashAndCashEquivalentsAtCarryingValue",),
    "total_liabilities": ("Liabilities",),
    "current_liabilities": ("LiabilitiesCurrent",),
    "stockholders_equity": (
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ),
    "operating_cash_flow": (
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ),
    "investing_cash_flow": ("NetCashProvidedByUsedInInvestingActivities",),
    "financing_cash_flow": ("NetCashProvidedByUsedInFinancingActivities",),
    "capex": ("PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"),
    "long_term_debt": ("LongTermDebtNoncurrent", "LongTermDebt"),
    "short_term_debt": ("LongTermDebtCurrent", "DebtCurrent", "CommercialPaper"),
    "shares_outstanding": ("CommonStockSharesOutstanding",),
    "diluted_shares": ("WeightedAverageNumberOfDilutedSharesOutstanding",),
}
_INCOME_KEYS = {
    "revenue",
    "cost_of_revenue",
    "gross_profit",
    "operating_income",
    "net_income",
    "eps_diluted",
    "diluted_shares",
}
_BALANCE_KEYS = {
    "total_assets",
    "current_assets",
    "cash",
    "total_liabilities",
    "current_liabilities",
    "stockholders_equity",
    "long_term_debt",
    "short_term_debt",
    "shares_outstanding",
}
_CASHFLOW_KEYS = {"operating_cash_flow", "investing_cash_flow", "financing_cash_flow", "capex"}
_ANNUAL_FORMS = ("10-K", "20-F", "40-F")
_SPANS = {"quarterly": (60, 115), "annual": (300, 400)}


class SecEdgarProvider:
    id = "sec_edgar"
    capabilities = frozenset({Capability.STATEMENTS, Capability.FILINGS})

    def is_configured(self) -> bool:
        return bool(get_settings().sec_edgar_user_agent)

    def supports(self, cap: Capability, symbol: Symbol | None) -> bool:
        if cap not in self.capabilities:
            return False
        return not (symbol is not None and symbol.market == Market.TH)

    def _user_agent(self) -> str:
        ua = get_settings().sec_edgar_user_agent
        if not ua:
            raise NotConfigured("SEC_EDGAR_USER_AGENT is not set")
        return ua

    async def _cached_json(self, client: httpx.AsyncClient, url: str, cache_name: str) -> dict[str, Any]:
        path = get_settings().cache_dir / "sec_edgar" / cache_name
        if path.exists() and time.time() - path.stat().st_mtime < _CACHE_TTL:
            try:
                return dict(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                pass
        resp = await request_with_retry(client, "GET", url, headers={"User-Agent": self._user_agent()})
        if resp.status_code != 200:
            raise Transient(f"SEC EDGAR request failed ({resp.status_code})")
        data = resp.json()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        return dict(data)

    async def _cik_for(self, client: httpx.AsyncClient, ticker: str) -> str | None:
        table = await self._cached_json(client, _TICKERS_URL, "company_tickers.json")
        wanted = ticker.strip().upper()
        for entry in table.values():
            if entry.get("ticker", "").upper() == wanted:
                return f"{int(entry['cik_str']):010d}"
        return None

    async def fetch(self, cap: Capability, req: DataRequest) -> DataResult:
        if req.symbol is None:
            raise Unsupported(f"{cap.value} requires a symbol")
        if req.symbol.market == Market.TH:
            raise Unsupported("SEC EDGAR only covers US filers")
        async with httpx.AsyncClient(timeout=30.0) as client:
            cik = await self._cik_for(client, req.symbol.canonical)
            if cik is None:
                raise Unsupported(f"{req.symbol.canonical} is not a US SEC filer")
            if cap == Capability.STATEMENTS:
                return await self._statements(client, cik, req)
            return await self._filings(client, cik, req)

    async def _statements(self, client: httpx.AsyncClient, cik: str, req: DataRequest) -> DataResult:
        facts_doc = await self._cached_json(client, _FACTS_URL.format(cik=cik), f"CIK{cik}_facts.json")
        us_gaap = (facts_doc.get("facts") or {}).get("us-gaap") or {}
        if not us_gaap:
            raise NoData("US filer with no us-gaap facts")

        curr_date = (req.as_of or date.today()).isoformat()
        period_type = str(req.params.get("period_type", "annual"))
        quarterly = period_type == "quarterly"
        span = _SPANS["quarterly" if quarterly else "annual"]
        forms = () if quarterly else _ANNUAL_FORMS

        values: dict[str, dict[str, float]] = {}
        for key, tags in _TAGS.items():
            period_values = self._as_of_values(us_gaap, tags, curr_date, span, forms)
            if period_values:
                values[key] = period_values

        if not values:
            raise NoData(f"no {period_type} facts on or before {curr_date}")

        periods = max(1, min(int(req.params.get("periods", 1) or 1), 8))
        ends = sorted({end for series in values.values() for end in series}, reverse=True)[:periods]
        latest_period = ends[0]
        sets = [
            StatementSet(
                symbol=req.symbol.canonical if req.symbol else "",
                period_type="quarterly" if quarterly else "annual",
                fiscal_period=end,
                income={k: values[k].get(end) for k in _INCOME_KEYS if k in values},
                balance={k: values[k].get(end) for k in _BALANCE_KEYS if k in values},
                cashflow={k: values[k].get(end) for k in _CASHFLOW_KEYS if k in values},
                fiscal_date=date.fromisoformat(end),
            )
            for end in ends
        ]
        statement = sets[0]
        record = new_record(
            "data",
            latest_period,
            source=self.id,
            tool="statements",
            as_of=req.as_of,
            url=f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}",
        )
        data: StatementSet | list[StatementSet] = sets if periods > 1 else statement
        return DataResult(
            provider=self.id, capability=Capability.STATEMENTS, data=data, records=[record], status="ok"
        )

    def _as_of_values(
        self,
        us_gaap: dict[str, Any],
        tags: tuple[str, ...],
        curr_date: str,
        span: tuple[int, int],
        forms: tuple[str, ...],
    ) -> dict[str, float]:
        low, high = span
        out: dict[str, float] = {}
        for tag in tags:
            units = (us_gaap.get(tag) or {}).get("units", {})
            for unit_values in units.values():
                latest: dict[str, dict[str, Any]] = {}
                covered: set[str] = set()
                for fact in unit_values:
                    if fact["filed"] > curr_date or fact["end"] in out:
                        continue
                    if "start" in fact:
                        days = (date.fromisoformat(fact["end"]) - date.fromisoformat(fact["start"])).days
                        if not low <= days <= high:
                            continue
                    if not forms or str(fact.get("form", "")).startswith(forms):
                        covered.add(fact["end"])
                    seen = latest.get(fact["end"])
                    if seen is None or fact["filed"] >= seen["filed"]:
                        latest[fact["end"]] = fact
                for end, fact in latest.items():
                    if end in covered:
                        out[end] = fact["val"]
        return out

    async def _filings(self, client: httpx.AsyncClient, cik: str, req: DataRequest) -> DataResult:
        doc = await self._cached_json(client, _SUBMISSIONS_URL.format(cik=cik), f"CIK{cik}_submissions.json")
        recent = (doc.get("filings") or {}).get("recent") or {}
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accessions = recent.get("accessionNumber", [])
        cutoff = (req.as_of or date.today()).isoformat()
        items = [
            {"form": f, "filed": d, "accession": a}
            for f, d, a in zip(forms, dates, accessions, strict=False)
            if d <= cutoff
        ][:20]
        if not items:
            raise NoData("no filings on or before cutoff")
        record = new_record("data", {"count": len(items)}, source=self.id, tool="filings", as_of=req.as_of)
        return DataResult(
            provider=self.id, capability=Capability.FILINGS, data=items, records=[record], status="ok"
        )


assert isinstance(SecEdgarProvider(), DataProvider)
