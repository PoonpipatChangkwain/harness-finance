"""ContextManager: runs before every agent LLM call so the context never blows up (D1).

Ported from ``manageContextThreshold`` in ``../dexter/src/agent/agent.ts``
plus ``../dexter/src/memory/flush.ts``. Order per call:

1. microcompact (every turn, no LLM)
2. context size ≤ threshold → done
3. memory flush (once per compaction cycle, optional, best effort)
4. full compaction when failures < cap and ≥ N active tool results
5. fallback: drop the oldest whole rounds, keeping ≥ ``keep_tool_uses`` results

Every step that changes the messages emits a ``context.*`` event and writes a
``compaction`` scratchpad entry. The scratchpad itself is never rewritten.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from app.domain.ports import LLMMessage, LLMPort
from app.harness.context.compact import CompactionFailed, compact_context, compact_messages, fit_text
from app.harness.context.config import ContextConfig
from app.harness.context.messages import assert_tool_pairing, truncate_messages
from app.harness.context.microcompact import ToolLookup, microcompact
from app.harness.context.tokens import (
    compact_threshold,
    context_window,
    current_context_tokens,
    estimate_messages_tokens,
    usage_percent,
)
from app.harness.runtime.context import RunContext
from app.harness.state.memory import MemoryStore
from app.harness.state.scratchpad import format_tool_result

logger = logging.getLogger(__name__)

MEMORY_FLUSH_TOKEN = "NO_MEMORY_TO_FLUSH"

MEMORY_FLUSH_PROMPT = f"""Session context is close to compaction. Summarize durable facts and user preferences worth remembering long-term.

Rules:
- Output concise markdown bullet points, in the user's language.
- Include durable facts, explicit user preferences, and stable decisions.
- Prioritize capturing personal financial information:
  - Financial goals (retirement targets, savings goals, income targets)
  - Risk tolerance and investment philosophy
  - Portfolio decisions and allocation changes
  - Trade history and the reasoning behind buy/sell decisions
- Also capture personal context that affects financial advice: time horizons, liquidity needs, tax jurisdiction.
- Do not include temporary tool output, market data, or stock prices.
- If nothing should be stored, reply exactly with {MEMORY_FLUSH_TOKEN}."""  # noqa: E501


class ContextManager:
    def __init__(
        self,
        llm: LLMPort,
        tools: ToolLookup,
        *,
        model: str,
        summary_model: str | None = None,
        config: ContextConfig | None = None,
        memory: MemoryStore | None = None,
    ) -> None:
        self.llm = llm
        self.tools = tools
        self.config = config or ContextConfig()
        self.window = context_window(model, self.config)
        self.threshold = compact_threshold(model, self.config)
        summary_window = context_window(summary_model or model, self.config)
        # Room for the summarizer's input: its window minus the output reserve and prompt overhead.
        self.summary_input_chars = int(
            max(8_000, summary_window - self.config.max_output_tokens_for_summary - 6_000)
            * self.config.chars_per_token
        )
        self.memory = memory
        self.failures = 0
        self.memory_flushed = False

    # -- public ------------------------------------------------------------------------

    async def report_usage(self, ctx: RunContext, messages: list[LLMMessage]) -> int:
        """Emit ``context.usage`` (call right after each agent LLM call)."""
        used = current_context_tokens(ctx, messages, self.config)
        await ctx.emit.emit(
            "context.usage",
            {"used_tokens": used, "context_window": self.window, "percent": usage_percent(used, self.window)},
        )
        return used

    async def before_llm_call(self, ctx: RunContext, messages: list[LLMMessage]) -> list[LLMMessage]:
        messages = await self._microcompact(ctx, messages)
        tokens = current_context_tokens(ctx, messages, self.config)
        if tokens <= self.threshold:
            return self._checked(messages)

        if self.config.memory_flush and self.memory is not None and not self.memory_flushed:
            await self._memory_flush(ctx)

        if (
            self.failures < self.config.max_consecutive_compaction_failures
            and ctx.scratchpad.active_tool_result_count() >= self.config.min_tool_results_for_compaction
        ):
            compacted = await self._compact(ctx, messages, tokens)
            if compacted is not None:
                return self._checked(compacted)

        return self._checked(await self._fallback_clear(ctx, messages, tokens))

    # -- steps ---------------------------------------------------------------------------

    def _checked(self, messages: list[LLMMessage]) -> list[LLMMessage]:
        if self.config.check_pairing:
            assert_tool_pairing(messages)
        return messages

    async def _microcompact(self, ctx: RunContext, messages: list[LLMMessage]) -> list[LLMMessage]:
        result = microcompact(messages, tools=self.tools, scratchpad=ctx.scratchpad, config=self.config)
        if not result.cleared:
            return result.messages
        ctx.token_counter.discount(result.tokens_saved)
        ctx.scratchpad.append(
            "compaction",
            kind="microcompact",
            trigger=result.trigger,
            cleared_call_ids=result.cleared_call_ids,
            tokens_saved=result.tokens_saved,
        )
        await ctx.emit.emit(
            "context.microcompact", {"cleared": result.cleared, "tokens_saved": result.tokens_saved}
        )
        return result.messages

    def _tool_results_text(self, ctx: RunContext) -> str:
        return fit_text([ctx.scratchpad.compaction_input()], self.summary_input_chars)

    async def _compact(
        self, ctx: RunContext, messages: list[LLMMessage], pre_tokens: int
    ) -> list[LLMMessage] | None:
        await ctx.emit.emit("context.compaction", {"phase": "start", "pre_tokens": pre_tokens})
        entries = ctx.scratchpad.tool_results_since_compaction()
        blocks = [format_tool_result(e) for e in entries]
        if ctx.scratchpad.compaction_summary:
            blocks.insert(0, ctx.scratchpad.compaction_summary)
        try:
            result = await compact_context(
                self.llm, query=ctx.query, tool_results=fit_text(blocks, self.summary_input_chars)
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # LLM/provider errors, CompactionFailed
            self.failures += 1
            reason = str(exc) if isinstance(exc, CompactionFailed) else f"{type(exc).__name__}: {exc}"
            logger.warning("compaction failed (%d in a row): %s", self.failures, reason)
            ctx.scratchpad.append("compaction", kind="failed", error=reason, pre_tokens=pre_tokens)
            await ctx.emit.emit(
                "context.compaction", {"phase": "failed", "pre_tokens": pre_tokens, "summary": reason}
            )
            return None

        compacted = compact_messages(messages, result.summary, ctx.query)
        post_tokens = estimate_messages_tokens(compacted, self.config.chars_per_token)
        ctx.scratchpad.set_compaction_summary(result.summary)
        ctx.scratchpad.append(
            "compaction",
            kind="full",
            summary=result.summary,
            pre_tokens=pre_tokens,
            post_tokens=post_tokens,
            covered_tool_results=len(entries),
        )
        ctx.token_counter.invalidate()
        self.failures = 0
        self.memory_flushed = False
        await ctx.emit.emit(
            "context.compaction",
            {"phase": "end", "pre_tokens": pre_tokens, "post_tokens": post_tokens, "summary": result.summary},
        )
        return compacted

    async def _fallback_clear(
        self, ctx: RunContext, messages: list[LLMMessage], pre_tokens: int
    ) -> list[LLMMessage]:
        result = truncate_messages(messages, self.config.keep_tool_uses)
        if not result.removed_messages:
            return result.messages
        ctx.scratchpad.mark_cleared(result.removed_call_ids)
        post_tokens = estimate_messages_tokens(result.messages, self.config.chars_per_token)
        ctx.token_counter.invalidate()
        self.memory_flushed = False
        ctx.scratchpad.append(
            "compaction",
            kind="fallback_clear",
            removed_call_ids=result.removed_call_ids,
            removed_messages=result.removed_messages,
            pre_tokens=pre_tokens,
            post_tokens=post_tokens,
        )
        await ctx.emit.emit(
            "context.compaction",
            {
                "phase": "fallback_clear",
                "pre_tokens": pre_tokens,
                "post_tokens": post_tokens,
                "cleared": result.removed_messages,
                "kept": result.kept_tool_results,
            },
        )
        return result.messages

    async def _memory_flush(self, ctx: RunContext) -> None:
        """Best effort: never blocks compaction. Writes only when the LLM returns real notes."""
        self.memory_flushed = True
        assert self.memory is not None
        prompt = (
            f"Original user query:\n{ctx.query}\n\nRelevant retrieved context:\n"
            f"{self._tool_results_text(ctx) or '[no tool results yet]'}\n\n{MEMORY_FLUSH_PROMPT}"
        )
        try:
            response = await self.llm.complete(
                [LLMMessage(role="user", content=prompt)], tier="quick", tools=None, purpose="compact"
            )
            notes = response.text.strip()
            if not notes or notes == MEMORY_FLUSH_TOKEN or response.tool_calls:
                return
            value: dict[str, Any] = {
                "query": ctx.query,
                "notes": notes,
                "session_id": ctx.session_id,
                "at": datetime.now(UTC).isoformat(),
            }
            await self.memory.put(self.config.memory_namespace, ctx.run_id, value)
            ctx.scratchpad.add_note("memory flush", namespace=self.config.memory_namespace, notes=notes)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("memory flush skipped: %s", exc)
