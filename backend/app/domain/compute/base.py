"""Shared result wrapper for every deterministic compute function (F1).

Every public function in `domain/compute/` returns a `ComputeResult[T]`
carrying the value plus a `ProvenanceRecord` whose `calculation` field is
`"compute.<module>.<func>@<version>"`, so a compute step's output can always
be traced back to exactly which formula produced it. Never return a bare
number from a compute function (contracts.md §2).
"""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel

from app.domain.models.provenance import ProvenanceRecord, new_record

T = TypeVar("T")


class ComputeResult[T](BaseModel):
    value: T
    records: list[ProvenanceRecord]


def compute_record(
    calculation: str,
    value: object,
    *,
    params: dict[str, object] | None = None,
    inputs: list[str] | None = None,
    unit: str | None = None,
    agent: str | None = None,
) -> ProvenanceRecord:
    return new_record(
        "compute",
        value,
        source="compute",
        calculation=calculation,
        params=params,
        inputs=inputs,
        unit=unit,
        agent=agent,
    )
