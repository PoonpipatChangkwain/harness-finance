"""Turn what the user typed into ``Position``\\ s (P12 §12.5, §12.7): validation, CSV, value entry.

Three ways to enter a holding, one per row:

- ``quantity`` (+ optional ``avg_cost``) — the classic mode
- ``value`` + ``pnl_pct`` (Dime!-style: "NVDA 549.96 USD, +14.52%") — the latest price turns
  it into units and a cost basis via ``compute.portfolio.units_from_value`` so value and P&L
  follow the market afterwards (provenance: the user's numbers + the compute step)
- ``weight`` only — a declared % when the user does not know the share count

A book is either all-declared-% or all-quantities; mixing them is refused instead of
guessing what the declared rows are worth. Errors are returned per row (index, field,
message) so the API can answer 422 with the exact rows to fix.
"""

from __future__ import annotations

import csv
import io
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, Field

from app.domain.compute.portfolio import units_from_value
from app.domain.models.coerce import coerce_optional_float
from app.domain.models.portfolio import Position
from app.domain.models.provenance import ProvenanceRecord, new_record
from app.domain.models.symbol import Market, Symbol, normalize_symbol, safe_path_component
from app.domain.portfolio.market import MarketData, data_source, fetch_quote

MAX_POSITIONS = 200
CSV_COLUMNS = ("symbol", "quantity", "avg_cost", "weight", "note", "value", "pnl_pct", "target_weight")


class PositionInput(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    market: Market | None = None
    quantity: Decimal | None = None
    avg_cost: Decimal | None = Field(default=None, ge=0)
    weight: Decimal | None = Field(default=None, description="Declared weight 0..1 (weight-only mode)")
    target_weight: Decimal | None = Field(default=None, ge=0, le=1)
    value: Decimal | None = Field(default=None, description="Market value in the symbol's currency")
    pnl_pct: Decimal | None = Field(
        default=None, description="Unrealized gain as a fraction (0.1452 = +14.52%)"
    )
    note: str | None = Field(default=None, max_length=500)


class EntryError(BaseModel):
    index: int | None  # row index (0-based); None = the whole book
    symbol: str | None = None
    field: str | None = None
    message: str


class ResolvedEntry(BaseModel):
    positions: list[Position]
    errors: list[EntryError]
    records: list[ProvenanceRecord] = Field(default_factory=list)
    converted: list[dict[str, Any]] = Field(default_factory=list)  # value-mode rows → units/cost


def _symbol(row: PositionInput) -> Symbol:
    sym = normalize_symbol(row.symbol, row.market)
    safe_path_component(sym.canonical)
    return sym


async def resolve_positions(rows: list[PositionInput], *, data: MarketData | None = None) -> ResolvedEntry:
    errors: list[EntryError] = []
    positions: list[Position] = []
    records: list[ProvenanceRecord] = []
    converted: list[dict[str, Any]] = []
    if len(rows) > MAX_POSITIONS:
        return ResolvedEntry(
            positions=[], errors=[EntryError(index=None, message=f"at most {MAX_POSITIONS} positions")]
        )
    seen: dict[str, int] = {}
    modes: set[str] = set()
    source = data
    for i, row in enumerate(rows):

        def err(message: str, field: str | None = None, *, _i: int = i, _row: PositionInput = row) -> None:
            errors.append(EntryError(index=_i, symbol=_row.symbol, field=field, message=message))

        try:
            sym = _symbol(row)
        except ValueError as exc:
            err(f"symbol ไม่ถูกต้อง: {exc}", "symbol")
            continue
        if sym.canonical in seen:
            err(f"{sym.canonical} ซ้ำกับแถว {seen[sym.canonical] + 1}", "symbol")
            continue
        seen[sym.canonical] = i

        if row.value is not None:
            if row.quantity is not None:
                err("กรอก value หรือ quantity อย่างใดอย่างหนึ่ง", "value")
                continue
            if row.value <= 0:
                err("มูลค่าต้องมากกว่า 0", "value")
                continue
            pnl = row.pnl_pct if row.pnl_pct is not None else Decimal(0)
            if pnl <= -1:
                err("% กำไร/ขาดทุนต้องมากกว่า -100%", "pnl_pct")
                continue
            source = source or data_source()
            quote, why = await fetch_quote(sym, source)
            if quote is None:
                err(f"ดึงราคา {sym.canonical} ไม่ได้ ({why}) จึงแปลงมูลค่าเป็นจำนวนหน่วยไม่ได้", "value")
                continue
            user = new_record(
                "user_input",
                {"value": str(row.value), "pnl_pct": str(pnl)},
                source="user",
                unit=sym.currency,
                params={"symbol": sym.canonical, "mode": "value_pnl"},
            )
            conv = units_from_value(row.value, pnl, quote.price, inputs=[user.id, quote.record.id])
            records += [*quote.records, user, *conv.records]
            modes.add("quantity")
            positions.append(
                Position(
                    symbol=sym,
                    quantity=conv.value["units"],
                    avg_cost=conv.value["avg_cost"],
                    target_weight=row.target_weight,
                    note=row.note,
                )
            )
            converted.append(
                {
                    "index": i,
                    "symbol": sym.canonical,
                    "price": str(quote.price),
                    "units": str(conv.value["units"]),
                    "avg_cost": str(conv.value["avg_cost"]),
                    "provenance_id": conv.records[0].id,
                }
            )
            continue

        if row.quantity is not None:
            if row.quantity == 0:
                err("จำนวนต้องไม่เป็น 0", "quantity")
                continue
            modes.add("quantity")
            positions.append(
                Position(
                    symbol=sym,
                    quantity=row.quantity,
                    avg_cost=row.avg_cost,
                    target_weight=row.target_weight,
                    note=row.note,
                )
            )
            continue

        if row.weight is not None:
            if not (0 < row.weight <= 1):
                err("% น้ำหนักต้องอยู่ระหว่าง 0 ถึง 100%", "weight")
                continue
            modes.add("weight")
            positions.append(
                Position(
                    symbol=sym,
                    quantity=Decimal(0),
                    declared_weight=row.weight,
                    target_weight=row.target_weight,
                    note=row.note,
                )
            )
            continue
        err("ต้องกรอก quantity, value หรือ weight อย่างน้อยหนึ่งอย่าง", "quantity")

    if modes == {"quantity", "weight"}:
        errors.append(
            EntryError(
                index=None,
                field="weight",
                message="พอร์ตเดียวกันต้องกรอกแบบจำนวนหุ้นทั้งหมด หรือแบบ % ทั้งหมด (ห้ามปนกัน)",
            )
        )
    declared = sum((p.declared_weight or Decimal(0) for p in positions), Decimal(0))
    if declared > 1:
        errors.append(
            EntryError(index=None, field="weight", message=f"% น้ำหนักรวม {float(declared):.2%} เกิน 100%")
        )
    return ResolvedEntry(positions=positions, errors=errors, records=records, converted=converted)


def _decimal(value: str, *, percent: bool = False) -> Decimal | None:
    """``"1,234.5"`` → 1234.5; with ``percent``: ``"15%"``/``"15"``/``"0.15"`` → 0.15, ``"-5"`` → -0.05."""
    text = value.strip()
    if not text:
        return None
    if percent:
        is_pct = text.endswith("%")
        number = coerce_optional_float(text.rstrip("%").strip())
        if number is not None and (is_pct or abs(number) > 1):
            number /= 100.0
    else:
        number = coerce_optional_float(text)
    if number is None:
        raise ValueError(f"not a number: {value!r}")
    try:
        return Decimal(str(number))
    except InvalidOperation as exc:  # pragma: no cover - coerce already validated
        raise ValueError(f"not a number: {value!r}") from exc


def parse_csv(text: str) -> tuple[list[PositionInput], list[EntryError]]:
    """CSV ``symbol,quantity,avg_cost,weight,note`` (+ optional ``value,pnl_pct,target_weight``).

    ``weight``/``pnl_pct``/``target_weight`` accept ``15%``, ``15`` or ``0.15``.
    """
    rows: list[PositionInput] = []
    errors: list[EntryError] = []
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    header = [h.strip().lower() for h in (reader.fieldnames or [])]
    if "symbol" not in header:
        return [], [EntryError(index=None, field="symbol", message="CSV ต้องมีคอลัมน์ symbol")]
    unknown = [h for h in header if h and h not in CSV_COLUMNS]
    if unknown:
        errors.append(EntryError(index=None, message=f"ไม่รู้จักคอลัมน์ {', '.join(unknown)}"))
    for i, raw in enumerate(reader):
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items() if k is not None}
        if not any(row.values()):
            continue
        try:
            rows.append(
                PositionInput(
                    symbol=row.get("symbol", ""),
                    quantity=_decimal(row.get("quantity", "")),
                    avg_cost=_decimal(row.get("avg_cost", "")),
                    weight=_decimal(row.get("weight", ""), percent=True),
                    value=_decimal(row.get("value", "")),
                    pnl_pct=_decimal(row.get("pnl_pct", ""), percent=True),
                    target_weight=_decimal(row.get("target_weight", ""), percent=True),
                    note=row.get("note") or None,
                )
            )
        except ValueError as exc:
            errors.append(EntryError(index=i, symbol=row.get("symbol") or None, message=str(exc)))
    return rows, errors
