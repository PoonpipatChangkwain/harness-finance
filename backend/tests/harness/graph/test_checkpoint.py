"""Unit tests for the checkpoint scope/thread-id helpers and ``CheckpointerCache`` (T3)."""

from __future__ import annotations

from pathlib import Path

from app.harness.graph.checkpoint import CheckpointerCache, db_path, safe_scope, thread_id


def _files_exist(path: Path) -> tuple[bool, bool, bool]:
    return path.exists(), Path(f"{path}-wal").exists(), Path(f"{path}-shm").exists()


def test_safe_scope_symbol_vs_workflow() -> None:
    assert safe_scope("PTT.BK") == "PTT.BK"
    assert safe_scope("_workflow_research") == "_workflow_research"


def test_thread_id_is_deterministic_and_scope_sensitive() -> None:
    a = thread_id("PTT.BK", "2026-09-26", "sig1")
    b = thread_id("PTT.BK", "2026-09-26", "sig1")
    c = thread_id("AAPL", "2026-09-26", "sig1")
    assert a == b
    assert a != c
    assert len(a) == 16


async def test_clear_checkpoint_and_clear_all_remove_files(tmp_path: Path) -> None:
    cache = CheckpointerCache(tmp_path)
    saver = await cache.get("_workflow_test")
    tid = thread_id("_workflow_test", "run_1", "sig")
    await saver.aput(
        {"configurable": {"thread_id": tid, "checkpoint_ns": ""}},
        {
            "v": 1,
            "id": "chk1",
            "ts": "2026-09-26T00:00:00+00:00",
            "channel_values": {},
            "channel_versions": {},
            "versions_seen": {},
        },
        {"source": "input", "step": 0, "writes": {}, "parents": {}},
        {},
    )
    path = db_path(tmp_path, "_workflow_test")
    assert path.exists()

    await cache.clear_checkpoint("_workflow_test", tid)
    tuple_after = await saver.aget_tuple({"configurable": {"thread_id": tid, "checkpoint_ns": ""}})
    assert tuple_after is None

    await cache.clear_all("_workflow_test")
    assert _files_exist(path) == (False, False, False)

    await cache.close_all()
