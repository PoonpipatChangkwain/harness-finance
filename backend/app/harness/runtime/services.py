"""Process-wide harness services (bus, run manager, tools, skills, LLM, stores).

The API builds one ``HarnessServices`` at startup; tests build their own
(fake tools / ``FakeLLM``) and install it with ``set_services``. The LLM is
created lazily so the app starts even when no LLM key is configured yet.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.domain.data.router import DataRouter
from app.domain.tools.base import ToolSpec
from app.domain.tools.catalog import domain_tools
from app.harness.config.overrides import Overrides, SettingsOverrides
from app.harness.events.bus import EventBus
from app.harness.executor.registry import ToolRegistry
from app.harness.graph.checkpoint import CheckpointerCache
from app.harness.memory.bank import ReasoningBank
from app.harness.memory.engine import MemoryEngine
from app.harness.memory.store import UserMemoryStore
from app.harness.memory.tools import user_memory_tool
from app.harness.portfolio.service import PortfolioService
from app.harness.portfolio.tools import portfolio_analytics_tool
from app.harness.provenance.store import ProvenanceStore
from app.harness.runtime.runs import RunManager
from app.harness.skills.registry import SkillRegistry
from app.harness.skills.tool import skill_tool
from app.harness.state.memory import MemoryStore
from app.harness.state.memory_tools import memory_tools
from app.settings import Settings, get_settings


@dataclass
class HarnessServices:
    settings: Settings
    bus: EventBus
    tools: ToolRegistry
    provenance: ProvenanceStore = field(default_factory=ProvenanceStore)
    memory: MemoryStore = field(default_factory=MemoryStore)
    skills: SkillRegistry = field(default_factory=SkillRegistry)
    portfolio: PortfolioService = field(default_factory=PortfolioService)
    user_memory: UserMemoryStore = field(default_factory=UserMemoryStore)  # (P17) long-term user memory
    llm: Any = None  # LLMPort with bind()/primary(); HarnessLLM unless a test injects FakeLLM
    # tests inject a factory for per-message model choices; production builds a HarnessLLM
    llm_for_choice: Callable[[str, str], Any] | None = None
    # re-read API keys from env/.env for model choices, so a key added while the server runs works
    reload_llm_keys: bool = False
    runs: RunManager = field(init=False)
    checkpoints: CheckpointerCache = field(init=False)
    weekly: Any = field(init=False)  # (P12) WeeklyReportJob: Friday 03:00 portfolio report
    # (P17) memory engine (MemoryPort for workflow nodes: ctx.config["memory"]) + the shared ReasoningBank
    memory_engine: MemoryEngine = field(init=False)
    reasoning_bank: ReasoningBank = field(init=False)
    # (P13) Settings-page overrides on top of env; tests may pass their own data router
    overrides: SettingsOverrides = field(init=False)
    data_router: DataRouter | None = None
    _llm_built: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        from app.harness.portfolio.scheduler import WeeklyReportJob

        self.overrides = SettingsOverrides(self.settings)
        self.runs = RunManager(self.bus, self.settings, services=self)
        self.checkpoints = CheckpointerCache(self.settings.data_dir)
        self.weekly = WeeklyReportJob(self)
        # the LLM is resolved lazily on each memory call (the app starts without an LLM key)
        self.reasoning_bank = ReasoningBank(self.get_llm, self.settings)
        self.memory_engine = MemoryEngine(self.get_llm, self.user_memory, self.reasoning_bank, self.settings)
        from app.harness.runtime.demo import echo_tools
        from app.harness.runtime.graph_demo import graph_demo, graph_demo_resume
        from app.harness.workflow.backtest import backtest_workflow, backtest_workflow_resume
        from app.harness.workflow.portfolio_advisor import advisor_workflow, advisor_workflow_resume
        from app.harness.workflow.research import research_workflow, research_workflow_resume
        from app.harness.workflow.trading import trading_workflow, trading_workflow_resume

        self.runs.register("echo_tools", echo_tools)
        self.runs.register("research", research_workflow, graceful=True, resume=research_workflow_resume)
        self.runs.register("graph_demo", graph_demo, resume=graph_demo_resume)
        self.runs.register("trading", trading_workflow, resume=trading_workflow_resume)
        self.runs.register("portfolio_advisor", advisor_workflow, resume=advisor_workflow_resume)
        self.runs.register("backtest", backtest_workflow, resume=backtest_workflow_resume)  # (P15)

    def get_llm(self) -> Any:
        if self.llm is None:
            from app.harness.llm.adapter import HarnessLLM

            self.llm = HarnessLLM.from_settings(self.settings)
            self._llm_built = True
        return self.llm

    def llm_settings(self) -> Settings:
        """Settings for LLM keys: fresh from env/.env when ``reload_llm_keys`` (never logged),
        with the Settings-page overrides applied."""
        return self.overrides.on_fresh(Settings()) if self.reload_llm_keys else self.settings

    def router(self) -> DataRouter:
        if self.data_router is not None:
            return self.data_router
        from app.domain.data.registry import get_router

        return get_router()

    def apply_overrides(self, overrides: Overrides) -> None:
        """Make ``overrides`` effective for the next run (the LLM is rebuilt from the new tiers)."""
        self.overrides.apply(self.settings, overrides, self.router())
        if self._llm_built:
            self.llm = None
            self._llm_built = False

    async def load_overrides(self) -> list[str]:
        """Apply the overrides stored in the ``settings`` table; returns sections that were skipped."""
        from pydantic import ValidationError

        from app.harness.storage import settings_repo

        stored = await settings_repo.load_all()
        sections: dict[str, Any] = {}
        skipped: list[str] = []
        for key, value in stored.items():
            try:
                sections[key] = getattr(Overrides.model_validate({key: value}), key)
            except (ValidationError, AttributeError):
                skipped.append(key)
        self.apply_overrides(Overrides(**sections))
        return skipped

    def get_llm_for_choice(self, provider: str, model: str) -> Any:
        """An LLM for a model the user picked for one message: the deep tier is that model only
        (no failover); the quick tier stays as configured (e.g. a free model for plan/compaction)."""
        if self.llm_for_choice is not None:
            return self.llm_for_choice(provider, model)
        from app.harness.llm.adapter import HarnessLLM
        from app.harness.llm.config import llm_config_for_choice, load_llm_config
        from app.harness.llm.errors import LLMNotConfigured

        settings = self.llm_settings()
        try:
            quick = load_llm_config(settings).quick
        except (ValueError, LLMNotConfigured):
            quick = None
        config = llm_config_for_choice(provider, model, timeout_s=settings.llm_timeout_s, quick=quick)
        return HarnessLLM.from_config(config, settings)


def default_tools(
    memory: MemoryStore,
    skills: SkillRegistry,
    portfolio: PortfolioService | None = None,
    user_memory: UserMemoryStore | None = None,
) -> list[ToolSpec]:
    extra = [portfolio_analytics_tool(portfolio)] if portfolio is not None else []
    if user_memory is not None:
        extra.append(user_memory_tool(user_memory))
    return [*domain_tools(), *memory_tools(memory), skill_tool(skills), *extra]


def build_services(settings: Settings | None = None, tools: list[ToolSpec] | None = None) -> HarnessServices:
    reload_llm_keys = settings is None  # the app's own settings come from .env: re-read keys for choices
    settings = settings or get_settings()
    memory = MemoryStore()
    skills = SkillRegistry()
    portfolio = PortfolioService()
    user_memory = UserMemoryStore()
    return HarnessServices(
        settings=settings,
        bus=EventBus(),
        tools=ToolRegistry(
            tools if tools is not None else default_tools(memory, skills, portfolio, user_memory)
        ),
        memory=memory,
        skills=skills,
        portfolio=portfolio,
        user_memory=user_memory,
        reload_llm_keys=reload_llm_keys,
    )


_services: HarnessServices | None = None


def get_services() -> HarnessServices:
    global _services
    if _services is None:
        _services = build_services()
    return _services


def set_services(services: HarnessServices | None) -> None:
    global _services
    _services = services
