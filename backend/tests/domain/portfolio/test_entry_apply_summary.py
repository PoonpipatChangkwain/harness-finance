"""Position entry (qty / value+% / weight), CSV import parsing, plan application, cross-book summary."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.models.portfolio import portfolio_hash
from app.domain.portfolio.analytics import analyze
from app.domain.portfolio.apply import apply_legs
from app.domain.portfolio.entry import PositionInput, parse_csv, resolve_positions
from app.domain.portfolio.summary import summarize
from tests.domain.portfolio.fakes import FakeMarket, book, position

D = Decimal


async def test_quantity_value_and_weight_modes() -> None:
    market = FakeMarket()
    rows = [
        PositionInput(symbol="PTT", quantity=D(1000), avg_cost=D(30)),
        PositionInput(symbol="AAPL", value=D("549.96"), pnl_pct=D("0.1452")),  # Dime!-style
    ]
    out = await resolve_positions(rows, data=market)
    assert out.errors == []
    ptt, aapl = out.positions
    assert ptt.symbol.canonical == "PTT.BK" and ptt.quantity == 1000
    assert aapl.quantity == D("549.96") / D(200)  # units at the latest price
    assert aapl.avg_cost is not None and aapl.avg_cost * aapl.quantity == pytest.approx(
        D("549.96") / D("1.1452")
    )
    kinds = {r.kind for r in out.records}
    assert {"user_input", "compute", "data"} <= kinds  # the user's numbers + the conversion + the price
    assert out.converted[0]["symbol"] == "AAPL"

    weights = await resolve_positions([PositionInput(symbol="PTT", weight=D("0.6"))], data=market)
    assert weights.errors == [] and weights.positions[0].declared_weight == D("0.6")
    assert weights.positions[0].quantity == 0


@pytest.mark.parametrize(
    ("rows", "field"),
    [
        ([PositionInput(symbol="../etc", quantity=D(1))], "symbol"),
        ([PositionInput(symbol="PTT", quantity=D(0))], "quantity"),
        ([PositionInput(symbol="PTT")], "quantity"),
        (
            [PositionInput(symbol="PTT", quantity=D(1)), PositionInput(symbol="PTT.BK", quantity=D(2))],
            "symbol",
        ),
        ([PositionInput(symbol="PTT", weight=D("1.5"))], "weight"),
        ([PositionInput(symbol="PTT", value=D(100), quantity=D(1))], "value"),
        (
            [PositionInput(symbol="PTT", weight=D("0.7")), PositionInput(symbol="AAPL", weight=D("0.5"))],
            "weight",
        ),
        (
            [PositionInput(symbol="PTT", weight=D("0.5")), PositionInput(symbol="AAPL", quantity=D(1))],
            "weight",
        ),
    ],
)
async def test_invalid_rows_are_reported_per_row(rows: list[PositionInput], field: str) -> None:
    out = await resolve_positions(rows, data=FakeMarket())
    assert out.errors and out.errors[-1].field == field


async def test_value_entry_without_a_price_is_an_error_not_a_guess() -> None:
    out = await resolve_positions(
        [PositionInput(symbol="MSFT", value=D(1000), pnl_pct=D("0.1"))], data=FakeMarket(missing={"MSFT"})
    )
    assert out.positions == [] and out.errors[0].field == "value" and "MSFT" in out.errors[0].message


def test_csv_parse_formats_and_errors() -> None:
    rows, errors = parse_csv('symbol,quantity,avg_cost,weight,note\nPTT,1000,"30.5",,core\nAAPL,10,$150,,\n')
    assert errors == [] and [r.symbol for r in rows] == ["PTT", "AAPL"]
    assert rows[1].avg_cost == D(150) and rows[0].note == "core"

    rows, errors = parse_csv("symbol,weight\nPTT,40%\nAAPL,35\nKBANK,0.1\n")
    assert [r.weight for r in rows] == [D("0.4"), D("0.35"), D("0.1")]

    rows, errors = parse_csv("symbol,value,pnl_pct\nNVDA,549.96,+14.52%\nMSFT,100,-5\n")
    assert rows[0].pnl_pct == D("0.1452") and rows[1].pnl_pct == D("-0.05")

    _, errors = parse_csv("ticker,qty\nPTT,1\n")
    assert errors[0].field == "symbol"  # no symbol column
    rows, errors = parse_csv("symbol,quantity,foo\nPTT,abc,\n")
    assert rows == [] and {e.index for e in errors} == {None, 0}  # unknown column + bad number


async def test_csv_weight_and_quantity_mixed_is_refused() -> None:
    rows, errors = parse_csv("symbol,quantity,weight\nPTT,1000,\nAAPL,,0.3\n")
    assert errors == []
    out = await resolve_positions(rows, data=FakeMarket())
    assert any(e.index is None and "ปน" in e.message for e in out.errors)


def test_apply_legs_blends_cost_removes_zero_and_moves_cash() -> None:
    pf = book([position("PTT", "1000", "30"), position("AAPL", "10", "150")], cash="50000")
    legs = [
        {"symbol": "PTT.BK", "shares": 200, "value": 6900, "price": 34.5},
        {"symbol": "AAPL", "shares": -10, "value": -72000, "price": 200},
        {"symbol": "MSFT", "shares": 3, "value": 43200, "price": 400},
        {"symbol": "KBANK.BK", "shares": 0, "value": 0, "price": 150},
    ]
    v = apply_legs(pf, legs).value
    by = {p.symbol.canonical: p for p in v["positions"]}
    assert set(by) == {"PTT.BK", "MSFT"}  # AAPL sold out, KBANK untouched (hold)
    assert by["PTT.BK"].quantity == 1200
    assert by["PTT.BK"].avg_cost == (D(1000) * 30 + D(200) * D("34.5")) / 1200
    assert by["MSFT"].avg_cost == D(400)
    assert v["cash_after"] == D(50000) - D(6900) + D(72000) - D(43200)
    assert [d["symbol"] for d in v["diff"]] == ["PTT.BK", "AAPL", "MSFT"]


async def test_portfolio_hash_ignores_name_but_not_holdings() -> None:
    a = book([position("PTT", "1000", "30")])
    assert portfolio_hash(a) == portfolio_hash(a.model_copy(update={"name": "renamed"}))
    assert portfolio_hash(a) != portfolio_hash(book([position("PTT", "1001", "30")]))


async def test_summary_merges_symbols_across_books_and_filters_market() -> None:
    market = FakeMarket()
    one = await analyze(
        book([position("PTT", "1000", "30"), position("AAPL", "10", "150")], id="a"), data=market
    )
    two = await analyze(
        book([position("AAPL", "5", "180")], cash="0", id="b", base_currency="USD"), data=market
    )
    fx = {"THB": (D(1), None), "USD": (D(36), "prov_fx")}
    out, records = summarize([({"id": "a"}, one), ({"id": "b"}, two)], currency="THB", fx=fx)
    aapl = next(r for r in out["assets"] if r["symbol"] == "AAPL")
    assert aapl["quantity"] == 15 and aapl["portfolios"] == ["a", "b"]
    assert aapl["value"] == pytest.approx(72_000 + 5 * 200 * 36)
    assert out["total_value"] == pytest.approx(156_500 + 36_000)
    assert sum(p["weight"] for p in out["portfolios"]) == pytest.approx(1.0)
    assert out["provenance_id"] == records[0].id and "prov_fx" in records[0].inputs

    th, _ = summarize([({"id": "a"}, one), ({"id": "b"}, two)], currency="THB", fx=fx, market="TH")
    assert [r["symbol"] for r in th["assets"]] == ["PTT.BK"] and th["total_value"] == pytest.approx(34_500)
