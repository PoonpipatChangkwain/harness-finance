"""(P17) LLM schemas are lenient (clamp/cut) and the prompt messages only take facts from the user."""

from __future__ import annotations

from datetime import date

from app.domain.memory.messages import (
    DISTILL_PROMPT,
    EXTRACT_PROMPT,
    distill_messages,
    extract_messages,
    load_prompt,
    privacy_messages,
)
from app.domain.memory.schemas import (
    DistilledLesson,
    DistilledLessons,
    ExtractedFacts,
    OutcomeJudgement,
    ReconcileOps,
    success_rate,
)


def test_extracted_facts_are_clamped_and_capped() -> None:
    facts = ExtractedFacts.model_validate(
        {
            "facts": [
                {"category": "preference", "text": f"User likes thing {i}", "confidence": 3, "importance": 99}
                for i in range(8)
            ]
        }
    )
    assert len(facts.facts) == 5
    assert facts.facts[0].confidence == 1.0 and facts.facts[0].importance == 10


def test_reconcile_op_aliases() -> None:
    ops = ReconcileOps.model_validate({"ops": [{"op": "delete", "target_id": ""}, {"op": "none"}]})
    assert [o.op for o in ops.ops] == ["RETIRE", "NOOP"]
    assert ops.ops[0].target_id is None


def test_lessons_capped_and_content_cut_to_150_words() -> None:
    lesson = {"title": "T" * 300, "description": "When it applies.", "content": "word " * 200}
    out = DistilledLessons.model_validate({"items": [lesson] * 5})
    assert len(out.items) == 3
    assert len(out.items[0].title) == 120
    assert len(out.items[0].content.split()) == 150


def test_success_rate_is_bayesian() -> None:
    assert success_rate(0, 0) == 0.5
    assert success_rate(9, 10) == 10 / 12


def test_extract_messages_fence_user_text_and_resolve_date() -> None:
    msgs = extract_messages(
        [f"m{i}" for i in range(14)] + ["  "],
        observation_date=date(2026, 10, 17),
        assistant_text="You should buy more NVDA.",
        confirmed_goal="Condo down payment by 2029",
    )
    assert msgs[0].role == "system" and "2026-10-17" in msgs[0].content
    assert "{observation_date}" not in msgs[0].content
    user = msgs[1].content
    assert "m3" not in user.split("<<<USER")[1].split("USER>>>")[0].split()  # only the last 10
    assert "10. m13" in user
    assert "never extract facts from it" in user
    assert "Condo down payment by 2029" in user
    assert load_prompt(EXTRACT_PROMPT).startswith("You maintain")


def test_distill_and_privacy_messages() -> None:
    msgs = distill_messages(
        run_summary="research run",
        outcome=OutcomeJudgement(success=False, score=0.2, reason="limit"),
        outcome_signal="failure",
        tags={"workflow": "research"},
        max_items=1,
    )
    assert "outcome=failure" in msgs[0].content and "at most 1" in msgs[0].content
    assert DISTILL_PROMPT == "rb_distill.v1"
    lesson = DistilledLesson(title="Check horizon", description="Before sizing.", content="Ask first.")
    assert '"title": "Check horizon"' in privacy_messages(lesson)[1].content
