"""``/api/trade/runs`` end to end over HTTP (phase-10 DoD)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.api.main import app
from app.domain.agents.schemas import TraderProposal
from app.domain.tools.common import to_symbol
from app.harness.llm.fake import FakeLLM
from app.settings import Settings
from tests.harness.trading.helpers import SNAPSHOTS, happy_script, proposal, trading_services

pytestmark = pytest.mark.usefixtures("db")


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", timeout=10)


async def test_post_ptt_waits_for_approval_then_reports(settings: Settings, install_services: Any) -> None:
    services = install_services(trading_services(settings, FakeLLM([*happy_script(4), "narrative"])))
    async with _client() as client:
        resp = await client.post("/api/trade/runs", json={"symbol": "PTT"})
        assert resp.status_code == 200, resp.text
        run_id, tid = resp.json()["run_id"], resp.json()["thread_id"]
        await services.runs.wait(run_id)

        detail = (await client.get(f"/api/trade/runs/{run_id}")).json()
        assert detail["run"]["status"] == "waiting_approval"
        assert detail["run"]["thread_id"] == tid
        assert detail["nodes"]["analysts"]["status"] == "done"
        assert detail["nodes"]["policy_gate"]["summary"].startswith("passed")
        assert detail["decision"]["sizing"]["stop"] is not None
        assert {n["id"] for n in detail["graph_view"]["nodes"]} >= {"trader", "approval", "report"}

        # a second run on the same symbol/day/graph must not clobber the paused one
        dup = await client.post("/api/trade/runs", json={"symbol": "PTT"})
        assert dup.status_code == 409

        approval_id = (await client.get(f"/api/runs/{run_id}")).json()["pending_approval"]["id"]
        assert (
            await client.post(f"/api/approvals/{approval_id}", json={"outcome": "approved"})
        ).status_code == 200
        await services.runs.wait(run_id)

        detail = (await client.get(f"/api/trade/runs/{run_id}")).json()
        assert detail["run"]["status"] == "completed"
        assert detail["outcome"]["status"] == "approved"
        assert detail["report_md"] and "PTT" in detail["report_md"]
        assert detail["nodes"]["report"]["status"] == "done"

        history = (await client.get("/api/trade/runs", params={"symbol": "PTT"})).json()["runs"]
        assert [h["run_id"] for h in history] == [run_id]
        assert history[0]["rating"] == "Buy" and history[0]["outcome"] == "approved"


@pytest.mark.parametrize("symbol", ["XAUUSD", "BTC-USD", "EURUSD"])
async def test_gold_crypto_forex_reach_approval(
    symbol: str, settings: Settings, install_services: Any
) -> None:
    close = SNAPSHOTS[to_symbol(symbol).canonical]["close"]
    script = [
        p if not isinstance(p, TraderProposal) else proposal(targets=[close * 1.1]) for p in happy_script(3)
    ]
    services = install_services(trading_services(settings, FakeLLM(script)))
    async with _client() as client:
        run_id = (await client.post("/api/trade/runs", json={"symbol": symbol})).json()["run_id"]
        await services.runs.wait(run_id)
        detail = (await client.get(f"/api/trade/runs/{run_id}")).json()
        assert detail["run"]["status"] == "waiting_approval", detail["run"]
        sizing = detail["decision"]["sizing"]
        assert sizing["stop"] is not None and sizing["units"] > 0


async def test_run_with_model_choice(settings: Settings, install_services: Any) -> None:
    from pydantic import SecretStr

    keyed = settings.model_copy(update={"groq_api_key": SecretStr("k")})
    chosen = FakeLLM([*happy_script(4)])
    picked: list[tuple[str, str]] = []

    def for_choice(provider: str, model: str) -> Any:
        picked.append((provider, model))
        return chosen

    services = trading_services(keyed, FakeLLM([]))
    services.llm_for_choice = for_choice
    install_services(services)
    async with _client() as client:
        gemini = {"provider": "gemini", "model": "gemini-3.8-flash"}
        missing = await client.post("/api/trade/runs", json={"symbol": "PTT", "llm": gemini})
        assert missing.status_code == 400
        assert "GOOGLE_API_KEY" in missing.json()["error"]["message"]

        groq = {"provider": "groq", "model": "openai/gpt-oss-20b"}
        resp = await client.post("/api/trade/runs", json={"symbol": "PTT", "llm": groq})
        assert resp.status_code == 200, resp.text
        run_id = resp.json()["run_id"]
        await services.runs.wait(run_id)
        detail = (await client.get(f"/api/trade/runs/{run_id}")).json()

    assert picked == [("groq", "openai/gpt-oss-20b")]
    assert chosen.calls and not services.llm.calls  # the default LLM was not used
    assert detail["run"]["status"] == "waiting_approval"
    assert detail["input"]["llm"] == {"provider": "groq", "model": "openai/gpt-oss-20b"}


async def test_delete_trade_run(settings: Settings, install_services: Any) -> None:
    services = install_services(trading_services(settings, FakeLLM([*happy_script(4), "narrative"])))
    async with _client() as client:
        run_id = (await client.post("/api/trade/runs", json={"symbol": "PTT"})).json()["run_id"]
        await services.runs.wait(run_id)
        scratchpad = settings.scratchpad_dir / f"{run_id}.jsonl"
        assert scratchpad.exists()

        resp = await client.delete(f"/api/trade/runs/{run_id}")
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"deleted": run_id}

        assert (await client.get(f"/api/trade/runs/{run_id}")).status_code == 404
        assert (await client.get("/api/trade/runs")).json()["runs"] == []
        assert not scratchpad.exists()
        assert (await client.delete(f"/api/trade/runs/{run_id}")).status_code == 404
        # the paused thread's checkpoint went with it, so the same symbol/day can run again
        again = await client.post("/api/trade/runs", json={"symbol": "PTT"})
        assert again.status_code == 200, again.text
        await services.runs.wait(again.json()["run_id"])


async def test_bad_symbol_is_400(settings: Settings, install_services: Any) -> None:
    install_services(trading_services(settings, FakeLLM([])))
    async with _client() as client:
        assert (await client.post("/api/trade/runs", json={"symbol": "../../etc"})).status_code == 400
        assert (await client.get("/api/trade/runs/run_nope")).status_code == 404
