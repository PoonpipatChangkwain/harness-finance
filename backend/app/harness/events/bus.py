"""EventBus: persist every run event, then fan it out to live subscribers.

- ``seq`` is monotonic per run (one ``asyncio.Lock`` per run) and is the
  SSE ``id`` so a client can reconnect with ``Last-Event-ID``.
- ``subscribe`` registers its queue *before* reading the DB and dedupes by
  ``seq``, so nothing is lost or duplicated at the replay→live seam.
- ``llm.delta`` is coalesced: each chunk goes out live immediately as an
  *ephemeral* envelope (no seq of its own), and the chunks are persisted as
  one ``llm.delta`` roughly every 200 ms (or before any other event). The
  persisted delta carries exactly the text of the ephemeral chunks since the
  previous one, so a client shows ephemeral text as pending and replaces it
  when the persisted delta arrives (live or on replay).
Event vocabulary from ``../dexter/src/agent/types.ts``; replay design from
contracts.md §10.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.harness.events.schema import EventEnvelope, validate_payload
from app.harness.storage import events_repo

DELTA_FLUSH_S = 0.2


@dataclass
class _DeltaBuffer:
    channel: str
    node: str | None
    call_id: str | None
    parent_call_id: str | None
    started: float
    parts: list[str] = field(default_factory=list)


class EventBus:
    def __init__(self, *, delta_flush_s: float = DELTA_FLUSH_S) -> None:
        self._delta_flush_s = delta_flush_s
        self._locks: dict[str, asyncio.Lock] = {}
        self._seq: dict[str, int] = {}
        self._subs: dict[str, set[asyncio.Queue[EventEnvelope]]] = {}
        self._sessions: dict[str, str | None] = {}
        self._active: set[str] = set()
        self._deltas: dict[str, _DeltaBuffer] = {}

    # -- run lifecycle --------------------------------------------------------------

    def open_run(self, run_id: str, session_id: str | None = None) -> None:
        self._active.add(run_id)
        self._sessions[run_id] = session_id

    def is_active(self, run_id: str) -> bool:
        return run_id in self._active

    def _lock(self, run_id: str) -> asyncio.Lock:
        lock = self._locks.get(run_id)
        if lock is None:
            lock = self._locks[run_id] = asyncio.Lock()
        return lock

    async def _next_seq(self, run_id: str) -> int:
        if run_id not in self._seq:
            self._seq[run_id] = await events_repo.max_seq(run_id)
        self._seq[run_id] += 1
        return self._seq[run_id]

    def _publish(self, env: EventEnvelope) -> None:
        for queue in list(self._subs.get(env.run_id, ())):
            queue.put_nowait(env)

    async def _persist(
        self,
        run_id: str,
        type_: str,
        payload: dict[str, Any],
        *,
        node: str | None,
        call_id: str | None,
        parent_call_id: str | None,
    ) -> EventEnvelope:
        seq = await self._next_seq(run_id)
        ts = datetime.now(UTC)
        await events_repo.insert_event(
            run_id, seq, ts, type_, payload, node=node, call_id=call_id, parent_call_id=parent_call_id
        )
        env = EventEnvelope(
            seq=seq,
            run_id=run_id,
            session_id=self._sessions.get(run_id),
            ts=ts,
            type=type_,
            node=node,
            call_id=call_id,
            parent_call_id=parent_call_id,
            payload=payload,
        )
        self._publish(env)
        return env

    async def _flush_delta(self, run_id: str) -> None:
        buf = self._deltas.pop(run_id, None)
        if buf is None or not buf.parts:
            return
        await self._persist(
            run_id,
            "llm.delta",
            {"channel": buf.channel, "text": "".join(buf.parts)},
            node=buf.node,
            call_id=buf.call_id,
            parent_call_id=buf.parent_call_id,
        )

    # -- emit -----------------------------------------------------------------------

    async def emit(
        self,
        run_id: str,
        type: str,
        payload: dict[str, Any],
        *,
        node: str | None = None,
        call_id: str | None = None,
        parent_call_id: str | None = None,
    ) -> int:
        """Validate, persist and publish one event. Returns its ``seq``."""
        clean = validate_payload(type, payload)
        async with self._lock(run_id):
            if type == "llm.delta":
                return await self._emit_delta(run_id, clean, node, call_id, parent_call_id)
            await self._flush_delta(run_id)
            env = await self._persist(
                run_id, type, clean, node=node, call_id=call_id, parent_call_id=parent_call_id
            )
            if type == "run.finished":
                self._active.discard(run_id)
            return env.seq

    async def _emit_delta(
        self,
        run_id: str,
        payload: dict[str, Any],
        node: str | None,
        call_id: str | None,
        parent_call_id: str | None,
    ) -> int:
        buf = self._deltas.get(run_id)
        if buf is not None and (
            buf.channel != payload["channel"] or buf.node != node or buf.call_id != call_id
        ):
            await self._flush_delta(run_id)
            buf = None
        current = self._seq.get(run_id)
        if current is None:
            current = self._seq[run_id] = await events_repo.max_seq(run_id)
        self._publish(
            EventEnvelope(
                seq=current,
                run_id=run_id,
                session_id=self._sessions.get(run_id),
                ts=datetime.now(UTC),
                type="llm.delta",
                node=node,
                call_id=call_id,
                parent_call_id=parent_call_id,
                payload=payload,
                ephemeral=True,
            )
        )
        if buf is None:
            buf = self._deltas[run_id] = _DeltaBuffer(
                payload["channel"], node, call_id, parent_call_id, time.monotonic()
            )
        buf.parts.append(payload["text"])
        if time.monotonic() - buf.started >= self._delta_flush_s:
            await self._flush_delta(run_id)
        return current

    async def flush(self, run_id: str) -> None:
        async with self._lock(run_id):
            await self._flush_delta(run_id)

    # -- subscribe --------------------------------------------------------------------

    async def subscribe(
        self, run_id: str, after_seq: int = 0, *, session_id: str | None = None
    ) -> AsyncIterator[EventEnvelope]:
        """Replay persisted events with ``seq > after_seq``, then follow live until ``run.finished``."""
        queue: asyncio.Queue[EventEnvelope] = asyncio.Queue()
        self._subs.setdefault(run_id, set()).add(queue)
        try:
            live = self.is_active(run_id)
            rows = await events_repo.events_after(run_id, after_seq)
            last = after_seq
            for row in rows:
                yield EventEnvelope(
                    seq=row.seq,
                    run_id=row.run_id,
                    session_id=self._sessions.get(run_id, session_id),
                    ts=row.ts,
                    type=row.type,
                    node=row.node,
                    call_id=row.call_id,
                    parent_call_id=row.parent_call_id,
                    payload=row.payload,
                )
                last = row.seq
                if row.type == "run.finished":
                    return
            if not live:
                return
            while True:
                env = await queue.get()
                if env.ephemeral:
                    if env.seq < last:
                        continue  # already covered by a persisted delta we replayed
                elif env.seq <= last:
                    continue
                else:
                    last = env.seq
                yield env
                if env.type == "run.finished":
                    return
        finally:
            subs = self._subs.get(run_id)
            if subs is not None:
                subs.discard(queue)
                if not subs:
                    self._subs.pop(run_id, None)


class RunEmitter:
    """``EventPort`` bound to one run (what domain code and the executor receive)."""

    def __init__(self, bus: EventBus, run_id: str) -> None:
        self.bus = bus
        self.run_id = run_id

    async def emit(
        self,
        type: str,
        payload: dict[str, Any],
        *,
        node: str | None = None,
        call_id: str | None = None,
        parent_call_id: str | None = None,
    ) -> int:
        return await self.bus.emit(
            self.run_id, type, payload, node=node, call_id=call_id, parent_call_id=parent_call_id
        )
