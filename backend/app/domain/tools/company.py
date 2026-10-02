"""Company tools: profile, fundamentals, statements, analyst estimates, insiders, holders, filings (P07).

Each wraps one capability of the data router (F4). Where a provider only
records a label or a count, the tool adds a derived record with the actual
numbers it returns so final-answer numbers can be checked (F3).
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import Field

from app.domain.compute.statements import margins, period_changes
from app.domain.data.base import Capability
from app.domain.data.schemas import CompanyProfile, FundamentalsSnapshot, InsiderTxn, StatementSet
from app.domain.tools.base import ToolContext, ToolOutput, ToolSpec
from app.domain.tools.common import SymbolArgs, derived_record, fetch, jsonable, to_symbol


class StatementArgs(SymbolArgs):
    period: Literal["annual", "quarterly"] = "annual"
    periods: int = Field(
        default=2,
        ge=1,
        le=5,
        description="How many periods, newest first (5 quarterly = same quarter last year)",
    )


PRICE_TARGET_KEYS = frozenset({"current", "low", "high", "mean", "median"})


def _fmt(v: float | None) -> str:
    if v is None:
        return ""
    return f"{v:,.4f}".rstrip("0").rstrip(".") if abs(v) < 100 else f"{v:,.0f}"


def statements_markdown(sets: list[StatementSet], currency: str) -> str:
    """One table per section: rows = line items, columns = periods (newest first)."""
    heads = [s.fiscal_period for s in sets]
    out = []
    for section in ("income", "balance", "cashflow"):
        items: list[str] = []
        for st in sets:
            items += [k for k in getattr(st, section) if k not in items]
        if not items:
            continue
        lines = [
            f"### {section} ({currency})",
            "| item | " + " | ".join(heads) + " |",
            "|---" * (len(heads) + 1) + "|",
        ]
        for item in items:
            cells = [_fmt(getattr(st, section).get(item)) for st in sets]
            lines.append(f"| {item} | " + " | ".join(cells) + " |")
        out.append("\n".join(lines))
    return "\n\n".join(out)


def _ratio_table(title: str, heads: list[str], rows: dict[str, list[float | None]]) -> str:
    kept = {k: v for k, v in rows.items() if any(x is not None for x in v)}
    if not kept:
        return ""
    lines = [f"### {title}", "| item | " + " | ".join(heads) + " |", "|---" * (len(heads) + 1) + "|"]
    for item, values in kept.items():
        cells = ["" if v is None else f"{v * 100:.2f}%" for v in values]
        lines.append(f"| {item} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


class ListArgs(SymbolArgs):
    limit: int = Field(default=20, ge=1, le=50)


class FilingArgs(SymbolArgs):
    item: str | None = Field(
        default=None, description="10-K/10-Q section to extract, e.g. '1A' (risk factors), '7' (MD&A)"
    )
    filing_url: str | None = Field(default=None, description="Filing URL from a previous read_filing list")


async def get_company_profile(args: SymbolArgs, ctx: ToolContext) -> ToolOutput:
    sym = to_symbol(args.symbol, args.market)
    result = await fetch(Capability.PROFILE, sym)
    profile: CompanyProfile = result.data
    value = profile.model_dump(mode="json", exclude={"description"})
    return ToolOutput(
        data=profile.model_dump(mode="json"),
        summary=f"{profile.name} · {profile.sector or '?'} / {profile.industry or '?'}",
        records=[
            *result.records,
            derived_record(result, value, tool="get_company_profile", unit=sym.currency),
        ],
        status=result.status,
    )


async def get_fundamentals(args: SymbolArgs, ctx: ToolContext) -> ToolOutput:
    sym = to_symbol(args.symbol, args.market)
    result = await fetch(Capability.FUNDAMENTALS, sym)
    snap: FundamentalsSnapshot = result.data
    present = {k: v for k, v in snap.metrics.items() if v is not None}
    return ToolOutput(
        data={"provider": result.provider, **snap.model_dump(mode="json")},
        summary=f"{sym.canonical} · {len(present)} metrics ({snap.period})",
        records=[
            *result.records,
            derived_record(result, snap.metrics, tool="get_fundamentals", as_of=snap.as_of),
        ],
        status=result.status,
    )


async def get_statements(args: StatementArgs, ctx: ToolContext) -> ToolOutput:
    sym = to_symbol(args.symbol, args.market)
    result = await fetch(Capability.STATEMENTS, sym, period_type=args.period, periods=args.periods)
    data = result.data
    sets: list[StatementSet] = data if isinstance(data, list) else [data]
    records = list(result.records)
    for st in sets:
        value = {
            "fiscal_period": st.fiscal_period,
            "income": st.income,
            "balance": st.balance,
            "cashflow": st.cashflow,
        }
        records.append(
            derived_record(result, value, tool="get_statements", unit=sym.currency, as_of=st.fiscal_date)
        )
    periods = ", ".join(s.fiscal_period for s in sets)
    header = f"{sym.canonical} {args.period} statements from {result.provider}, newest first ({sym.currency})"
    heads = [s.fiscal_period for s in sets]
    inputs = [r.id for r in records]
    marg = margins(heads, [s.income for s in sets], inputs=inputs)
    records += marg.records
    computed = [_ratio_table("margins (computed)", heads, marg.value)]
    if len(sets) > 1:
        changes = period_changes(heads, [{**s.income, **s.cashflow} for s in sets], inputs=inputs)
        records += changes.records
        change_heads = [f"{heads[i]} vs {heads[i + 1]}" for i in range(len(heads) - 1)]
        computed.append(_ratio_table("% change vs previous period (computed)", change_heads, changes.value))
    body = "\n\n".join([statements_markdown(sets, sym.currency), *[t for t in computed if t]])
    return ToolOutput(
        data=f"{header}\n\n{body}",
        summary=f"{sym.canonical} · {args.period} · {periods}",
        records=records,
        status=result.status,
    )


async def get_analyst_estimates(args: SymbolArgs, ctx: ToolContext) -> ToolOutput:
    sym = to_symbol(args.symbol, args.market)
    result = await fetch(Capability.ANALYST, sym)
    value = jsonable(result.data)
    kind = "price_targets" if isinstance(value, dict) and set(value) <= PRICE_TARGET_KEYS else "estimates"
    note = "analyst 12-month PRICE TARGETS per share (not EPS)" if kind == "price_targets" else None
    return ToolOutput(
        data={"provider": result.provider, "symbol": sym.canonical, kind: value, "note": note},
        summary=f"{sym.canonical} analyst estimates ({result.provider})",
        records=[
            *result.records,
            derived_record(result, value, tool="get_analyst_estimates", unit=sym.currency),
        ],
        status=result.status,
    )


async def get_insider_activity(args: ListArgs, ctx: ToolContext) -> ToolOutput:
    sym = to_symbol(args.symbol, args.market)
    result = await fetch(Capability.INSIDER, sym)
    items: list[InsiderTxn] = list(result.data or [])[: args.limit]
    rows = [t.model_dump(mode="json") for t in items]
    return ToolOutput(
        data={"provider": result.provider, "symbol": sym.canonical, "transactions": rows},
        summary=f"{sym.canonical} · {len(rows)} insider transactions",
        records=[*result.records, derived_record(result, rows, tool="get_insider_activity")],
        status=result.status,
    )


async def get_institutional_holdings(args: ListArgs, ctx: ToolContext) -> ToolOutput:
    sym = to_symbol(args.symbol, args.market)
    result = await fetch(Capability.INSTITUTIONAL, sym)
    rows = jsonable(list(result.data or [])[: args.limit])
    return ToolOutput(
        data={"provider": result.provider, "symbol": sym.canonical, "holders": rows},
        summary=f"{sym.canonical} · {len(rows)} institutional holders",
        records=[*result.records, derived_record(result, rows, tool="get_institutional_holdings")],
        status=result.status,
    )


async def read_filing(args: FilingArgs, ctx: ToolContext) -> ToolOutput:
    sym = to_symbol(args.symbol, args.market)
    params: dict[str, Any] = {}
    if args.filing_url:
        params["filing_url"] = args.filing_url
        params["item"] = args.item or "1A"
    result = await fetch(Capability.FILINGS, sym, **params)
    data = jsonable(result.data)
    if isinstance(data, dict) and "text" in data:
        summary = f"{sym.canonical} · item {data.get('item')} · {len(str(data['text']))} chars"
    else:
        count = len(data) if isinstance(data, list) else 0
        summary = f"{sym.canonical} · {count} filings as of {date.today()}"
    return ToolOutput(
        data={"provider": result.provider, "symbol": sym.canonical, "filings": data},
        summary=summary,
        records=result.records,
        status=result.status,
    )


COMPANY_TOOLS: list[ToolSpec] = [
    ToolSpec(
        name="get_company_profile",
        description=(
            "Company name, sector, industry, exchange, country, market cap and business description. "
            "Use to identify what a company does or its sector (e.g. to pick a WACC range)."
        ),
        args_model=SymbolArgs,
        limit_by="symbol",
        category="data",
        requires=frozenset({Capability.PROFILE}),
        fn=get_company_profile,
    ),
    ToolSpec(
        name="get_fundamentals",
        description=(
            "Snapshot of valuation and quality ratios (P/E, P/B, ROE, margins, debt/equity, growth, beta, "
            "EPS). Use for quick valuation/quality checks; use get_statements for line items."
        ),
        args_model=SymbolArgs,
        limit_by="symbol",
        category="data",
        requires=frozenset({Capability.FUNDAMENTALS}),
        fn=get_fundamentals,
    ),
    ToolSpec(
        name="get_statements",
        description=(
            "Income statement, balance sheet and cash-flow line items for the latest N periods (annual or "
            "quarterly), as tables with one column per period. Use for revenue, profit, margins, debt, cash, "
            "FCF, shares and period-over-period comparison. Call once per period type."
        ),
        args_model=StatementArgs,
        limit_by="symbol",
        category="data",
        requires=frozenset({Capability.STATEMENTS}),
        fn=get_statements,
    ),
    ToolSpec(
        name="get_analyst_estimates",
        description=(
            "Sell-side price targets / recommendations. Use for consensus; never invent consensus numbers."
        ),
        args_model=SymbolArgs,
        limit_by="symbol",
        category="data",
        requires=frozenset({Capability.ANALYST}),
        fn=get_analyst_estimates,
    ),
    ToolSpec(
        name="get_insider_activity",
        description=(
            "Recent insider buys/sells (mostly US). Use when insider sentiment matters to the question."
        ),
        args_model=ListArgs,
        limit_by="symbol",
        category="data",
        requires=frozenset({Capability.INSIDER}),
        fn=get_insider_activity,
    ),
    ToolSpec(
        name="get_institutional_holdings",
        description="Largest institutional holders (US). Use for ownership questions only.",
        args_model=ListArgs,
        limit_by="symbol",
        category="data",
        requires=frozenset({Capability.INSTITUTIONAL}),
        fn=get_institutional_holdings,
    ),
    ToolSpec(
        name="read_filing",
        description=(
            "SEC filings (US only): without filing_url lists recent filings; with filing_url and item "
            "returns that section's text (e.g. item 1A risk factors). Not available for SET stocks."
        ),
        args_model=FilingArgs,
        limit_by="symbol",
        category="data",
        timeout_s=45,
        requires=frozenset({Capability.FILINGS}),
        fn=read_filing,
    ),
]
