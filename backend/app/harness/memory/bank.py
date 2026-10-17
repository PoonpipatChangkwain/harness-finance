"""(P17) ReasoningBank: anonymised lessons shared across users (contracts.md §13.3).

Pattern from ReasoningBank (Google, ICLR 2026; research §C.2): every judged run is distilled into
≤ 3 ``title / description / content`` items — strategies from successes, pitfalls from failures —
and a new run gets the top 1-2 injected. Retrieval is FTS5 (trigram) + tag filter, re-ranked by
``bm25_norm x (0.5 + success_rate)``; ``settle`` feeds real outcomes back into ``success_rate``.

Privacy pipeline before any insert (research §C.5): generic-only distillation prompt →
deterministic ``scrub_text`` (a PII hit drops the item) → a second LLM privacy judge (no answer
or "identifies" drops the item) → k-anonymity gate (``REASONING_BANK_MIN_SOURCES``, 1 while the
app is single-user).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from app.domain.memory.messages import (
    DISTILL_PROMPT,
    distill_messages,
    outcome_judge_messages,
    privacy_messages,
)
from app.domain.memory.schemas import (
    DistilledLesson,
    DistilledLessons,
    OutcomeJudgement,
    OutcomeSignal,
    PrivacyVerdict,
    RBKind,
    ReasoningItem,
    success_rate,
)
from app.domain.memory.scrub import has_pii, scrub_text
from app.domain.ports import EventPort, LLMPort
from app.harness.memory.store import aware, fts_query, utcnow
from app.harness.storage.db import session_scope
from app.harness.storage.models import ReasoningBankRow, ReasoningBankUseRow
from app.settings import Settings

logger = logging.getLogger(__name__)

PURPOSE = "memory"
TAG_KEYS = ("workflow", "scenario", "asset_class", "market", "regime", "investor_style", "bias", "horizon")
FILTER_TAGS = ("market", "asset_class")
RETIRE_MIN_USES = 10
RETIRE_BELOW_RATE = 0.35
DEDUPE_JACCARD = 0.8
_WS_RE = re.compile(r"\s+")


def new_rb_id() -> str:
    return f"rb_{ULID()}"


def new_rbu_id() -> str:
    return f"rbu_{ULID()}"


def to_item(row: ReasoningBankRow) -> ReasoningItem:
    return ReasoningItem(
        id=row.id,
        title=row.title,
        description=row.description,
        content=row.content,
        kind=row.kind,  # type: ignore[arg-type]
        outcome_signal=row.outcome_signal,  # type: ignore[arg-type]
        judge_score=row.judge_score,
        tags=dict(row.tags or {}),
        use_count=row.use_count,
        success_count=row.success_count,
        success_rate=success_rate(row.success_count, row.use_count),
        source_count=row.source_count,
        status=row.status,  # type: ignore[arg-type]
        prompt_version=row.prompt_version,
        model=row.model,
        source_run_id=row.source_run_id,
        created_at=aware(row.created_at) or utcnow(),
        updated_at=aware(row.updated_at) or utcnow(),
    )


def trigrams(value: str) -> set[str]:
    norm = _WS_RE.sub(" ", value.lower()).strip()
    return {norm[i : i + 3] for i in range(max(len(norm) - 2, 1))} if norm else set()


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _tags_match(item_tags: Mapping[str, Any], want: Mapping[str, Any]) -> bool:
    workflow = want.get("workflow")
    if workflow and item_tags.get("workflow") != workflow:
        return False
    for key in FILTER_TAGS:
        wanted = want.get(key)
        have = item_tags.get(key)
        if wanted and have and str(have).upper() != str(wanted).upper():
            return False
    return True


class UseOut(BaseModel):
    id: str
    run_id: str
    outcome: str | None
    created_at: datetime


class ReasoningBank:
    def __init__(self, llm: Callable[[], LLMPort] | None, settings: Settings) -> None:
        self._llm_factory = llm
        self.settings = settings

    # -- LLM helpers ----------------------------------------------------------------------------

    def _llm(self, llm: LLMPort | None) -> LLMPort | None:
        if llm is not None:
            return llm
        if self._llm_factory is None:
            return None
        try:
            return self._llm_factory()
        except Exception as exc:  # LLMNotConfigured, bad config: memory never breaks a run
            logger.warning("reasoning bank: no LLM (%s)", exc)
            return None

    @staticmethod
    async def _warn(emit: EventPort | None, node: str | None, code: str, message: str) -> None:
        logger.warning("%s: %s", code, message)
        if emit is not None:
            try:
                await emit.emit("warning", {"code": code, "message": message}, node=node)
            except Exception:  # pragma: no cover - a broken emitter must not break memory
                logger.exception("could not emit %s", code)

    # -- distill --------------------------------------------------------------------------------

    @staticmethod
    def _scrub(lesson: DistilledLesson) -> DistilledLesson | None:
        """Deterministic scrub of every field; ``None`` when any PII is found."""
        fields: dict[str, str] = {}
        for name in ("title", "description", "content"):
            clean, hits = scrub_text(getattr(lesson, name))
            if has_pii(hits):
                return None
            fields[name] = clean
        tags: dict[str, Any] = {}
        for key, value in lesson.tags.items():
            if key not in TAG_KEYS or key == "workflow" or value in (None, ""):
                continue
            clean, hits = scrub_text(str(value)[:40])
            if has_pii(hits):
                return None
            tags[key] = clean
        return lesson.model_copy(update={**fields, "title": fields["title"][:120], "tags": tags})

    @staticmethod
    def _kind(kind: RBKind, signal: OutcomeSignal) -> RBKind:
        if kind == "coaching" or signal == "mixed":
            return kind
        return "strategy" if signal == "success" else "pitfall"

    async def _duplicate(self, s: AsyncSession, title: str, description: str) -> ReasoningBankRow | None:
        same = (
            (
                await s.execute(
                    select(ReasoningBankRow).where(func.lower(ReasoningBankRow.title) == title.lower())
                )
            )
            .scalars()
            .first()
        )
        if same is not None:
            return same
        match = fts_query(f"{title} {description}")
        if match is None:
            return None
        hits = (
            await s.execute(
                text(
                    "SELECT id FROM reasoning_bank_fts WHERE reasoning_bank_fts MATCH :q "
                    "ORDER BY bm25(reasoning_bank_fts) LIMIT 5"
                ),
                {"q": match},
            )
        ).all()
        mine = trigrams(f"{title} {description}")
        for (hit_id,) in hits:
            row = await s.get(ReasoningBankRow, str(hit_id))
            if (
                row is not None
                and jaccard(mine, trigrams(f"{row.title} {row.description}")) >= DEDUPE_JACCARD
            ):
                return row
        return None

    async def _insert_or_merge(
        self,
        lesson: DistilledLesson,
        *,
        kind: RBKind,
        signal: OutcomeSignal,
        score: float,
        tags: dict[str, Any],
        model: str | None,
        source_run_id: str | None,
        user_id: str,
    ) -> ReasoningItem:
        min_sources = max(1, self.settings.reasoning_bank_min_sources)
        now = utcnow()
        async with session_scope() as s:
            dup = await self._duplicate(s, lesson.title, lesson.description)
            if dup is not None:
                dup.source_count += 1
                if dup.status == "candidate" and dup.source_count >= min_sources:
                    dup.status = "active"
                dup.updated_at = now
                await s.commit()
                return to_item(dup)
            row = ReasoningBankRow(
                id=new_rb_id(),
                title=lesson.title,
                description=lesson.description,
                content=lesson.content,
                kind=kind,
                outcome_signal=signal,
                judge_score=score,
                tags=tags,
                use_count=0,
                success_count=0,
                source_count=1,
                status="active" if min_sources <= 1 else "candidate",
                prompt_version=DISTILL_PROMPT,
                model=model,
                source_run_id=source_run_id,
                user_id=user_id,
                created_at=now,
                updated_at=now,
            )
            s.add(row)
            await s.flush()
            await s.execute(
                text(
                    "INSERT INTO reasoning_bank_fts(id, title, description, content) "
                    "VALUES (:id, :title, :description, :content)"
                ),
                {"id": row.id, "title": row.title, "description": row.description, "content": row.content},
            )
            await s.commit()
            return to_item(row)

    async def distill(
        self,
        *,
        run_summary: str,
        outcome: OutcomeJudgement,
        tags: Mapping[str, Any],
        source_run_id: str | None,
        user_id: str,
        outcome_signal: OutcomeSignal | None = None,
        max_items: int = 3,
        llm: LLMPort | None = None,
        emit: EventPort | None = None,
        node: str | None = None,
    ) -> list[ReasoningItem]:
        """Distill ≤ ``max_items`` lessons from a judged run; returns the items inserted or merged."""
        if not self.settings.memory_enabled:
            return []
        signal: OutcomeSignal = outcome_signal or ("success" if outcome.success else "failure")
        client = self._llm(llm)
        if client is None:
            await self._warn(emit, node, "reasoning_distill_failed", "no LLM configured for memory")
            return []
        caller_tags = {k: v for k, v in tags.items() if k in TAG_KEYS and v not in (None, "")}
        result = await client.structured(
            distill_messages(
                run_summary=run_summary,
                outcome=outcome,
                outcome_signal=signal,
                tags=caller_tags,
                max_items=max_items,
            ),
            DistilledLessons,
            tier="quick",
            purpose=PURPOSE,
        )
        if result.value is None:
            await self._warn(
                emit, node, "reasoning_distill_failed", f"distill output not parsed ({result.mode})"
            )
            return []
        out: list[ReasoningItem] = []
        for lesson in result.value.items[: max(0, max_items)]:
            clean = self._scrub(lesson)
            if clean is None:
                logger.info("reasoning bank: dropped a lesson with PII (scrubber)")
                continue
            verdict = await client.structured(
                privacy_messages(clean), PrivacyVerdict, tier="quick", purpose=PURPOSE
            )
            if verdict.value is None or verdict.value.identifies_person_or_holdings:
                logger.info(
                    "reasoning bank: dropped a lesson (privacy judge: %s)",
                    verdict.value.reason if verdict.value else verdict.mode,
                )
                continue
            out.append(
                await self._insert_or_merge(
                    clean,
                    kind=self._kind(clean.kind, signal),
                    signal=signal,
                    score=outcome.score,
                    tags={**clean.tags, **caller_tags},
                    model=result.model,
                    source_run_id=source_run_id,
                    user_id=user_id,
                )
            )
        if out and emit is not None:
            payload = {"items": [{"id": i.id, "title": i.title, "kind": i.kind} for i in out]}
            await emit.emit("reasoning.distilled", payload, node=node)
        return out

    async def judge_outcome(
        self, *, request: str, trajectory: str, output: str, llm: LLMPort | None = None
    ) -> OutcomeJudgement | None:
        """LLM-as-judge (``rb_outcome_judge.v1``) for runs without a deterministic verdict; None = unknown."""
        client = self._llm(llm)
        if client is None:
            return None
        result = await client.structured(
            outcome_judge_messages(request=request, trajectory=trajectory, output=output),
            OutcomeJudgement,
            tier="quick",
            purpose=PURPOSE,
        )
        return result.value

    # -- retrieve / use / settle ------------------------------------------------------------------

    async def retrieve(
        self, query: str, tags: Mapping[str, Any] | None = None, *, k: int = 2
    ) -> list[ReasoningItem]:
        """Active items whose tags fit, best ``bm25_norm x (0.5 + success_rate)`` first (≤ ``k``)."""
        if k <= 0 or not self.settings.memory_enabled:
            return []
        want = dict(tags or {})
        async with session_scope() as s:
            rows = (
                await s.execute(select(ReasoningBankRow).where(ReasoningBankRow.status == "active"))
            ).scalars()
            candidates = {r.id: r for r in rows if _tags_match(r.tags or {}, want)}
            if not candidates:
                return []
            tag_text = " ".join(str(v) for key, v in want.items() if key != "workflow" and v)
            match = fts_query(f"{query} {tag_text}")
            relevance: dict[str, float] = {}
            if match is not None:
                hits = (
                    await s.execute(
                        text(
                            "SELECT id, bm25(reasoning_bank_fts) AS rank FROM reasoning_bank_fts "
                            "WHERE reasoning_bank_fts MATCH :q ORDER BY rank LIMIT 50"
                        ),
                        {"q": match},
                    )
                ).all()
                for hit_id, rank in hits:
                    if str(hit_id) in candidates and len(relevance) < 10:
                        relevance[str(hit_id)] = -float(rank)
        items = {rid: to_item(row) for rid, row in candidates.items()}
        if relevance:
            top = max(relevance.values())
            scored = [
                ((score / top if top > 0 else 1.0) * (0.5 + items[rid].success_rate), items[rid])
                for rid, score in relevance.items()
            ]
        else:  # nothing matched the words: ReasoningBank still injects its best item for this context
            scored = [(0.5 * (0.5 + item.success_rate), item) for item in items.values()]
        scored.sort(key=lambda pair: (pair[0], pair[1].created_at.timestamp()), reverse=True)
        return [item for _, item in scored[:k]]

    async def record_use(self, item_ids: Sequence[str], run_id: str, user_id: str) -> None:
        ids = list(dict.fromkeys(item_ids))
        if not ids:
            return
        async with session_scope() as s:
            existing = set(
                (
                    await s.execute(
                        select(ReasoningBankUseRow.item_id).where(
                            ReasoningBankUseRow.run_id == run_id, ReasoningBankUseRow.item_id.in_(ids)
                        )
                    )
                ).scalars()
            )
            for item_id in ids:
                if item_id not in existing:
                    s.add(
                        ReasoningBankUseRow(
                            id=new_rbu_id(),
                            item_id=item_id,
                            run_id=run_id,
                            user_id=user_id,
                            created_at=utcnow(),
                        )
                    )
            await s.commit()

    async def settle(self, run_id: str, success: bool) -> list[ReasoningItem]:
        """Record the run's outcome on the items it used (once); retire items that keep failing."""
        now = utcnow()
        async with session_scope() as s:
            uses = (
                (
                    await s.execute(
                        select(ReasoningBankUseRow).where(
                            ReasoningBankUseRow.run_id == run_id, ReasoningBankUseRow.outcome.is_(None)
                        )
                    )
                )
                .scalars()
                .all()
            )
            updated: list[ReasoningBankRow] = []
            for use in uses:
                use.outcome = "success" if success else "failure"
                row = await s.get(ReasoningBankRow, use.item_id)
                if row is None:
                    continue
                row.use_count += 1
                if success:
                    row.success_count += 1
                if (
                    row.status == "active"
                    and row.use_count >= RETIRE_MIN_USES
                    and success_rate(row.success_count, row.use_count) < RETIRE_BELOW_RATE
                ):
                    row.status = "retired"
                row.updated_at = now
                updated.append(row)
            await s.commit()
            return [to_item(r) for r in updated]

    # -- API reads --------------------------------------------------------------------------------

    async def get(self, item_id: str) -> ReasoningItem | None:
        async with session_scope() as s:
            row = await s.get(ReasoningBankRow, item_id)
            return to_item(row) if row else None

    async def uses(self, item_id: str, limit: int = 20) -> list[UseOut]:
        async with session_scope() as s:
            rows = (
                await s.execute(
                    select(ReasoningBankUseRow)
                    .where(ReasoningBankUseRow.item_id == item_id)
                    .order_by(ReasoningBankUseRow.created_at.desc(), ReasoningBankUseRow.id.desc())
                    .limit(limit)
                )
            ).scalars()
            return [
                UseOut(
                    id=r.id, run_id=r.run_id, outcome=r.outcome, created_at=aware(r.created_at) or utcnow()
                )
                for r in rows
            ]

    async def retire(self, item_id: str) -> ReasoningItem | None:
        async with session_scope() as s:
            row = await s.get(ReasoningBankRow, item_id)
            if row is None:
                return None
            row.status = "retired"
            row.updated_at = utcnow()
            await s.commit()
            return to_item(row)

    async def list_items(
        self,
        *,
        workflow: str | None = None,
        kind: str | None = None,
        status: str | None = None,
        q: str | None = None,
        limit: int = 50,
    ) -> list[ReasoningItem]:
        async with session_scope() as s:
            stmt = select(ReasoningBankRow)
            if kind:
                stmt = stmt.where(ReasoningBankRow.kind == kind)
            if status:
                stmt = stmt.where(ReasoningBankRow.status == status)
            if q:
                match = fts_query(q)
                if match is None:
                    return []
                ids = [
                    str(r[0])
                    for r in (
                        await s.execute(
                            text("SELECT id FROM reasoning_bank_fts WHERE reasoning_bank_fts MATCH :q"),
                            {"q": match},
                        )
                    ).all()
                ]
                stmt = stmt.where(ReasoningBankRow.id.in_(ids))
            rows = (await s.execute(stmt.order_by(ReasoningBankRow.created_at.desc()))).scalars().all()
        items = [to_item(r) for r in rows if not workflow or (r.tags or {}).get("workflow") == workflow]
        return items[:limit]

    async def all_items(self) -> list[ReasoningItem]:
        async with session_scope() as s:
            return [to_item(r) for r in (await s.execute(select(ReasoningBankRow))).scalars()]

    async def counts(self) -> dict[str, int]:
        items = await self.all_items()
        out = {"total": len(items), "candidate": 0, "active": 0, "retired": 0}
        for item in items:
            out[item.status] += 1
        return out

    async def stats(self) -> dict[str, Any]:
        items = await self.all_items()
        active = [i for i in items if i.status == "active"]
        by_kind: dict[str, int] = {}
        by_workflow: dict[str, int] = {}
        for item in items:
            by_kind[item.kind] = by_kind.get(item.kind, 0) + 1
            wf = str(item.tags.get("workflow") or "unknown")
            by_workflow[wf] = by_workflow.get(wf, 0) + 1
        return {
            "total": len(items),
            "active": len(active),
            "by_kind": by_kind,
            "by_workflow": by_workflow,
            "avg_success_rate": round(sum(i.success_rate for i in active) / len(active), 4)
            if active
            else None,
        }

    async def fts_ids(self) -> set[str]:
        async with session_scope() as s:
            return {str(r[0]) for r in (await s.execute(text("SELECT id FROM reasoning_bank_fts"))).all()}
