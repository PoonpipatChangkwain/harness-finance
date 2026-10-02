"""Period-over-period changes and margins from statement line items (F1).

The LLM must not compute growth or margins itself; ``get_statements``
returns these computed tables (with provenance) next to the raw items.
Line-item names differ by provider, so lookups are case/punctuation-insensitive.
"""

from __future__ import annotations

import re

from app.domain.compute.base import ComputeResult, compute_record

_VERSION = "v1"
_NORM = re.compile(r"[^a-z0-9]")

REVENUE = ("revenue", "Total Revenue", "Operating Revenue", "revenues")
GROSS = ("gross_profit", "Gross Profit")
OPERATING = ("operating_income", "Operating Income")
NET = ("net_income", "Net Income", "Net Income Common Stockholders")


def _get(items: dict[str, float | None], names: tuple[str, ...]) -> float | None:
    norm = {_NORM.sub("", k.lower()): v for k, v in items.items()}
    for name in names:
        v = norm.get(_NORM.sub("", name.lower()))
        if v is not None:
            return v
    return None


def period_changes(
    periods: list[str], items: list[dict[str, float | None]], *, inputs: list[str] | None = None
) -> ComputeResult[dict[str, list[float | None]]]:
    """For each line item: % change of period i vs period i+1 (periods newest first)."""
    keys: list[str] = []
    for row in items:
        keys += [k for k in row if k not in keys]
    out: dict[str, list[float | None]] = {}
    for key in keys:
        changes: list[float | None] = []
        for i in range(len(items) - 1):
            cur, prev = items[i].get(key), items[i + 1].get(key)
            changes.append(cur / prev - 1.0 if cur is not None and prev not in (None, 0) else None)
        out[key] = changes
    record = compute_record(
        f"compute.statements.period_changes@{_VERSION}", out, params={"periods": periods}, inputs=inputs
    )
    return ComputeResult(value=out, records=[record])


def margins(
    periods: list[str], incomes: list[dict[str, float | None]], *, inputs: list[str] | None = None
) -> ComputeResult[dict[str, list[float | None]]]:
    """Gross / operating / net margin per period (ratio 0-1), None when an item is missing."""
    out: dict[str, list[float | None]] = {"gross_margin": [], "operating_margin": [], "net_margin": []}
    for income in incomes:
        rev = _get(income, REVENUE)
        for name, keys in (("gross_margin", GROSS), ("operating_margin", OPERATING), ("net_margin", NET)):
            num = _get(income, keys)
            out[name].append(num / rev if num is not None and rev else None)
    record = compute_record(
        f"compute.statements.margins@{_VERSION}", out, params={"periods": periods}, inputs=inputs
    )
    return ComputeResult(value=out, records=[record])
