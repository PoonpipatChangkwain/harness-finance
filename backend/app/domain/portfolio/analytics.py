"""Portfolio analytics (P12 §12.2): every number from ``domain/compute``, every number provenanced.

``analyze(portfolio, as_of=, data=)``:

1. quotes of every symbol + FX into the base currency (``USDTHB=X`` …), fetched concurrently
2. 1-year daily closes of every symbol + the benchmark (``^SET.BK`` for THB, ``SPY`` for USD)
3. compute: market values, weights (incl. cash), unrealized and day P&L, concentration
   (HHI, top-1/3, effective N), exposure by sector / asset type / currency / market
4. risk on current weights: annualized vol, max drawdown, beta, historical VaR/CVaR 95%,
   correlation (≤ 30 largest), contribution to risk
5. declared-weights-only book: no values or P&L; the declared % are the weights (flagged)

A symbol whose price (or FX) cannot be fetched gets a warning and *no* weight —
the analytics never guesses one (§12.6). Numbers are ``Provenanced`` (value +
provenance id); the records themselves ride along in ``records`` (excluded from
serialization) for the harness to persist (contracts.md §2).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.domain.compute import portfolio as pc
from app.domain.compute import returns as rc
from app.domain.compute.base import compute_record
from app.domain.models.portfolio import Portfolio, Position, portfolio_hash
from app.domain.models.provenance import Provenanced, ProvenanceRecord
from app.domain.models.symbol import Symbol
from app.domain.portfolio.market import (
    History,
    MarketData,
    PriceQuote,
    benchmark_symbol,
    data_source,
    fetch_history,
    fetch_quote,
    fx_pair,
    sector_of,
)

PV = Provenanced[float]
MIN_RISK_OBSERVATIONS = 20
MAX_CORRELATION_SYMBOLS = 30
DECLARED_ONLY_MESSAGE = "พอร์ตกรอกเฉพาะ % น้ำหนัก: ไม่มีมูลค่าและกำไร/ขาดทุน ใช้ % ที่กรอกคำนวณความเสี่ยง"
EXPOSURE_KEYS: tuple[Literal["sector", "asset_type", "currency", "market"], ...] = (
    "sector",
    "asset_type",
    "currency",
    "market",
)


class AnalyticsWarning(BaseModel):
    code: str
    message: str
    symbol: str | None = None


class HoldingAnalytics(BaseModel):
    symbol: str
    raw: str
    display: str
    asset_type: str
    market: str
    currency: str
    sector: str = "Unknown"
    quantity: float
    avg_cost: float | None = None
    declared_weight: float | None = None
    target_weight: float | None = None
    note: str | None = None
    status: Literal["ok", "no_price", "no_fx", "declared"] = "ok"
    price: PV | None = None
    prev_close: float | None = None
    fx_rate: PV | None = None
    market_value_local: PV | None = None
    market_value: PV | None = None  # base currency
    cost_basis: PV | None = None  # base currency
    weight: PV | None = None
    unrealized_pnl: PV | None = None  # base currency
    unrealized_pnl_pct: PV | None = None
    day_pnl: PV | None = None  # base currency
    day_change_pct: PV | None = None
    risk_contribution: PV | None = None


class Concentration(BaseModel):
    hhi: float
    top1: float
    top3: float
    effective_n: float
    provenance_id: str


class RiskMetrics(BaseModel):
    benchmark: str | None
    observations: int
    start: date
    end: date
    volatility: PV
    max_drawdown: PV
    beta: PV | None
    var_95: PV
    cvar_95: PV
    correlation: dict[str, dict[str, float]]
    correlation_provenance_id: str
    contribution: dict[str, float]
    contribution_provenance_id: str
    method: str = "daily simple returns in each symbol's own currency, current weights held constant"


class PortfolioAnalytics(BaseModel):
    portfolio_id: str
    name: str
    base_currency: str
    portfolio_hash: str
    computed_at: datetime
    as_of: date | None
    weights_declared_only: bool
    total_value: PV | None = None
    cash: PV | None = None
    invested_value: PV | None = None
    cost_basis: PV | None = None
    unrealized_pnl: PV | None = None
    unrealized_pnl_pct: PV | None = None
    day_pnl: PV | None = None
    day_pnl_pct: PV | None = None
    positions_count: int
    holdings: list[HoldingAnalytics]
    weights: dict[str, float] = Field(default_factory=dict)
    weights_provenance_id: str | None = None
    concentration: Concentration | None = None
    exposure: dict[str, dict[str, float]] = Field(default_factory=dict)
    exposure_provenance: dict[str, str] = Field(default_factory=dict)
    risk: RiskMetrics | None = None
    fx: dict[str, PV] = Field(default_factory=dict)
    warnings: list[AnalyticsWarning] = Field(default_factory=list)
    records: list[ProvenanceRecord] = Field(default_factory=list, exclude=True)

    def holding(self, symbol: str) -> HoldingAnalytics | None:
        return next((h for h in self.holdings if h.symbol == symbol), None)


def _pv(value: Decimal | float | None, record: ProvenanceRecord | str | None) -> PV | None:
    if value is None or record is None:
        return None
    pid = record if isinstance(record, str) else record.id
    return PV(value=float(value), provenance_id=pid)


def _holding_base(pos: Position, sector: str) -> HoldingAnalytics:
    sym = pos.symbol
    return HoldingAnalytics(
        symbol=sym.canonical,
        raw=sym.raw,
        display=sym.display,
        asset_type=sym.asset_type.value,
        market=sym.market.value,
        currency=sym.currency,
        sector=sector,
        quantity=float(pos.quantity),
        avg_cost=None if pos.avg_cost is None else float(pos.avg_cost),
        declared_weight=None if pos.declared_weight is None else float(pos.declared_weight),
        target_weight=None if pos.target_weight is None else float(pos.target_weight),
        note=pos.note,
    )


class _Ctx:
    def __init__(self, portfolio: Portfolio) -> None:
        self.records: list[ProvenanceRecord] = []
        self.warnings: list[AnalyticsWarning] = []
        self.user_inputs = [portfolio.provenance_id] if portfolio.provenance_id else []

    def warn(self, code: str, message: str, symbol: str | None = None) -> None:
        self.warnings.append(AnalyticsWarning(code=code, message=message, symbol=symbol))

    def keep(self, records: list[ProvenanceRecord]) -> list[str]:
        self.records.extend(records)
        return [r.id for r in records]


async def analyze(
    portfolio: Portfolio,
    *,
    as_of: date | None = None,
    data: MarketData | None = None,
    history_range: str = "1y",
) -> PortfolioAnalytics:
    source = data or data_source()
    base = portfolio.base_currency.upper()
    ctx = _Ctx(portfolio)
    positions = portfolio.positions
    declared_only = pc.declared_weights_only(positions)
    symbols = {p.symbol.canonical: p.symbol for p in positions}

    # 1-2. fetch everything concurrently (quotes + FX are skipped for a declared-only book)
    bench = benchmark_symbol(base)
    ccys = sorted({s.currency for s in symbols.values()} - {base})
    fx_pairs = {c: fx_pair(c, base) for c in ccys}
    quote_syms = {} if declared_only else {**symbols, **{p.canonical: p for p in fx_pairs.values()}}
    hist_syms = dict(symbols)
    if bench is not None and bench.canonical not in hist_syms:
        hist_syms[bench.canonical] = bench

    quote_keys, hist_keys, sector_keys = list(quote_syms), list(hist_syms), list(symbols)
    gathered = await asyncio.gather(
        asyncio.gather(*(fetch_quote(quote_syms[k], source) for k in quote_keys)),
        asyncio.gather(
            *(fetch_history(hist_syms[k], source, range_=history_range, as_of=as_of) for k in hist_keys)
        ),
        asyncio.gather(*(sector_of(symbols[k], source) for k in sector_keys)),
    )
    quotes: dict[str, PriceQuote] = {}
    for key, (q, why) in zip(quote_keys, gathered[0], strict=True):
        if q is None:
            is_fx = key.endswith("=X") and key not in symbols
            code = "fx_unavailable" if is_fx else "price_unavailable"
            ctx.warn(code, f"ดึงราคา {key} ไม่ได้: {why}", key)
        else:
            quotes[key] = q
            ctx.keep(q.records)
    histories: dict[str, History] = {}
    for key, (h, why) in zip(hist_keys, gathered[1], strict=True):
        if h is None:
            code = "benchmark_unavailable" if key not in symbols else "history_unavailable"
            ctx.warn(code, f"ดึงราคาย้อนหลัง {key} ไม่ได้: {why}", key)
        else:
            histories[key] = h
            ctx.keep(h.records)
    sectors = dict(zip(sector_keys, gathered[2], strict=True))
    holdings = {
        p.symbol.canonical: _holding_base(p, sectors.get(p.symbol.canonical, "Unknown")) for p in positions
    }

    # FX: base → 1, others from the pair quote
    fx: dict[str, Decimal] = {base: Decimal(1)}
    fx_out: dict[str, PV] = {}
    for ccy, pair in fx_pairs.items():
        q = quotes.get(pair.canonical)
        if q is not None:
            fx[ccy] = q.price
            pv = _pv(q.price, q.record)
            if pv is not None:
                fx_out[ccy] = pv

    out = PortfolioAnalytics(
        portfolio_id=portfolio.id,
        name=portfolio.name,
        base_currency=base,
        portfolio_hash=portfolio_hash(portfolio),
        computed_at=datetime.now(UTC),
        as_of=as_of or max((q.as_of for q in quotes.values() if q.as_of), default=None),
        weights_declared_only=declared_only,
        positions_count=len(positions),
        holdings=[],
        fx=fx_out,
    )

    weights_map: dict[str, Decimal]
    wid: str | None
    if declared_only:
        weights_map, wid = _declared_weights(positions, ctx)
        for c, w in weights_map.items():
            if c in holdings:
                holdings[c].status = "declared"
                holdings[c].weight = _pv(w, wid)
        ctx.warn("weights_declared_only", DECLARED_ONLY_MESSAGE)
    else:
        weights_map, wid = _valuation(portfolio, positions, quotes, fx, holdings, out, ctx)
    out.weights = {k: float(v) for k, v in weights_map.items()}
    out.weights_provenance_id = wid

    # 3. concentration + exposure
    if weights_map:
        conc = pc.concentration(weights_map, inputs=[wid] if wid else None)
        cid = ctx.keep(conc.records)[0]
        out.concentration = Concentration(**conc.value, provenance_id=cid)
        meta = {
            c: {"sector": h.sector, "asset_type": h.asset_type, "currency": h.currency, "market": h.market}
            for c, h in holdings.items()
        }
        for by in EXPOSURE_KEYS:
            exp = pc.exposure(weights_map, by, meta, inputs=[wid] if wid else None)
            out.exposure[by] = {k: float(v) for k, v in exp.value.items()}
            out.exposure_provenance[by] = ctx.keep(exp.records)[0]

    # 4. risk
    out.risk = _risk(weights_map, histories, bench, holdings, ctx, wid)
    out.holdings = list(holdings.values())
    out.warnings = ctx.warnings
    out.records = ctx.records
    return out


def _declared_weights(positions: list[Position], ctx: _Ctx) -> tuple[dict[str, Decimal], str]:
    weights_map = {p.symbol.canonical: p.declared_weight or Decimal(0) for p in positions}
    total = sum(weights_map.values(), Decimal(0))
    if total > 1:
        ctx.warn("declared_weights_over_100", f"% น้ำหนักที่กรอกรวม {float(total):.2%} เกิน 100%")
    weights_map["CASH"] = max(Decimal(0), 1 - total)
    record = compute_record(
        "compute.portfolio.declared_weights@v1",
        {k: str(v) for k, v in weights_map.items()},
        params={"declared_total": str(total)},
        inputs=ctx.user_inputs,
    )
    return weights_map, ctx.keep([record])[0]


def _valuation(
    portfolio: Portfolio,
    positions: list[Position],
    quotes: dict[str, PriceQuote],
    fx: dict[str, Decimal],
    holdings: dict[str, HoldingAnalytics],
    out: PortfolioAnalytics,
    ctx: _Ctx,
) -> tuple[dict[str, Decimal], str | None]:
    base = out.base_currency
    priced: list[Position] = []
    prices: dict[str, Decimal] = {}
    for pos in positions:
        c = pos.symbol.canonical
        h = holdings[c]
        if pos.quantity == 0:
            h.status = "declared"
            if pos.declared_weight is not None:
                ctx.warn("declared_weight_ignored", f"{c}: ไม่มีจำนวนหน่วย ใช้ % ที่กรอกไม่ได้ในพอร์ตที่มีมูลค่า", c)
            continue
        q = quotes.get(c)
        if q is None:
            h.status = "no_price"
            continue
        if pos.symbol.currency not in fx:
            h.status = "no_fx"
            ctx.warn("fx_missing_for_symbol", f"{c}: ไม่มีอัตราแลกเปลี่ยน {pos.symbol.currency}→{base}", c)
            continue
        priced.append(pos)
        prices[c] = q.price
        h.price = _pv(q.price, q.record)
        h.prev_close = None if q.prev_close is None else float(q.prev_close)
        fx_pv = out.fx.get(pos.symbol.currency)
        h.fx_rate = fx_pv or (
            PV(value=1.0, provenance_id=q.record.id) if pos.symbol.currency == base else None
        )

    price_ids = [quotes[c].record.id for c in prices] + [p.provenance_id for p in out.fx.values()]
    inputs = [*ctx.user_inputs, *price_ids]
    mv = pc.market_values(priced, prices, fx, base, inputs=inputs)
    mv_id = ctx.keep(mv.records)[0]
    cash = portfolio.cash
    if cash is None:
        ctx.warn("cash_not_provided", "ไม่ได้ระบุเงินสด: น้ำหนักคิดจากหุ้นที่มีราคาเท่านั้น")
    wr = pc.weights(mv.value, cash, inputs=[mv_id])
    wid = ctx.keep(wr.records)[0]
    if len(priced) < len([p for p in positions if p.quantity != 0]):
        ctx.warn("weights_partial", "มีบางตัวดึงราคาไม่ได้: น้ำหนักคิดเฉพาะตัวที่มีราคา (ไม่เดาน้ำหนักตัวที่ขาด)")

    rows: list[dict[str, Decimal | None]] = []
    for pos in priced:
        c = pos.symbol.canonical
        h = holdings[c]
        rate = fx[pos.symbol.currency]
        value = mv.value[c]
        h.market_value = _pv(value, mv_id)
        h.market_value_local = _pv(pos.quantity * prices[c], mv_id)
        h.weight = _pv(wr.value.get(c), wid)
        cost = pos.quantity * pos.avg_cost * rate if pos.avg_cost is not None else None
        q = quotes[c]
        prev_value = pos.quantity * q.prev_close * rate if q.prev_close is not None else None
        rows.append({"value": value, "cost": cost, "prev_value": prev_value})
        pnl = pc.unrealized_pnl(pos, prices[c], inputs=[*ctx.user_inputs, q.record.id])
        if pnl.value is not None:
            pid = ctx.keep(pnl.records)[0]
            abs_local, pct = pnl.value
            h.unrealized_pnl = _pv(abs_local * rate, pid)
            h.unrealized_pnl_pct = _pv(pct, pid)
            h.cost_basis = _pv(cost, pid)
        if prev_value is not None and q.prev_close:
            day = compute_record(
                "compute.portfolio.day_change@v1",
                {"abs": str(value - prev_value), "pct": str(prices[c] / q.prev_close - 1)},
                params={"price": str(prices[c]), "prev_close": str(q.prev_close), "fx": str(rate)},
                inputs=[q.record.id, mv_id],
            )
            did = ctx.keep([day])[0]
            h.day_pnl = _pv(value - prev_value, did)
            h.day_change_pct = _pv(prices[c] / q.prev_close - 1, did)

    summary = pc.pnl_summary(rows, inputs=[mv_id])
    sid = ctx.keep(summary.records)[0]
    s = summary.value
    invested = s["invested_value"] or Decimal(0)
    total = invested + (cash or Decimal(0))
    total_rec = compute_record(
        "compute.portfolio.total_value@v1",
        {"total": str(total), "invested": str(invested), "cash": None if cash is None else str(cash)},
        params={"base_ccy": base},
        inputs=[mv_id, *ctx.user_inputs],
    )
    tid = ctx.keep([total_rec])[0]
    out.total_value = _pv(total, tid)
    out.invested_value = _pv(invested, sid)
    out.cash = _pv(cash, ctx.user_inputs[0] if ctx.user_inputs else tid)
    out.cost_basis = _pv(s["cost_basis"], sid)
    out.unrealized_pnl = _pv(s["unrealized_pnl"], sid)
    out.unrealized_pnl_pct = _pv(s["unrealized_pnl_pct"], sid)
    out.day_pnl = _pv(s["day_pnl"], sid)
    out.day_pnl_pct = _pv(s["day_pnl_pct"], sid)
    return wr.value, wid


def _aligned(histories: dict[str, History], symbols: list[str]) -> list[date]:
    common: set[date] | None = None
    for s in symbols:
        dates = set(histories[s].closes)
        common = dates if common is None else common & dates
    return sorted(common or set())


def _returns_on(closes: dict[date, float], dates: list[date]) -> list[float]:
    return [closes[dates[i]] / closes[dates[i - 1]] - 1.0 for i in range(1, len(dates))]


def _risk(
    weights_map: dict[str, Decimal],
    histories: dict[str, History],
    bench: Symbol | None,
    holdings: dict[str, HoldingAnalytics],
    ctx: _Ctx,
    wid: str | None,
) -> RiskMetrics | None:
    symbols = [
        s for s, w in weights_map.items() if s != "CASH" and w > 0 and s in histories and s in holdings
    ]
    missing = [s for s, w in weights_map.items() if s != "CASH" and w > 0 and s not in histories]
    if missing:
        ctx.warn("risk_partial", f"ความเสี่ยงไม่รวม {', '.join(missing)} (ไม่มีราคาย้อนหลัง)")
    if not symbols:
        return None
    dates = _aligned(histories, symbols)
    if len(dates) < MIN_RISK_OBSERVATIONS + 1:
        ctx.warn("risk_insufficient_history", f"ราคาย้อนหลังที่ตรงกันมีแค่ {len(dates)} วัน: ไม่คำนวณความเสี่ยง")
        return None
    inputs = [histories[s].record.id for s in symbols] + ([wid] if wid else [])
    rets = {s: _returns_on(histories[s].closes, dates) for s in symbols}
    rw = {s: float(weights_map[s]) for s in symbols}
    port = rc.portfolio_returns(rets, rw, inputs=inputs)
    pid = ctx.keep(port.records)[0]
    vol = rc.annualized_volatility(port.value, inputs=[pid])
    nav = [1.0]
    for r in port.value:
        nav.append(nav[-1] * (1.0 + r))
    mdd = rc.max_drawdown(nav, [d.isoformat() for d in dates], inputs=[pid])
    var = rc.historical_var(port.value, 0.95, inputs=[pid])
    cvar = rc.cvar(port.value, 0.95, inputs=[pid])
    beta_pv: PV | None = None
    if bench is not None and bench.canonical in histories:
        bh = histories[bench.canonical]
        bdates = [d for d in dates if d in bh.closes]
        if len(bdates) > MIN_RISK_OBSERVATIONS:
            nav_by = dict(zip(dates, nav, strict=True))
            port_b = [nav_by[bdates[i]] / nav_by[bdates[i - 1]] - 1.0 for i in range(1, len(bdates))]
            b = rc.beta(port_b, _returns_on(bh.closes, bdates), inputs=[pid, bh.record.id])
            beta_pv = _pv(b.value, ctx.keep(b.records)[0])
        else:  # e.g. Yahoo serves only a few bars of ^SET.BK: say so, never a silent gap
            ctx.warn(
                "beta_unavailable",
                f"คำนวณ beta ไม่ได้: ราคาย้อนหลังของ {bench.canonical} ตรงกับพอร์ตแค่ {len(bdates)} วัน",
                bench.canonical,
            )
    top = sorted(symbols, key=lambda s: rw[s], reverse=True)[:MAX_CORRELATION_SYMBOLS]
    corr = rc.correlation_matrix({s: rets[s] for s in top}, inputs=inputs)
    contrib = rc.risk_contribution(rets, rw, inputs=inputs)
    contrib_id = ctx.keep(contrib.records)[0]
    for s, v in contrib.value.items():
        holdings[s].risk_contribution = _pv(v, contrib_id)
    return RiskMetrics(
        benchmark=bench.canonical if bench is not None else None,
        observations=len(port.value),
        start=dates[0],
        end=dates[-1],
        volatility=PV(value=vol.value, provenance_id=ctx.keep(vol.records)[0]),
        max_drawdown=PV(value=float(mdd.value["mdd"]), provenance_id=ctx.keep(mdd.records)[0]),  # type: ignore[arg-type]
        beta=beta_pv,
        var_95=PV(value=var.value, provenance_id=ctx.keep(var.records)[0]),
        cvar_95=PV(value=cvar.value, provenance_id=ctx.keep(cvar.records)[0]),
        correlation=corr.value,
        correlation_provenance_id=ctx.keep(corr.records)[0],
        contribution=contrib.value,
        contribution_provenance_id=contrib_id,
    )


def summary_text(a: PortfolioAnalytics, *, max_holdings: int = 25) -> str:
    """Compact plain-text snapshot for an LLM context (research toggle, advisor, weekly report)."""

    def f(pv: PV | None, pct: bool = False) -> str:
        if pv is None:
            return "n/a"
        return f"{pv.value:.2%} [^{pv.provenance_id}]" if pct else f"{pv.value:,.2f} [^{pv.provenance_id}]"

    lines = [
        f"Portfolio {a.name} ({a.portfolio_id}), base {a.base_currency}, as of {a.as_of}:",
        f"- total value {f(a.total_value)}, cash {f(a.cash)}, unrealized P&L {f(a.unrealized_pnl)} "
        f"({f(a.unrealized_pnl_pct, True)}), today {f(a.day_pnl)}",
    ]
    if a.weights_declared_only:
        lines.append("- weights are user-declared percentages only (no quantities, no P&L)")
    if a.concentration:
        c = a.concentration
        lines.append(
            f"- concentration HHI {c.hhi:.3f}, top1 {c.top1:.2%}, top3 {c.top3:.2%}, "
            f"effective N {c.effective_n:.1f} [^{c.provenance_id}]"
        )
    if a.risk:
        r = a.risk
        lines.append(
            f"- risk ({r.observations} days): vol {f(r.volatility, True)}, "
            f"max drawdown {f(r.max_drawdown, True)}, beta vs {r.benchmark} {f(r.beta)}, "
            f"VaR95 {f(r.var_95, True)}, CVaR95 {f(r.cvar_95, True)}"
        )
    sectors = a.exposure.get("sector") or {}
    if sectors:
        sid = a.exposure_provenance.get("sector", "")
        top = ", ".join(f"{k} {v:.1%}" for k, v in sorted(sectors.items(), key=lambda kv: -kv[1])[:8])
        lines.append(f"- sector exposure [^{sid}]: {top}")
    lines.append("Holdings (symbol | weight | value | P&L % | risk contribution):")
    ranked = sorted(a.holdings, key=lambda h: -(h.weight.value if h.weight else 0.0))
    for h in ranked[:max_holdings]:
        lines.append(
            f"- {h.symbol} ({h.sector}) | {f(h.weight, True)} | {f(h.market_value)} | "
            f"{f(h.unrealized_pnl_pct, True)} | {f(h.risk_contribution, True)}"
            + (f" | status {h.status}" if h.status != "ok" else "")
        )
    if len(ranked) > max_holdings:
        lines.append(f"- … {len(ranked) - max_holdings} more")
    if a.warnings:
        lines.append("Warnings: " + "; ".join(w.message for w in a.warnings))
    return "\n".join(lines)


def jsonable_analytics(a: PortfolioAnalytics) -> dict[str, Any]:
    return a.model_dump(mode="json")
