"""DCF / comps valuation math (F1).

Concept ported from ``../FinRobot/finrobot_equity/core/src/modules/
valuation_engine.py`` (EV/EBITDA with net-debt bridge) and
``sensitivity_analyzer.py`` (wacc x growth grid), rewritten as plain
functions operating on a bounded ``DCFAssumptions`` model instead of the
original's dataclass/engine-object shape, so an LLM-proposed assumption
that falls outside a sane range raises before it ever reaches arithmetic.
"""

from __future__ import annotations

import statistics
from decimal import Decimal

from pydantic import BaseModel, Field, model_validator

from app.domain.compute.base import ComputeResult, compute_record

_VERSION = "v1"


class DCFAssumptions(BaseModel):
    wacc: float = Field(ge=0.03, le=0.25)
    terminal_growth: float = Field(ge=-0.02, le=0.05)
    years: int = Field(ge=3, le=10)
    growth_rates: list[float]

    @model_validator(mode="after")
    def _check(self) -> DCFAssumptions:
        if len(self.growth_rates) != self.years:
            raise ValueError(f"growth_rates must have {self.years} entries, got {len(self.growth_rates)}")
        if not all(-0.5 <= g <= 1.0 for g in self.growth_rates):
            raise ValueError("each growth rate must be within [-0.5, 1.0]")
        if self.terminal_growth >= self.wacc:
            raise ValueError("terminal_growth must be strictly less than wacc")
        return self


def _project_fcf(fcf0: float, growth_rates: list[float]) -> list[float]:
    fcfs = []
    prev = fcf0
    for g in growth_rates:
        prev = prev * (1 + g)
        fcfs.append(prev)
    return fcfs


def dcf(
    fcf0: Decimal,
    assumptions: DCFAssumptions,
    net_debt: Decimal,
    shares: Decimal,
    *,
    inputs: list[str] | None = None,
) -> ComputeResult[dict[str, object]]:
    """DCF: projected FCF -> PV per year -> terminal value -> equity/share."""
    fcf0_f = float(fcf0)
    fcfs = _project_fcf(fcf0_f, assumptions.growth_rates)
    pv_by_year = [fcfs[t] / (1 + assumptions.wacc) ** (t + 1) for t in range(len(fcfs))]
    terminal_value = (
        fcfs[-1] * (1 + assumptions.terminal_growth) / (assumptions.wacc - assumptions.terminal_growth)
    )
    terminal_pv = terminal_value / (1 + assumptions.wacc) ** assumptions.years
    ev = sum(pv_by_year) + terminal_pv
    equity_value = ev - float(net_debt)
    per_share = equity_value / float(shares) if shares else None
    value = {
        "fcf_by_year": fcfs,
        "pv_by_year": pv_by_year,
        "terminal_value": terminal_value,
        "terminal_value_pv": terminal_pv,
        "enterprise_value": ev,
        "equity_value": equity_value,
        "per_share": per_share,
    }
    record = compute_record(
        f"compute.valuation.dcf@{_VERSION}",
        value,
        params={"assumptions": assumptions.model_dump(), "net_debt": str(net_debt), "shares": str(shares)},
        inputs=inputs,
    )
    return ComputeResult(value=value, records=[record])


def dcf_sensitivity(
    fcf0: Decimal,
    assumptions: DCFAssumptions,
    wacc_grid: list[float],
    g_grid: list[float],
    net_debt: Decimal,
    shares: Decimal,
    *,
    inputs: list[str] | None = None,
) -> ComputeResult[dict[str, object]]:
    """Per-share value across a (wacc x terminal growth) grid."""
    matrix: list[list[float | None]] = []
    for w in wacc_grid:
        row: list[float | None] = []
        for g in g_grid:
            if g >= w:
                row.append(None)
                continue
            try:
                variant = assumptions.model_copy(update={"wacc": w, "terminal_growth": g})
            except Exception:
                row.append(None)
                continue
            per_share = dcf(fcf0, variant, net_debt, shares).value["per_share"]
            row.append(per_share if isinstance(per_share, float) else None)
        matrix.append(row)
    value = {"wacc_grid": wacc_grid, "g_grid": g_grid, "per_share": matrix}
    record = compute_record(f"compute.valuation.dcf_sensitivity@{_VERSION}", value, inputs=inputs)
    return ComputeResult(value=value, records=[record])


def ev_ebitda_value(
    ebitda: Decimal, multiple: float, net_debt: Decimal, shares: Decimal, *, inputs: list[str] | None = None
) -> ComputeResult[dict[str, float]]:
    ev = float(ebitda) * multiple
    equity_value = ev - float(net_debt)
    per_share = equity_value / float(shares) if shares else 0.0
    value = {"enterprise_value": ev, "equity_value": equity_value, "per_share": per_share}
    record = compute_record(
        f"compute.valuation.ev_ebitda_value@{_VERSION}",
        value,
        params={
            "ebitda": str(ebitda),
            "multiple": multiple,
            "net_debt": str(net_debt),
            "shares": str(shares),
        },
        inputs=inputs,
    )
    return ComputeResult(value=value, records=[record])


def peer_multiple_stats(
    peers: list[float], *, inputs: list[str] | None = None
) -> ComputeResult[dict[str, float]]:
    if not peers:
        value = {"median": 0.0, "mean": 0.0, "q1": 0.0, "q3": 0.0}
    else:
        sorted_p = sorted(peers)
        quartiles = statistics.quantiles(sorted_p, n=4) if len(sorted_p) >= 2 else [sorted_p[0], sorted_p[0]]
        value = {
            "median": statistics.median(sorted_p),
            "mean": statistics.fmean(sorted_p),
            "q1": quartiles[0],
            "q3": quartiles[-1],
        }
    record = compute_record(f"compute.valuation.peer_multiple_stats@{_VERSION}", value, inputs=inputs)
    return ComputeResult(value=value, records=[record])


def wacc(
    rf: float,
    beta: float,
    erp: float,
    cost_of_debt: float,
    tax: float,
    e_weight: float,
    *,
    inputs: list[str] | None = None,
) -> ComputeResult[float]:
    """CAPM cost of equity blended with after-tax cost of debt."""
    cost_of_equity = rf + beta * erp
    value = e_weight * cost_of_equity + (1 - e_weight) * cost_of_debt * (1 - tax)
    record = compute_record(
        f"compute.valuation.wacc@{_VERSION}",
        value,
        params={
            "rf": rf,
            "beta": beta,
            "erp": erp,
            "cost_of_debt": cost_of_debt,
            "tax": tax,
            "e_weight": e_weight,
        },
        inputs=inputs,
    )
    return ComputeResult(value=value, records=[record])
