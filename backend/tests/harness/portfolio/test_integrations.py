"""P12 integrations: trading reads the saved book (3 states, §12.4), the research "use portfolio
data" toggle (§12.7), portfolio chat sessions, and the weekly report job with catch-up."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from app.harness.llm.fake import FakeLLM
from app.harness.portfolio.scheduler import MEMORY_KEY, MEMORY_NS, WeeklyReportJob, last_slot, next_slot
from app.harness.portfolio.service import PortfolioService
from app.harness.portfolio.tools import portfolio_analytics_tool
from app.harness.storage import portfolio_repo, runs_repo, sessions_repo
from app.settings import Settings
from tests.domain.portfolio.fakes import FakeMarket, install
from tests.harness.portfolio.helpers import client, portfolio_services, save_book
from tests.harness.research.helpers import CitingFakeLLM, events_of, of, plan, research_services
from tests.harness.trading.helpers import (
    analyst_turns,
    debate_turns,
    happy_script,
    pm,
    research_plan,
    risk_turns,
    trading_services,
)
from tests.harness.trading.helpers import proposal as trade_proposal

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture(autouse=True)
def market(monkeypatch: pytest.MonkeyPatch) -> FakeMarket:
    return install(monkeypatch)


# -- trading uses the saved book ----------------------------------------------------------------


async def _trade(install: Any, settings: Settings, script: list[Any], **inp: Any) -> dict[str, Any]:
    services = install(trading_services(settings, FakeLLM(script)))
    async with client() as c:
        run_id = (await c.post("/api/trade/runs", json={"symbol": "PTT", **inp})).json()["run_id"]
        await services.runs.wait(run_id)
        detail: dict[str, Any] = (await c.get(f"/api/trade/runs/{run_id}")).json()
    return detail


async def test_trading_with_positions_sizes_on_equity_and_checks_combined_weight(
    settings: Settings, install_services: Any
) -> None:
    await save_book(rows=[("PTT", "10000", "30")], cash="100000")  # 345,000 + 100,000 THB
    blocked = [
        *analyst_turns(4), *debate_turns(), research_plan(), trade_proposal(), *risk_turns(), pm(),
        trade_proposal(), *risk_turns(), pm(), "narrative",
    ]  # fmt: skip
    detail = await _trade(install_services, settings, blocked)
    ctx = detail["portfolio_ctx"]
    assert ctx["state"] == "positions" and ctx["portfolio_id"] == "pf_default"
    assert ctx["equity"] == pytest.approx(445_000) and ctx["cash"] == pytest.approx(100_000)
    assert ctx["symbol_weight"] == pytest.approx(345_000 / 445_000)
    assert detail["decision"]["sizing"]["equity"] == pytest.approx(445_000)  # sized on the real book
    # the gate adds the new trade to the 77.5% already held → blocked twice → rejected
    weight_rule = next(r for r in detail["policy"]["results"] if r["rule_id"] == "max_position_weight")
    assert not weight_rule["passed"] and weight_rule["values"]["weight"] >= 345_000 / 445_000
    assert detail["outcome"]["status"] == "rejected"


async def test_trading_flat_book_and_not_provided(settings: Settings, install_services: Any) -> None:
    await save_book(rows=[], cash="1000000")
    flat = await _trade(install_services, settings, [*happy_script(4)])
    assert flat["portfolio_ctx"]["state"] == "flat"
    assert flat["portfolio_ctx"]["equity"] == pytest.approx(1_000_000)
    assert flat["decision"]["sizing"]["equity"] == pytest.approx(1_000_000)

    # use_portfolio=false is "not provided" even though a book exists — never "flat"
    off = await _trade(install_services, settings, [*happy_script(4)], use_portfolio=False)
    assert off["portfolio_ctx"] == {"state": "not_provided"}


async def test_trading_context_converts_into_the_instrument_currency(settings: Settings) -> None:
    await save_book(rows=[], cash="360000")  # THB book
    ctx = await PortfolioService().trading_context("AAPL")
    assert ctx["state"] == "flat" and ctx["currency"] == "USD" and ctx["fx_rate"] == 36
    assert ctx["equity"] == pytest.approx(10_000) and ctx["cash"] == pytest.approx(10_000)
    assert await PortfolioService().trading_context("AAPL", "pf_none") == {"state": "not_provided"}


# -- research toggle + portfolio chat -----------------------------------------------------------


def _research(settings: Settings, llm: FakeLLM) -> Any:
    return research_services(
        settings, [], llm=llm, extra_tools=[portfolio_analytics_tool(PortfolioService())]
    )


async def test_research_toggle_preloads_the_selected_book(settings: Settings, install_services: Any) -> None:
    await save_book()
    llm = CitingFakeLLM([plan()], default="brief {cite}")
    services = install_services(_research(settings, llm))
    async with client() as c:
        sid = (await c.post("/api/sessions", json={"kind": "research"})).json()["id"]
        bad = await c.post(f"/api/sessions/{sid}/messages", json={"text": "x", "portfolio_ids": ["pf_zzz"]})
        assert bad.status_code == 400
        resp = await c.post(
            f"/api/sessions/{sid}/messages", json={"text": "พอร์ตฉันเสี่ยงไหม", "portfolio_ids": ["pf_default"]}
        )
        assert resp.status_code == 200, resp.text
        run_id = resp.json()["run_id"]
    await services.runs.wait(run_id)
    events = await events_of(run_id)
    started = [p for p in of(events, "tool.started") if p["tool"] == "get_portfolio_analytics"]
    assert started and started[0]["args"] == {"portfolio_id": "pf_default"}
    finished = [p for p in of(events, "tool.finished") if p["tool"] == "get_portfolio_analytics"]
    assert finished[0]["ok"] and finished[0]["provenance_ids"]
    agent_calls = [x for x in llm.calls if x.purpose == "agent"]
    assert '<portfolio id="pf_default">' in agent_calls[0].messages[-1].content
    assert "get_portfolio_analytics" in agent_calls[0].tools
    message = (await sessions_repo.list_messages(sid))[0]
    assert message.meta["portfolio_ids"] == ["pf_default"]  # the "used portfolio" chip


async def test_research_without_toggle_never_sees_portfolios(
    settings: Settings, install_services: Any
) -> None:
    await save_book()
    llm = CitingFakeLLM([plan()], default="brief")
    services = install_services(_research(settings, llm))
    async with client() as c:
        sid = (await c.post("/api/sessions", json={"kind": "research"})).json()["id"]
        run_id = (await c.post(f"/api/sessions/{sid}/messages", json={"text": "PTT ราคา"})).json()["run_id"]
    await services.runs.wait(run_id)
    assert all("get_portfolio_analytics" not in x.tools for x in llm.calls if x.purpose == "agent")
    assert not [
        p for p in of(await events_of(run_id), "tool.started") if p["tool"] == "get_portfolio_analytics"
    ]


async def test_portfolio_chat_sees_its_books(settings: Settings, install_services: Any) -> None:
    llm = CitingFakeLLM([plan()], default="brief")
    services = install_services(_research(settings, llm))
    async with client() as c:
        sid = (await c.post("/api/sessions", json={"kind": "portfolio"})).json()["id"]
        assert (
            await c.post(f"/api/sessions/{sid}/messages", json={"text": "x"})
        ).status_code == 400  # no book
        await save_book()
        run_id = (await c.post(f"/api/sessions/{sid}/messages", json={"text": "หาจุดอ่อน"})).json()["run_id"]
    await services.runs.wait(run_id)
    run = await runs_repo.get_run(run_id)
    assert run is not None and run.input["portfolio_ids"] == ["pf_default"]


# -- weekly report job --------------------------------------------------------------------------


def test_slots_are_friday_0300_bangkok() -> None:
    wed = datetime(2026, 9, 23, 12, tzinfo=UTC)  # Wednesday
    assert last_slot(wed) == datetime(2026, 9, 17, 20, tzinfo=UTC)  # Fri 18 Sep 03:00 +07
    assert next_slot(wed) == datetime(2026, 9, 24, 20, tzinfo=UTC)  # Fri 25 Sep 03:00 +07
    just_before = datetime(2026, 9, 24, 19, 59, tzinfo=UTC)
    assert last_slot(just_before) == datetime(2026, 9, 17, 20, tzinfo=UTC)
    at = datetime(2026, 9, 24, 20, 0, tzinfo=UTC)
    assert last_slot(at) == at


async def test_weekly_job_snapshots_and_starts_a_report_run(settings: Settings) -> None:
    services = portfolio_services(settings, FakeLLM(default="ok"))
    await save_book()
    await save_book("pf_empty", rows=[], name="empty")
    results = await services.weekly.run_once(kind="manual")
    assert [r["portfolio_id"] for r in results] == ["pf_default"]  # books without positions are skipped
    run_id = results[0]["run_id"]
    await services.runs.wait(run_id)
    run = await runs_repo.get_run(run_id)
    assert run is not None and run.workflow == "research"
    assert run.input["skill"] == "portfolio-risk-review" and run.input["portfolio_ids"] == ["pf_default"]
    session = await sessions_repo.get_session(results[0]["session_id"])
    assert session is not None and session.kind == "portfolio" and "รายสัปดาห์" in (session.title or "")
    snaps = await portfolio_repo.list_snapshots("pf_default")
    assert len(snaps) == 1 and snaps[0].summary["total_value"]["value"] == pytest.approx(156_500)
    # a full "run now" counts as this week's report, so a restart does not produce a duplicate
    item = await services.memory.get(MEMORY_NS, MEMORY_KEY)
    assert item is not None and item.value["kind"] == "manual" and item.value["reports"] == 1
    # "run now" for selected books only does not move the schedule
    await services.memory.delete(MEMORY_NS, MEMORY_KEY)
    more = await services.weekly.run_once(kind="manual", portfolio_ids=["pf_default"])
    await services.runs.wait(more[0]["run_id"])
    assert await services.memory.get(MEMORY_NS, MEMORY_KEY) is None


async def test_weekly_catch_up_runs_after_a_missed_slot_only(settings: Settings) -> None:
    services = portfolio_services(settings, FakeLLM(default="ok"))
    now = datetime(2026, 9, 26, 8, tzinfo=UTC)  # Saturday, after Fri 25 Sep 03:00 +07
    job = WeeklyReportJob(services, clock=lambda: now)

    assert await job.catch_up() is False  # first start, no books: just remember "now"
    await save_book()
    assert await job.catch_up() is False  # nothing missed since

    await services.memory.put(MEMORY_NS, MEMORY_KEY, {"last_run": "2026-09-20T00:00:00+00:00"})
    assert await job.catch_up() is True  # the machine was off at the Friday slot
    assert await job.last_run() == now
    snaps = await portfolio_repo.list_snapshots("pf_default")
    assert len(snaps) == 1 and snaps[0].kind == "weekly"
    await services.runs.wait(snaps[0].run_id or "")


async def test_reports_endpoint_and_run_now(settings: Settings, install_services: Any) -> None:
    services = install_services(portfolio_services(settings, FakeLLM(default="ok")))
    await save_book()
    async with client() as c:
        started = (await c.post("/api/portfolios/reports/run", json={})).json()["started"]
        assert len(started) == 1
        await services.runs.wait(started[0]["run_id"])
        listed = (await c.get("/api/portfolios/reports")).json()
    assert listed["schedule"]["cron"] == "Fri 03:00 Asia/Bangkok"
    assert listed["reports"][0]["run_id"] == started[0]["run_id"]
    assert listed["reports"][0]["run_status"] in ("completed", "failed", "limited")
