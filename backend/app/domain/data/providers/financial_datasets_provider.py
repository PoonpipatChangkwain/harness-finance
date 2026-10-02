"""Financial Datasets — Tier-C paid US/crypto data, highest-quality PRICES/STATEMENTS.

Ported concept from ``../dexter/src/tools/finance/`` (``api.ts`` for the
base URL / ``X-API-KEY`` auth, ``stock-price.ts`` for ``/prices/``,
``fundamentals.ts`` for ``/financials/*``, ``key-ratios.ts``,
``filings.ts``, ``insider_trades.ts``, ``institutional_holdings.ts``,
``earnings.ts``). When configured this is the highest-quality US source and
is placed first for STATEMENTS (data-sources.md §2).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.domain.data.base import Capability, DataRequest, DataResult, NoData, NotConfigured, Unsupported
from app.domain.data.http import make_client, request_with_retry
from app.domain.data.schemas import FundamentalsSnapshot, InsiderTxn, StatementSet
from app.domain.models.market import OHLCVBar, OHLCVFrame
from app.domain.models.provenance import new_record
from app.domain.models.symbol import AssetType, Symbol
from app.settings import get_settings

_BASE = "https://api.financialdatasets.ai"


class FinancialDatasetsProvider:
    id = "financial_datasets"
    capabilities = frozenset(
        {
            Capability.PRICES,
            Capability.STATEMENTS,
            Capability.FUNDAMENTALS,
            Capability.FILINGS,
            Capability.INSIDER,
            Capability.INSTITUTIONAL,
            Capability.ANALYST,
        }
    )

    def is_configured(self) -> bool:
        return bool(get_settings().financial_datasets_api_key)

    def supports(self, cap: Capability, symbol: Symbol | None) -> bool:
        if cap not in self.capabilities:
            return False
        return not (symbol is not None and symbol.asset_type not in (AssetType.STOCK, AssetType.CRYPTO))

    def _key(self) -> str:
        key = get_settings().financial_datasets_api_key
        if not key:
            raise NotConfigured("FINANCIAL_DATASETS_API_KEY is not set")
        return key.get_secret_value()

    async def _get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        async with make_client() as client:
            resp = await request_with_retry(
                client, "GET", f"{_BASE}{path}", params=params, headers={"X-API-KEY": self._key()}
            )
        data: dict[str, Any] = resp.json()
        return data

    async def fetch(self, cap: Capability, req: DataRequest) -> DataResult:
        handler = {
            Capability.PRICES: self._prices,
            Capability.STATEMENTS: self._statements,
            Capability.FUNDAMENTALS: self._fundamentals,
            Capability.FILINGS: self._filings,
            Capability.INSIDER: self._insider,
            Capability.INSTITUTIONAL: self._institutional,
            Capability.ANALYST: self._earnings,
        }.get(cap)
        if handler is None:
            raise Unsupported(f"financial_datasets does not implement {cap.value}")
        return await handler(req)

    async def _prices(self, req: DataRequest) -> DataResult:
        if req.symbol is None:
            raise Unsupported("PRICES requires a symbol")
        end = req.as_of or date.today()
        start = (
            date.fromisoformat(str(req.params.get("start")))
            if req.params.get("start")
            else end.replace(year=end.year - 1)
        )
        path = "/crypto/prices/" if req.symbol.asset_type == AssetType.CRYPTO else "/prices/"
        params = {
            "ticker": req.symbol.canonical,
            "interval": str(req.params.get("interval_name", "day")),
            "interval_multiplier": "1",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        }
        body = await self._get(path, params)
        rows = body.get("prices", [])
        if not rows:
            raise NoData(f"no financial_datasets prices for {req.symbol.canonical}")
        bars = [
            OHLCVBar(
                ts=datetime.fromisoformat(str(r.get("time") or r.get("date"))),
                open=Decimal(str(r["open"])),
                high=Decimal(str(r["high"])),
                low=Decimal(str(r["low"])),
                close=Decimal(str(r["close"])),
                volume=int(r.get("volume", 0)),
            )
            for r in rows
        ]
        frame = OHLCVFrame(symbol=req.symbol.canonical, interval="1d", bars=bars)
        record = new_record("data", None, source=self.id, tool="prices", as_of=req.as_of)
        return DataResult(
            provider=self.id, capability=Capability.PRICES, data=frame, records=[record], status="ok"
        )

    async def _statements(self, req: DataRequest) -> DataResult:
        if req.symbol is None:
            raise Unsupported("STATEMENTS requires a symbol")
        period = str(req.params.get("period_type", "annual"))
        body = await self._get(
            "/financials/", {"ticker": req.symbol.canonical, "period": period, "limit": "1"}
        )
        income = (body.get("income_statements") or [{}])[0]
        balance = (body.get("balance_sheets") or [{}])[0]
        cashflow = (body.get("cash_flow_statements") or [{}])[0]
        if not income and not balance:
            raise NoData(f"no statements for {req.symbol.canonical}")
        fiscal_period = income.get("report_period") or income.get("fiscal_period", "")
        statement = StatementSet(
            symbol=req.symbol.canonical,
            period_type="quarterly" if period == "quarterly" else "annual",
            fiscal_period=str(fiscal_period),
            income={k: v for k, v in income.items() if isinstance(v, int | float)},
            balance={k: v for k, v in balance.items() if isinstance(v, int | float)},
            cashflow={k: v for k, v in cashflow.items() if isinstance(v, int | float)},
        )
        record = new_record("data", fiscal_period, source=self.id, tool="financials")
        return DataResult(
            provider=self.id, capability=Capability.STATEMENTS, data=statement, records=[record], status="ok"
        )

    async def _fundamentals(self, req: DataRequest) -> DataResult:
        if req.symbol is None:
            raise Unsupported("FUNDAMENTALS requires a symbol")
        body = await self._get("/financial-metrics/snapshot/", {"ticker": req.symbol.canonical})
        snapshot_row = body.get("snapshot", {})
        if not snapshot_row:
            raise NoData(f"no financial metrics for {req.symbol.canonical}")
        metrics: dict[str, float | None] = {
            str(k): float(v) for k, v in snapshot_row.items() if isinstance(v, int | float)
        }
        snapshot = FundamentalsSnapshot(symbol=req.symbol.canonical, period="ttm", metrics=metrics)
        record = new_record(
            "data", {"count": len(metrics)}, source=self.id, tool="financial_metrics_snapshot"
        )
        return DataResult(
            provider=self.id, capability=Capability.FUNDAMENTALS, data=snapshot, records=[record], status="ok"
        )

    async def _filings(self, req: DataRequest) -> DataResult:
        if req.symbol is None:
            raise Unsupported("FILINGS requires a symbol")
        body = await self._get("/filings/", {"ticker": req.symbol.canonical})
        items = body.get("filings", [])
        if not items:
            raise NoData(f"no filings for {req.symbol.canonical}")
        record = new_record("data", {"count": len(items)}, source=self.id, tool="filings")
        return DataResult(
            provider=self.id, capability=Capability.FILINGS, data=items, records=[record], status="ok"
        )

    async def _insider(self, req: DataRequest) -> DataResult:
        if req.symbol is None:
            raise Unsupported("INSIDER requires a symbol")
        body = await self._get("/insider-trades/", {"ticker": req.symbol.canonical, "limit": "50"})
        rows = body.get("insider_trades", [])
        if not rows:
            raise NoData(f"no insider trades for {req.symbol.canonical}")
        items = [
            InsiderTxn(
                symbol=req.symbol.canonical,
                insider_name=r.get("name", "unknown"),
                relation=r.get("title"),
                transaction_date=date.fromisoformat(r["transaction_date"]),
                transaction_type=r.get("transaction_shares_type", "unknown"),
                shares=int(r.get("transaction_shares", 0)),
                price=Decimal(str(r["transaction_price_per_share"]))
                if r.get("transaction_price_per_share")
                else None,
            )
            for r in rows
            if r.get("transaction_date")
        ]
        record = new_record("data", {"count": len(items)}, source=self.id, tool="insider_trades")
        return DataResult(
            provider=self.id, capability=Capability.INSIDER, data=items, records=[record], status="ok"
        )

    async def _institutional(self, req: DataRequest) -> DataResult:
        if req.symbol is None:
            raise Unsupported("INSTITUTIONAL requires a symbol")
        body = await self._get("/institutional-holdings/", {"ticker": req.symbol.canonical})
        rows = body.get("institutional_ownership", body.get("institutional_holdings", []))
        if not rows:
            raise NoData(f"no institutional holdings for {req.symbol.canonical}")
        record = new_record("data", {"count": len(rows)}, source=self.id, tool="institutional_holdings")
        return DataResult(
            provider=self.id, capability=Capability.INSTITUTIONAL, data=rows, records=[record], status="ok"
        )

    async def _earnings(self, req: DataRequest) -> DataResult:
        if req.symbol is None:
            raise Unsupported("ANALYST requires a symbol")
        body = await self._get("/earnings", {"ticker": req.symbol.canonical})
        rows = body.get("earnings", [])
        if not rows:
            raise NoData(f"no earnings history for {req.symbol.canonical}")
        record = new_record("data", {"count": len(rows)}, source=self.id, tool="earnings")
        return DataResult(
            provider=self.id, capability=Capability.ANALYST, data=rows, records=[record], status="ok"
        )
