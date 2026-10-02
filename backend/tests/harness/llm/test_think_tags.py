"""Inline ``<think>`` reasoning (ThaiLLM / Qwen-family via vLLM) is kept out of the answer."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from app.harness.llm.messages import ThinkStreamSplitter, split_content
from app.harness.structured.invoke import parse_structured


def test_split_content_moves_think_block_to_reasoning() -> None:
    answer, reasoning = split_content("<think>\nplan {not json}\n</think>\n\nสวัสดีครับ")
    assert answer == "สวัสดีครับ"
    assert reasoning == "plan {not json}"


def test_split_content_unclosed_think_is_all_reasoning() -> None:
    assert split_content("<think>cut off by max_tokens") == ("", "cut off by max_tokens")


def test_split_content_plain_text_untouched() -> None:
    assert split_content("no tags here") == ("no tags here", "")


def test_fallback_parse_ignores_braces_inside_think() -> None:
    class View(BaseModel):
        stance: str

    text = split_content('<think>{"stance": "draft"}</think>```json\n{"stance": "bear"}\n```')[0]
    view = parse_structured(text, View)
    assert view is not None and view.stance == "bear"


@pytest.mark.parametrize("size", [1, 2, 3, 5, 100])
def test_stream_splitter_handles_tags_split_across_chunks(size: int) -> None:
    text = "<think>reason A</think>answer <b>ok</b> <thin"
    splitter = ThinkStreamSplitter()
    parts: list[tuple[str, str]] = []
    for i in range(0, len(text), size):
        parts += splitter.feed(text[i : i + size])
    parts += splitter.flush()
    answer = "".join(t for c, t in parts if c == "answer")
    reasoning = "".join(t for c, t in parts if c == "reasoning")
    assert reasoning == "reason A"
    assert answer == "answer <b>ok</b> <thin"
