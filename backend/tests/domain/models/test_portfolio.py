from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.domain.models.portfolio import Portfolio, PortfolioContext, Position, portfolio_context
from app.domain.models.symbol import normalize_symbol


def _pos(symbol: str, qty: str) -> Position:
    return Position(symbol=normalize_symbol(symbol), quantity=Decimal(qty))


def test_portfolio_context_not_provided() -> None:
    ctx = portfolio_context(None)
    assert ctx.state == "not_provided"
    assert ctx.portfolio is None


def test_portfolio_context_flat() -> None:
    pf = Portfolio(id="pf_1", name="test", positions=[], updated_at=datetime.now(UTC))
    ctx = portfolio_context(pf)
    assert ctx.state == "flat"
    assert ctx.portfolio is pf


def test_portfolio_context_positions() -> None:
    pf = Portfolio(
        id="pf_1",
        name="test",
        positions=[_pos("PTT", "100")],
        updated_at=datetime.now(UTC),
    )
    ctx = portfolio_context(pf)
    assert ctx.state == "positions"


def test_portfolio_context_states_are_distinct() -> None:
    states = {
        portfolio_context(None).state,
        portfolio_context(Portfolio(id="a", name="a", positions=[], updated_at=datetime.now(UTC))).state,
        portfolio_context(
            Portfolio(id="b", name="b", positions=[_pos("AAPL", "1")], updated_at=datetime.now(UTC))
        ).state,
    }
    assert states == {"not_provided", "flat", "positions"}
    assert isinstance(PortfolioContext(state="flat", portfolio=None), PortfolioContext)


def test_cash_none_is_not_zero() -> None:
    pf_no_cash = Portfolio(id="a", name="a", cash=None, positions=[], updated_at=datetime.now(UTC))
    pf_zero_cash = Portfolio(id="b", name="b", cash=Decimal(0), positions=[], updated_at=datetime.now(UTC))
    assert pf_no_cash.cash is None
    assert pf_zero_cash.cash == Decimal(0)
    assert pf_no_cash.cash != pf_zero_cash.cash
