"""Cross-portfolio view (P12 §12.7, the Dime! "my assets" screen): by portfolio and by asset.

Pure compute over already-computed ``PortfolioAnalytics``: values are converted from each
book's base currency into one display currency with a provenanced FX rate, the same symbol
held in several books is merged, and a market filter (TH / US) narrows the asset view and
its totals. Holdings without a value are listed but never counted (no guessing).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from app.domain.compute.base import compute_record
from app.domain.models.provenance import ProvenanceRecord
from app.domain.portfolio.analytics import PortfolioAnalytics

MarketFilter = Literal["ALL", "TH", "US"]
_TOTALS = (
    "total_value", "cash", "cost_basis", "unrealized_pnl", "unrealized_pnl_pct", "day_pnl", "day_pnl_pct",
)  # fmt: skip


def _d(x: float | None) -> Decimal | None:
    return None if x is None else Decimal(str(x))


def summarize(
    books: list[tuple[dict[str, Any], PortfolioAnalytics]],
    *,
    currency: str,
    fx: dict[str, tuple[Decimal, str | None]],
    market: MarketFilter = "ALL",
) -> tuple[dict[str, Any], list[ProvenanceRecord]]:
    """``books`` = (portfolio meta, analytics); ``fx`` = base currency → (rate into ``currency``, pid)."""
    zero = Decimal(0)
    warnings: list[str] = []
    inputs: list[str] = []
    assets: dict[str, dict[str, Any]] = {}
    portfolios: list[dict[str, Any]] = []
    totals = {"value": zero, "cost": zero, "pnl": zero, "day": zero, "prev": zero, "cash": zero}
    costed_any = dated_any = False
    for meta, a in books:
        rate_pid = fx.get(a.base_currency)
        if rate_pid is None:
            warnings.append(f"{a.name}: no {a.base_currency}→{currency} rate — left out of the totals")
            portfolios.append({**meta, "value": None, "unrealized_pnl": None, "unrealized_pnl_pct": None})
            continue
        rate, pid = rate_pid
        if pid:
            inputs.append(pid)
        inputs += [p for p in (a.weights_provenance_id,) if p]
        pf_value = pf_cost = pf_pnl = zero
        pf_costed = False
        for h in a.holdings:
            if market != "ALL" and h.market != market:
                continue
            mv = _d(h.market_value.value) if h.market_value else None
            if mv is None:
                continue
            value = mv * rate
            cost = _d(h.cost_basis.value) * rate if h.cost_basis else None  # type: ignore[operator]
            day = _d(h.day_pnl.value) * rate if h.day_pnl else None  # type: ignore[operator]
            asset = assets.setdefault(
                h.symbol,
                {"symbol": h.symbol, "display": h.display, "market": h.market, "asset_type": h.asset_type,
                 "currency": h.currency, "sector": h.sector, "quantity": zero, "value": zero, "cost": zero,
                 "costed": False, "day": zero, "prev": zero, "portfolios": []},
            )  # fmt: skip
            asset["quantity"] += Decimal(str(h.quantity))
            asset["value"] += value
            asset["portfolios"].append(a.portfolio_id)
            pf_value += value
            totals["value"] += value
            if cost is not None:
                asset["cost"] += cost
                asset["costed"] = True
                pf_cost += cost
                pf_pnl += value - cost
                pf_costed = costed_any = True
                totals["cost"] += cost
                totals["pnl"] += value - cost
            if day is not None:
                asset["day"] += day
                asset["prev"] += value - day
                totals["day"] += day
                totals["prev"] += value - day
                dated_any = True
        cash = _d(a.cash.value) * rate if a.cash and market == "ALL" else zero  # type: ignore[operator]
        totals["cash"] += cash
        totals["value"] += cash
        portfolios.append(
            {
                **meta,
                "value": float(pf_value + cash),
                "cash": float(cash),
                "cost_basis": float(pf_cost) if pf_costed else None,
                "unrealized_pnl": float(pf_pnl) if pf_costed else None,
                "unrealized_pnl_pct": float(pf_pnl / pf_cost) if pf_costed and pf_cost else None,
                "holdings": len([h for h in a.holdings if market == "ALL" or h.market == market]),
            }
        )
    grand = totals["value"]
    for p in portfolios:
        p["weight"] = (
            float(Decimal(str(p["value"])) / grand) if p.get("value") is not None and grand else None
        )
    asset_rows = []
    for row in assets.values():
        costed = row.pop("costed")
        cost, value, day, prev = row.pop("cost"), row["value"], row.pop("day"), row.pop("prev")
        asset_rows.append(
            {
                **row,
                "quantity": float(row["quantity"]),
                "value": float(value),
                "weight": float(value / grand) if grand else None,
                "cost_basis": float(cost) if costed else None,
                "unrealized_pnl": float(value - cost) if costed else None,
                "unrealized_pnl_pct": float((value - cost) / cost) if costed and cost else None,
                "day_change_pct": float(day / prev) if prev else None,
            }
        )
    asset_rows.sort(key=lambda r: -r["value"])
    out = {
        "currency": currency,
        "market": market,
        "total_value": float(grand),
        "cash": float(totals["cash"]),
        "cost_basis": float(totals["cost"]) if costed_any else None,
        "unrealized_pnl": float(totals["pnl"]) if costed_any else None,
        "unrealized_pnl_pct": float(totals["pnl"] / totals["cost"])
        if costed_any and totals["cost"]
        else None,
        "day_pnl": float(totals["day"]) if dated_any else None,
        "day_pnl_pct": float(totals["day"] / totals["prev"]) if dated_any and totals["prev"] else None,
        "portfolios": portfolios,
        "assets": asset_rows,
        "warnings": warnings,
    }
    record = compute_record(
        "compute.portfolio.summary@v1",
        {k: out[k] for k in _TOTALS},
        params={"currency": currency, "market": market, "books": len(books)},
        inputs=list(dict.fromkeys(inputs)),
        unit=currency,
    )
    out["provenance_id"] = record.id
    return out, [record]
