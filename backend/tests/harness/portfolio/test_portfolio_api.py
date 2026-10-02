"""``/api/portfolio*`` over HTTP (phase-12 §12.5, §12.7 and the Definition of Done)."""

from __future__ import annotations

from typing import Any

import pytest

from app.harness.llm.fake import FakeLLM
from app.settings import Settings
from tests.domain.portfolio.fakes import FakeMarket, install
from tests.harness.portfolio.helpers import client, portfolio_services, proposal

pytestmark = pytest.mark.usefixtures("db")

DOD_BOOK = {
    "cash": "50000",
    "positions": [
        {"symbol": "PTT", "quantity": "1000", "avg_cost": "30"},
        {"symbol": "AAPL", "quantity": "10", "avg_cost": "150"},
    ],
}


@pytest.fixture(autouse=True)
def market(monkeypatch: pytest.MonkeyPatch) -> FakeMarket:
    return install(monkeypatch)


def _pvs(obj: Any) -> list[dict[str, Any]]:
    """Every ``{value, provenance_id}`` in a JSON tree."""
    if isinstance(obj, dict):
        if set(obj) == {"value", "provenance_id"}:
            return [obj]
        return [pv for v in obj.values() for pv in _pvs(v)]
    if isinstance(obj, list):
        return [pv for v in obj for pv in _pvs(v)]
    return []


async def test_dod_put_then_analytics_with_provenance(settings: Settings, install_services: Any) -> None:
    install_services(portfolio_services(settings))
    async with client() as c:
        assert (await c.get("/api/portfolio")).status_code == 404  # never saved = not provided, not empty
        resp = await c.put("/api/portfolio", json=DOD_BOOK)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["state"] == "positions" and body["portfolio"]["id"] == "pf_default"
        assert [p["symbol"]["canonical"] for p in body["portfolio"]["positions"]] == ["PTT.BK", "AAPL"]

        a = (await c.get("/api/portfolio/analytics")).json()
        assert a["total_value"]["value"] == pytest.approx(156_500)
        assert a["weights"]["PTT.BK"] == pytest.approx(34_500 / 156_500)
        assert a["unrealized_pnl"]["value"] == pytest.approx(22_500)
        assert a["risk"]["volatility"]["value"] > 0 and a["concentration"]["hhi"] > 0
        pvs = _pvs(a)
        assert len(pvs) > 25
        for pv in pvs[:40]:  # every number resolves to a stored provenance record
            rec = await c.get(f"/api/provenance/{pv['provenance_id']}")
            assert rec.status_code == 200, pv
        # the book itself is a user_input record, and the valuation traces back to it
        user = (await c.get(f"/api/provenance/{body['portfolio']['provenance_id']}")).json()["record"]
        assert user["kind"] == "user_input" and user["source"] == "user"
        lineage = (await c.get(f"/api/provenance/{a['total_value']['provenance_id']}?lineage=1")).json()
        assert body["portfolio"]["provenance_id"] in str(lineage)

        revisions = (await c.get("/api/portfolio/revisions")).json()["revisions"]
        assert [r["reason"] for r in revisions] == ["created"]


async def test_put_validation_returns_422_with_row_errors(settings: Settings, install_services: Any) -> None:
    install_services(portfolio_services(settings))
    rows = [
        {"symbol": "PTT", "quantity": "0"},
        {"symbol": "../x", "quantity": "1"},
        {"symbol": "AAPL", "weight": "0.6"},
        {"symbol": "KBANK", "weight": "0.6"},
    ]
    bad = {"positions": rows}
    async with client() as c:
        resp = await c.put("/api/portfolio", json=bad)
        assert resp.status_code == 422
        errors = resp.json()["errors"]
        assert {(e["index"], e["field"]) for e in errors} >= {
            (0, "quantity"),
            (1, "symbol"),
            (None, "weight"),
        }
        assert (await c.get("/api/portfolio")).status_code == 404  # nothing saved


async def test_value_and_pnl_entry(settings: Settings, install_services: Any) -> None:
    install_services(portfolio_services(settings))
    async with client() as c:
        resp = await c.put(
            "/api/portfolio", json={"positions": [{"symbol": "AAPL", "value": "2000", "pnl_pct": "0.25"}]}
        )
        assert resp.status_code == 200, resp.text
        pos = resp.json()["portfolio"]["positions"][0]
        assert float(pos["quantity"]) == pytest.approx(10)  # 2,000 / 200
        assert float(pos["avg_cost"]) == pytest.approx(160)  # 2,000 / 1.25 / 10
        a = (await c.get("/api/portfolio/analytics")).json()
        assert a["holdings"][0]["unrealized_pnl_pct"]["value"] == pytest.approx(0.25)


async def test_csv_import_preview_then_commit(settings: Settings, install_services: Any) -> None:
    install_services(portfolio_services(settings))
    csv = "symbol,quantity,avg_cost,weight,note\nPTT,1000,30,,core\nAAPL,10,150,,\n"
    async with client() as c:
        await c.put("/api/portfolio", json={"cash": "50000", "positions": []})
        preview = await c.post("/api/portfolio/import", content=csv, headers={"content-type": "text/csv"})
        assert preview.status_code == 200 and preview.json()["committed"] is False
        assert len(preview.json()["positions"]) == 2 and preview.json()["errors"] == []
        assert (await c.get("/api/portfolio")).json()["state"] == "flat"  # preview writes nothing

        done = await c.post(
            "/api/portfolio/import?commit=1", content=csv, headers={"content-type": "text/csv"}
        )
        assert done.status_code == 200 and done.json()["committed"] is True
        pf = (await c.get("/api/portfolio")).json()
        assert pf["state"] == "positions" and pf["portfolio"]["cash"] == "50000"  # cash kept
        reasons = [r["reason"] for r in (await c.get("/api/portfolio/revisions")).json()["revisions"]]
        assert reasons == ["import", "created"]

        mixed = "symbol,quantity,weight\nPTT,1000,\nAAPL,,0.3\n"
        bad = await c.post(
            "/api/portfolio/import?commit=1", content=mixed, headers={"content-type": "text/csv"}
        )
        assert bad.status_code == 422
        broken = "symbol,quantity\nPTT,abc\n"
        prev = (
            await c.post("/api/portfolio/import", content=broken, headers={"content-type": "text/csv"})
        ).json()
        assert prev["errors"] and prev["positions"] == []


async def test_dod_advise_reaches_approval_then_plan_then_apply(
    settings: Settings, install_services: Any
) -> None:
    llm = FakeLLM(["risk brief", "allocation brief", proposal({"PTT": 0.20, "AAPL": 0.20}, 0.60)])
    services = install_services(portfolio_services(settings, llm))
    async with client() as c:
        assert (await c.post("/api/portfolio/advise", json={})).status_code == 404
        await c.put("/api/portfolio", json=DOD_BOOK)
        body = {"risk_profile": "balanced", "constraints": {"max_weight": 0.3, "max_turnover": 0.5}}
        started = await c.post("/api/portfolio/advise", json=body)
        assert started.status_code == 200, started.text
        run_id = started.json()["run_id"]
        await services.runs.wait(run_id)
        assert (await c.post("/api/portfolio/advise", json=body)).status_code == 409  # same book + settings

        run = (await c.get(f"/api/runs/{run_id}")).json()
        assert run["run"]["status"] == "waiting_approval"
        approval_id = run["pending_approval"]["id"]
        assert (
            await c.post(f"/api/approvals/{approval_id}", json={"outcome": "approved"})
        ).status_code == 200
        await services.runs.wait(run_id)
        listed = (await c.get("/api/portfolios/pf_default/advisor-runs")).json()["runs"]
        assert [r["run_id"] for r in listed] == [run_id]  # the page finds its runs again after a reload
        assert (await c.get("/api/portfolios/other/advisor-runs")).json()["runs"] == []

        plans = (await c.get("/api/portfolio/plans")).json()["plans"]
        assert len(plans) == 1 and plans[0]["status"] == "approved" and plans[0]["run_id"] == run_id
        plan_id = plans[0]["id"]
        before = (await c.get("/api/portfolio")).json()["portfolio"]["positions"]
        assert [p["quantity"] for p in before] == ["1000", "10"]  # approval did not touch positions

        dry = (await c.post(f"/api/portfolio/plans/{plan_id}/apply?dry_run=1")).json()
        # PTT 22.0% → 20%: 3,195 THB is under one 100-share lot → hold; AAPL 46% → 20%: sell 5
        assert dry["dry_run"] is True and [d["symbol"] for d in dry["diff"]["positions"]] == ["AAPL"]
        applied = await c.post(f"/api/portfolio/plans/{plan_id}/apply")
        assert applied.status_code == 200, applied.text
        after = {
            p["symbol"]["canonical"]: p["quantity"]
            for p in applied.json()["portfolio"]["portfolio"]["positions"]
        }
        assert after == {"PTT.BK": "1000", "AAPL": "5"}
        assert applied.json()["diff"]["cash_after"] == "86000.0"
        revisions = (await c.get("/api/portfolio/revisions")).json()["revisions"]
        assert revisions[0]["reason"] == "apply_plan" and revisions[0]["plan_id"] == plan_id
        assert (await c.get("/api/portfolio/plans")).json()["plans"][0]["status"] == "applied"
        assert (await c.post(f"/api/portfolio/plans/{plan_id}/apply")).status_code == 409
        done = (await c.get("/api/portfolios/pf_default/advisor-runs")).json()["runs"][0]
        assert done["status"] == "completed" and done["plan_id"] == plan_id and done["report_md"]


async def test_apply_refuses_a_plan_for_a_changed_book(settings: Settings, install_services: Any) -> None:
    llm = FakeLLM(["r", "a", proposal({"PTT": 0.20, "AAPL": 0.20}, 0.60)])
    services = install_services(portfolio_services(settings, llm))
    async with client() as c:
        await c.put("/api/portfolio", json=DOD_BOOK)
        run_id = (await c.post("/api/portfolio/advise", json={"constraints": {"max_weight": 0.3}})).json()[
            "run_id"
        ]
        await services.runs.wait(run_id)
        approval_id = (await c.get(f"/api/runs/{run_id}")).json()["pending_approval"]["id"]
        await c.post(f"/api/approvals/{approval_id}", json={"outcome": "approved"})
        await services.runs.wait(run_id)
        plan_id = (await c.get("/api/portfolio/plans")).json()["plans"][0]["id"]
        await c.put("/api/portfolio", json={**DOD_BOOK, "cash": "60000"})
        assert (await c.post(f"/api/portfolio/plans/{plan_id}/apply")).status_code == 409
        assert (await c.post(f"/api/portfolio/plans/{plan_id}/apply?force=1")).status_code == 200


async def test_multi_portfolio_crud_and_summary(settings: Settings, install_services: Any) -> None:
    install_services(portfolio_services(settings))
    async with client() as c:
        th_book = {
            "name": "หุ้นไทย",
            "icon": "flag",
            "color": "#0a0",
            "cash": "10000",
            "positions": [{"symbol": "PTT", "quantity": "1000", "avg_cost": "30"}],
        }
        us_book = {
            "name": "US",
            "base_currency": "USD",
            "positions": [
                {"symbol": "AAPL", "quantity": "5", "avg_cost": "180"},
                {"symbol": "MSFT", "quantity": "1"},
            ],
        }
        th = await c.post("/api/portfolios", json=th_book)
        assert th.status_code == 200, th.text
        us = await c.post("/api/portfolios", json=us_book)
        assert us.status_code == 200, us.text
        th_id, us_id = th.json()["portfolio"]["id"], us.json()["portfolio"]["id"]
        listed = (await c.get("/api/portfolios")).json()["portfolios"]
        assert [(p["name"], p["positions"]) for p in listed] == [("หุ้นไทย", 1), ("US", 2)]

        s = (await c.get("/api/portfolios/summary", params={"currency": "THB", "alt_currency": "USD"})).json()
        assert s["currency"] == "THB"
        # 34,500 + 10,000 cash + (5x200 + 1x400) x 36
        assert s["total_value"] == pytest.approx(44_500 + 1_400 * 36)
        assert {p["id"] for p in s["portfolios"]} == {th_id, us_id}
        assert [a["symbol"] for a in s["assets"]][:1] == ["AAPL"]  # sorted by value
        assert s["provenance_id"] and s["fx"]["USD"]["rate"] == 36
        assert s["alt"]["currency"] == "USD" and s["alt"]["fx"]["USD"]["rate"] == 1
        th_only = (await c.get("/api/portfolios/summary", params={"market": "TH"})).json()
        assert [a["symbol"] for a in th_only["assets"]] == ["PTT.BK"]

        renamed = await c.patch(f"/api/portfolios/{us_id}", json={"name": "สหรัฐฯ"})
        assert renamed.json()["portfolio"]["name"] == "สหรัฐฯ"
        assert (await c.put("/api/portfolios/pf_missing", json={"positions": []})).status_code == 404
        assert (await c.delete(f"/api/portfolios/{us_id}")).json() == {"deleted": us_id}
        assert (await c.get(f"/api/portfolios/{us_id}")).status_code == 404
