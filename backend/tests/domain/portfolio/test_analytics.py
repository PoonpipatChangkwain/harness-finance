"""Portfolio analytics (phase-12 §12.2, §12.6) against fixture prices — numbers checked by hand."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from app.domain.models.provenance import Provenanced
from app.domain.portfolio.analytics import PortfolioAnalytics, analyze, summary_text
from tests.domain.portfolio.fakes import FakeMarket, book, position

# PTT.BK 1000 @ 30 (last 34.5, prev 34), AAPL 10 @ 150 USD (last 200, prev 198), USDTHB 36, cash 50,000 THB
MIXED = [position("PTT", "1000", "30"), position("AAPL", "10", "150")]


def _ids(a: PortfolioAnalytics) -> set[str]:
    return {r.id for r in a.records}


def _pv_fields(obj: Any) -> list[Provenanced[float]]:
    return [v for v in vars(obj).values() if isinstance(v, Provenanced)]


async def test_mixed_th_us_cash_values_weights_pnl_by_hand() -> None:
    a = await analyze(book(MIXED), data=FakeMarket())
    assert a.base_currency == "THB" and not a.weights_declared_only
    assert a.fx["USD"].value == 36.0
    ptt, aapl = a.holding("PTT.BK"), a.holding("AAPL")
    assert ptt is not None and aapl is not None
    assert ptt.market_value is not None and ptt.market_value.value == pytest.approx(34_500)
    assert aapl.market_value is not None and aapl.market_value.value == pytest.approx(72_000)  # 10 x 200 x 36
    assert aapl.market_value_local is not None and aapl.market_value_local.value == pytest.approx(2_000)
    assert a.total_value is not None and a.total_value.value == pytest.approx(156_500)
    assert a.invested_value is not None and a.invested_value.value == pytest.approx(106_500)
    assert a.weights["PTT.BK"] == pytest.approx(34_500 / 156_500)
    assert a.weights["AAPL"] == pytest.approx(72_000 / 156_500)
    assert a.weights["CASH"] == pytest.approx(50_000 / 156_500)
    assert sum(a.weights.values()) == pytest.approx(1.0)
    # unrealized: PTT (34.5-30)x1000 = 4,500; AAPL (200-150)x10x36 = 18,000 on cost 30,000 + 54,000
    assert ptt.unrealized_pnl is not None and ptt.unrealized_pnl.value == pytest.approx(4_500)
    assert aapl.unrealized_pnl is not None and aapl.unrealized_pnl.value == pytest.approx(18_000)
    assert aapl.unrealized_pnl_pct is not None and aapl.unrealized_pnl_pct.value == pytest.approx(50 / 150)
    assert a.unrealized_pnl is not None and a.unrealized_pnl.value == pytest.approx(22_500)
    assert a.cost_basis is not None and a.cost_basis.value == pytest.approx(84_000)
    assert a.unrealized_pnl_pct is not None and a.unrealized_pnl_pct.value == pytest.approx(22_500 / 84_000)
    # day: PTT +0.5x1000 = 500; AAPL +2x10x36 = 720 over prev 34,000 + 71,280
    assert a.day_pnl is not None and a.day_pnl.value == pytest.approx(1_220)
    assert a.day_pnl_pct is not None and a.day_pnl_pct.value == pytest.approx(1_220 / 105_280)
    assert a.warnings == []


async def test_every_number_has_a_provenance_record_in_the_result() -> None:
    a = await analyze(book(MIXED), data=FakeMarket())
    ids = _ids(a)
    numbers = _pv_fields(a) + [pv for h in a.holdings for pv in _pv_fields(h)] + list(a.fx.values())
    assert a.risk is not None
    numbers += _pv_fields(a.risk)
    assert len(numbers) > 25
    assert all(pv.provenance_id in ids for pv in numbers)
    assert a.weights_provenance_id in ids and a.concentration is not None
    assert a.concentration.provenance_id in ids
    assert set(a.exposure_provenance.values()) <= ids
    assert a.risk.correlation_provenance_id in ids and a.risk.contribution_provenance_id in ids
    calcs = {r.calculation for r in a.records if r.kind == "compute"}
    assert {"compute.portfolio.market_values@v1", "compute.portfolio.weights@v1",
            "compute.portfolio.pnl_summary@v1", "compute.returns.risk_contribution@v1"} <= calcs  # fmt: skip
    # records are for the harness to persist, never serialized to the client
    assert "records" not in a.model_dump(mode="json")


async def test_concentration_exposure_and_risk() -> None:
    a = await analyze(book(MIXED), data=FakeMarket())
    c = a.concentration
    assert c is not None
    w1, w2 = 34_500 / 156_500, 72_000 / 156_500
    assert c.hhi == pytest.approx(w1**2 + w2**2)
    assert c.top1 == pytest.approx(w2) and c.effective_n == pytest.approx(1 / (w1**2 + w2**2))
    assert a.exposure["currency"]["USD"] == pytest.approx(w2)
    assert a.exposure["market"]["TH"] == pytest.approx(w1) and a.exposure["market"]["CASH"] > 0
    assert set(a.exposure["sector"]) == {"Energy", "Technology", "CASH"}
    r = a.risk
    assert r is not None and r.benchmark == "^SET.BK" and r.observations > 200
    assert 0 < r.volatility.value < 1 and r.max_drawdown.value <= 0
    assert r.var_95.value > 0 and r.cvar_95.value >= r.var_95.value
    assert r.beta is not None
    assert sum(r.contribution.values()) == pytest.approx(1.0)
    assert r.correlation["AAPL"]["AAPL"] == pytest.approx(1.0)
    text = summary_text(a)
    assert "PTT.BK" in text and "[^prov_" in text


async def test_declared_weights_only_mode() -> None:
    positions = [
        position("PTT", "0", declared_weight=Decimal("0.40")),
        position("AAPL", "0", declared_weight=Decimal("0.35")),
    ]
    market = FakeMarket()
    a = await analyze(book(positions, cash=None), data=market)
    assert a.weights_declared_only
    assert a.total_value is None and a.unrealized_pnl is None  # no values or P&L
    assert a.weights == pytest.approx({"PTT.BK": 0.40, "AAPL": 0.35, "CASH": 0.25})
    assert all(h.status == "declared" for h in a.holdings)
    assert a.risk is not None  # the declared weights drive the risk numbers
    assert not [c for c in market.calls if c[0] == "quote"]  # no pricing needed
    assert any(w.code == "weights_declared_only" for w in a.warnings)


async def test_unpriced_symbol_warns_and_its_weight_is_not_guessed() -> None:
    market = FakeMarket(missing={"MSFT"})
    a = await analyze(book([*MIXED, position("MSFT", "5", "300")]), data=market)
    msft = a.holding("MSFT")
    assert msft is not None and msft.status == "no_price"
    assert msft.weight is None and msft.market_value is None
    assert "MSFT" not in a.weights
    codes = {w.code for w in a.warnings}
    assert {"price_unavailable", "weights_partial", "history_unavailable"} <= codes
    # the priced book is still fully analysed
    assert a.total_value is not None and a.total_value.value == pytest.approx(156_500)
    assert sum(a.weights.values()) == pytest.approx(1.0)


async def test_missing_fx_leaves_foreign_holdings_unvalued() -> None:
    market = FakeMarket(missing={"USDTHB=X"})
    a = await analyze(book(MIXED), data=market)
    aapl = a.holding("AAPL")
    assert aapl is not None and aapl.status == "no_fx" and aapl.weight is None
    assert {"fx_unavailable", "fx_missing_for_symbol"} <= {w.code for w in a.warnings}
    assert a.total_value is not None and a.total_value.value == pytest.approx(84_500)


async def test_short_benchmark_history_warns_instead_of_a_silent_beta_gap() -> None:
    class ShortSet(FakeMarket):
        async def get(self, cap: Any, req: Any) -> Any:
            result = await super().get(cap, req)
            if req.symbol.canonical == "^SET.BK" and cap.value == "prices":
                result.data.bars = result.data.bars[-1:]  # what Yahoo actually serves for ^SET.BK
            return result

    a = await analyze(book(MIXED), data=ShortSet())
    assert a.risk is not None and a.risk.beta is None
    assert any(w.code == "beta_unavailable" for w in a.warnings)


async def test_cash_not_given_is_flagged_not_zeroed() -> None:
    a = await analyze(book(MIXED, cash=None), data=FakeMarket())
    assert a.cash is None
    assert a.weights["CASH"] == 0
    assert any(w.code == "cash_not_provided" for w in a.warnings)


async def test_usd_base_uses_spy_benchmark_and_thb_fx() -> None:
    quotes = {"PTT.BK": ("34.5", "34.0"), "AAPL": ("200", "198"), "THBUSD=X": ("0.0278", "0.0278")}
    a = await analyze(book(MIXED, cash="1000", base_currency="USD"), data=FakeMarket(quotes))
    assert a.risk is not None and a.risk.benchmark == "SPY"
    ptt = a.holding("PTT.BK")
    assert ptt is not None and ptt.market_value is not None
    assert ptt.market_value.value == pytest.approx(34_500 * 0.0278)
