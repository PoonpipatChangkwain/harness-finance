"""(P17) ReasoningBank market tags and the system-prompt memory blocks (pure)."""

from __future__ import annotations

from app.domain.memory.messages import LESSONS_HEADING, PROFILE_HEADING, context_blocks, question_messages
from app.domain.memory.tags import market_of, market_tag


def test_market_tags() -> None:
    assert market_tag("PTT") == "SET" and market_tag("PTT.BK") == "SET"
    assert market_tag("AAPL") == "US"
    assert market_tag("EURUSD") == "FX" and market_tag("EURUSD=X") == "FX"
    assert market_tag("BTC-USD") == "CRYPTO"
    assert market_tag("^SET") is None and market_tag("") is None
    assert market_of(["", "^SET", "NVDA", "PTT"]) == "US" and market_of([]) is None


def test_context_blocks() -> None:
    assert context_blocks() == []
    lessons = [{"title": f"t{i}", "content": f"c{i}"} for i in range(3)]
    blocks = context_blocks(profile_text="ผู้ใช้ชอบปันผล", lessons=lessons, advice_mode="stabilise")
    assert blocks[0].startswith(PROFILE_HEADING) and "ผู้ใช้ชอบปันผล" in blocks[0]
    assert blocks[1].startswith(LESSONS_HEADING) and "- t0 — c0" in blocks[1] and "t2" not in blocks[1]
    assert "ADVICE MODE: stabilise" in blocks[2]
    assert context_blocks(advice_mode="normal") == []


def test_question_messages_quote_the_question() -> None:
    system, user = question_messages(question="Q?", user_message="อยากรวย", history=[("user", "hi")])
    assert "word for word" in system.content and "QUESTION" in user.content and "Q?" in user.content
    assert "อยากรวย" in user.content
