"""Builders for research-workflow tests: services with fake tools + FakeLLM, run + collect events."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Callable
from typing import Any

from app.domain.agents.schemas import ResearchPlan
from app.domain.data.base import Capability
from app.domain.ports import LLMDelta, LLMMessage, Tier
from app.domain.tools.base import ToolSpec
from app.harness.events.bus import EventBus
from app.harness.executor.registry import ToolRegistry
from app.harness.llm.fake import FakeLLM, ScriptItem
from app.harness.runtime.limits import RunLimits
from app.harness.runtime.services import HarnessServices
from app.harness.skills.registry import SkillRegistry
from app.harness.skills.tool import skill_tool
from app.harness.state.memory import MemoryStore
from app.harness.state.memory_tools import memory_tools
from app.harness.storage import events_repo
from app.settings import Settings
from tests.harness import fake_tools

PROV_RE = re.compile(r"prov_[0-9A-HJKMNP-TV-Z]{26}")


def plan(skill: str | None = None, role: str = "research_lead", title: str = "PTT วิเคราะห์") -> ResearchPlan:
    return ResearchPlan(skill=skill, role=role, title=title, symbols=["PTT"])  # type: ignore[arg-type]


class CitingFakeLLM(FakeLLM):
    """Final-answer stream replaces ``{cite}`` with a real provenance id from the DATA section."""

    async def stream(
        self, messages: list[LLMMessage], *, tier: Tier, purpose: str
    ) -> AsyncIterator[LLMDelta]:
        ids = PROV_RE.findall(messages[-1].content)
        marker = f"[^{ids[0]}]" if ids else ""
        async for delta in super().stream(messages, tier=tier, purpose=purpose):
            yield delta.model_copy(update={"text": delta.text.replace("{cite}", marker)})


def research_services(
    settings: Settings,
    script: list[ScriptItem],
    *,
    llm: FakeLLM | None = None,
    extra_tools: list[ToolSpec] | None = None,
    capability_available: Callable[[Capability], bool] = lambda cap: True,
    long_term_memory: bool = False,
) -> HarnessServices:
    # (P17) memory/5-Whys add LLM calls to the scripted sequence; off unless a test asks for it
    settings.memory_enabled = long_term_memory
    memory = MemoryStore()
    skills = SkillRegistry(provider_ok=lambda provider: provider != "x_api")
    tools = [*fake_tools.all_tools(), skill_tool(skills), *memory_tools(memory), *(extra_tools or [])]
    return HarnessServices(
        settings=settings,
        bus=EventBus(),
        tools=ToolRegistry(tools, capability_available=capability_available),
        memory=memory,
        skills=skills,
        llm=llm if llm is not None else CitingFakeLLM(script),
    )


async def run_research(
    services: HarnessServices,
    text: str = "PTT ราคาปัจจุบันและ RSI เท่าไร",
    *,
    skill: str | None = None,
    limits: RunLimits | None = None,
    session_id: str | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    run_id = await services.runs.start(
        "research", {"text": text, "skill": skill, "history": []}, session_id=session_id, limits=limits
    )
    await services.runs.wait(run_id)
    return run_id, await events_of(run_id)


async def events_of(run_id: str) -> list[dict[str, Any]]:
    rows = await events_repo.events_after(run_id, 0)
    return [
        {"type": r.type, "payload": r.payload, "node": r.node, "call_id": r.call_id, "seq": r.seq}
        for r in rows
    ]


def types(events: list[dict[str, Any]]) -> list[str]:
    return [e["type"] for e in events]


def of(events: list[dict[str, Any]], type_: str) -> list[dict[str, Any]]:
    return [e["payload"] for e in events if e["type"] == type_]
