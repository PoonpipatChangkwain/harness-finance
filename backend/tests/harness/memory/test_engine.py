"""(P17) ``MemoryEngine`` with a scripted FakeLLM: ADD / UPDATE / SUPERSEDE / RETIRE / NOOP, the
deterministic guards, candidates, fail-safe on parse failure, events, discovery facts and recall."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from app.domain.memory.schemas import DiscoveryAssessment, ReconcileOp, ReconcileOps
from app.harness.events.schema import validate_payload
from app.harness.llm.fake import FakeLLM
from app.harness.memory.engine import MemoryEngine
from app.harness.memory.store import UserMemoryStore
from app.settings import Settings
from tests.harness.conftest import RecordingEmitter
from tests.harness.memory.helpers import USER, build, fact, facts, lesson, lessons, row, structured_calls

pytestmark = pytest.mark.usefixtures("db")

DAY = date(2026, 10, 17)


def op(kind: str, target: str | None = None, text: str = "", **kw: Any) -> ReconcileOps:
    return ReconcileOps(
        ops=[ReconcileOp.model_validate({"op": kind, "target_id": target, "text": text} | kw)]
    )


async def learn(engine: MemoryEngine, *messages: str, **kw: Any) -> Any:
    return await engine.learn(
        USER, user_messages=list(messages), source_ref={"session_id": "ses_1"}, observation_date=DAY, **kw
    )


async def test_add_without_neighbours_skips_reconcile(settings: Settings) -> None:
    llm = FakeLLM([facts(fact("ผู้ใช้ชอบลงทุนเรื่อย ๆ ระยะยาว", key="pace", value_json={"pace": "slow"}))])
    engine, store, _ = build(settings, llm)
    changes = await learn(engine, "ผมชอบลงทุนเรื่อย ๆ ระยะยาว")
    assert [c.op for c in changes] == ["ADD"]
    item = changes[0].item
    assert item.status == "active" and item.source == "user_stated" and item.confidence == 0.9
    assert item.source_ref == {"session_id": "ses_1", "prompt_version": "memory_extract.v1"}
    assert structured_calls(llm) == ["ExtractedFacts"]
    assert {(c.tier, c.purpose) for c in llm.calls} == {("quick", "memory")}
    assert "ผมชอบลงทุนเรื่อย ๆ ระยะยาว" in llm.calls[0].messages[1].content
    assert [i.id for i in await store.list_items(USER)] == [item.id]


async def test_supersede_slow_to_fast_via_reconcile(settings: Settings) -> None:
    llm = FakeLLM([facts(fact("ผู้ใช้ชอบลงทุนเรื่อย ๆ ระยะยาว"))])
    engine, store, _ = build(settings, llm)
    old = (await learn(engine, "ผมชอบลงทุนเรื่อย ๆ"))[0].item
    llm.push(facts(fact("ผู้ใช้ตอนนี้ชอบลงทุนเร็ว ๆ ระยะสั้น")))
    llm.push(op("SUPERSEDE", old.id, "ผู้ใช้ตอนนี้ชอบลงทุนแบบเร็ว ๆ ระยะสั้น", reason="preference changed"))
    changes = await learn(engine, "ตอนนี้ไม่ชอบแล้ว ชอบเร็ว ๆ")
    assert [c.op for c in changes] == ["SUPERSEDE"]
    assert structured_calls(llm)[-2:] == ["ExtractedFacts", "ReconcileOps"]
    new = changes[0].item
    assert new.text == "ผู้ใช้ตอนนี้ชอบลงทุนแบบเร็ว ๆ ระยะสั้น" and new.supersedes == old.id
    assert new.source_ref["reconcile_prompt"] == "memory_reconcile.v1"
    previous = await store.get(USER, old.id)
    assert previous is not None and previous.status == "superseded" and previous.valid_to is not None
    profile, _ = await engine.profile(USER)
    assert profile.items_used == [new.id]
    assert "เร็ว ๆ" in profile.text and "เรื่อย ๆ" not in profile.text


async def test_update_merges_into_a_new_row(settings: Settings) -> None:
    llm = FakeLLM([facts(fact("ผู้ใช้เก็บเงินซื้อบ้าน", category="goal"))])
    engine, store, _ = build(settings, llm)
    old = (await learn(engine, "ผมเก็บเงินซื้อบ้าน"))[0].item
    llm.push(facts(fact("ผู้ใช้มีงบซื้อบ้าน 5 ล้านบาทภายในปี 2028", category="goal")))
    llm.push(op("UPDATE", old.id, "ผู้ใช้เก็บเงินซื้อบ้านงบ 5 ล้านบาทภายในปี 2028", value_json={"amount": "5000000"}))
    changes = await learn(engine, "งบ 5 ล้านภายในปี 2028")
    assert [c.op for c in changes] == ["UPDATE"]
    assert changes[0].previous is not None and changes[0].previous.id == old.id
    assert changes[0].item.value_json == {"amount": "5000000"}
    assert [i.status for i in await store.history(USER, old.id)] == ["superseded", "active"]


async def test_retire_and_noop(settings: Settings) -> None:
    llm = FakeLLM([facts(fact("ผู้ใช้ไม่ลงทุนคริปโต", category="constraint"))])
    engine, store, _ = build(settings, llm)
    rule = (await learn(engine, "ผมไม่ลงทุนคริปโตเด็ดขาด"))[0].item
    llm.push(facts(fact("ผู้ใช้ไม่ลงทุนในคริปโตเลย", category="constraint")), op("NOOP", rule.id))
    changes = await learn(engine, "ย้ำว่าไม่เอาคริปโต")
    assert [c.op for c in changes] == ["NOOP"]
    touched = await store.get(USER, rule.id)
    assert touched is not None and touched.access_count == 1 and touched.status == "active"
    assert len(await store.list_items(USER)) == 1
    llm.push(facts(fact("ผู้ใช้ยกเลิกข้อห้ามคริปโตแล้ว", category="constraint")), op("RETIRE", rule.id))
    changes = await learn(engine, "เรื่องไม่เอาคริปโตไม่ใช้แล้ว")
    assert [c.op for c in changes] == ["RETIRE"]
    retired = await store.get(USER, rule.id)
    assert retired is not None and retired.status == "retired" and retired.valid_to is not None


async def test_key_collision_supersedes_without_llm(settings: Settings) -> None:
    engine, store, llm = build(settings)
    old = await store.add(USER, row("ผู้ใช้ลงทุนช้า ๆ", key="pace", value_json={"pace": "slow"}))
    llm.push(facts(fact("ผู้ใช้ชอบลงทุนเร็ว ๆ", key="pace", value_json={"pace": "fast"})))
    changes = await learn(engine, "ชอบเร็ว ๆ")
    assert [c.op for c in changes] == ["SUPERSEDE"]
    assert structured_calls(llm) == ["ExtractedFacts"]  # the guard decided, no reconcile call
    assert changes[0].previous is not None and changes[0].previous.id == old.id
    # same key + same value → NOOP, still no LLM
    llm.push(facts(fact("ผู้ใช้ชอบลงทุนแบบเร็ว", key="pace", value_json={"pace": "FAST"})))
    changes = await learn(engine, "ย้ำว่าชอบเร็ว")
    assert [c.op for c in changes] == ["NOOP"]
    assert structured_calls(llm) == ["ExtractedFacts", "ExtractedFacts"]


async def test_inferred_cannot_supersede_stated(settings: Settings) -> None:
    engine, store, llm = build(settings)
    stated = await store.add(
        USER,
        row("ผู้ใช้รับความเสี่ยงได้ 7/10", category="risk_tolerance", key="risk_tolerance.score",
            value_json={"score": 7}),
    )  # fmt: skip
    llm.push(
        facts(
            fact("ผู้ใช้ดูกลัวขาดทุนมากหลังตลาดลง", category="risk_tolerance", key="risk_tolerance.score",
                 value_json={"score": 3}, source="inferred_behavior", confidence=0.5)
        )
    )  # fmt: skip
    changes = await learn(engine, "ตลาดลงแรง กลัวมาก")
    assert [c.op for c in changes] == ["ADD"]
    pattern = changes[0].item
    assert pattern.category == "behavioral_pattern" and pattern.flags == ["stated_vs_revealed"]
    assert pattern.source_ref["conflicts_with"] == stated.id
    assert pattern.confidence == 0.5
    still = await store.get(USER, stated.id)
    assert still is not None and still.status == "active"
    # the LLM path: reconcile says SUPERSEDE a stated item → converted the same way
    llm.push(
        facts(fact("ผู้ใช้น่าจะรับความเสี่ยงต่ำ", category="risk_tolerance", source="inferred_behavior")),
        op("SUPERSEDE", stated.id, "ผู้ใช้รับความเสี่ยงต่ำ"),
    )
    changes = await learn(engine, "ขายหมดดีไหม")
    assert [(c.op, c.item.category) for c in changes] == [("ADD", "behavioral_pattern")]
    assert (await store.get(USER, stated.id)).status == "active"  # type: ignore[union-attr]


async def test_inferred_risk_tolerance_is_a_candidate(settings: Settings) -> None:
    engine, store, llm = build(settings)
    llm.push(facts(fact("ผู้ใช้น่าจะรับความเสี่ยงได้สูง", category="risk_tolerance", source="inferred_behavior",
                        confidence=0.65)))  # fmt: skip
    changes = await learn(engine, "all-in หุ้นซิ่งเลย")
    assert changes[0].item.status == "candidate" and changes[0].item.confidence == 0.65
    profile, _ = await engine.profile(USER)
    assert profile.items_used == []  # a candidate is not in the profile until confirmed
    # a candidate never replaces an active (non-stated) row before confirmation
    base = await store.add(
        USER, row("ผู้ใช้มีความเสี่ยงปานกลางจากพอร์ต", category="goal", key="goal:main", source="portfolio_data")
    )
    llm.push(facts(fact("ผู้ใช้น่าจะอยากเกษียณเร็ว", category="goal", key="goal:main", source="inferred_behavior")))
    changes = await learn(engine, "อยากหยุดทำงานเร็ว ๆ")
    assert [c.op for c in changes] == ["SUPERSEDE"]
    pending = changes[0].item
    assert pending.status == "candidate" and pending.supersedes == base.id
    assert (await store.get(USER, base.id)).status == "active"  # type: ignore[union-attr]


async def test_structured_failure_writes_nothing(settings: Settings) -> None:
    engine, store, llm = build(settings)
    emitter = RecordingEmitter()
    llm.push(TimeoutError("down"))
    assert await learn(engine, "ผมชอบ ETF", emit=emitter) == []
    assert emitter.types() == ["warning"]
    assert emitter.events[0]["payload"]["code"] == "memory_learn_failed"
    # extract works, but the second fact's reconcile fails → the first fact is not written either
    await store.add(USER, row("ผู้ใช้ชอบหุ้นปันผล", category="preference"))
    llm.push(
        facts(
            fact("ผู้ใช้ชอบกองทุนดัชนี", category="knowledge_level"), fact("ผู้ใช้ชอบหุ้นปันผลสูง", category="preference")
        ),
        "not json at all",
    )
    assert await learn(engine, "ชอบกองทุนดัชนีกับหุ้นปันผล") == []
    assert len(await store.list_items(USER)) == 1


async def test_low_confidence_dropped_and_disabled(settings: Settings) -> None:
    engine, _, llm = build(settings)
    llm.push(facts(fact("ผู้ใช้อาจจะชอบทองคำ", confidence=0.3)))
    assert await learn(engine, "ทองก็น่าสนใจนะ") == []
    settings.memory_enabled = False
    assert await learn(engine, "anything") == []
    assert len(llm.calls) == 1


async def test_memory_updated_event_skips_noop(settings: Settings) -> None:
    engine, store, llm = build(settings)
    emitter = RecordingEmitter()
    old = await store.add(USER, row("ผู้ใช้ลงทุนช้า ๆ", key="pace", value_json={"pace": "slow"}))
    llm.push(
        facts(
            fact("ผู้ใช้ชอบลงทุนเร็ว ๆ", key="pace", value_json={"pace": "fast"}),
            fact("ผู้ใช้อยากได้คำตอบสั้น ๆ", category="preference"),
        )
    )
    changes = await learn(engine, "ชอบเร็ว ตอบสั้น ๆ", emit=emitter, node="learn")
    assert sorted(c.op for c in changes) == ["ADD", "SUPERSEDE"]
    assert emitter.types() == ["memory.updated"]
    event = emitter.events[0]
    assert event["node"] == "learn"
    payload = validate_payload("memory.updated", event["payload"])
    sup = next(c for c in payload["changes"] if c["op"] == "SUPERSEDE")
    assert sup["previous_text"] == old.text and sup["status"] == "active"


async def test_discovery_goal_becomes_a_stated_fact(settings: Settings) -> None:
    engine, _, llm = build(settings)
    discovery = DiscoveryAssessment.model_validate(
        {
            "request_type": "buy_single",
            "root_goal_identified": True,
            "confidence": 0.8,
            "root_goal": {"category": "retirement", "text": "เกษียณภายใน 10 ปี"},
            "memory_candidates": [
                {
                    "category": "goal",
                    "key": "goal:retire_2036",
                    "text": "ผู้ใช้ตั้งเป้าเกษียณภายในปี 2036",
                    "confidence": 0.8,
                }
            ],
        }
    )
    changes = await engine.learn(USER, user_messages=[], discovery=discovery, observation_date=DAY)
    assert [(c.op, c.item.category, c.item.status, c.item.source) for c in changes] == [
        ("ADD", "goal", "active", "user_stated")
    ]
    assert llm.calls == []  # nothing to extract → no LLM
    # with messages, the confirmed root goal is given to the extractor as context
    llm.push(facts())
    await engine.learn(USER, user_messages=["ใช่ครับ"], discovery=discovery, observation_date=DAY)
    assert "เกษียณภายใน 10 ปี" in llm.calls[0].messages[1].content


async def test_discovery_facts_own_their_category_in_the_batch(settings: Settings) -> None:
    """Live P17 run: the extractor restated the confirmed goal under another key → two goal rows.
    The root goal's candidates win their category; other extracted facts still go through."""
    engine, store, llm = build(settings)
    discovery = DiscoveryAssessment.model_validate(
        {
            "root_goal_identified": True,
            "confidence": 0.9,
            "root_goal": {"category": "home", "text": "ดาวน์คอนโดภายใน 3 ปี"},
            "memory_candidates": [
                {
                    "category": "goal",
                    "key": "goal:condo_2029",
                    "text": "ผู้ใช้เก็บเงินดาวน์คอนโดภายในปี 2029",
                    "confidence": 0.9,
                }
            ],
        }
    )
    llm.push(
        facts(
            fact("ผู้ใช้ต้องการเก็บเงินดาวน์คอนโดให้ได้ภายใน 3 ปี", category="goal", key="goal:condo_downpayment"),
            fact("ผู้ใช้ชอบให้เงินโตเร็ว", key="pace", value_json={"pace": "fast"}),
        )
    )
    changes = await engine.learn(
        USER, user_messages=["อยากได้เงินดาวน์คอนโดใน 3 ปี"], discovery=discovery, observation_date=DAY
    )
    assert sorted((c.op, c.item.category, c.item.key) for c in changes) == [
        ("ADD", "goal", "goal:condo_2029"),
        ("ADD", "investment_style", "pace"),
    ]
    assert len(await store.list_items(USER, statuses=["active"], category="goal")) == 1


async def test_recall_profile_items_and_lessons(settings: Settings) -> None:
    engine, store, llm = build(settings)
    goal = await store.add(USER, row("ผู้ใช้เก็บเงินดาวน์คอนโดปี 2029", category="goal", importance=9))
    cand = await store.add(USER, row("ผู้ใช้อาจรับความเสี่ยงได้สูง", category="risk_tolerance", status="candidate"))
    await store.add("usr_other", row("ผู้ใช้อีกคนเก็บเงินดาวน์คอนโด", category="goal"))
    llm.push(lessons(lesson("Check the goal horizon before sizing")), FakeLLMVerdict.ok())
    await engine.bank.distill(
        run_summary="research",
        outcome=FakeLLMVerdict.success(),
        tags={"workflow": "research"},
        source_run_id="run_1",
        user_id=USER,
    )
    recall = await engine.recall(USER, "คอนโดกับความเสี่ยง", workflow="research", tags={"market": "US"})
    assert recall.profile_text.startswith("- [goal] ผู้ใช้เก็บเงินดาวน์คอนโดปี 2029")
    assert {i.id for i in recall.items} == {goal.id, cand.id}
    assert [r.title for r in recall.reasoning] == ["Check the goal horizon before sizing"]
    assert recall.provenance["profile_item_ids"] == [goal.id]
    assert (await store.get(USER, goal.id)).access_count == 1  # type: ignore[union-attr]
    settings.memory_enabled = False
    assert (await engine.recall(USER, "x", workflow="research")).profile_text == ""


async def test_add_manual_without_llm_and_edit(settings: Settings) -> None:
    def broken() -> Any:
        raise RuntimeError("no key")

    store = UserMemoryStore()
    from app.harness.memory.bank import ReasoningBank

    engine = MemoryEngine(broken, store, ReasoningBank(broken, settings), settings)
    await store.add(USER, row("ผู้ใช้ชอบหุ้นปันผล", category="preference"))
    changes, warnings = await engine.add_manual(USER, category="preference", text="ผู้ใช้ชอบหุ้นปันผลสูง")
    assert [c.op for c in changes] == ["ADD"] and warnings
    assert changes[0].item.source == "manual" and changes[0].item.confidence == 0.95
    # key collision still works without an LLM
    changes, warnings = await engine.add_manual(
        USER, category="goal", text="ผู้ใช้เก็บเงิน 2 ล้าน", key="goal:fund", value_json={"amount": "2000000"}
    )
    changes, warnings = await engine.add_manual(
        USER, category="goal", text="ผู้ใช้เก็บเงิน 3 ล้าน", key="goal:fund", value_json={"amount": "3000000"}
    )
    assert [c.op for c in changes] == ["SUPERSEDE"] and warnings == []
    edited = await engine.edit(USER, changes[0].item.id, text="ผู้ใช้เก็บเงิน 3.5 ล้าน")
    assert edited is not None
    new, old = edited
    assert new.supersedes == old.id and new.key == "goal:fund" and new.value_json == {"amount": "3000000"}
    assert old.status == "superseded"
    assert await engine.edit(USER, "mem_missing", text="x x x") is None


class FakeLLMVerdict:
    """Small factory for the bank's privacy verdict / outcome inputs."""

    @staticmethod
    def ok() -> Any:
        from app.domain.memory.schemas import PrivacyVerdict

        return PrivacyVerdict(identifies_person_or_holdings=False, reason="generic")

    @staticmethod
    def success() -> Any:
        from app.domain.memory.schemas import OutcomeJudgement

        return OutcomeJudgement(success=True, score=0.9, reason="cited answer")
