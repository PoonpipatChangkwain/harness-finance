"""``GET/PUT /api/settings`` (phase-13 §13.4): overrides persist, apply to the next run, never leak keys."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import SecretStr

from app.domain.data.base import Capability, DataRequest
from app.domain.data.router import DataRouter
from app.domain.workflows.portfolio_advisor import AdviseInput, effective_limits
from app.domain.workflows.trading import trading_policy
from app.harness.config.overrides import advisor_policy_config, trading_policy_config
from app.harness.events.bus import EventBus
from app.harness.executor.registry import ToolRegistry
from app.harness.llm.fake import FakeLLM
from app.harness.runtime.limits import RunLimits
from app.harness.runtime.services import HarnessServices
from app.harness.storage import settings_repo
from app.settings import Settings
from tests.domain.data.test_router import FakeProvider
from tests.harness.portfolio.helpers import client

pytestmark = pytest.mark.usefixtures("db")

SECRET = "gsk-very-secret-key-123"


def _router() -> DataRouter:
    router = DataRouter()
    for pid in ("alpha", "beta", "gamma"):
        router.register(FakeProvider(pid, Capability.QUOTE, data={"from": pid}))
    router._config = {"quote": {"mode": "failover", "order": ["alpha", "beta"]}}
    return router


def _services(settings: Settings, router: DataRouter | None = None) -> HarnessServices:
    services = HarnessServices(
        settings=settings, bus=EventBus(), tools=ToolRegistry([]), llm=FakeLLM(default="ok")
    )
    services.data_router = router or _router()
    return services


@pytest.fixture
def keyed(settings: Settings) -> Settings:
    settings.groq_api_key = SecretStr(SECRET)
    settings.finnhub_api_key = SecretStr("fh-secret-999")
    return settings


def _walk_keys(obj: Any) -> set[str]:
    if isinstance(obj, dict):
        return set(obj) | {k for v in obj.values() for k in _walk_keys(v)}
    if isinstance(obj, list):
        return {k for v in obj for k in _walk_keys(v)}
    return set()


async def test_get_reports_configured_but_never_keys(keyed: Settings, install_services: Any) -> None:
    install_services(_services(keyed))
    async with client() as c:
        resp = await c.get("/api/settings")
    assert resp.status_code == 200
    assert SECRET not in resp.text and "fh-secret-999" not in resp.text
    assert not any("key" in k.lower() and k != "keys" for k in _walk_keys(resp.json()))
    body = resp.json()
    groq = next(p for p in body["catalog"]["llm"] if p["id"] == "groq")
    assert groq["configured"] is True and groq["env_hint"]
    assert body["values"]["limits"]["max_iterations"] == 10
    assert body["values"]["data"]["order"]["quote"] == ["alpha", "beta"]
    assert [p["id"] for p in body["catalog"]["data"]["quote"]["providers"]] == ["alpha", "beta", "gamma"]


async def test_put_rejects_api_key_fields(keyed: Settings, install_services: Any) -> None:
    install_services(_services(keyed))
    async with client() as c:
        resp = await c.put("/api/settings", json={"llm": {"groq_api_key": "x"}})
    assert resp.status_code == 422


async def test_reorder_provider_applies_to_the_next_fetch_and_persists(
    settings: Settings, install_services: Any
) -> None:
    router = _router()
    services = install_services(_services(settings, router))
    assert (await router.get(Capability.QUOTE, DataRequest())).provider == "alpha"
    async with client() as c:
        resp = await c.put("/api/settings", json={"data": {"order": {"quote": ["gamma", "alpha"]}}})
        assert resp.status_code == 200, resp.text
        assert resp.json()["values"]["data"]["order"]["quote"] == ["gamma", "alpha"]
        assert resp.json()["defaults"]["data"]["order"]["quote"] == ["alpha", "beta"]
    # the next data call (and its provenance.source) comes from the new first provider
    assert (await router.get(Capability.QUOTE, DataRequest())).provider == "gamma"
    assert await settings_repo.load_all() == {"data": {"order": {"quote": ["gamma", "alpha"]}}}

    # a fresh process loads the stored order at startup
    router2 = _router()
    await _services(settings, router2).load_overrides()
    assert (await router2.get(Capability.QUOTE, DataRequest())).provider == "gamma"

    # reset the section: back to routing.yaml
    async with client() as c:
        resp = await c.put("/api/settings", json={"data": {}})
    assert resp.json()["values"]["data"]["order"]["quote"] == ["alpha", "beta"]
    assert (await router.get(Capability.QUOTE, DataRequest())).provider == "alpha"
    assert await settings_repo.load_all() == {}
    assert services.overrides.current.data.order == {}


async def test_reorder_validation(settings: Settings, install_services: Any) -> None:
    install_services(_services(settings))
    async with client() as c:
        for order in (
            {"quote": ["nope"]},
            {"quote": ["alpha", "alpha"]},
            {"quote": []},
            {"bogus": ["alpha"]},
        ):
            resp = await c.put("/api/settings", json={"data": {"order": order}})
            assert resp.status_code == 422, order


async def test_limits_and_policy_apply_to_the_next_run(settings: Settings, install_services: Any) -> None:
    services = install_services(_services(settings))
    async with client() as c:
        resp = await c.put(
            "/api/settings",
            json={
                "limits": {"max_iterations": 6, "similarity_threshold": 0.8, "mc_count_trigger": 5},
                "policy": {"max_position_weight": 0.1, "require_stop_loss": False, "min_confidence": 0.7},
            },
        )
        assert resp.status_code == 200, resp.text
        # other sections untouched; unset fields keep their defaults
        assert resp.json()["values"]["limits"]["max_tool_calls_total"] == 40
        assert resp.json()["overrides"]["policy"] == {
            "max_position_weight": 0.1,
            "require_stop_loss": False,
            "min_confidence": 0.7,
        }
    limits = RunLimits.from_settings(services.settings)
    assert (limits.max_iterations, limits.similarity_threshold) == (6, 0.8)
    assert services.settings.context_mc_count_trigger == 5

    trading_cfg = trading_policy_config(services.settings)
    rule_ids = [r.id for r in trading_policy("stock", trading_cfg).rules]
    assert "require_stop_loss" not in rule_ids
    assert trading_cfg["max_position_weight"] == 0.1

    advisor_cfg = advisor_policy_config(services.settings)
    limits_advisor = effective_limits(AdviseInput(risk_profile="aggressive"), advisor_cfg)
    assert limits_advisor["max_weight"] == 0.1 and limits_advisor["min_confidence"] == 0.7
    assert limits_advisor["max_sector"] == 0.50  # not overridden: the risk profile's value

    async with client() as c:
        resp = await c.put("/api/settings", json={"limits": {}, "policy": {}})
    assert RunLimits.from_settings(services.settings).max_iterations == 10
    assert services.settings.policy_require_stop_loss is True
    assert resp.json()["overrides"] == {"llm": {}, "data": {}, "limits": {}, "policy": {}}


async def test_limits_out_of_range_is_422(settings: Settings, install_services: Any) -> None:
    install_services(_services(settings))
    async with client() as c:
        assert (await c.put("/api/settings", json={"limits": {"max_iterations": 0}})).status_code == 422
        assert (await c.put("/api/settings", json={"policy": {"max_position_weight": 2}})).status_code == 422


async def test_llm_tiers_and_failover(keyed: Settings, install_services: Any) -> None:
    services = install_services(_services(keyed))
    body = {
        "llm": {
            "deep": {"provider": "groq", "model": "llama-deep"},
            "quick": {"provider": "groq", "model": "llama-quick"},
            "failover": [{"provider": "openai", "model": "gpt-x"}],
        }
    }
    async with client() as c:
        resp = await c.put("/api/settings", json=body)
        assert resp.status_code == 200, resp.text
        llm = resp.json()["values"]["llm"]
        assert llm["deep"] == {"provider": "groq", "model": "llama-deep", "configured": True}
        assert llm["failover"] == [{"provider": "openai", "model": "gpt-x", "configured": False}]
        assert (
            await c.put("/api/settings", json={"llm": {"deep": {"provider": "nope", "model": "m"}}})
        ).status_code == 422
    assert services.settings.llm_quick_model == "llama-quick"
    assert services.settings.llm_failover == "openai:gpt-x"
    assert services.llm is not None  # an injected (test) LLM is never dropped
