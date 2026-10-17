"""Final answer: a separate deep-tier call with NO tools, built from the scratchpad (D2, F3).

Ported from Dexter's final-answer step (``../dexter/src/agent/agent.ts``):
the answer is written from the full tool results in the scratchpad (never
the compacted conversation), streamed as ``llm.delta{channel:"answer"}``,
then citations are resolved and numbers checked against the run's
provenance records before ``answer.final`` is emitted.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from app.domain.models.provenance import ProvenanceRecord
from app.domain.ports import AnswerCheck, LLMMessage, LLMPort
from app.harness.context.compact import fit_text
from app.harness.provenance.citations import resolve_citations, strip_citation_markers
from app.harness.provenance.number_check import cite_numbers, find_unverified_numbers
from app.harness.provenance.store import ProvenanceStore
from app.harness.runtime.context import RunContext
from app.harness.runtime.prompts import CLOSING_QUESTION_RULE, FINAL_SYSTEM_PROMPT
from app.harness.state.scratchpad import format_tool_result

logger = logging.getLogger(__name__)

STOP_NOTES = {
    "cancelled": "The user stopped the research early.",
    "iterations": "The research reached its maximum number of steps.",
    "tool_calls": "The research used its whole tool-call budget.",
    "timeout": "The research ran out of time.",
    "budget": "The research reached its cost/token budget.",
    "similar_query": "The research was stopped for repeating the same query.",
    "llm_error": "The model failed during research.",
}


def stop_note(stop_reason: str | None) -> str:
    if not stop_reason:
        return ""
    kind = stop_reason.split(":", 1)[0]
    why = STOP_NOTES.get(kind, stop_reason)
    return (
        f"NOTE: {why} ({stop_reason}). Answer from the data that is available, and say clearly what is "
        "missing because of this."
    )


def final_context(ctx: RunContext, max_chars: int) -> str:
    """Full tool results; if too long, the compaction summary + results after it, cut to fit."""
    entries = ctx.scratchpad.tool_results()
    blocks = [format_tool_result(e) for e in entries]
    full = "\n\n".join(blocks)
    if len(full) <= max_chars:
        return full
    summary = ctx.scratchpad.compaction_summary
    if summary:
        blocks = [summary, *(format_tool_result(e) for e in ctx.scratchpad.tool_results_since_compaction())]
    return fit_text(blocks, max_chars)


def check_answer(markdown: str, records: list[ProvenanceRecord]) -> tuple[AnswerCheck, list[str]]:
    resolved = resolve_citations(cite_numbers(markdown, records), {r.id for r in records})
    unverified = find_unverified_numbers(strip_citation_markers(resolved.markdown), records)
    check = AnswerCheck(
        markdown=resolved.markdown,
        citations=[c.model_dump() for c in resolved.citations],
        unverified_numbers=unverified,
    )
    return check, resolved.unknown_ids


DISCLAIMER = "ข้อมูลนี้ไม่ใช่คำแนะนำการลงทุน"
_DISCLAIMER_RE = re.compile(
    rf"{DISCLAIMER}|disclaimer|คำเตือน|หมายเหตุ"
    r"|(this is |the answer is )?(information,? )?not (an? )?investment advice",
    re.IGNORECASE,
)

RETRY_HOLLOW = (
    "Your reply contained only the disclaimer and no answer. Write the full answer to the question now "
    "from the DATA section, following all the rules, and end with the disclaimer line."
)


def is_hollow(markdown: str) -> bool:
    """True when the answer is empty or only the disclaimer (weak models sometimes return just rule 8)."""
    body = _DISCLAIMER_RE.sub("", strip_citation_markers(markdown))
    return not re.sub(r"[\s*_#>\-|.:—()]+", "", body)


def fallback_answer(ctx: RunContext, error: str, brief: str = "") -> str:
    if not is_hollow(brief):  # the agent's own draft beats a bare list of tool statuses
        note = f"> ไม่สามารถเขียนคำตอบสรุปได้ ({error}) — แสดงร่างผลวิจัยของ agent แทน"
        return f"{note}\n\n{brief.strip()}\n\n{DISCLAIMER}"
    lines = [f"ไม่สามารถเขียนคำตอบสรุปได้ ({error}) — ข้อมูลที่ดึงมาได้ในรอบนี้:", ""]
    for entry in ctx.scratchpad.tool_results():
        lines.append(f"- {entry.get('tool')}: status {entry.get('status')}")
    lines += ["", DISCLAIMER]
    return "\n".join(lines)


async def _stream_answer(ctx: RunContext, llm: LLMPort, messages: list[LLMMessage]) -> str:
    parts: list[str] = []
    async for delta in llm.stream(messages, tier="deep", purpose="final"):
        await ctx.emit.emit("llm.delta", {"channel": delta.channel, "text": delta.text})
        if delta.channel == "answer":
            parts.append(delta.text)
    return "".join(parts).strip()


async def write_final_answer(
    ctx: RunContext,
    llm: LLMPort,
    provenance: ProvenanceStore,
    *,
    title: str,
    query: str,
    history: list[LLMMessage],
    brief: str,
    stop_reason: str | None,
    context_chars: int,
    context_blocks: list[str] | None = None,
    closing_question: str | None = None,
    discovery: dict[str, Any] | None = None,
) -> AnswerCheck:
    """(P17) ``context_blocks`` (profile, lessons, advice mode) extend the system prompt;
    ``closing_question`` must end the answer word for word (appended if the model left it out);
    ``discovery`` goes into ``answer.final``."""
    data = final_context(ctx, context_chars) or "(no tool results)"
    note = stop_note(stop_reason)
    user = (
        f"Question: {query}\n\n"
        + (f"{note}\n\n" if note else "")
        + (f"## Research brief (guide only)\n{brief}\n\n" if brief else "")
        + f"## DATA (tool results with provenance ids)\n{data}"
    )
    system = FINAL_SYSTEM_PROMPT.format(title=title)
    if context_blocks:
        system = f"{system}\n\n" + "\n\n".join(context_blocks)
    if closing_question:
        system = f"{system}\n\n{CLOSING_QUESTION_RULE.format(question=closing_question)}"
    messages = [
        LLMMessage(role="system", content=system),
        *history,
        LLMMessage(role="user", content=user),
    ]
    try:
        text = await _stream_answer(ctx, llm, messages)
        if is_hollow(text):
            await ctx.emit.emit(
                "warning",
                {"code": "final_answer_hollow", "message": "final answer had no content; retrying once"},
            )
            retry = [*messages, LLMMessage(role="assistant", content=text or "(empty)")]
            text = await _stream_answer(ctx, llm, [*retry, LLMMessage(role="user", content=RETRY_HOLLOW)])
        if is_hollow(text):
            text = fallback_answer(ctx, "empty answer", brief)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("final answer failed: %s", exc)
        await ctx.emit.emit(
            "warning", {"code": "final_answer_failed", "message": f"{type(exc).__name__}: {exc}"}
        )
        text = fallback_answer(ctx, type(exc).__name__, brief)
    if closing_question and closing_question.strip() not in text:
        text = f"{text.rstrip()}\n\n{closing_question.strip()}"

    records = await provenance.list(ctx.run_id)
    check, unknown = check_answer(text, records)
    if unknown:
        await ctx.emit.emit(
            "warning",
            {
                "code": "unknown_citation",
                "message": f"dropped citations to unknown ids: {', '.join(unknown[:5])}",
            },
        )
    payload: dict[str, Any] = {
        "markdown": check.markdown,
        "citations": check.citations,
        "unverified_numbers": check.unverified_numbers,
    }
    if discovery:
        payload["discovery"] = discovery
    await ctx.emit.emit("answer.final", payload)
    return check


def context_budget_chars(window: int, chars_per_token: float = 3.5) -> int:
    """Room for DATA in the final prompt: window minus output reserve and prompt overhead."""
    return int(max(8_000, window - 20_000 - 8_000) * chars_per_token)
