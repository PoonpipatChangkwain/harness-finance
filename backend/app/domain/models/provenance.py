"""Provenance record: the answer to "where did this number come from?" (F3).

See docs/contracts.md §2. Every `domain/data/*` and `domain/compute/*`
function returns a `Provenanced[T]` (or `(value, list[ProvenanceRecord])`)
instead of a bare number, so nothing numeric can leave the domain layer
without an attached source, calculation, and timestamp.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, Field
from ulid import ULID

T = TypeVar("T")

ProvenanceKind = Literal["data", "compute", "llm_assumption", "user_input"]


class ProvenanceRecord(BaseModel):
    id: str
    kind: ProvenanceKind
    value: Any
    unit: str | None = None
    source: str
    tool: str | None = None
    calculation: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    inputs: list[str] = Field(default_factory=list)
    agent: str | None = None
    run_id: str | None = None
    as_of: date | datetime | None = None
    fetched_at: datetime
    url: str | None = None


class Provenanced[T](BaseModel):
    value: T
    provenance_id: str


def new_record(
    kind: ProvenanceKind,
    value: Any,
    *,
    source: str,
    tool: str | None = None,
    calculation: str | None = None,
    params: dict[str, Any] | None = None,
    inputs: list[str] | None = None,
    as_of: date | datetime | None = None,
    unit: str | None = None,
    url: str | None = None,
    agent: str | None = None,
    run_id: str | None = None,
) -> ProvenanceRecord:
    return ProvenanceRecord(
        id=f"prov_{ULID()}",
        kind=kind,
        value=value,
        unit=unit,
        source=source,
        tool=tool,
        calculation=calculation,
        params=params or {},
        inputs=inputs or [],
        agent=agent,
        run_id=run_id,
        as_of=as_of,
        fetched_at=datetime.now(UTC),
        url=url,
    )
