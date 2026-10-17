"""(P17) ``MemoryEngine`` — implements ``MemoryPort`` (contracts.md §13.3-13.4).

Pattern ported from Mem0 (extract → reconcile ``ADD/UPDATE/SUPERSEDE/RETIRE/NOOP``) with the
2026 lesson that destructive updates lose history: a change is always a new row that supersedes
the old one. Recall = a Letta-style always-on profile block + FTS hits + ReasoningBank lessons.

``learn`` plans every fact first and only then writes, so a failed structured call (extract or
any reconcile) writes nothing at all (fail-safe; never guess). Deterministic guards beat the LLM:

1. an ``inferred_behavior`` fact may not update/supersede/retire the user's own word
   (``user_stated/user_confirmed/manual/questionnaire``) → stored as a separate
   ``behavioral_pattern`` flagged ``stated_vs_revealed``;
2. same ``key`` → same value is NOOP, a different value is SUPERSEDE (no LLM);
3. no neighbour at all → ADD (no LLM);
4. ``risk_tolerance/goal/constraint`` not in the user's own word → ``candidate``; a candidate never
   supersedes an active row until the user confirms it.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from app.domain.memory.messages import (
    EXTRACT_PROMPT,
    RECONCILE_PROMPT,
    extract_messages,
    recent_user_messages,
    reconcile_messages,
)
from app.domain.memory.profile import ProfileRender, render_profile
from app.domain.memory.schemas import (
    ACTIVE_WITHOUT_CONFIRM_SOURCES,
    CONFIRM_CATEGORIES,
    STATED_SOURCES,
    DiscoveryAssessment,
    ExtractedFacts,
    MemoryCategory,
    MemoryChange,
    MemoryItemModel,
    MemorySource,
    MemoryStatus,
    Recall,
    ReconcileOpKind,
    ReconcileOps,
    Sensitivity,
)
from app.domain.ports import EventPort, LLMPort
from app.harness.memory.bank import ReasoningBank
from app.harness.memory.store import CURRENT_STATUSES, NewMemory, UserMemoryStore
from app.settings import Settings

logger = logging.getLogger(__name__)

PURPOSE = "memory"
MIN_FACT_CONFIDENCE = 0.4
FTS_NEIGHBOURS = 5
RECALL_ITEMS = 8
RECALL_REASONING = 2
_WS_RE = re.compile(r"\s+")


@dataclass
class Fact:
    category: MemoryCategory
    text: str
    source: MemorySource
    key: str | None = None
    value_json: dict[str, Any] | None = None
    confidence: float = 0.9
    importance: int = 5
    sensitivity: Sensitivity = "normal"


@dataclass
class _Plan:
    op: ReconcileOpKind
    fact: Fact
    target: MemoryItemModel | None = None
    reason: str = ""
    flags: list[str] = field(default_factory=list)
    used_llm: bool = False


class _ReconcileFailed(Exception):
    pass


def normalized_confidence(source: MemorySource, value: float) -> float:
    if source in ("manual", "user_confirmed"):
        return 0.95
    if source == "inferred_behavior":
        return min(max(value, MIN_FACT_CONFIDENCE), 0.7)
    if source == "user_stated":
        return min(value, 0.9)
    return value


def status_for(category: str, source: str) -> MemoryStatus:
    if category in CONFIRM_CATEGORIES and source not in ACTIVE_WITHOUT_CONFIRM_SOURCES:
        return "candidate"
    return "active"


def _norm_text(value: str) -> str:
    return _WS_RE.sub(" ", value.casefold()).strip()


def _norm_value(value: Mapping[str, Any]) -> dict[str, str]:
    return {str(k): str(v).strip().casefold() for k, v in value.items()}


def same_value(fact: Fact, item: MemoryItemModel) -> bool:
    if fact.value_json and item.value_json:
        return _norm_value(fact.value_json) == _norm_value(item.value_json)
    return _norm_text(fact.text) == _norm_text(item.text)


def _default_importance(category: str) -> int:
    return 8 if category in CONFIRM_CATEGORIES else 6


class MemoryEngine:
    def __init__(
        self,
        llm: Callable[[], LLMPort] | None,
        store: UserMemoryStore,
        bank: ReasoningBank,
        settings: Settings,
    ) -> None:
        self._llm_factory = llm
        self.store = store
        self.bank = bank
        self.settings = settings

    # -- helpers ----------------------------------------------------------------------------------

    def _llm(self, llm: LLMPort | None) -> LLMPort | None:
        if llm is not None:
            return llm
        if self._llm_factory is None:
            return None
        try:
            return self._llm_factory()
        except Exception as exc:  # LLMNotConfigured etc.: memory must never break a run
            logger.warning("memory: no LLM (%s)", exc)
            return None

    @staticmethod
    async def _warn(emit: EventPort | None, node: str | None, code: str, message: str) -> None:
        logger.warning("%s: %s", code, message)
        if emit is not None:
            try:
                await emit.emit("warning", {"code": code, "message": message}, node=node)
            except Exception:  # pragma: no cover
                logger.exception("could not emit %s", code)

    # -- recall -----------------------------------------------------------------------------------

    async def profile(self, user_id: str) -> tuple[ProfileRender, datetime | None]:
        """The profile block injected into prompts, and when its newest item changed."""
        active = await self.store.list_items(user_id, statuses=["active"])
        render = render_profile(active, max_tokens=self.settings.memory_profile_max_tokens)
        used = {i.id: i for i in active if i.id in set(render.items_used)}
        updated = max((i.updated_at for i in used.values()), default=None)
        return render, updated

    async def recall(
        self, user_id: str, query: str, *, workflow: str, tags: Mapping[str, Any] | None = None
    ) -> Recall:
        if not self.settings.memory_enabled:
            return Recall(provenance={"source": "memory", "enabled": False})
        active = await self.store.list_items(user_id, statuses=["active"])
        render = render_profile(active, max_tokens=self.settings.memory_profile_max_tokens)
        used = set(render.items_used)
        hits = await self.store.search_fts(user_id, query, statuses=CURRENT_STATUSES, limit=RECALL_ITEMS)
        items = [item for item, _ in hits]
        reasoning = await self.bank.retrieve(
            query, {**dict(tags or {}), "workflow": workflow}, k=RECALL_REASONING
        )
        await self.store.touch(user_id, [*render.items_used, *(i.id for i in items)])
        return Recall(
            profile_text=render.text,
            token_estimate=render.token_estimate,
            profile_items=[i for i in active if i.id in used],
            items=items,
            reasoning=reasoning,
            provenance={
                "source": "memory",
                "workflow": workflow,
                "query": query[:200],
                "profile_item_ids": render.items_used,
                "item_ids": [i.id for i in items],
                "reasoning_ids": [r.id for r in reasoning],
            },
        )

    # -- planning ---------------------------------------------------------------------------------

    async def _neighbours(self, user_id: str, fact: Fact) -> list[MemoryItemModel]:
        """Active/candidate items of this user and category: same ``key``, or FTS top-5 by text."""
        found: dict[str, MemoryItemModel] = {}
        if fact.key:
            for item in await self.store.list_items(
                user_id, statuses=CURRENT_STATUSES, category=fact.category, key=fact.key
            ):
                found[item.id] = item
        for item, _ in await self.store.search_fts(
            user_id, fact.text, statuses=CURRENT_STATUSES, category=fact.category, limit=FTS_NEIGHBOURS
        ):
            found.setdefault(item.id, item)
        return list(found.values())

    @staticmethod
    def _revealed(fact: Fact, target: MemoryItemModel, reason: str) -> _Plan:
        """Guard 1: an inference that contradicts the user's own word is kept apart, flagged."""
        pattern = Fact(
            category="behavioral_pattern",
            text=fact.text,
            source="inferred_behavior",
            key=None,
            value_json=fact.value_json,
            confidence=fact.confidence,
            importance=fact.importance,
            sensitivity=fact.sensitivity,
        )
        return _Plan("ADD", pattern, target=target, reason=reason, flags=["stated_vs_revealed"])

    async def _plan(
        self, user_id: str, fact: Fact, observation_date: date, llm: LLMPort | None
    ) -> _Plan | None:
        """One fact → one plan (``None`` = nothing to do). Raises ``_ReconcileFailed`` on a parse failure."""
        neighbours = await self._neighbours(user_id, fact)
        inferred = fact.source == "inferred_behavior"
        keyed = [n for n in neighbours if fact.key and n.key == fact.key]
        if keyed:
            keyed.sort(key=lambda n: (n.status != "active", -n.created_at.timestamp()))
            slot = keyed[0]
            if same_value(fact, slot):
                return _Plan("NOOP", fact, target=slot, reason="same key, same value")
            if inferred and slot.source in STATED_SOURCES:
                return self._revealed(fact, slot, "inference contradicts what the user said")
            return _Plan("SUPERSEDE", fact, target=slot, reason="same key, new value")
        if not neighbours:
            return _Plan("ADD", fact, reason="no related memory")
        if llm is None:
            raise _ReconcileFailed("no LLM configured for memory")
        result = await llm.structured(
            reconcile_messages(
                category=fact.category,
                text=fact.text,
                value_json=fact.value_json,
                source=fact.source,
                key=fact.key,
                neighbours=neighbours,
                observation_date=observation_date,
            ),
            ReconcileOps,
            tier="quick",
            purpose=PURPOSE,
        )
        if result.value is None:
            raise _ReconcileFailed(f"reconcile output not parsed ({result.mode})")
        if not result.value.ops:
            return None
        op = result.value.ops[0]
        by_id = {n.id: n for n in neighbours}
        target = by_id.get(op.target_id) if op.target_id else None
        merged = Fact(
            category=fact.category,
            text=op.text.strip() or fact.text,
            source=fact.source,
            key=fact.key or (target.key if target else None),
            value_json=op.value_json or fact.value_json,
            confidence=normalized_confidence(fact.source, op.confidence or fact.confidence),
            importance=op.importance or fact.importance,
            sensitivity=fact.sensitivity,
        )
        kind = op.op
        if kind in ("UPDATE", "SUPERSEDE") and target is None:
            kind = "ADD"  # an invented / missing id: keep the information as new
        if kind in ("NOOP", "RETIRE") and target is None:
            return None
        if (
            inferred
            and target is not None
            and target.source in STATED_SOURCES
            and kind
            in (
                "UPDATE",
                "SUPERSEDE",
                "RETIRE",
            )
        ):
            return self._revealed(fact, target, op.reason or "inference contradicts what the user said")
        return _Plan(kind, merged if kind != "NOOP" else fact, target=target, reason=op.reason, used_llm=True)

    # -- applying ---------------------------------------------------------------------------------

    def _new_row(self, plan: _Plan, source_ref: Mapping[str, Any]) -> NewMemory:
        fact = plan.fact
        ref: dict[str, Any] = dict(source_ref)
        if plan.used_llm:
            ref["reconcile_prompt"] = RECONCILE_PROMPT
        if plan.flags:
            ref["flags"] = list(plan.flags)
            if plan.target is not None:
                ref["conflicts_with"] = plan.target.id
        return NewMemory(
            category=fact.category,
            text=fact.text,
            key=fact.key,
            value_json=fact.value_json,
            confidence=normalized_confidence(fact.source, fact.confidence),
            importance=fact.importance,
            source=fact.source,
            source_ref=ref,
            status=status_for(fact.category, fact.source),
            sensitivity=fact.sensitivity,
        )

    async def _apply(
        self, user_id: str, plans: Sequence[_Plan], source_ref: Mapping[str, Any]
    ) -> list[MemoryChange]:
        changes: list[MemoryChange] = []
        consumed: set[str] = set()
        for plan in plans:
            target = plan.target
            op = plan.op
            if target is not None and target.id in consumed and op != "ADD":
                op = "ADD" if op in ("UPDATE", "SUPERSEDE") else "NOOP"
                if op == "NOOP":
                    continue
                target = None
            if op == "NOOP" and target is not None:
                await self.store.touch(user_id, [target.id])
                changes.append(MemoryChange(op="NOOP", item=target, reason=plan.reason or None))
                continue
            row = self._new_row(plan, source_ref)
            if op == "ADD":
                item = await self.store.add(user_id, row)
                changes.append(MemoryChange(op="ADD", item=item, reason=plan.reason or None))
            elif op in ("UPDATE", "SUPERSEDE") and target is not None:
                consumed.add(target.id)
                if row.status == "candidate" and target.status == "active":
                    item = await self.store.link_candidate(user_id, row, target.id)
                    changes.append(
                        MemoryChange(op=op, item=item, previous=target, reason=plan.reason or None)
                    )
                    continue
                done = await self.store.supersede(user_id, target.id, row)
                if done is None:  # deleted meanwhile
                    item = await self.store.add(user_id, row)
                    changes.append(MemoryChange(op="ADD", item=item, reason=plan.reason or None))
                else:
                    changes.append(
                        MemoryChange(op=op, item=done[0], previous=done[1], reason=plan.reason or None)
                    )
            elif op == "RETIRE" and target is not None:
                consumed.add(target.id)
                retired = await self.store.set_status(user_id, target.id, "retired")
                if retired is not None:
                    changes.append(
                        MemoryChange(op="RETIRE", item=retired, previous=target, reason=plan.reason or None)
                    )
        return changes

    @staticmethod
    async def _emit_changes(
        emit: EventPort | None, node: str | None, changes: Sequence[MemoryChange]
    ) -> None:
        visible = [c for c in changes if c.op != "NOOP"]
        if emit is None or not visible:
            return
        payload = {
            "changes": [
                {
                    "op": c.op,
                    "id": c.item.id,
                    "category": c.item.category,
                    "text": c.item.text,
                    "previous_text": c.previous.text if c.previous else None,
                    "status": c.item.status,
                }
                for c in visible
            ]
        }
        await emit.emit("memory.updated", payload, node=node)

    # -- learn ------------------------------------------------------------------------------------

    @staticmethod
    def _discovery_facts(discovery: DiscoveryAssessment | None) -> list[Fact]:
        if discovery is None or not discovery.root_goal_identified:
            return []
        return [
            Fact(
                category=c.category,
                text=c.text,
                source="user_stated",
                key=c.key,
                confidence=normalized_confidence("user_stated", max(c.confidence, MIN_FACT_CONFIDENCE)),
                importance=_default_importance(c.category),
            )
            for c in discovery.memory_candidates[:2]
            if c.confidence >= MIN_FACT_CONFIDENCE
        ]

    async def learn(
        self,
        user_id: str,
        *,
        user_messages: Sequence[str],
        assistant_text: str = "",
        discovery: DiscoveryAssessment | None = None,
        source_ref: Mapping[str, Any] | None = None,
        observation_date: date | None = None,
        llm: LLMPort | None = None,
        emit: EventPort | None = None,
        node: str | None = None,
    ) -> list[MemoryChange]:
        """Extract durable facts from the user's own messages and reconcile them with memory.

        Returns every change (NOOP included); emits ``memory.updated`` for the non-NOOP ones.
        """
        if not self.settings.memory_enabled:
            return []
        today = observation_date or datetime.now(UTC).date()
        messages = recent_user_messages(user_messages)
        facts = self._discovery_facts(discovery)
        # the confirmed root goal's facts own their categories in this batch: the extractor sees
        # the same goal (as context) and restates it under another key → a duplicate row
        discovery_categories = {f.category for f in facts}
        if not messages and not facts:
            return []
        client = self._llm(llm)
        if messages:
            if client is None:
                await self._warn(emit, node, "memory_learn_failed", "no LLM configured for memory")
                return []
            goal = (
                discovery.root_goal.text
                if discovery and discovery.root_goal_identified and discovery.root_goal
                else None
            )
            extracted = await client.structured(
                extract_messages(
                    messages, observation_date=today, assistant_text=assistant_text, confirmed_goal=goal
                ),
                ExtractedFacts,
                tier="quick",
                purpose=PURPOSE,
            )
            if extracted.value is None:
                await self._warn(
                    emit, node, "memory_learn_failed", f"extract output not parsed ({extracted.mode})"
                )
                return []
            for f in extracted.value.facts:
                if f.confidence < MIN_FACT_CONFIDENCE or f.category in discovery_categories:
                    continue
                facts.append(
                    Fact(
                        category=f.category,
                        text=f.text,
                        source=f.source,
                        key=f.key,
                        value_json=f.value_json,
                        confidence=normalized_confidence(f.source, f.confidence),
                        importance=f.importance,
                        sensitivity=f.sensitivity,
                    )
                )
        unique: dict[tuple[str, str], Fact] = {}
        for i, fact in enumerate(facts):
            unique[(fact.category, fact.key or f"#{i}")] = fact  # one fact per slot: the latest wins
        try:
            plans = [
                p for f in unique.values() if (p := await self._plan(user_id, f, today, client)) is not None
            ]
        except _ReconcileFailed as exc:
            await self._warn(emit, node, "memory_learn_failed", str(exc))
            return []
        ref = {**dict(source_ref or {}), "prompt_version": EXTRACT_PROMPT}
        changes = await self._apply(user_id, plans, ref)
        await self._emit_changes(emit, node, changes)
        return changes

    # -- user actions (API) -----------------------------------------------------------------------

    async def add_manual(
        self,
        user_id: str,
        *,
        category: MemoryCategory,
        text: str,
        key: str | None = None,
        value_json: dict[str, Any] | None = None,
        llm: LLMPort | None = None,
    ) -> tuple[list[MemoryChange], list[str]]:
        """A fact the user typed on the Memory page: guards + reconcile, source ``manual`` (0.95).

        If the reconcile LLM is unavailable the fact is still added (the user asked for it) with a
        warning — the key-collision guard needs no LLM and still applies.
        """
        fact = Fact(
            category=category,
            text=text.strip(),
            source="manual",
            key=key or None,
            value_json=value_json,
            confidence=0.95,
            importance=_default_importance(category),
        )
        warnings: list[str] = []
        try:
            plan = await self._plan(user_id, fact, datetime.now(UTC).date(), self._llm(llm))
        except _ReconcileFailed as exc:
            warnings.append(f"reconcile unavailable, added as new: {exc}")
            plan = _Plan("ADD", fact, reason="reconcile unavailable")
        if plan is None:
            return [], warnings
        changes = await self._apply(user_id, [plan], {"entered": "memory_page"})
        return changes, warnings

    async def edit(
        self,
        user_id: str,
        memory_id: str,
        *,
        text: str | None = None,
        value_json: dict[str, Any] | None = None,
        set_value: bool = False,
    ) -> tuple[MemoryItemModel, MemoryItemModel] | None:
        """PATCH: a new ``manual`` row that supersedes the old one. ``(new, old)``; None if missing."""
        old = await self.store.get(user_id, memory_id)
        if old is None:
            return None
        row = NewMemory(
            category=old.category,
            text=(text or old.text).strip(),
            key=old.key,
            value_json=value_json if set_value else old.value_json,
            confidence=0.95,
            importance=old.importance,
            source="manual",
            source_ref={"edited_from": old.id},
            status="active",
            sensitivity=old.sensitivity,
        )
        return await self.store.supersede(user_id, old.id, row)
