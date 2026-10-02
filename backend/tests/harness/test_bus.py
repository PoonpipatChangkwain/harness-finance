from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.harness.events import export
from app.harness.events.bus import EventBus
from app.harness.events.schema import PAYLOADS, envelope_json_schema
from app.harness.storage import events_repo

pytestmark = pytest.mark.usefixtures("db")

RUN = "run_BUS"


async def _collect(bus: EventBus, after: int = 0) -> list[tuple[int, str, bool]]:
    return [(e.seq, e.type, e.ephemeral) async for e in bus.subscribe(RUN, after)]


async def test_seq_is_continuous_and_persisted() -> None:
    bus = EventBus()
    bus.open_run(RUN)
    seqs = [await bus.emit(RUN, "warning", {"code": "x", "message": str(i)}) for i in range(5)]
    assert seqs == [1, 2, 3, 4, 5]
    rows = await events_repo.events_after(RUN, 0)
    assert [r.seq for r in rows] == [1, 2, 3, 4, 5]


async def test_replay_then_live_without_loss_or_duplicates() -> None:
    bus = EventBus()
    bus.open_run(RUN)
    total = 120

    async def producer() -> None:
        for i in range(total):
            await bus.emit(RUN, "tool.progress", {"message": f"m{i}"})
            if i % 7 == 0:
                await asyncio.sleep(0)
        await bus.emit(RUN, "run.finished", {"status": "completed"})

    async def late_subscriber() -> list[tuple[int, str, bool]]:
        await asyncio.sleep(0)  # join mid-stream
        return await _collect(bus)

    _, got = await asyncio.gather(producer(), late_subscriber())
    assert [s for s, _, _ in got] == list(range(1, total + 2))
    assert got[-1][1] == "run.finished"


async def test_subscribe_after_seq_and_finished_run_replay() -> None:
    bus = EventBus()
    bus.open_run(RUN)
    for i in range(4):
        await bus.emit(RUN, "warning", {"code": "c", "message": str(i)})
    await bus.emit(RUN, "run.finished", {"status": "completed"})
    got = await _collect(bus, after=2)
    assert [s for s, _, _ in got] == [3, 4, 5]
    # a new bus (fresh process) replays from the DB and stops at run.finished
    assert [s for s, _, _ in await _collect(EventBus())] == [1, 2, 3, 4, 5]


async def test_llm_delta_coalesced_in_db_but_live_per_chunk() -> None:
    bus = EventBus(delta_flush_s=60)  # only flush when another event arrives
    bus.open_run(RUN)
    got: list[tuple[int, str, bool, str]] = []

    async def subscriber() -> None:
        async for e in bus.subscribe(RUN):
            got.append((e.seq, e.type, e.ephemeral, e.payload.get("text", "")))

    task = asyncio.create_task(subscriber())
    await asyncio.sleep(0)
    await bus.emit(RUN, "llm.started", {"provider": "p", "model": "m", "tier": "quick", "purpose": "final"})
    for chunk in ["สวัส", "ดี ", "ครับ"]:
        await bus.emit(RUN, "llm.delta", {"channel": "answer", "text": chunk})
    await bus.emit(RUN, "run.finished", {"status": "completed"})
    await task

    ephemeral = [t for _, typ, eph, t in got if typ == "llm.delta" and eph]
    persisted = [t for _, typ, eph, t in got if typ == "llm.delta" and not eph]
    assert ephemeral == ["สวัส", "ดี ", "ครับ"]
    assert persisted == ["สวัสดี ครับ"]
    rows = await events_repo.events_after(RUN, 0)
    assert [r.type for r in rows] == ["llm.started", "llm.delta", "run.finished"]


async def test_delta_flush_on_channel_switch() -> None:
    bus = EventBus(delta_flush_s=60)
    bus.open_run(RUN)
    await bus.emit(RUN, "llm.delta", {"channel": "reasoning", "text": "think"})
    await bus.emit(RUN, "llm.delta", {"channel": "answer", "text": "answer"})
    await bus.flush(RUN)
    rows = await events_repo.events_after(RUN, 0)
    assert [(r.payload["channel"], r.payload["text"]) for r in rows] == [
        ("reasoning", "think"),
        ("answer", "answer"),
    ]


async def test_invalid_payload_rejected() -> None:
    bus = EventBus()
    with pytest.raises(ValueError):
        await bus.emit(RUN, "no.such.event", {})
    with pytest.raises(ValueError):
        await bus.emit(RUN, "run.finished", {"status": "exploded"})


def test_schema_export(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    schema = envelope_json_schema()
    assert len(schema["oneOf"]) == len(PAYLOADS) == 22  # (P15) + backtest.progress/cell/sim
    out = tmp_path / "events.schema.json"
    monkeypatch.setattr(export, "OUT", out)
    export.main()
    assert json.loads(out.read_text(encoding="utf-8"))["title"] == "EventEnvelope"
