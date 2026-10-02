"""Deterministic ranking of symbols by a metric (F1): used by sector scans and company comparisons."""

from __future__ import annotations

from app.domain.compute.base import ComputeResult, compute_record

_VERSION = "v1"


def rank_by(
    values: dict[str, float | None],
    *,
    metric: str,
    descending: bool = True,
    inputs: list[str] | None = None,
) -> ComputeResult[list[dict[str, object]]]:
    """1 = best. Missing values are listed last with rank None (never silently dropped)."""
    present = [(k, v) for k, v in values.items() if v is not None]
    present.sort(key=lambda kv: kv[1], reverse=descending)
    rows: list[dict[str, object]] = [
        {"symbol": k, metric: v, "rank": i + 1} for i, (k, v) in enumerate(present)
    ]
    rows += [{"symbol": k, metric: None, "rank": None} for k, v in values.items() if v is None]
    record = compute_record(
        f"compute.ranking.rank_by@{_VERSION}",
        rows,
        params={"metric": metric, "descending": descending},
        inputs=inputs,
    )
    return ComputeResult(value=rows, records=[record])
