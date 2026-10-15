"""Turn what the user typed into ``Position``\\ s (P12 §12.5, §12.7; P16 §16.2).

Broker apps show a holding in different ways, so a row can be entered in one of these modes
(``EntryMode``; ``None`` = inferred from the fields that are set):

==================== ============================ ===================== ===========
mode                 fields                       computed              needs price
==================== ============================ ===================== ===========
``qty_cost``         quantity (+ avg_cost)        —                     no
``qty_pnl_pct``      quantity + pnl_pct           avg_cost              yes
``qty_pnl_amount``   quantity + pnl_amount        avg_cost              yes
``cost_total``       quantity + cost_total        avg_cost              no
``value_pnl_pct``    value (+ pnl_pct)            units (+ avg_cost)    yes
``value_pnl_amount`` value + pnl_amount           units, avg_cost       yes
``weight``           weight                       — (declared %)        no
==================== ============================ ===================== ===========

Everything that is derived goes through ``compute.portfolio`` (F1) with the user's numbers as
a ``user_input`` record and the latest price as a ``data`` record, so value and P&L follow
the market afterwards. An ``avg_cost`` typed directly always wins over a computed one (a
quantity row then counts as ``qty_cost`` and needs no price). A field that does not belong
to the row's mode is refused with that field named, never silently dropped.

A book is either all-declared-% or all-quantities; mixing them is refused instead of
guessing what the declared rows are worth. Errors are returned per row (index, field,
message) so the API can answer 422 with the exact rows to fix.
"""

from __future__ import annotations

import asyncio
import csv
import io
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, get_args

from pydantic import BaseModel, Field

from app.domain.compute.base import ComputeResult
from app.domain.compute.portfolio import (
    avg_cost_from_cost_total,
    avg_cost_from_pnl_amount,
    avg_cost_from_pnl_pct,
    position_value,
    units_from_value,
    units_from_value_and_pnl_amount,
)
from app.domain.models.coerce import coerce_optional_float
from app.domain.models.portfolio import Position
from app.domain.models.provenance import ProvenanceRecord, new_record
from app.domain.models.symbol import Market, Symbol, normalize_symbol, safe_path_component
from app.domain.portfolio.market import MarketData, PriceQuote, data_source, fetch_quote

MAX_POSITIONS = 200
QUOTE_CONCURRENCY = 8
CSV_COLUMNS = (
    "symbol",
    "quantity",
    "avg_cost",
    "weight",
    "note",
    "value",
    "pnl_pct",
    "pnl_amount",
    "cost_total",
    "mode",
    "target_weight",
)

EntryMode = Literal[
    "qty_cost", "qty_pnl_pct", "qty_pnl_amount", "value_pnl_pct", "value_pnl_amount", "cost_total", "weight"
]
ENTRY_MODES: tuple[EntryMode, ...] = get_args(EntryMode)
_NUMERIC = ("quantity", "avg_cost", "value", "pnl_pct", "pnl_amount", "cost_total", "weight")
# fields a row of each mode may carry (``avg_cost`` is allowed everywhere but ``weight``: it wins)
MODE_FIELDS: dict[EntryMode, frozenset[str]] = {
    "qty_cost": frozenset({"quantity", "avg_cost"}),
    "qty_pnl_pct": frozenset({"quantity", "pnl_pct", "avg_cost"}),
    "qty_pnl_amount": frozenset({"quantity", "pnl_amount", "avg_cost"}),
    "cost_total": frozenset({"quantity", "cost_total", "avg_cost"}),
    "value_pnl_pct": frozenset({"value", "pnl_pct", "avg_cost"}),
    "value_pnl_amount": frozenset({"value", "pnl_amount", "avg_cost"}),
    "weight": frozenset({"weight"}),
}
MODE_REQUIRED: dict[EntryMode, tuple[str, ...]] = {
    "qty_cost": ("quantity",),
    "qty_pnl_pct": ("quantity", "pnl_pct"),
    "qty_pnl_amount": ("quantity", "pnl_amount"),
    "cost_total": ("quantity", "cost_total"),
    "value_pnl_pct": ("value",),
    "value_pnl_amount": ("value", "pnl_amount"),
    "weight": ("weight",),
}
_NEEDS_PRICE: frozenset[EntryMode] = frozenset(
    {"qty_pnl_pct", "qty_pnl_amount", "value_pnl_pct", "value_pnl_amount"}
)
_VALUE_MODES: frozenset[EntryMode] = frozenset({"value_pnl_pct", "value_pnl_amount"})
_FIELD_TH = {
    "quantity": "จำนวนหุ้น",
    "avg_cost": "ต้นทุนเฉลี่ย",
    "value": "มูลค่า",
    "pnl_pct": "% กำไร/ขาดทุน",
    "pnl_amount": "กำไร/ขาดทุน (เงิน)",
    "cost_total": "ต้นทุนรวม",
    "weight": "% น้ำหนัก",
}


class PositionInput(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    market: Market | None = None
    mode: EntryMode | None = Field(default=None, description="Entry mode; null = inferred from the fields")
    quantity: Decimal | None = None
    avg_cost: Decimal | None = Field(default=None, ge=0)
    weight: Decimal | None = Field(default=None, description="Declared weight 0..1 (weight-only mode)")
    target_weight: Decimal | None = Field(default=None, ge=0, le=1)
    value: Decimal | None = Field(default=None, description="Market value in the symbol's currency")
    pnl_pct: Decimal | None = Field(
        default=None, description="Unrealized gain as a fraction (0.1452 = +14.52%, negative = loss)"
    )
    pnl_amount: Decimal | None = Field(
        default=None, description="Unrealized gain in the symbol's currency (negative = loss)"
    )
    cost_total: Decimal | None = Field(default=None, description="Total cost paid (>= 0), symbol's currency")
    note: str | None = Field(default=None, max_length=500)


class EntryError(BaseModel):
    index: int | None  # row index (0-based); None = the whole book
    symbol: str | None = None
    field: str | None = None
    message: str
    # (P16) "warning" = shown to the user, the row can still be saved (e.g. no price for a preview)
    severity: Literal["error", "warning"] = "error"


class ResolvedEntry(BaseModel):
    positions: list[Position]
    errors: list[EntryError]
    records: list[ProvenanceRecord] = Field(default_factory=list)
    converted: list[dict[str, Any]] = Field(default_factory=list)  # computed rows → units/cost


class PreviewRow(BaseModel):
    """One row of ``POST …/positions/preview`` (contracts §11): nothing is saved."""

    index: int
    input_symbol: str
    symbol: Symbol | None = None
    mode: EntryMode | None = None
    status: Literal["ok", "error"]
    quantity: Decimal | None = None
    avg_cost: Decimal | None = None
    weight: Decimal | None = Field(default=None, description="Declared weight (weight mode)")
    price: Decimal | None = None
    price_as_of: date | None = None
    currency: str | None = None
    market_value: Decimal | None = None
    cost_basis: Decimal | None = None
    unrealized_pnl: Decimal | None = None
    unrealized_pnl_pct: Decimal | None = None
    provenance_id: str | None = Field(
        default=None, description="Valuation record; its inputs trace the price, the typed numbers, compute"
    )
    provenance: dict[str, str] = Field(default_factory=dict, description="field → provenance id")
    errors: list[EntryError] = Field(default_factory=list)


class PositionsPreview(BaseModel):
    rows: list[PreviewRow]
    errors: list[EntryError] = Field(default_factory=list, description="Book-level errors")


def _symbol(row: PositionInput) -> Symbol:
    sym = normalize_symbol(row.symbol, row.market)
    safe_path_component(sym.canonical)
    return sym


def infer_mode(row: PositionInput) -> EntryMode | None:
    """The row's mode: ``row.mode`` when given, else from the fields that are set (None = nothing)."""
    if row.mode is not None:
        return row.mode
    if row.value is not None:
        return "value_pnl_amount" if row.pnl_amount is not None else "value_pnl_pct"
    if row.quantity is not None:
        if row.cost_total is not None:
            return "cost_total"
        if row.pnl_amount is not None:
            return "qty_pnl_amount"
        if row.pnl_pct is not None:
            return "qty_pnl_pct"
        return "qty_cost"
    if row.weight is not None:
        return "weight"
    return None


@dataclass
class _Row:
    """Working state of one row between validation, quotes and compute."""

    index: int
    row: PositionInput
    sym: Symbol | None = None
    mode: EntryMode | None = None
    errors: list[EntryError] = field(default_factory=list)
    position: Position | None = None
    quote: PriceQuote | None = None
    quote_error: str | None = None
    user: ProvenanceRecord | None = None
    records: list[ProvenanceRecord] = field(default_factory=list)  # user + conversion + valuation
    conv_id: str | None = None  # the conversion's compute record (computed rows only)
    value_id: str | None = None  # ``position_value`` record
    valuation: dict[str, Decimal | None] = field(default_factory=dict)

    def err(self, message: str, field_: str | None = None) -> None:
        self.errors.append(
            EntryError(index=self.index, symbol=self.row.symbol, field=field_, message=message)
        )

    @property
    def effective_mode(self) -> EntryMode | None:
        """``avg_cost`` typed directly wins: a quantity row then needs no price and is ``qty_cost``."""
        if self.mode in ("qty_pnl_pct", "qty_pnl_amount", "cost_total") and self.row.avg_cost is not None:
            return "qty_cost"
        return self.mode

    @property
    def needs_price(self) -> bool:
        mode = self.effective_mode
        return mode is not None and mode in _NEEDS_PRICE

    def user_record(self) -> ProvenanceRecord:
        """The numbers exactly as typed (one ``user_input`` record per row, made once)."""
        assert self.sym is not None and self.mode is not None
        if self.user is None:
            typed = {n: str(getattr(self.row, n)) for n in _NUMERIC if getattr(self.row, n) is not None}
            self.user = new_record(
                "user_input",
                typed,
                source="user",
                unit=self.sym.currency,
                params={"symbol": self.sym.canonical, "mode": self.mode},
            )
            self.records.append(self.user)
        return self.user


def _validate(item: _Row) -> None:
    """Symbol, mode and the fields that belong to it (no prices, no compute)."""
    row = item.row
    try:
        item.sym = _symbol(row)
    except ValueError as exc:
        item.err(f"symbol ไม่ถูกต้อง: {exc}", "symbol")
        return
    if row.mode is None and row.value is not None and row.quantity is not None:
        item.err("กรอก value หรือ quantity อย่างใดอย่างหนึ่ง", "value")
        return
    mode = infer_mode(row)
    if mode is None:
        item.err("ต้องกรอก quantity, value หรือ weight อย่างน้อยหนึ่งอย่าง", "quantity")
        return
    item.mode = mode
    allowed = MODE_FIELDS[mode]
    for name in _NUMERIC:
        if getattr(row, name) is not None and name not in allowed:
            item.err(f"โหมด {mode} ไม่ใช้ช่อง{_FIELD_TH[name]} — ลบออกหรือเปลี่ยนโหมด", name)
    for name in MODE_REQUIRED[mode]:
        if getattr(row, name) is None:
            item.err(f"โหมด {mode} ต้องกรอก{_FIELD_TH[name]}", name)
    if item.errors:
        return
    if row.quantity is not None and row.quantity == 0:
        item.err("จำนวนต้องไม่เป็น 0", "quantity")
    elif row.quantity is not None and row.quantity < 0 and item.effective_mode != "qty_cost":
        item.err("จำนวนต้องมากกว่า 0 เมื่อให้ระบบคำนวณต้นทุน", "quantity")
    if row.value is not None and row.value <= 0:
        item.err("มูลค่าต้องมากกว่า 0", "value")
    if mode == "weight" and row.weight is not None and not (0 < row.weight <= 1):
        item.err("% น้ำหนักต้องอยู่ระหว่าง 0 ถึง 100%", "weight")
    if row.pnl_pct is not None and row.pnl_pct <= -1:
        item.err("% กำไร/ขาดทุนต้องมากกว่า -100%", "pnl_pct")
    if row.cost_total is not None and row.cost_total < 0:
        item.err("ต้นทุนรวมต้องไม่ติดลบ", "cost_total")


def _derive(
    item: _Row, mode: EntryMode, inputs: list[str]
) -> tuple[ComputeResult[Any] | None, Decimal, Decimal | None]:
    """(conversion, quantity, avg_cost) of a quantity/value row; raises ``ValueError`` from compute."""
    row, quote = item.row, item.quote
    if mode == "qty_cost":
        assert row.quantity is not None
        return None, row.quantity, row.avg_cost
    if mode == "cost_total":
        assert row.quantity is not None and row.cost_total is not None
        conv: ComputeResult[Any] = avg_cost_from_cost_total(row.quantity, row.cost_total, inputs=inputs)
        return conv, row.quantity, conv.value["avg_cost"]
    assert quote is not None
    if mode == "qty_pnl_pct":
        assert row.quantity is not None and row.pnl_pct is not None
        conv = avg_cost_from_pnl_pct(quote.price, row.pnl_pct, inputs=inputs)
        return conv, row.quantity, conv.value["avg_cost"]
    if mode == "qty_pnl_amount":
        assert row.quantity is not None and row.pnl_amount is not None
        conv = avg_cost_from_pnl_amount(row.quantity, quote.price, row.pnl_amount, inputs=inputs)
        return conv, row.quantity, conv.value["avg_cost"]
    assert row.value is not None
    if mode == "value_pnl_amount":
        assert row.pnl_amount is not None
        conv = units_from_value_and_pnl_amount(row.value, row.pnl_amount, quote.price, inputs=inputs)
    else:
        conv = units_from_value(row.value, row.pnl_pct, quote.price, inputs=inputs)
    units: Decimal | None = conv.value["units"]
    assert units is not None
    return conv, units, row.avg_cost if row.avg_cost is not None else conv.value["avg_cost"]


def _convert(item: _Row) -> None:
    """Run the compute step of the row's mode and build its ``Position``."""
    row, sym, mode = item.row, item.sym, item.effective_mode
    assert sym is not None and mode is not None
    if mode == "weight":
        assert row.weight is not None
        item.position = Position(
            symbol=sym,
            quantity=Decimal(0),
            declared_weight=row.weight,
            target_weight=row.target_weight,
            note=row.note,
        )
        return
    quote = item.quote
    if mode in _NEEDS_PRICE and quote is None:
        is_value = mode in _VALUE_MODES
        field_ = "value" if is_value else ("pnl_pct" if mode == "qty_pnl_pct" else "pnl_amount")
        what = "แปลงมูลค่าเป็นจำนวนหน่วย" if is_value else "คำนวณต้นทุนเฉลี่ย"
        item.err(f"ดึงราคา {sym.canonical} ไม่ได้ ({item.quote_error}) จึง{what}ไม่ได้", field_)
        return
    user = item.user_record()
    inputs = [user.id, *([quote.record.id] if quote is not None and mode in _NEEDS_PRICE else [])]
    try:
        conv, quantity, avg_cost = _derive(item, mode, inputs)
    except ValueError as exc:
        item.err(str(exc), MODE_REQUIRED[mode][-1])  # the number the user typed besides qty/value
        return
    if conv is not None:
        item.records.extend(conv.records)
        item.conv_id = conv.records[0].id
    item.position = Position(
        symbol=sym, quantity=quantity, avg_cost=avg_cost, target_weight=row.target_weight, note=row.note
    )


def _value(item: _Row) -> None:
    """Market value / cost basis / P&L of a converted quantity row (the price may be missing)."""
    pos = item.position
    if pos is None or item.errors or pos.declared_weight is not None:
        return
    quote = item.quote
    inputs = [item.conv_id or item.user_record().id, *([quote.record.id] if quote is not None else [])]
    out = position_value(pos.quantity, pos.avg_cost, quote.price if quote else None, inputs=inputs)
    item.records.extend(out.records)
    item.value_id = out.records[0].id
    item.valuation = out.value


async def _fetch_quotes(items: list[_Row], source: MarketData) -> None:
    sem = asyncio.Semaphore(QUOTE_CONCURRENCY)
    unique: dict[str, Symbol] = {it.sym.canonical: it.sym for it in items if it.sym is not None}

    async def one(sym: Symbol) -> tuple[str, tuple[PriceQuote | None, str | None]]:
        async with sem:
            return sym.canonical, await fetch_quote(sym, source)

    got = dict(await asyncio.gather(*(one(s) for s in unique.values())))
    for it in items:
        if it.sym is not None:
            it.quote, it.quote_error = got[it.sym.canonical]


async def _resolve(rows: list[PositionInput], source: MarketData | None, *, all_quotes: bool) -> list[_Row]:
    """Validate every row, fetch the prices that are needed (or all, for a preview), compute."""
    items = [_Row(index=i, row=row) for i, row in enumerate(rows)]
    seen: dict[str, int] = {}
    for item in items:
        _validate(item)
        if item.sym is None:
            continue
        if item.sym.canonical in seen:
            item.errors.append(
                EntryError(
                    index=item.index,
                    symbol=item.row.symbol,
                    field="symbol",
                    message=f"{item.sym.canonical} ซ้ำกับแถว {seen[item.sym.canonical] + 1}",
                )
            )
            continue
        seen[item.sym.canonical] = item.index
    ready = [it for it in items if not it.errors and it.mode is not None]
    wanted = [it for it in ready if all_quotes or it.needs_price]
    if wanted:
        await _fetch_quotes(wanted, source or data_source())
    for item in ready:
        _convert(item)
    return items


def _book_errors(items: list[_Row]) -> list[EntryError]:
    positions = [it.position for it in items if it.position is not None and not it.errors]
    errors: list[EntryError] = []
    modes = {"weight" if p.declared_weight is not None else "quantity" for p in positions}
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
    return errors


def _too_many(rows: list[PositionInput]) -> EntryError | None:
    if len(rows) > MAX_POSITIONS:
        return EntryError(index=None, message=f"at most {MAX_POSITIONS} positions")
    return None


def _str(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


async def resolve_positions(rows: list[PositionInput], *, data: MarketData | None = None) -> ResolvedEntry:
    """Rows → positions to save. Computed rows also return their records (price, typed numbers,
    conversion, valuation) and a ``converted`` summary; ``qty_cost``/``weight`` rows return none
    (the saved book itself is the ``user_input`` record)."""
    if (too_many := _too_many(rows)) is not None:
        return ResolvedEntry(positions=[], errors=[too_many])
    items = await _resolve(rows, data, all_quotes=False)
    positions: list[Position] = []
    records: list[ProvenanceRecord] = []
    converted: list[dict[str, Any]] = []
    errors: list[EntryError] = []
    for item in items:
        errors.extend(item.errors)
        if item.position is None or item.errors:
            continue
        positions.append(item.position)
        if item.conv_id is None:
            continue
        _value(item)  # cost basis (and value at the price used) of the converted row
        quote = item.quote if item.needs_price else None
        records += [*(quote.records if quote is not None else []), *item.records]
        assert item.sym is not None
        converted.append(
            {
                "index": item.index,
                "symbol": item.sym.canonical,
                "mode": item.effective_mode,
                "price": None if quote is None else str(quote.price),
                "price_as_of": None if quote is None or quote.as_of is None else quote.as_of.isoformat(),
                "currency": item.sym.currency if quote is None else quote.currency,
                "units": str(item.position.quantity),
                "avg_cost": _str(item.position.avg_cost),
                "cost_basis": _str(item.valuation.get("cost_basis")),
                "provenance_id": item.conv_id,
            }
        )
    errors.extend(_book_errors(items))
    return ResolvedEntry(positions=positions, errors=errors, records=records, converted=converted)


def _preview_provenance(item: _Row) -> dict[str, str]:
    """field → the record that explains it (typed number, conversion, price or valuation)."""
    prov: dict[str, str] = {}
    pos = item.position
    if item.quote is not None:
        prov["price"] = item.quote.record.id
    if pos is None or item.errors or item.user is None:
        return prov
    if pos.declared_weight is not None:
        prov["weight"] = item.user.id
        return prov
    derived = item.conv_id or item.user.id
    prov["quantity"] = derived if item.effective_mode in _VALUE_MODES else item.user.id
    if pos.avg_cost is not None:
        prov["avg_cost"] = item.user.id if item.row.avg_cost is not None else derived
    if item.value_id is not None:
        for name in ("market_value", "cost_basis", "unrealized_pnl", "unrealized_pnl_pct"):
            if item.valuation.get(name) is not None:
                prov[name] = item.value_id
    return prov


async def preview_positions(
    rows: list[PositionInput], *, data: MarketData | None = None
) -> tuple[PositionsPreview, list[ProvenanceRecord]]:
    """Resolve every row and value it at the latest price without saving anything (P16 §16.2).

    Returns the preview plus the provenance records its ids point to (the caller stores them so
    a hover on any number resolves). A row whose mode does not need a price still gets one when
    available; if not, its value/P&L stay ``None`` with a warning and the row stays ``ok``.
    """
    if (too_many := _too_many(rows)) is not None:
        return PositionsPreview(rows=[], errors=[too_many]), []
    items = await _resolve(rows, data, all_quotes=True)
    out: list[PreviewRow] = []
    records: list[ProvenanceRecord] = []
    for item in items:
        ok = not item.errors and item.position is not None
        _value(item)
        if ok and item.position is not None and item.position.declared_weight is not None:
            item.user_record()
        quote = item.quote
        warnings: list[EntryError] = []
        if ok and item.sym is not None and quote is None:
            warnings.append(
                EntryError(
                    index=item.index,
                    symbol=item.row.symbol,
                    field="price",
                    message=f"ดึงราคาล่าสุดของ {item.sym.canonical} ไม่ได้ ({item.quote_error}) "
                    "จึงยังไม่แสดงมูลค่า/กำไร — บันทึกได้ตามปกติ",
                    severity="warning",
                )
            )
        if quote is not None:
            records.extend(quote.records)
        records.extend(item.records)
        pos = item.position if ok else None
        valuation = item.valuation if pos is not None else {}
        out.append(
            PreviewRow(
                index=item.index,
                input_symbol=item.row.symbol,
                symbol=item.sym,
                mode=item.effective_mode,
                status="ok" if ok else "error",
                quantity=None if pos is None or pos.declared_weight is not None else pos.quantity,
                avg_cost=None if pos is None else pos.avg_cost,
                weight=None if pos is None else pos.declared_weight,
                price=None if quote is None else quote.price,
                price_as_of=None if quote is None else quote.as_of,
                currency=None if item.sym is None else (quote.currency if quote else item.sym.currency),
                market_value=valuation.get("market_value"),
                cost_basis=valuation.get("cost_basis"),
                unrealized_pnl=valuation.get("unrealized_pnl"),
                unrealized_pnl_pct=valuation.get("unrealized_pnl_pct"),
                provenance_id=item.value_id if pos is not None else None,
                provenance=_preview_provenance(item),
                errors=[*item.errors, *warnings],
            )
        )
    unique = list({r.id: r for r in records}.values())
    return PositionsPreview(rows=out, errors=_book_errors(items)), unique


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
    """CSV ``symbol,quantity,avg_cost,weight,note`` (+ optional ``value,pnl_pct,pnl_amount,
    cost_total,mode,target_weight``; P16).

    ``weight``/``pnl_pct``/``target_weight`` accept ``15%``, ``15`` or ``0.15``; ``pnl_amount``
    accepts a sign and thousands separators (``-1,250.50``); ``mode`` is one of ``ENTRY_MODES``
    (empty = inferred from the filled columns).
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
        mode = row.get("mode", "").lower() or None
        if mode is not None and mode not in ENTRY_MODES:
            errors.append(
                EntryError(
                    index=i,
                    symbol=row.get("symbol") or None,
                    field="mode",
                    message=f"ไม่รู้จักโหมด {mode!r} (ใช้ {', '.join(ENTRY_MODES)})",
                )
            )
            continue
        try:
            rows.append(
                PositionInput(
                    symbol=row.get("symbol", ""),
                    mode=mode,
                    quantity=_decimal(row.get("quantity", "")),
                    avg_cost=_decimal(row.get("avg_cost", "")),
                    weight=_decimal(row.get("weight", ""), percent=True),
                    value=_decimal(row.get("value", "")),
                    pnl_pct=_decimal(row.get("pnl_pct", ""), percent=True),
                    pnl_amount=_decimal(row.get("pnl_amount", "")),
                    cost_total=_decimal(row.get("cost_total", "")),
                    target_weight=_decimal(row.get("target_weight", ""), percent=True),
                    note=row.get("note") or None,
                )
            )
        except ValueError as exc:
            errors.append(EntryError(index=i, symbol=row.get("symbol") or None, message=str(exc)))
    return rows, errors
