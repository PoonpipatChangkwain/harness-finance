"""P16 §16.2 entry modes: the resolve table, field ownership, CSV columns, preview, paste mapping."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from app.domain.portfolio.entry import (
    CSV_COLUMNS,
    PositionInput,
    infer_mode,
    parse_csv,
    preview_positions,
    resolve_positions,
)
from app.domain.portfolio.paste import (
    PROMPT_ID,
    ParsedHolding,
    ParsedHoldings,
    holdings_messages,
    to_position_inputs,
)
from tests.domain.portfolio.fakes import FakeMarket

D = Decimal


def _quote_calls(market: FakeMarket) -> list[str]:
    return [sym for cap, sym in market.calls if cap == "quote"]


# PTT.BK = 34.5 THB, AAPL = 200 USD in the fixture
@pytest.mark.parametrize(
    ("fields", "mode", "needs_price", "quantity", "avg_cost"),
    [
        ({"symbol": "PTT", "quantity": "1000", "avg_cost": "30"}, "qty_cost", False, D(1000), D(30)),
        ({"symbol": "PTT", "quantity": "1000", "pnl_pct": "0.15"}, "qty_pnl_pct", True, D(1000), D(30)),
        ({"symbol": "PTT", "quantity": "1000", "pnl_amount": "4500"}, "qty_pnl_amount", True, D(1000), D(30)),
        ({"symbol": "PTT", "quantity": "1000", "cost_total": "30000"}, "cost_total", False, D(1000), D(30)),
        ({"symbol": "PTT", "quantity": "1000"}, "qty_cost", False, D(1000), None),
        ({"symbol": "AAPL", "value": "2000", "pnl_pct": "0.25"}, "value_pnl_pct", True, D(10), D(160)),
        ({"symbol": "AAPL", "value": "2000", "pnl_amount": "400"}, "value_pnl_amount", True, D(10), D(160)),
        ({"symbol": "AAPL", "value": "2000"}, "value_pnl_pct", True, D(10), None),
    ],
)
async def test_resolve_table(
    fields: dict[str, Any], mode: str, needs_price: bool, quantity: Decimal, avg_cost: Decimal | None
) -> None:
    market = FakeMarket()
    for row in (PositionInput(**fields), PositionInput(**fields, mode=mode)):  # inferred = explicit
        assert infer_mode(row) == mode
        out = await resolve_positions([row], data=market)
        assert out.errors == [], out.errors
        (pos,) = out.positions
        assert pos.quantity == quantity
        assert pos.avg_cost == pytest.approx(avg_cost) if avg_cost is not None else pos.avg_cost is None
    assert bool(_quote_calls(market)) is needs_price
    computed = mode not in ("qty_cost",)
    assert bool(out.converted) is computed
    if computed:
        conv = out.converted[0]
        assert conv["mode"] == mode and conv["units"] == str(quantity)
        assert set(conv) >= {"price", "price_as_of", "currency", "avg_cost", "cost_basis", "provenance_id"}
        kinds = {r.kind for r in out.records}
        assert {"user_input", "compute"} <= kinds and ("data" in kinds) is needs_price
        assert conv["provenance_id"] in {r.id for r in out.records}
        if needs_price:
            assert conv["price_as_of"] == "2026-09-25" and conv["price"] is not None


async def test_weight_mode_is_declared() -> None:
    market = FakeMarket()
    out = await resolve_positions([PositionInput(symbol="PTT", weight=D("0.4"))], data=market)
    assert (
        out.errors == [] and out.positions[0].declared_weight == D("0.4") and out.positions[0].quantity == 0
    )
    assert _quote_calls(market) == []


async def test_converted_row_details() -> None:
    out = await resolve_positions(
        [PositionInput(symbol="PTT", quantity=D(1000), pnl_pct=D("0.15"))], data=FakeMarket()
    )
    conv = out.converted[0]
    assert conv["symbol"] == "PTT.BK" and conv["price"] == "34.5" and conv["currency"] == "THB"
    assert D(conv["cost_basis"]) == pytest.approx(D(30000))
    by_id = {r.id: r for r in out.records}
    assert by_id[conv["provenance_id"]].calculation == "compute.portfolio.avg_cost_from_pnl_pct@v1"
    user = next(r for r in out.records if r.kind == "user_input")
    assert user.value == {"quantity": "1000", "pnl_pct": "0.15"} and user.params["mode"] == "qty_pnl_pct"
    assert user.id in by_id[conv["provenance_id"]].inputs


@pytest.mark.parametrize("mode", [None, "qty_pnl_pct"])
async def test_avg_cost_typed_directly_wins(mode: str | None) -> None:
    market = FakeMarket()
    row = PositionInput(symbol="PTT", mode=mode, quantity=D(1000), avg_cost=D(31), pnl_pct=D("0.15"))
    out = await resolve_positions([row], data=market)
    assert out.errors == [] and out.positions[0].avg_cost == D(31)
    assert out.converted == [] and _quote_calls(market) == []  # no price needed, nothing computed
    preview, _ = await preview_positions([row], data=market)
    assert preview.rows[0].mode == "qty_cost" and preview.rows[0].avg_cost == D(31)


async def test_value_mode_with_avg_cost_keeps_units_from_price() -> None:
    row = PositionInput(symbol="AAPL", value=D(2000), pnl_pct=D("0.25"), avg_cost=D(150))
    out = await resolve_positions([row], data=FakeMarket())
    assert out.positions[0].quantity == 10 and out.positions[0].avg_cost == D(150)


@pytest.mark.parametrize(
    ("fields", "field"),
    [
        ({"mode": "qty_cost", "quantity": "1", "pnl_pct": "0.1"}, "pnl_pct"),
        ({"mode": "qty_cost", "quantity": "1", "value": "100"}, "value"),
        ({"mode": "value_pnl_pct", "value": "100", "quantity": "1"}, "quantity"),
        ({"mode": "weight", "weight": "0.1", "quantity": "5"}, "quantity"),
        ({"mode": "cost_total", "quantity": "5", "cost_total": "10", "pnl_amount": "1"}, "pnl_amount"),
        ({"quantity": "1", "weight": "0.1"}, "weight"),
        ({"quantity": "1", "pnl_pct": "0.1", "pnl_amount": "5"}, "pnl_pct"),
        ({"value": "100", "pnl_pct": "0.1", "pnl_amount": "5"}, "pnl_pct"),
        ({"value": "100", "cost_total": "90"}, "cost_total"),
        ({"mode": "qty_pnl_amount", "quantity": "10"}, "pnl_amount"),
        ({"mode": "value_pnl_amount", "value": "100"}, "pnl_amount"),
        ({"mode": "qty_pnl_pct", "pnl_pct": "0.1"}, "quantity"),
    ],
)
async def test_fields_outside_the_mode_are_refused_by_name(fields: dict[str, Any], field: str) -> None:
    out = await resolve_positions([PositionInput(symbol="PTT", **fields)], data=FakeMarket())
    assert out.positions == []
    assert field in {e.field for e in out.errors}, out.errors
    assert all(e.index == 0 for e in out.errors)


@pytest.mark.parametrize(
    ("fields", "field"),
    [
        ({"quantity": "1000", "pnl_amount": "34500"}, "pnl_amount"),  # gain = whole value → cost 0
        ({"quantity": "1000", "pnl_pct": "-1"}, "pnl_pct"),
        ({"quantity": "-10", "pnl_pct": "0.1"}, "quantity"),
        ({"quantity": "1000", "cost_total": "-5"}, "cost_total"),
        ({"value": "1000", "pnl_amount": "1000"}, "pnl_amount"),
        ({"value": "-5"}, "value"),
    ],
)
async def test_invalid_numbers_name_the_field(fields: dict[str, Any], field: str) -> None:
    out = await resolve_positions([PositionInput(symbol="PTT", **fields)], data=FakeMarket())
    assert out.positions == [] and out.errors[0].field == field, out.errors


async def test_modes_that_need_a_price_fail_without_one() -> None:
    market = FakeMarket(missing={"PTT.BK"})
    for fields, field in [
        ({"quantity": "1000", "pnl_pct": "0.1"}, "pnl_pct"),
        ({"quantity": "1000", "pnl_amount": "100"}, "pnl_amount"),
        ({"value": "1000", "pnl_amount": "100"}, "value"),
    ]:
        out = await resolve_positions([PositionInput(symbol="PTT", **fields)], data=market)
        assert out.positions == [] and out.errors[0].field == field and "PTT.BK" in out.errors[0].message
    ok = await resolve_positions([PositionInput(symbol="PTT", quantity=D(5), cost_total=D(150))], data=market)
    assert ok.errors == [] and ok.positions[0].avg_cost == D(30)


async def test_all_modes_in_one_book_and_weight_mix_still_refused() -> None:
    rows = [
        PositionInput(symbol="PTT", quantity=D(1000), pnl_amount=D(4500)),
        PositionInput(symbol="AAPL", value=D(2000), pnl_amount=D(400)),
        PositionInput(symbol="KBANK", quantity=D(100), cost_total=D(14000)),
        PositionInput(symbol="MSFT", quantity=D(2)),
    ]
    out = await resolve_positions(rows, data=FakeMarket())
    assert out.errors == [] and len(out.positions) == 4 and len(out.converted) == 3
    mixed = await resolve_positions([*rows, PositionInput(symbol="SCB", weight=D("0.1"))], data=FakeMarket())
    assert any(e.index is None and "ปน" in e.message for e in mixed.errors)


def test_csv_new_columns() -> None:
    assert {"pnl_amount", "cost_total", "mode"} <= set(CSV_COLUMNS)
    text = (
        "symbol,quantity,value,pnl_amount,cost_total,pnl_pct,mode\n"
        'PTT,1000,,"-1,250.50",,,qty_pnl_amount\n'
        "KBANK,100,,,14000,,\n"
        "AAPL,,2000,+400,,,VALUE_PNL_AMOUNT\n"
        "MSFT,,1000,,,-5%,\n"
    )
    rows, errors = parse_csv(text)
    assert errors == []
    assert rows[0].pnl_amount == D("-1250.5") and rows[0].mode == "qty_pnl_amount"
    assert rows[1].cost_total == D(14000) and rows[1].mode is None and infer_mode(rows[1]) == "cost_total"
    assert rows[2].mode == "value_pnl_amount" and rows[2].pnl_amount == D(400)
    assert rows[3].pnl_pct == D("-0.05")

    rows, errors = parse_csv("symbol,quantity,mode\nPTT,1,dime\nAAPL,1,\n")
    assert [r.symbol for r in rows] == ["AAPL"] and errors[0].field == "mode" and errors[0].index == 0


async def test_preview_values_every_row_and_warns_on_missing_price() -> None:
    market = FakeMarket(missing={"MSFT"})
    rows = [
        PositionInput(symbol="PTT", quantity=D(1000), avg_cost=D(30)),  # no price needed, fetched anyway
        PositionInput(symbol="AAPL", value=D(2000), pnl_pct=D("0.25")),
        PositionInput(symbol="MSFT", quantity=D(2), avg_cost=D(300)),  # no quote → warning, still ok
        PositionInput(symbol="PTT.BK", quantity=D(1)),  # duplicate → error row
        PositionInput(symbol="MSFT", value=D(10)),  # never reached: duplicate of row 2
    ]
    preview, records = await preview_positions(rows, data=market)
    ptt, aapl, msft, dup, dup2 = preview.rows
    assert preview.errors == []
    assert ptt.status == "ok" and ptt.mode == "qty_cost" and ptt.price == D("34.5")
    assert (ptt.market_value, ptt.cost_basis, ptt.unrealized_pnl) == (D(34500), D(30000), D(4500))
    assert (
        ptt.unrealized_pnl_pct == D("0.15") and ptt.currency == "THB" and str(ptt.price_as_of) == "2026-09-25"
    )
    assert ptt.symbol is not None and ptt.symbol.canonical == "PTT.BK"
    assert aapl.quantity == 10 and aapl.avg_cost == D(160) and aapl.unrealized_pnl == D(400)
    assert msft.status == "ok" and msft.price is None and msft.market_value is None
    assert (
        msft.cost_basis == D(600) and msft.errors[0].severity == "warning" and msft.errors[0].field == "price"
    )
    assert dup.status == "error" and dup.errors[0].field == "symbol" and dup.market_value is None
    assert dup2.status == "error"
    ids = {r.id for r in records}
    assert len(ids) == len(records)  # de-duplicated for the store
    for row in (ptt, aapl):
        assert row.provenance_id in ids
        for name in ("quantity", "avg_cost", "price", "market_value", "cost_basis", "unrealized_pnl"):
            assert row.provenance[name] in ids, (row.input_symbol, name)
    by_id = {r.id: r for r in records}
    assert by_id[aapl.provenance["quantity"]].calculation == "compute.portfolio.units_from_value@v1"
    assert by_id[ptt.provenance["avg_cost"]].kind == "user_input"


async def test_preview_book_errors_and_weight_rows() -> None:
    rows = [PositionInput(symbol="PTT", weight=D("0.7")), PositionInput(symbol="AAPL", weight=D("0.5"))]
    preview, records = await preview_positions(rows, data=FakeMarket())
    assert all(r.status == "ok" and r.weight is not None and r.quantity is None for r in preview.rows)
    assert preview.rows[0].provenance["weight"] in {r.id for r in records}
    assert preview.errors and preview.errors[0].index is None and "เกิน" in preview.errors[0].message
    too_many, _ = await preview_positions(
        [PositionInput(symbol="PTT", quantity=D(1))] * 201, data=FakeMarket()
    )
    assert too_many.rows == [] and too_many.errors[0].index is None


# -- paste from a broker app ----------------------------------------------------------------------


def test_parsed_holding_coerces_llm_numbers() -> None:
    h = ParsedHolding.model_validate(
        {
            "symbol": "PTT",
            "quantity": "1,000",
            "avg_cost": "฿32.50",
            "pnl_pct": "+7.69%",
            "pnl_amount": "(1,250.00)",
            "market": "th",
        }
    )
    assert h.quantity == 1000 and h.avg_cost == 32.5 and h.pnl_pct == pytest.approx(0.0769)
    assert h.pnl_amount == -1250 and h.market == "TH"
    assert ParsedHolding.model_validate({"symbol": "X", "pnl_pct": -7.69}).pnl_pct == pytest.approx(-0.0769)
    assert ParsedHolding.model_validate(
        {"symbol": "X", "pnl_pct": chr(0x2212) + "3.1%"}
    ).pnl_pct == pytest.approx(-0.031)
    assert ParsedHolding.model_validate({"symbol": "X", "pnl_pct": "N/A", "market": "HK"}).market is None


def test_paste_rows_map_to_one_mode_each() -> None:
    parsed = ParsedHoldings(
        rows=[
            # Streaming: qty, avg, last, %unrealized → avg wins, the % is dropped
            ParsedHolding(symbol="PTT", quantity=1000, avg_cost=32.5, pnl_pct=0.0769),
            # Dime!: value + %
            ParsedHolding(symbol="NVDA", value=549.96, pnl_pct=0.1452, market="US"),
            ParsedHolding(symbol="KBANK", quantity=100, pnl_amount=-500, pnl_pct=-0.03),
            ParsedHolding(symbol="AAPL", value=2000, pnl_amount=400, quantity=None),
            ParsedHolding(symbol="SCB", note="no numbers visible"),
            ParsedHolding(symbol="X" * 35, quantity=1),  # too long for a symbol → unparsed
        ],
        unparsed=["  ???  ", ""],
    )
    rows, unparsed, notes = to_position_inputs(parsed, market_hint="TH")
    ptt, nvda, kbank, aapl, scb = rows
    assert (ptt.mode, ptt.quantity, ptt.avg_cost, ptt.pnl_pct) == ("qty_cost", D(1000), D("32.5"), None)
    assert ptt.market is not None and ptt.market.value == "TH"  # the hint fills a missing market
    assert (nvda.mode, nvda.value, nvda.pnl_pct) == ("value_pnl_pct", D("549.96"), D("0.1452"))
    assert nvda.market is not None and nvda.market.value == "US"  # the text's market beats the hint
    assert (kbank.mode, kbank.pnl_amount, kbank.pnl_pct) == ("qty_pnl_amount", D(-500), None)
    assert aapl.mode == "value_pnl_amount"
    assert scb.mode is None and scb.quantity is None
    assert unparsed == ["???", "symbol=" + "X" * 35 + ", quantity=1.0"]
    assert notes[0].index == 4 and notes[0].severity == "warning"


def test_paste_prompt_is_versioned_and_fences_the_text() -> None:
    msgs = holdings_messages("PTT 1,000 32.50", "TH")
    assert PROMPT_ID == "portfolio_parse_holdings.v1"
    assert msgs[0].role == "system" and "never invent" in msgs[0].content.lower()
    assert "Streaming" in msgs[0].content and "Dime!" in msgs[0].content
    assert "PTT 1,000 32.50" in msgs[1].content and "Market hint: TH" in msgs[1].content
