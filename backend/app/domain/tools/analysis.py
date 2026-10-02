"""Compute tools: returns/risk, DCF, comps, multi-symbol comparisons (F1).

The LLM proposes assumptions (bounded ``DCFAssumptions``); code fetches the
inputs and does all arithmetic. Assumptions are recorded as
``kind="llm_assumption"`` so provenance shows who chose them (F3).
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.domain.compute import returns as rt
from app.domain.compute.base import ComputeResult
from app.domain.compute.ranking import rank_by
from app.domain.compute.valuation import (
    DCFAssumptions,
    dcf,
    dcf_sensitivity,
    peer_multiple_stats,
)
from app.domain.data.base import Capability, DataResult
from app.domain.data.schemas import FundamentalsSnapshot, StatementSet
from app.domain.models.market import OHLCVFrame
from app.domain.models.provenance import ProvenanceRecord, new_record
from app.domain.models.symbol import Symbol
from app.domain.tools.base import ToolContext, ToolOutput, ToolSpec
from app.domain.tools.common import SymbolArgs, benchmark_for, fetch, pick, record_ids, to_symbol

Range = Literal["3mo", "6mo", "1y", "2y", "5y"]

MULTIPLE_KEYS: dict[str, tuple[str, ...]] = {
    "pe": ("trailingPE", "price_to_earnings_ratio", "pe_ratio", "peRatio"),
    "forward_pe": ("forwardPE", "forward_pe", "forward_price_to_earnings_ratio"),
    "pb": ("priceToBook", "price_to_book_ratio", "pb_ratio"),
    "ev_ebitda": ("enterpriseToEbitda", "enterprise_value_to_ebitda_ratio", "ev_to_ebitda", "evToEbitda"),
}
FUNDAMENTAL_KEYS: dict[str, tuple[str, ...]] = {
    **MULTIPLE_KEYS,
    "roe": ("returnOnEquity", "return_on_equity"),
    "net_margin": ("profitMargins", "net_margin", "net_profit_margin"),
    "debt_to_equity": ("debtToEquity", "debt_to_equity"),
    "revenue_growth": ("revenueGrowth", "revenue_growth"),
    "dividend_yield": ("dividendYield", "dividend_yield"),
}


class ReturnsRiskArgs(SymbolArgs):
    range: Range = "1y"
    benchmark: str | None = Field(
        default=None, description="Override benchmark (default .BK→^SET.BK, US→SPY)"
    )


class DCFArgs(SymbolArgs):
    assumptions: DCFAssumptions = Field(
        description=(
            "Your proposed WACC, terminal growth, years and one growth rate per year (bounded). "
            "All rates are DECIMALS: wacc 0.09 = 9%, growth 0.05 = 5%."
        )
    )
    fcf0: float | None = Field(default=None, description="Override base free cash flow if statements lack it")
    net_debt: float | None = Field(default=None, description="Override net debt (debt - cash)")
    shares: float | None = Field(default=None, description="Override shares outstanding")


class CompsArgs(SymbolArgs):
    peers: list[str] = Field(min_length=1, max_length=8, description="Peer tickers")
    multiple: Literal["pe", "forward_pe", "pb", "ev_ebitda"] = "pe"


class MultiArgs(BaseModel):
    symbols: list[str] = Field(min_length=2, max_length=10)
    market: Literal["US", "TH"] | None = None


class PerformanceArgs(MultiArgs):
    range: Range = "1y"


class FundamentalsCompareArgs(MultiArgs):
    metrics: list[str] = Field(
        default_factory=lambda: ["pe", "pb", "roe", "net_margin", "debt_to_equity", "revenue_growth"],
        description=f"Any of: {', '.join(FUNDAMENTAL_KEYS)}",
    )


def _closes(frame: OHLCVFrame) -> tuple[list[float], list[str]]:
    return [float(b.close) for b in frame.bars], [b.ts.date().isoformat() for b in frame.bars]


async def _prices(sym: Symbol, range_: str) -> DataResult:
    return await fetch(Capability.PRICES, sym, range=range_, interval="1d")


def _risk_block(closes: list[float], dates: list[str], inputs: list[str]) -> tuple[dict[str, Any], list[Any]]:
    rets = rt.simple_returns(closes, inputs=inputs)
    parts: dict[str, ComputeResult[Any]] = {
        "cumulative_return": rt.cumulative_return(closes, inputs=inputs),
        "annualized_volatility": rt.annualized_volatility(rets.value, inputs=inputs),
        "max_drawdown": rt.max_drawdown(closes, list(dates), inputs=inputs),
        "var_95_1d": rt.historical_var(rets.value, 0.95, inputs=inputs),
        "cvar_95_1d": rt.cvar(rets.value, 0.95, inputs=inputs),
    }
    value = {k: v.value for k, v in parts.items()}
    records = [r for v in parts.values() for r in v.records]
    return value, records


async def compute_returns_risk(args: ReturnsRiskArgs, ctx: ToolContext) -> ToolOutput:
    sym = to_symbol(args.symbol, args.market)
    bench_raw = args.benchmark or benchmark_for(sym)
    bench = to_symbol(bench_raw) if bench_raw else None
    jobs = [_prices(sym, args.range)] + ([_prices(bench, args.range)] if bench else [])
    results = await asyncio.gather(*jobs, return_exceptions=True)
    main = results[0]
    if isinstance(main, BaseException):
        raise main
    closes, dates = _closes(main.data)
    if len(closes) < 3:
        return ToolOutput(data=None, summary=f"not enough bars for {sym.canonical}", status="unavailable")
    inputs = record_ids(main.records)
    value, records = _risk_block(closes, dates, inputs)
    records = [*main.records, *records]
    status: Literal["ok", "partial"] = "ok"
    notes: list[str] = []
    if bench is not None:
        bres = results[1]
        if isinstance(bres, BaseException):
            status = "partial"
            notes.append(f"benchmark {bench.canonical} unavailable: {bres}")
        else:
            bcloses, _ = _closes(bres.data)
            beta = rt.beta(
                rt.simple_returns(closes).value,
                rt.simple_returns(bcloses).value,
                inputs=[*inputs, *record_ids(bres.records)],
            )
            value["beta"] = beta.value
            value["benchmark"] = bench.canonical
            bench_ret = rt.cumulative_return(bcloses, inputs=record_ids(bres.records))
            value["benchmark_return"] = bench_ret.value
            records += [*bres.records, *beta.records, *bench_ret.records]
    value.update({"symbol": sym.canonical, "range": args.range, "bars": len(closes), "notes": notes})
    mdd = value["max_drawdown"]
    mdd_pct = mdd.get("mdd") if isinstance(mdd, dict) else None
    summary = f"{sym.canonical} {args.range}: return {value['cumulative_return']:.2%}"
    summary += f" · vol {value['annualized_volatility']:.2%}"
    if isinstance(mdd_pct, float):
        summary += f" · MDD {mdd_pct:.2%}"
    return ToolOutput(data=value, summary=summary, records=records, status=status)


def _latest_statement(result: DataResult) -> StatementSet:
    data = result.data
    latest = data[0] if isinstance(data, list) else data
    if not isinstance(latest, StatementSet):
        raise ValueError(f"unexpected statements payload from {result.provider}")
    return latest


def _dcf_inputs(st: StatementSet) -> dict[str, float | None]:
    fcf = pick(st.cashflow, "Free Cash Flow", "free_cash_flow")
    if fcf is None:
        ocf = pick(
            st.cashflow,
            "Operating Cash Flow",
            "operating_cash_flow",
            "net_cash_flow_from_operations",
            "Cash Flow From Continuing Operating Activities",
        )
        capex = pick(st.cashflow, "Capital Expenditure", "capital_expenditure", "capex")
        fcf = ocf - abs(capex) if ocf is not None and capex is not None else None
    debt = pick(st.balance, "Total Debt", "total_debt")
    if debt is None:
        parts = [p for p in (pick(st.balance, "long_term_debt"), pick(st.balance, "short_term_debt")) if p]
        debt = sum(parts) if parts else None
    cash = pick(
        st.balance,
        "Cash And Cash Equivalents",
        "cash_and_equivalents",
        "cash",
        "Cash Cash Equivalents And Short Term Investments",
    )
    shares = pick(
        st.balance, "Ordinary Shares Number", "Share Issued", "outstanding_shares", "shares_outstanding"
    ) or pick(st.income, "diluted_shares", "Diluted Average Shares")
    net_debt = debt - cash if debt is not None and cash is not None else None
    return {"fcf0": fcf, "net_debt": net_debt, "shares": shares, "total_debt": debt, "cash": cash}


async def compute_dcf(args: DCFArgs, ctx: ToolContext) -> ToolOutput:
    sym = to_symbol(args.symbol, args.market)
    result = await fetch(Capability.STATEMENTS, sym, period_type="annual")
    st = _latest_statement(result)
    found = _dcf_inputs(st)
    overrides = {"fcf0": args.fcf0, "net_debt": args.net_debt, "shares": args.shares}
    used: dict[str, float] = {}
    missing: list[str] = []
    records: list[ProvenanceRecord] = list(result.records)
    inputs_rec = new_record(
        "data",
        {**found, "fiscal_period": st.fiscal_period},
        source=result.provider,
        tool="compute_dcf",
        inputs=record_ids(result.records),
        unit=sym.currency,
        as_of=st.fiscal_date,
    )
    records.append(inputs_rec)
    input_ids = [inputs_rec.id]
    for key in ("fcf0", "net_debt", "shares"):
        if overrides[key] is not None:
            used[key] = float(overrides[key])  # type: ignore[arg-type]
            override = new_record(
                "llm_assumption", used[key], source="llm", tool="compute_dcf", params={"field": key}
            )
            records.append(override)
            input_ids.append(override.id)
        elif found[key] is not None:
            used[key] = float(found[key])  # type: ignore[arg-type]
        else:
            missing.append(key)
    if missing:
        return ToolOutput(
            data={"symbol": sym.canonical, "missing": missing, "found": found},
            summary=f"DCF needs {', '.join(missing)} (not in statements; pass overrides from other tools)",
            records=records,
            status="partial",
        )
    assumption_rec = new_record(
        "llm_assumption", args.assumptions.model_dump(), source="llm", tool="compute_dcf"
    )
    records.append(assumption_rec)
    input_ids.append(assumption_rec.id)
    fcf0, net_debt, shares = (
        Decimal(str(used["fcf0"])),
        Decimal(str(used["net_debt"])),
        Decimal(str(used["shares"])),
    )
    base = dcf(fcf0, args.assumptions, net_debt, shares, inputs=input_ids)
    w = args.assumptions.wacc
    g = args.assumptions.terminal_growth
    wacc_grid = [round(w - 0.01, 4), round(w, 4), round(w + 0.01, 4)]
    g_grid = [round(g - 0.005, 4), round(g, 4), round(g + 0.005, 4)]
    sens = dcf_sensitivity(fcf0, args.assumptions, wacc_grid, g_grid, net_debt, shares, inputs=input_ids)
    for rec in [*base.records, *sens.records]:
        records.append(rec.model_copy(update={"unit": sym.currency}))
    ev = base.value["enterprise_value"]
    tv_pv = base.value["terminal_value_pv"]
    tv_share = tv_pv / ev if isinstance(ev, float) and isinstance(tv_pv, float) and ev else None
    data = {
        "symbol": sym.canonical,
        "currency": sym.currency,
        "fiscal_period": st.fiscal_period,
        "inputs": used,
        "assumptions": args.assumptions.model_dump(),
        "result": base.value,
        "terminal_value_share_of_ev": tv_share,
        "sensitivity": sens.value,
    }
    per_share = base.value.get("per_share")
    ps = f"{per_share:,.2f}" if isinstance(per_share, float) else "n/a"
    return ToolOutput(data=data, summary=f"{sym.canonical} DCF {ps} {sym.currency}/share", records=records)


async def _fundamentals(sym: Symbol) -> DataResult | BaseException:
    try:
        return await fetch(Capability.FUNDAMENTALS, sym)
    except Exception as exc:
        return exc


async def compute_comps(args: CompsArgs, ctx: ToolContext) -> ToolOutput:
    target = to_symbol(args.symbol, args.market)
    peers = [to_symbol(p, args.market) for p in args.peers if p.strip()]
    results = await asyncio.gather(*(_fundamentals(s) for s in [target, *peers]))
    keys = MULTIPLE_KEYS[args.multiple]
    table: dict[str, float | None] = {}
    records: list[ProvenanceRecord] = []
    failed: list[str] = []
    for sym, res in zip([target, *peers], results, strict=True):
        if isinstance(res, BaseException):
            failed.append(sym.canonical)
            table[sym.canonical] = None
            continue
        snap: FundamentalsSnapshot = res.data
        table[sym.canonical] = pick(snap.metrics, *keys)
        records += res.records
    peer_values = [v for k, v in table.items() if k != target.canonical and v is not None and v > 0]
    ids = record_ids(records)
    stats = peer_multiple_stats(peer_values, inputs=ids)
    records += stats.records
    own = table.get(target.canonical)
    median = stats.value["median"]
    premium = (own / median - 1.0) if own and median else None
    prem_rec = new_record(
        "compute",
        {"own": own, "peer_median": median, "premium_vs_median": premium},
        source="compute",
        calculation="compute.valuation.premium_vs_median@v1",
        inputs=[r.id for r in stats.records],
    )
    records.append(prem_rec)
    table_rec = new_record(
        "data",
        table,
        source="+".join(sorted({r.source for r in records if r.kind == "data"})) or "none",
        tool="compute_comps",
        params={"multiple": args.multiple},
    )
    records.append(table_rec)
    data = {
        "symbol": target.canonical,
        "multiple": args.multiple,
        "values": table,
        "peer_stats": stats.value,
        "own": own,
        "premium_vs_median": premium,
        "unavailable": failed,
    }
    summary = (
        f"{target.canonical} {args.multiple} {own if own is not None else 'n/a'} vs peer median {median:.2f}"
    )
    return ToolOutput(data=data, summary=summary, records=records, status="partial" if failed else "ok")


async def compare_performance(args: PerformanceArgs, ctx: ToolContext) -> ToolOutput:
    syms = [to_symbol(s, args.market) for s in dict.fromkeys(args.symbols)]
    results = await asyncio.gather(*(_prices(s, args.range) for s in syms), return_exceptions=True)
    rows: dict[str, dict[str, Any]] = {}
    returns: dict[str, float | None] = {}
    records: list[ProvenanceRecord] = []
    for sym, res in zip(syms, results, strict=True):
        if isinstance(res, BaseException):
            returns[sym.canonical] = None
            rows[sym.canonical] = {"error": str(res)[:200]}
            continue
        closes, dates = _closes(res.data)
        if len(closes) < 3:
            returns[sym.canonical] = None
            rows[sym.canonical] = {"error": "not enough bars"}
            continue
        value, recs = _risk_block(closes, dates, record_ids(res.records))
        mdd = value["max_drawdown"]
        rows[sym.canonical] = {
            "return": value["cumulative_return"],
            "volatility": value["annualized_volatility"],
            "max_drawdown": mdd.get("mdd") if isinstance(mdd, dict) else None,
            "last_close": closes[-1],
        }
        returns[sym.canonical] = value["cumulative_return"]
        records += [*res.records, *recs]
    ranking = rank_by(returns, metric="return", inputs=record_ids(records))
    records += ranking.records
    missing = [k for k, v in returns.items() if v is None]
    return ToolOutput(
        data={"range": args.range, "metrics": rows, "ranking": ranking.value, "unavailable": missing},
        summary=f"{len(syms) - len(missing)}/{len(syms)} symbols ranked by {args.range} return",
        records=records,
        status="partial" if missing else "ok",
    )


async def compare_fundamentals(args: FundamentalsCompareArgs, ctx: ToolContext) -> ToolOutput:
    syms = [to_symbol(s, args.market) for s in dict.fromkeys(args.symbols)]
    metrics = [m for m in args.metrics if m in FUNDAMENTAL_KEYS] or ["pe", "pb", "roe"]
    results = await asyncio.gather(*(_fundamentals(s) for s in syms))
    table: dict[str, dict[str, float | None]] = {}
    records: list[ProvenanceRecord] = []
    missing: list[str] = []
    for sym, res in zip(syms, results, strict=True):
        if isinstance(res, BaseException):
            missing.append(sym.canonical)
            table[sym.canonical] = dict.fromkeys(metrics)
            continue
        snap: FundamentalsSnapshot = res.data
        table[sym.canonical] = {m: pick(snap.metrics, *FUNDAMENTAL_KEYS[m]) for m in metrics}
        records += res.records
    ids = record_ids(records)
    lower_is_better = {"pe", "forward_pe", "pb", "ev_ebitda", "debt_to_equity"}
    rankings = {}
    for m in metrics:
        ranked = rank_by(
            {s: row[m] for s, row in table.items()}, metric=m, descending=m not in lower_is_better, inputs=ids
        )
        rankings[m] = ranked.value
        records += ranked.records
    records.append(new_record("data", table, source="fundamentals", tool="compare_fundamentals", inputs=ids))
    return ToolOutput(
        data={"metrics": metrics, "table": table, "rankings": rankings, "unavailable": missing},
        summary=f"{len(syms)} companies x {len(metrics)} metrics",
        records=records,
        status="partial" if missing else "ok",
    )


ANALYSIS_TOOLS: list[ToolSpec] = [
    ToolSpec(
        name="compute_returns_risk",
        description=(
            "Return, annualized volatility, max drawdown, 1-day VaR/CVaR 95% and beta vs benchmark "
            "(.BK→^SET.BK, US→SPY) computed from real prices. Use for any risk/return number."
        ),
        args_model=ReturnsRiskArgs,
        limit_by="symbol",
        category="compute",
        timeout_s=45,
        requires=frozenset({Capability.PRICES}),
        fn=compute_returns_risk,
    ),
    ToolSpec(
        name="compute_dcf",
        description=(
            "DCF fair value per share + 3x3 WACC/terminal-growth sensitivity. YOU supply bounded assumptions "
            "(wacc 3-25%, terminal growth -2..5% < wacc, 3-10 years, one growth rate per year); the tool "
            "pulls FCF, net debt and shares from the latest annual statements and does all arithmetic. Pass "
            "fcf0/net_debt/shares only if the tool reports them missing."
        ),
        args_model=DCFArgs,
        limit_by="symbol",
        category="compute",
        timeout_s=45,
        requires=frozenset({Capability.STATEMENTS}),
        fn=compute_dcf,
    ),
    ToolSpec(
        name="compute_comps",
        description=(
            "Relative valuation: one multiple (pe, forward_pe, pb, ev_ebitda) for the target and up to "
            "8 peers, "
            "peer median/quartiles and the target's premium/discount, all computed in code."
        ),
        args_model=CompsArgs,
        limit_by="symbol",
        category="compute",
        timeout_s=60,
        requires=frozenset({Capability.FUNDAMENTALS}),
        fn=compute_comps,
    ),
    ToolSpec(
        name="compare_performance",
        description=(
            "Return, volatility and max drawdown for 2-10 symbols over one range, ranked by return in code. "
            "Use for sector scans and 'which performed best' questions."
        ),
        args_model=PerformanceArgs,
        category="compute",
        timeout_s=60,
        requires=frozenset({Capability.PRICES}),
        fn=compare_performance,
    ),
    ToolSpec(
        name="compare_fundamentals",
        description=(
            "Side-by-side fundamentals (pe, pb, ev_ebitda, roe, net_margin, debt_to_equity, revenue_growth, "
            "dividend_yield) for 2-10 companies with per-metric rankings computed in code."
        ),
        args_model=FundamentalsCompareArgs,
        category="compute",
        timeout_s=60,
        requires=frozenset({Capability.FUNDAMENTALS}),
        fn=compare_fundamentals,
    ),
]
