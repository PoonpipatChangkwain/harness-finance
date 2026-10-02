from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.api.main import app
from app.settings import Settings
from tests.harness import fake_tools
from tests.harness.conftest import make_services

pytestmark = pytest.mark.usefixtures("db")


def _parse_sse(text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        fields: dict[str, Any] = {}
        for line in block.split("\n"):
            if not line or line.startswith(":"):
                continue
            key, _, value = line.partition(":")
            fields[key] = value.lstrip(" ")
        if "data" in fields:
            fields["data"] = json.loads(fields["data"])
            events.append(fields)
    return events


async def _stream(
    client: httpx.AsyncClient,
    run_id: str,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    url = f"/api/runs/{run_id}/events"
    async with client.stream("GET", url, headers=headers or {}, params=params) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = "".join([chunk async for chunk in resp.aiter_text()])
    return _parse_sse(body)


async def test_echo_run_end_to_end(settings: Settings, install_services: Any) -> None:
    services = install_services(make_services(settings, fake_tools.all_tools()))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=10) as client:
        resp = await client.post("/api/dev/echo-run", params={"symbol": "PTT"})
        assert resp.status_code == 200
        run_id = resp.json()["run_id"]

        events = await _stream(client, run_id)
        types = [e["event"] for e in events]
        assert types[0] == "run.started" and types[-1] == "run.finished"
        assert sorted(types[1:-1]) == ["tool.finished", "tool.finished", "tool.started", "tool.started"]
        assert types.index("tool.started") < types.index("tool.finished")
        assert [int(e["id"]) for e in events] == list(range(1, len(events) + 1))
        assert events[-1]["data"]["payload"]["status"] == "completed"

        # Reconnect with Last-Event-ID: 2 → only seq ≥ 3
        replay = await _stream(client, run_id, headers={"Last-Event-ID": "2"})
        assert [int(e["id"]) for e in replay] == list(range(3, len(events) + 1))
        after_4 = await _stream(client, run_id, params={"after": 4})
        assert [int(e["id"]) for e in after_4] == list(range(5, len(events) + 1))

        run = (await client.get(f"/api/runs/{run_id}")).json()
        assert run["run"]["status"] == "completed" and run["run"]["usage"]["tool_calls"] == 2

        pad = (await client.get(f"/api/runs/{run_id}/scratchpad")).json()
        tool_results = [e for e in pad["entries"] if e["type"] == "tool_result"]
        assert {e["tool"] for e in tool_results} == {"get_prices", "compute_indicators"}
        only_prices = (
            await client.get(f"/api/runs/{run_id}/scratchpad", params={"tool": "get_prices"})
        ).json()
        assert only_prices["total"] == 1

        records = (await client.get(f"/api/runs/{run_id}/provenance")).json()["records"]
        indicator = next(r for r in records if r["kind"] == "compute")
        lineage = (await client.get(f"/api/provenance/{indicator['id']}", params={"lineage": 1})).json()[
            "lineage"
        ]
        assert lineage["inputs"][0]["record"]["kind"] == "data"
        assert lineage["inputs"][0]["record"]["value"] == 34.5

        assert (await client.post(f"/api/runs/{run_id}/cancel")).json()["cancelled"] is False
    assert not services.runs.is_running(run_id)


async def test_unknown_run_404(settings: Settings, install_services: Any) -> None:
    install_services(make_services(settings, []))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/api/runs/run_01J0000000000000000000000A")).status_code == 404
        assert (await client.get("/api/runs/../../etc/scratchpad")).status_code == 404
        assert (await client.get("/api/provenance/prov_nope")).status_code == 404


async def test_echo_run_hidden_outside_dev(tmp_path: Any, install_services: Any) -> None:
    prod = Settings(_env_file=None, data_dir=tmp_path, app_env="prod")  # type: ignore[call-arg]
    install_services(make_services(prod, fake_tools.all_tools()))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.post("/api/dev/echo-run")).status_code == 404
