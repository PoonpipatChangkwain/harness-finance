from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from app.domain.tools.base import ToolSpec
from app.harness.events.bus import EventBus
from app.harness.executor.registry import ToolRegistry
from app.harness.graph.checkpoint import close_all_caches
from app.harness.runtime.context import RunContext
from app.harness.runtime.limits import RunLimits
from app.harness.runtime.services import HarnessServices, set_services
from app.harness.state.scratchpad import Scratchpad
from app.harness.storage import db as db_module
from app.settings import Settings


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[None]:
    db_module.configure(f"sqlite+aiosqlite:///{(tmp_path / 'app.db').as_posix()}")
    await db_module.create_all()
    yield
    await close_all_caches()  # LangGraph checkpoint connections opened during the test
    await db_module.dispose()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, data_dir=tmp_path, app_env="dev")  # type: ignore[call-arg]


def make_services(settings: Settings, tools: list[ToolSpec]) -> HarnessServices:
    return HarnessServices(
        settings=settings,
        bus=EventBus(),
        tools=ToolRegistry(tools, capability_available=lambda cap: True),
    )


@pytest.fixture
def install_services() -> Any:
    installed: list[HarnessServices] = []

    def install(services: HarnessServices) -> HarnessServices:
        set_services(services)
        installed.append(services)
        return services

    yield install
    set_services(None)


class RecordingEmitter:
    """EventPort that keeps events in memory."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def emit(
        self,
        type: str,
        payload: dict[str, Any],
        *,
        node: str | None = None,
        call_id: str | None = None,
        parent_call_id: str | None = None,
    ) -> int:
        self.events.append(
            {
                "type": type,
                "payload": payload,
                "node": node,
                "call_id": call_id,
                "parent_call_id": parent_call_id,
            }
        )
        return len(self.events)

    def types(self) -> list[str]:
        return [e["type"] for e in self.events]


def make_ctx(tmp_path: Path, *, limits: RunLimits | None = None, run_id: str = "run_TEST") -> RunContext:
    limits = limits or RunLimits()
    return RunContext(
        run_id=run_id,
        workflow="test",
        input={},
        emit=RecordingEmitter(),
        scratchpad=Scratchpad(
            run_id,
            tmp_path / "scratchpad",
            query="test",
            max_calls_per_tool=limits.max_calls_per_tool,
            similarity_threshold=limits.similarity_threshold,
        ),
        limits=limits,
    )
