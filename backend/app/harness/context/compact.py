"""Full compaction: a fast LLM summarizes the session into a fixed 9-section structure (D1).

Ported from ``../dexter/src/agent/compact.ts`` (``NO_TOOLS_PREAMBLE``,
``ANALYSIS_INSTRUCTION``, ``BASE_COMPACT_PROMPT``, ``<analysis>``/``<summary>``
parsing, ``buildCompactSummaryMessage``) and ``compactMessages`` in
``../dexter/src/agent/agent.ts``. Finance changes: symbols in canonical
form (section 2), every number keeps its ``[prov_…]`` id (section 6) so
citations still resolve after compaction, and the summary is written in
the user's language. The summarizer reads the **full** tool results from
the scratchpad, never the microcompacted messages.
"""

from __future__ import annotations

import re

from pydantic import BaseModel

from app.domain.ports import LLMMessage, LLMPort, LLMUsage

COMPACT_SYSTEM_PROMPT = (
    "You are a meticulous financial research assistant. You summarize research sessions so that "
    "work can continue with no loss of data, numbers or provenance ids."
)

NO_TOOLS_PREAMBLE = """CRITICAL: Respond with TEXT ONLY. Do NOT call any tools.

- Do NOT use any tool calls. You already have all the context you need below.
- Tool calls will be REJECTED and will waste your only turn — you will fail the task.
- Your entire response must be plain text: an <analysis> block followed by a <summary> block.

"""

ANALYSIS_INSTRUCTION = """Before providing your final summary, wrap your analysis in <analysis> tags to organize your thoughts and ensure you've covered all necessary points. In your analysis process:

1. Chronologically review each tool call and its results. For each, thoroughly identify:
   - What data was requested and why
   - Key data points, numbers, and findings returned, with the provenance ids listed for that result
   - Any errors, empty or unavailable results, or unexpected responses
   - How this data relates to the user's original query
2. Double-check for numerical accuracy and completeness, and that every number keeps its provenance id."""  # noqa: E501

BASE_COMPACT_PROMPT = f"""Your task is to create a detailed summary of the research session below. This summary must preserve all important data, findings, and numerical results so that work can continue without losing context.

{ANALYSIS_INSTRUCTION}

Your summary should include the following sections:

1. Original Query and Intent: The user's exact request and what they are trying to learn or accomplish.
2. Key Concepts: Symbols in canonical form (e.g. PTT.BK, AAPL, EURUSD=X, GC=F), companies, sectors, financial metrics, or technical concepts involved.
3. Data Retrieved: For each tool call, summarize the tool name, arguments, and key results. Preserve important data points.
4. Errors and Retries: Any tool failures, empty or unavailable results, limits hit, or retried calls and their outcomes.
5. Analysis Progress: What has been analyzed so far, what conclusions or comparisons have been reached.
6. Numerical Data: ALL key numbers retrieved — prices, revenue figures, margins, ratios, growth rates, estimates, indicator values, dates. This section is critical; do not omit any numbers that were returned by tools. EVERY number must be followed by the provenance id(s) of the tool result it came from, written exactly as given, e.g. `34.50 THB [prov_01J…]`. A number without its provenance id cannot be cited later.
7. Pending Data Needs: What data has NOT yet been retrieved that would be needed to fully answer the query.
8. Current Work State: What was being worked on when this summary was requested.
9. Recommended Next Steps: What tool calls or analysis should happen next to complete the answer.

Write the summary in the same language as the user's original query (for example Thai if the query is in Thai). Keep tickers, tool names, provenance ids and numbers verbatim.

Here's an example of how your output should be structured:

<example>
<analysis>
[Your thought process, ensuring all numerical data, findings and provenance ids are captured accurately]
</analysis>

<summary>
1. Original Query and Intent:
   [Detailed description of what the user asked]

2. Key Concepts:
   - [Canonical symbol / concept 1]
   - [Canonical symbol / concept 2]

3. Data Retrieved:
   - [tool_name(args)]: [Key findings and data points]
   - [tool_name(args)]: [Key findings and data points]

4. Errors and Retries:
   - [Error description and resolution, or "None"]

5. Analysis Progress:
   [What has been analyzed, comparisons made, conclusions reached]

6. Numerical Data:
   - [Symbol/metric]: [value] ([date/period]) [prov_…]
   - [Symbol/metric]: [value] ([date/period]) [prov_…]

7. Pending Data Needs:
   - [Data still needed]

8. Current Work State:
   [What was being worked on]

9. Recommended Next Steps:
   [Next actions to take]

</summary>
</example>

Please provide your summary based on the research session below, following this structure and ensuring precision and thoroughness — especially for numerical data and provenance ids."""  # noqa: E501

NO_TOOLS_TRAILER = (
    "\n\nREMINDER: Do NOT call any tools. Respond with plain text only — "
    "an <analysis> block followed by a <summary> block. "
    "Tool calls will be rejected and you will fail the task."
)

_ANALYSIS_RE = re.compile(r"<analysis>[\s\S]*?</analysis>")
_SUMMARY_RE = re.compile(r"<summary>([\s\S]*?)</summary>")


class CompactionFailed(Exception):
    pass


class CompactResult(BaseModel):
    summary: str  # framed message content, ready to inject
    raw_summary: str
    usage: LLMUsage


def build_compaction_prompt(query: str, tool_results: str) -> str:
    return (
        f"{NO_TOOLS_PREAMBLE}{BASE_COMPACT_PROMPT}\n\nOriginal query: {query}\n\n"
        f"Data retrieved from tool calls:\n{tool_results or '[no tool results]'}{NO_TOOLS_TRAILER}"
    )


def format_compact_summary(raw: str) -> str:
    """Drop the ``<analysis>`` draft; keep the ``<summary>`` body (or the whole text if untagged)."""
    text = _ANALYSIS_RE.sub("", raw, count=1)
    match = _SUMMARY_RE.search(text)
    if match:
        text = _SUMMARY_RE.sub(lambda _: f"Summary:\n{match.group(1).strip()}", text, count=1)
    return re.sub(r"\n\n+", "\n\n", text).strip()


def build_compact_summary_message(raw: str) -> str:
    return (
        "This session is being continued from a previous research session that ran out of context. "
        "The summary below covers the data retrieved and analysis performed so far.\n\n"
        f"{format_compact_summary(raw)}\n\n"
        "Continue working toward answering the query without asking the user any further questions. "
        "Resume directly — do not acknowledge the summary, do not recap what was happening. "
        "Pick up the research as if the break never happened."
    )


def fit_text(blocks: list[str], max_chars: int) -> str:
    """Join blocks; if too long, cut each block to an equal share so every call stays represented."""
    joined = "\n\n".join(blocks)
    if len(joined) <= max_chars or not blocks:
        return joined
    share = max(400, max_chars // len(blocks))
    out = []
    for block in blocks:
        if len(block) > share:
            block = block[:share] + "\n…[cut for compaction; full result in scratchpad]"
        out.append(block)
    return "\n\n".join(out)


async def compact_context(llm: LLMPort, *, query: str, tool_results: str) -> CompactResult:
    """One quick-tier call with no tools. Raises ``CompactionFailed`` (caller falls back)."""
    messages = [
        LLMMessage(role="system", content=COMPACT_SYSTEM_PROMPT),
        LLMMessage(role="user", content=build_compaction_prompt(query, tool_results)),
    ]
    response = await llm.complete(messages, tier="quick", tools=None, purpose="compact")
    if response.tool_calls:
        raise CompactionFailed("summarizer tried to call tools")
    raw = response.text.strip()
    if not raw or not format_compact_summary(raw):
        raise CompactionFailed("compaction returned an empty summary")
    return CompactResult(summary=build_compact_summary_message(raw), raw_summary=raw, usage=response.usage)


def compact_messages(messages: list[LLMMessage], summary: str, query: str) -> list[LLMMessage]:
    """System prompt (if any) + one user message holding the query and the summary."""
    head = [messages[0]] if messages and messages[0].role == "system" else []
    content = f"{query}\n\n<context_summary>\n{summary}\n</context_summary>"
    return [*head, LLMMessage(role="user", content=content)]
