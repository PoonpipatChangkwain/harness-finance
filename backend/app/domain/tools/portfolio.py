"""Internal compute tools of the portfolio advisor (P12, F1/F3).

Called by workflow nodes through the executor (validated args, scratchpad, every
number persisted as provenance) but ``internal=True`` keeps them away from any LLM:

- ``portfolio_analytics``: ``domain.portfolio.analytics.analyze`` on the book the
  workflow loaded — the ground truth the advisors diagnose.
- ``rebalance_plan``: target weights (the only thing the LLM proposes) → lot-rounded
  trades, cash after the trades, turnover and the post-trade weights, sector
  exposure and concentration. The LLM never supplies share counts or money.

``limit_by`` partitions the call budget by ``purpose``/``attempt`` so a policy retry
or an approval edit is never mistaken for a repeated query.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from app.domain.compute import portfolio as pc
from app.domain.data.base import Capability
from app.domain.models.portfolio import Portfolio
from app.domain.models.provenance import ProvenanceRecord, new_record
from app.domain.models.symbol import normalize_symbol
from app.domain.portfolio.analytics import analyze
from app.domain.portfolio.market import data_source, fetch_quote, fx_pair, lot_size, sector_of
from app.domain.tools.base import ToolContext, ToolOutput, ToolSpec


class PortfolioAnalyticsArgs(BaseModel):
    portfolio: dict[str, Any] = Field(description="Portfolio (contracts.md §5) as JSON")
    as_of: date | None = None
    purpose: str = "compute_analytics"


async def portfolio_analytics(args: PortfolioAnalyticsArgs, ctx: ToolContext) -> ToolOutput:
    portfolio = Portfolio.model_validate(args.portfolio)
    result = await analyze(portfolio, as_of=args.as_of)
    total = f"{result.total_value.value:,.2f} {result.base_currency}" if result.total_value else "no value"
    n_holdings, n_warnings = len(result.holdings), len(result.warnings)
    return ToolOutput(
        data=result.model_dump(mode="json"),
        summary=f"{portfolio.name}: {n_holdings} holdings · {total} · {n_warnings} warnings",
        records=result.records,
        status="partial" if any(w.code == "price_unavailable" for w in result.warnings) else "ok",
    )


class PlanHolding(BaseModel):
    symbol: str
    currency: str
    price: float | None = Field(default=None, description="Last price in the symbol's currency")
    market_value: float | None = Field(default=None, description="Base currency; None = not valued")
    sector: str = "Unknown"


class RebalancePlanArgs(BaseModel):
    base_currency: str
    holdings: list[PlanHolding]
    cash: float = Field(ge=0, description="Cash in the base currency")
    fx: dict[str, float] = Field(default_factory=dict, description="currency → base multiplier")
    targets: dict[str, float] = Field(description="symbol → target weight 0..1 (after normalization)")
    lot_size_th: int = Field(default=100, ge=1)
    attempt: str = "propose#1"
    input_provenance_ids: list[str] = Field(default_factory=list)
    assumption: dict[str, Any] | None = Field(
        default=None, description="Who proposed the targets ({'agent': ...}); recorded as llm_assumption"
    )


def _d(x: float) -> Decimal:
    return Decimal(str(x))


async def rebalance_plan(args: RebalancePlanArgs, ctx: ToolContext) -> ToolOutput:
    base = args.base_currency.upper()
    inputs = list(args.input_provenance_ids)
    records: list[ProvenanceRecord] = []
    notes: list[str] = []
    if args.assumption is not None:
        # F1: the target weights are an LLM (or human-edit) assumption, not data — say so
        agent = str(args.assumption.get("agent") or "pf_advisor")
        assumed = new_record(
            "llm_assumption" if agent != "user" else "user_input",
            args.targets,
            source="llm" if agent != "user" else "user",
            unit="ratio",
            agent=agent,
            params={"attempt": args.attempt},
        )
        records.append(assumed)
        inputs.append(assumed.id)
    fx = {k.upper(): _d(v) for k, v in args.fx.items()}
    fx[base] = Decimal(1)

    held = {h.symbol: h for h in args.holdings}
    prices_local: dict[str, tuple[Decimal, str]] = {}  # symbol → (price, currency), for applying a plan
    prices_base: dict[str, Decimal] = {}
    values: dict[str, Decimal] = {}
    sectors: dict[str, str] = {}
    lots: dict[str, int] = {}
    for h in args.holdings:
        sym = normalize_symbol(h.symbol)
        lots[h.symbol] = lot_size(sym, args.lot_size_th)
        sectors[h.symbol] = h.sector
        rate = fx.get(h.currency.upper())
        if h.price is None or rate is None or h.market_value is None:
            notes.append(f"{h.symbol}: not valued — left out of the plan")
            continue
        prices_base[h.symbol] = _d(h.price) * rate
        prices_local[h.symbol] = (_d(h.price), h.currency.upper())
        values[h.symbol] = _d(h.market_value)

    # symbols the plan adds: price them now (quote + FX) — unpriced ones are dropped, not guessed
    source = None
    unpriced: list[str] = []
    for symbol in args.targets:
        if symbol in held or symbol == "CASH":
            continue
        sym = normalize_symbol(symbol)
        source = source or data_source()
        quote, why = await fetch_quote(sym, source)
        rate = fx.get(sym.currency)
        if rate is None and quote is not None:
            fx_quote, _ = await fetch_quote(fx_pair(sym.currency, base), source)
            if fx_quote is not None:
                rate = fx_quote.price
                fx[sym.currency] = rate
                records += fx_quote.records
        if quote is None or rate is None:
            unpriced.append(symbol)
            notes.append(f"{symbol}: no price/FX ({why or 'fx'}) — dropped from the plan")
            continue
        records += quote.records
        prices_base[symbol] = quote.price * rate
        prices_local[symbol] = (quote.price, sym.currency)
        lots[symbol] = lot_size(sym, args.lot_size_th)
        sectors[symbol] = await sector_of(sym, source)

    total = sum(values.values(), Decimal(0)) + _d(args.cash)
    if total <= 0:
        no_value = {"ok": False, "notes": ["portfolio has no value to rebalance"]}
        return ToolOutput(data=no_value, summary="no value", status="unavailable")
    current_w = {s: v / total for s, v in values.items()}
    target_w = {s: _d(w) for s, w in args.targets.items() if s in prices_base}
    inputs += [r.id for r in records]
    trades = pc.rebalance_trades(current_w, target_w, total, prices_base, lots, inputs=inputs)
    records += trades.records
    legs: list[dict[str, object]] = list(trades.value["legs"])  # type: ignore[call-overload]
    fitted = pc.trim_buys_to_cash(legs, prices_base, lots, _d(args.cash), inputs=[trades.records[0].id])
    records += fitted.records
    legs = list(fitted.value["legs"])  # type: ignore[call-overload]
    if fitted.value["trimmed"]:
        notes.append(f"buys trimmed to fit cash: {fitted.value['trimmed']}")
    post = pc.post_trade(values, _d(args.cash), legs, inputs=[fitted.records[0].id])
    records += post.records
    weights_after: dict[str, Decimal] = post.value["weights"]  # type: ignore[assignment]
    conc = pc.concentration(weights_after, inputs=[post.records[0].id])
    records += conc.records
    meta = {s: {"sector": sectors.get(s, "Unknown")} for s in weights_after if s != "CASH"}
    sector_after = pc.exposure(weights_after, "sector", meta, inputs=[post.records[0].id])
    records += sector_after.records

    out_legs = []
    for leg in legs:
        s = str(leg["symbol"])
        out_legs.append(
            {
                "symbol": s,
                "action": leg["action"],
                "shares": int(str(leg["shares"])),
                "value": float(Decimal(str(leg["value"]))),
                "price_base": float(prices_base[s]),
                "price": float(prices_local[s][0]),
                "currency": prices_local[s][1],
                "weight_before": float(current_w.get(s, Decimal(0))),
                "weight_target": float(target_w.get(s, Decimal(0))),
                "weight_after": float(weights_after.get(s, Decimal(0))),
                "lot_size": lots.get(s, 1),
            }
        )
    buys = sum((Decimal(str(x["value"])) for x in out_legs if int(str(x["shares"])) > 0), Decimal(0))
    sells = -sum((Decimal(str(x["value"])) for x in out_legs if int(str(x["shares"])) < 0), Decimal(0))
    cash_after: Decimal = post.value["cash"]  # type: ignore[assignment]
    data = {
        "ok": True,
        "base_currency": base,
        "total_value": float(total),
        "cash_before": args.cash,
        "cash_after": float(cash_after),
        "cash_weight_before": float(_d(args.cash) / total),
        "cash_weight_after": float(weights_after.get("CASH", Decimal(0))),
        "buy_value": float(buys),
        "sell_value": float(sells),
        "cash_required": float(buys - sells),
        "turnover": float(post.value["turnover"]),  # type: ignore[arg-type]
        "legs": out_legs,
        "weights_after": {k: float(v) for k, v in weights_after.items()},
        "sector_after": {k: float(v) for k, v in sector_after.value.items()},
        "concentration_after": conc.value,
        "unpriced": unpriced,
        "notes": notes,
        "provenance": {
            "trades": trades.records[0].id,
            "fit": fitted.records[0].id,
            "post_trade": post.records[0].id,
            "concentration": conc.records[0].id,
            "sector": sector_after.records[0].id,
        },
    }
    n = sum(1 for x in out_legs if x["action"] != "hold")
    return ToolOutput(
        data=data,
        summary=f"{n} trades · turnover {data['turnover']:.1%} · cash after {data['cash_after']:,.0f} {base}",
        records=records,
        status="partial" if unpriced else "ok",
    )


PORTFOLIO_TOOLS: list[ToolSpec] = [
    ToolSpec(
        name="portfolio_analytics",
        description="(internal) analytics of the loaded portfolio for the advisor workflow",
        args_model=PortfolioAnalyticsArgs,
        category="portfolio",
        requires=frozenset({Capability.QUOTE, Capability.PRICES}),
        timeout_s=90,
        limit_by="purpose",
        internal=True,
        fn=portfolio_analytics,
    ),
    ToolSpec(
        name="rebalance_plan",
        description="(internal) lot-rounded trades, cash and post-trade metrics from target weights",
        args_model=RebalancePlanArgs,
        category="compute",
        timeout_s=60,
        limit_by="attempt",
        internal=True,
        fn=rebalance_plan,
    ),
]
