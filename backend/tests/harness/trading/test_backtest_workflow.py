"""Backtest end to end with FakeLLM (Phase 15.3): cells → trading runs → simulation."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest

from app.api.main import app
from app.domain.compute.backtest import SimConfig
from app.domain.data.base import Capability, DataRequest, DataResult
from app.domain.data.router import DataRouter
from app.domain.models.market import OHLCVBar, OHLCVFrame
from app.domain.workflows.backtest import BacktestConfig, cell_decision
from app.harness.llm.fake import FakeLLM
from app.harness.runtime.services import HarnessServices
from app.harness.storage import backtest_repo, runs_repo
from app.harness.workflow import backtest as bt
from app.settings import Settings
from tests.harness.trading.helpers import SNAPSHOTS, events_of, happy_script, of, trading_services

pytestmark = pytest.mark.usefixtures("db")

TODAY = date.today()
START = TODAY - timedelta(days=40)
END = TODAY - timedelta(days=12)


def _bars() -> list[OHLCVBar]:
    """Weekdays from 500 days back; flat at 34.5 until START, then +0.25 a session (TP 37 reachable)."""
    out: list[OHLCVBar] = []
    px = Decimal("34.5")
    d = TODAY - timedelta(days=500)
    while d < TODAY:
        if d.weekday() < 5:
            if d > START:
                px += Decimal("0.25")
            ts = datetime.combine(d, datetime.min.time(), tzinfo=UTC)
            out.append(
                OHLCVBar(
                    ts=ts, open=px, high=px + Decimal("0.3"), low=px - Decimal("0.3"), close=px, volume=1
                )
            )
        d += timedelta(days=1)
    return out


class _Prices:
    id = "yfinance"  # takes yfinance's place in the routing order
    capabilities = frozenset({Capability.PRICES})

    def __init__(self) -> None:
        self.calls = 0

    def is_configured(self) -> bool:
        return True

    def supports(self, cap: Capability, symbol: Any) -> bool:
        return True

    async def fetch(self, cap: Capability, req: DataRequest) -> DataResult:
        self.calls += 1
        frame = OHLCVFrame(symbol=req.symbol.canonical, interval="1d", bars=_bars())  # type: ignore[union-attr]
        return DataResult(provider=self.id, capability=cap, data=frame, status="ok")


def _services(settings: Settings, llm: FakeLLM, prices: _Prices) -> HarnessServices:
    services = trading_services(settings, llm)
    services.data_router = DataRouter({prices.id: prices})  # type: ignore[dict-item]
    return services


def _cfg(**kw: Any) -> dict[str, Any]:
    base = {
        "name": "test", "symbols": ["PTT", "AOT"], "start_date": START.isoformat(),
        "end_date": END.isoformat(), "horizon": "position", "cadence": 10, "analysts": ["market"],
        "concurrency": 1, "sim": {"initial_capital": "1000000", "slippage_bps": "0"},
    }  # fmt: skip
    return {**base, **kw}


def _script(cells: int) -> list[Any]:
    return [item for _ in range(cells) for item in [*happy_script(1), "narrative"]]


@pytest.fixture(autouse=True)
def _aot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(SNAPSHOTS, "AOT.BK", {"close": 34.5, "atr14": 0.6})
    bt._MARKETS.clear()


async def _run(services: HarnessServices, cfg: dict[str, Any]) -> str:
    run_id = await services.runs.start("backtest", cfg, thread_id="backtest_t")
    await services.runs.wait(run_id)
    return run_id


def _n_cells(cfg: dict[str, Any]) -> int:
    c = BacktestConfig.model_validate(cfg)
    days = [d.ts.date() for d in _bars() if c.start_date <= d.ts.date() <= c.end_date]
    return len(days[:: c.sessions_between]) * len(c.symbols)


async def test_backtest_runs_every_cell_and_simulates(settings: Settings) -> None:
    cfg = _cfg()
    n = _n_cells(cfg)
    assert n >= 4
    llm = FakeLLM(_script(n))
    prices = _Prices()
    services = _services(settings, llm, prices)
    run_id = await _run(services, cfg)

    run = await runs_repo.get_run(run_id)
    assert run is not None and run.status == "completed", run and run.error
    assert prices.calls == 2  # one preload per symbol (position horizon: daily only)
    cells = await backtest_repo.cells(run_id)
    assert len(cells) == n and all(c.status == "done" for c in cells)
    # every cell is its own auto-approved trading run, tagged with the backtest
    child = await runs_repo.get_run(cells[0].run_id or "")
    assert child is not None and child.input["backtest_id"] == run_id
    assert child.input["analysis_date"] == cells[0].day and child.input["use_portfolio"] is False
    assert cells[0].decision is not None and cells[0].decision["side"] == "long"
    assert cells[0].decision["stop"] == pytest.approx(33.3)  # 34.5 - 2 x ATR 0.6

    out = run.output or {}
    assert out["cells"] == {"total": n, "done": n, "cached": 0, "failed": 0}
    m = out["metrics"]
    # a rising market, long at the next open with TP 37: the book ends above its capital
    assert Decimal(m["final_equity"]) > Decimal(1_000_000) and m["trades"] >= 1
    assert m["benchmark_return"] > 0

    events = await events_of(run_id)
    phases = [p["phase"] for p in of(events, "backtest.progress")]
    assert phases[0] == "preload" and phases[-1] == "done" and "simulate" in phases
    assert {c["status"] for c in of(events, "backtest.cell")} == {"running", "done"}
    [sim_ev] = of(events, "backtest.sim")
    assert sim_ev["sim_id"] == out["sim_id"]

    # re-simulate with 10k THB: 1 lot of 100 x 34.5 > the 20% weight cap → nothing trades, no LLM call
    calls = len(llm.calls)
    small = await bt.simulate_backtest(services, run_id, BacktestConfig.model_validate(cfg),
                                       SimConfig(initial_capital=Decimal(10_000)))  # fmt: skip
    assert len(llm.calls) == calls
    assert small.metrics["trades"] == 0 and small.metrics["skipped"].get("below_one_lot", 0) >= 1
    assert "baseline" in small.result and small.result["benchmark"]
    assert len(await backtest_repo.sims(run_id)) == 2


async def test_same_settings_reuse_decisions_and_resume_reruns_only_the_lost_cell(settings: Settings) -> None:
    cfg = _cfg()
    n = _n_cells(cfg)
    llm = FakeLLM(_script(n))
    services = _services(settings, llm, _Prices())
    first = await _run(services, cfg)

    # a crash left one cell running: resume re-runs that cell only
    cells = await backtest_repo.cells(first)
    lost = cells[1]
    await backtest_repo.update_cell(lost.id, status="running", decision=None)
    await runs_repo.set_status(first, "interrupted")
    llm.push(*_script(1))
    await services.runs.resume(first)
    await services.runs.wait(first)
    again = {c.id: c for c in await backtest_repo.cells(first)}
    assert again[lost.id].status == "done" and again[lost.id].run_id != lost.run_id
    assert all(again[c.id].run_id == c.run_id for c in cells if c.id != lost.id)
    assert len(await backtest_repo.sims(first)) == 2  # the resume simulated again

    # a second backtest with the same settings pays for nothing (the script is empty)
    second = await _run(services, cfg)
    run = await runs_repo.get_run(second)
    assert run is not None and run.status == "completed", run and run.error
    assert (run.output or {})["cells"]["cached"] == n
    reused = await backtest_repo.cells(second)
    assert {c.run_id for c in reused} == {c.run_id for c in again.values()}


async def test_a_failed_cell_does_not_stop_the_backtest(settings: Settings) -> None:
    cfg = _cfg(symbols=["PTT"])
    n = _n_cells(cfg)
    llm = FakeLLM(_script(n - 1))  # the last cell's run runs out of script → that run fails
    services = _services(settings, llm, _Prices())
    run_id = await _run(services, cfg)
    run = await runs_repo.get_run(run_id)
    assert run is not None and run.status == "completed"
    assert (run.output or {})["cells"]["failed"] == 1
    failed = [c for c in await backtest_repo.cells(run_id) if c.status == "failed"]
    assert failed and failed[0].error


async def test_budget_stops_new_cells(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _cfg(symbols=["PTT"], budget_usd=0.001)
    llm = FakeLLM(_script(_n_cells(cfg)))
    services = _services(settings, llm, _Prices())
    # every finished trading run reports $0.01: the first one exhausts the budget
    real_get = runs_repo.get_run

    async def priced(run_id: str) -> Any:
        rec = await real_get(run_id)
        if rec is not None and rec.workflow == "trading":
            rec.usage = {**(rec.usage or {}), "cost_usd": 0.01}
        return rec

    monkeypatch.setattr(bt.runs_repo, "get_run", priced)
    run_id = await _run(services, cfg)
    out = (await real_get(run_id)).output or {}  # type: ignore[union-attr]
    assert out["budget_hit"] is True and out["cells"]["done"] == 1 and out["pending"] >= 1
    assert "budget" in [e["kind"] for e in of(await events_of(run_id), "guard.limit")]


async def test_backtest_api(settings: Settings, install_services: Any) -> None:
    cfg = _cfg(symbols=["PTT"])
    n = _n_cells(cfg)
    services = install_services(_services(settings, FakeLLM(_script(n)), _Prices()))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=20
    ) as c:
        est = (await c.post("/api/backtests/estimate", json=cfg)).json()
        assert est["cells"] >= n and est["cached"] == 0 and est["est_cost_usd"] is None  # no run history

        bad = await c.post("/api/backtests", json={**cfg, "start_date": TODAY.isoformat()})
        assert bad.status_code == 422  # a backtest needs a past start
        user = await c.post("/api/trade/runs", json={"symbol": "PTT", "backtest_id": "x"})
        assert user.status_code == 400  # auto-approval is never a user's switch

        bid = (await c.post("/api/backtests", json=cfg)).json()["backtest_id"]
        await services.runs.wait(bid)
        detail = (await c.get(f"/api/backtests/{bid}")).json()
        assert detail["status"] == "completed" and len(detail["cells"]) == n
        assert detail["latest_sim"]["result"]["equity_curve"] and detail["final_equity"]
        assert [b["backtest_id"] for b in (await c.get("/api/backtests")).json()["backtests"]] == [bid]

        sim = (await c.post(f"/api/backtests/{bid}/simulate", json={"initial_capital": "500000"})).json()
        assert (
            sim["config"]["initial_capital"] == "500000" and sim["metrics"]["initial_capital"] == "500000.00"
        )
        got = await c.get(f"/api/backtests/{bid}/sims/{sim['sim_id']}")
        assert got.status_code == 200 and got.json()["result"]["trades"] == sim["result"]["trades"]
        # a USD book: THB positions are converted with THBUSD=X (the fake market serves any symbol)
        usd = await c.post(f"/api/backtests/{bid}/simulate", json={"base_currency": "USD"})
        assert usd.status_code == 200 and usd.json()["metrics"]["currency"] == "USD"

        assert (await c.delete(f"/api/backtests/{bid}")).status_code == 200
        assert (await c.get(f"/api/backtests/{bid}")).status_code == 404


def test_cell_decision_maps_run_endings() -> None:
    assert cell_decision("failed", None)["side"] == "flat"
    no_trade = {"status": "no_trade", "state": {"outcome": {"status": "no_trade", "reason": "PM: no"}}}
    assert cell_decision("completed", no_trade) | {} == cell_decision("completed", no_trade)
    assert cell_decision("completed", no_trade)["side"] == "flat"
    approved = {
        "status": "approved",
        "state": {
            "outcome": {"status": "approved"},
            "final_decision": {"final_size_pct": 0.5, "rating": "BUY"},
            "instrument": {"asset_type": "stock", "lot_size": 100, "currency": "THB"},
            "decision_record": {"card": {"side": "long", "order_type": "limit", "entry": 34.0, "stop": 33.0,
                                         "targets": [36.0], "time_stop_bars": 10, "currency": "THB"}},
        },
    }  # fmt: skip
    d = cell_decision("completed", approved)
    assert (d["side"], d["order_price"], d["size_fraction"], d["lot_size"]) == ("long", 34.0, 0.5, 100)
