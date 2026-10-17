"""Builders for P17 memory tests: engine/bank over the test DB with a scripted FakeLLM."""

from __future__ import annotations

from typing import Any

from app.domain.memory.schemas import DistilledLesson, DistilledLessons, ExtractedFact, ExtractedFacts
from app.harness.llm.fake import FakeLLM
from app.harness.memory.bank import ReasoningBank
from app.harness.memory.engine import MemoryEngine
from app.harness.memory.store import NewMemory, UserMemoryStore
from app.settings import Settings

USER = "usr_default"


def build(settings: Settings, llm: FakeLLM | None = None) -> tuple[MemoryEngine, UserMemoryStore, FakeLLM]:
    fake = llm if llm is not None else FakeLLM()
    store = UserMemoryStore()
    bank = ReasoningBank(lambda: fake, settings)
    return MemoryEngine(lambda: fake, store, bank, settings), store, fake


def fact(text: str, **kw: Any) -> ExtractedFact:
    base: dict[str, Any] = {"category": "investment_style", "text": text, "confidence": 0.9, "importance": 7}
    return ExtractedFact.model_validate(base | kw)


def facts(*items: ExtractedFact) -> ExtractedFacts:
    return ExtractedFacts(facts=list(items))


def row(text: str, **kw: Any) -> NewMemory:
    base: dict[str, Any] = {"category": "investment_style", "text": text, "importance": 7}
    return NewMemory.model_validate(base | kw)


def lesson(title: str, content: str = "Ask about the horizon before sizing.", **kw: Any) -> DistilledLesson:
    base: dict[str, Any] = {
        "title": title,
        "description": "Applies when a user wants to buy a single stock.",
        "content": content,
        "kind": "strategy",
    }
    return DistilledLesson.model_validate(base | kw)


def lessons(*items: DistilledLesson) -> DistilledLessons:
    return DistilledLessons(items=list(items))


def structured_calls(llm: FakeLLM) -> list[str]:
    return [c.schema or "" for c in llm.calls if c.method == "structured"]
