"""(P17) ReasoningBank: distill → scrub → privacy judge, dedupe, k-anonymity gate, retrieve rerank,
record_use / settle (+ auto-retire). No holdings or PII may ever reach the table."""

from __future__ import annotations

import pytest

from app.domain.memory.schemas import OutcomeJudgement, PrivacyVerdict
from app.harness.events.schema import validate_payload
from app.harness.llm.fake import FakeLLM
from app.harness.memory.bank import ReasoningBank
from app.settings import Settings
from tests.harness.conftest import RecordingEmitter
from tests.harness.memory.helpers import USER, lesson, lessons, structured_calls

pytestmark = pytest.mark.usefixtures("db")

OK = PrivacyVerdict(identifies_person_or_holdings=False, reason="generic")
LEAK = PrivacyVerdict(identifies_person_or_holdings=True, reason="names a holding with a quantity")
WIN = OutcomeJudgement(success=True, score=0.9, reason="cited answer, goal known")
LOSS = OutcomeJudgement(success=False, score=0.2, reason="hit the iteration limit")


def bank_with(settings: Settings, *script: object) -> tuple[ReasoningBank, FakeLLM]:
    llm = FakeLLM(list(script))  # type: ignore[arg-type]
    return ReasoningBank(lambda: llm, settings), llm


async def distill(bank: ReasoningBank, outcome: OutcomeJudgement = WIN, **kw: object) -> list:  # type: ignore[type-arg]
    tags = kw.pop("tags", {"workflow": "research", "market": "US"})
    return await bank.distill(
        run_summary="user asked to buy a stock; answered with sourced data; asked one why",
        outcome=outcome,
        tags=tags,  # type: ignore[arg-type]
        source_run_id="run_A",
        user_id=USER,
        **kw,  # type: ignore[arg-type]
    )


async def test_distill_scrubs_and_judges_before_insert(settings: Settings) -> None:
    holding = lesson("Trim the bank stock", content="User holds KBANK 2,000 หุ้น at ฿1,250,000; trim to 17%.")
    email = lesson("Email the client", content="Send the plan to client@example.com before rebalancing.")
    good = lesson(
        "Confirm the horizon before sizing a speculative buy", content="Keep it under 20% of the book."
    )
    bank, llm = bank_with(settings, lessons(holding, email, good), LEAK, OK)
    emitter = RecordingEmitter()
    items = await distill(bank, emit=emitter, node="learn")
    # email → dropped by the scrubber (no judge call); KBANK line → scrubbed, then rejected by the judge
    assert structured_calls(llm) == ["DistilledLessons", "PrivacyVerdict", "PrivacyVerdict"]
    judged = llm.calls[1].messages[1].content
    assert "KBANK" not in judged and "2,000" not in judged and "Thai bank large-cap" in judged
    assert [i.title for i in items] == ["Confirm the horizon before sizing a speculative buy"]
    item = items[0]
    assert item.id.startswith("rb_") and item.status == "active" and item.kind == "strategy"
    assert item.tags == {"workflow": "research", "market": "US"}
    assert (
        item.prompt_version == "rb_distill.v1" and item.source_run_id == "run_A" and item.success_rate == 0.5
    )
    assert {(c.tier, c.purpose) for c in llm.calls} == {("quick", "memory")}
    stored = await bank.list_items()
    assert [i.content for i in stored] == ["Keep it under 20% of the book."]
    assert await bank.fts_ids() == {item.id}
    assert emitter.types() == ["reasoning.distilled"]
    validate_payload("reasoning.distilled", emitter.events[0]["payload"])


async def test_failure_becomes_pitfall_and_judge_failure_drops(settings: Settings) -> None:
    bank, llm = bank_with(settings, lessons(lesson("Stop at the limit with a partial answer")), OK)
    items = await distill(bank, LOSS)
    assert [(i.kind, i.outcome_signal, i.judge_score) for i in items] == [("pitfall", "failure", 0.2)]
    llm.push(lessons(lesson("Another lesson about horizons")), "garbage")  # judge unparsable → drop
    assert await distill(bank) == []
    llm.push("not json")  # distill unparsable → nothing, warning
    emitter = RecordingEmitter()
    assert await distill(bank, emit=emitter) == []
    assert emitter.events[0]["payload"]["code"] == "reasoning_distill_failed"


async def test_dedupe_counts_sources_and_k_anonymity_gate(settings: Settings) -> None:
    settings.reasoning_bank_min_sources = 2
    first = lesson("Confirm the horizon before sizing")
    bank, llm = bank_with(settings, lessons(first), OK)
    [item] = await distill(bank)
    assert item.status == "candidate" and item.source_count == 1
    assert await bank.retrieve("horizon sizing", {"workflow": "research"}) == []  # candidates are not served
    llm.push(lessons(lesson("confirm the HORIZON before sizing")), OK)  # same title, other case
    [again] = await distill(bank)
    assert again.id == item.id and again.source_count == 2 and again.status == "active"
    near = lesson(
        "Confirm the horizon before sizing!",
        description="Applies when a user wants to buy a single stock.",
    )
    llm.push(lessons(near), OK)
    [third] = await distill(bank)
    assert third.id == item.id and third.source_count == 3
    assert len(await bank.list_items()) == 1


async def test_retrieve_filters_tags_and_reranks_by_success(settings: Settings) -> None:
    bank, _ = bank_with(
        settings,
        lessons(
            lesson("Size speculative buys as a satellite", content="Satellite cap for speculative names.")
        ),
        OK,
        lessons(
            lesson("Satellite cap again for speculative positions", content="Satellite speculative cap.")
        ),
        OK,
        lessons(lesson("Thai dividend traps", content="Check payout ratio for SET dividend names.")),
        OK,
        lessons(lesson("Rebalance bands", content="Use 5/25 bands.")),
        OK,
    )
    [a] = await distill(bank)
    [b] = await distill(bank)
    [c] = await distill(bank, tags={"workflow": "research", "market": "SET"})
    [d] = await distill(bank, tags={"workflow": "portfolio"})
    # b has a much better track record
    await bank.record_use([b.id], "run_1", USER)
    await bank.settle("run_1", True)
    await bank.record_use([a.id], "run_2", USER)
    await bank.settle("run_2", False)
    got = await bank.retrieve("speculative satellite", {"workflow": "research", "market": "US"}, k=2)
    assert [i.id for i in got] == [b.id, a.id]
    assert c.id not in [i.id for i in got]  # market SET filtered out
    assert [i.id for i in await bank.retrieve("bands", {"workflow": "portfolio"})] == [d.id]
    # no word overlap: still the best item for the context (ReasoningBank always injects top-1)
    assert [i.id for i in await bank.retrieve("zzzz qqqq", {"workflow": "portfolio"}, k=1)] == [d.id]
    assert await bank.retrieve("anything", {"workflow": "trade"}) == []
    assert await bank.retrieve("anything", {"workflow": "research"}, k=0) == []


async def test_record_use_settle_idempotent_and_auto_retire(settings: Settings) -> None:
    bank, _ = bank_with(settings, lessons(lesson("A lesson that keeps failing")), OK)
    [item] = await distill(bank)
    await bank.record_use([item.id, item.id], "run_X", USER)
    await bank.record_use([item.id], "run_X", USER)
    [after] = await bank.settle("run_X", True)
    assert (after.use_count, after.success_count) == (1, 1)
    assert await bank.settle("run_X", False) == []  # already settled
    uses = await bank.uses(item.id)
    assert [(u.run_id, u.outcome) for u in uses] == [("run_X", "success")]
    for n in range(9):
        await bank.record_use([item.id], f"run_F{n}", USER)
        await bank.settle(f"run_F{n}", False)
    final = await bank.get(item.id)
    assert final is not None and final.use_count == 10 and final.success_count == 1
    assert final.success_rate == pytest.approx(2 / 12) and final.status == "retired"


async def test_stats_counts_retire(settings: Settings) -> None:
    bank, _ = bank_with(settings, lessons(lesson("One"), lesson("Two lessons here", kind="coaching")), OK, OK)
    items = await distill(bank)
    assert {i.kind for i in items} == {"strategy", "coaching"}
    retired = await bank.retire(items[0].id)
    assert retired is not None and retired.status == "retired"
    assert await bank.retire("rb_missing") is None
    assert await bank.counts() == {"total": 2, "candidate": 0, "active": 1, "retired": 1}
    stats = await bank.stats()
    assert stats["total"] == 2 and stats["active"] == 1 and stats["by_workflow"] == {"research": 2}
    assert stats["avg_success_rate"] == 0.5
    assert [i.id for i in await bank.list_items(kind="coaching")] == [items[1].id]
    assert await bank.list_items(workflow="trade") == []
    assert [i.title for i in await bank.list_items(q="lessons")] == ["Two lessons here"]
