"""Append-only JSONL scratchpad: the single source of truth of a run (D2).

Ported from ``../dexter/src/agent/scratchpad.ts``:
- one file per run, ``DATA_DIR/scratchpad/{run_id}.jsonl``, flushed on every append
- ``tool_result`` entries keep the **full** result (the LLM only sees a truncated copy)
- per-tool call limits + Jaccard similarity of normalized queries
  (``canCallTool`` / ``recordToolCall`` / ``getToolUsageStatus``). Unlike
  Dexter, which only warns, a call over the limit or too similar to an
  earlier one is **blocked** (CLAUDE.md D5)
- clearing / compaction bookkeeping is in-memory only; the file is never rewritten
"""

from __future__ import annotations

import json
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, NamedTuple

from pydantic_core import to_jsonable_python

EntryType = Literal["init", "tool_result", "thinking", "compaction", "guard", "final", "note"]

_STOPWORDS = frozenset(
    {
        "the", "and", "for", "with", "from", "that", "this", "what", "when", "where", "which", "who",
        "how", "are", "was", "were", "has", "have", "had", "into", "over", "under", "about", "their",
        "there", "then", "than", "them", "they", "you", "your", "our", "its", "not", "but", "all",
        "any", "get", "show", "give", "find", "list", "latest", "recent", "current", "data", "info",
        "information", "please",
    }
)  # fmt: skip
_QUERY_KEYS = ("query", "search", "question", "q", "text", "input")
_WORD_RE = re.compile(r"[^\w\s]+", re.UNICODE)


def tokenize(text: str) -> set[str]:
    """Lowercase words, minus punctuation, stopwords, short tokens and numbers/dates."""
    words = _WORD_RE.sub(" ", text.lower()).split()
    return {w for w in words if len(w) > 2 and w not in _STOPWORDS and not w.replace("_", "").isdigit()}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def query_from_args(args: dict[str, Any]) -> str:
    """The "query" a call is about: a query-like arg if present, else all string-ish values."""
    for key in _QUERY_KEYS:
        value = args.get(key)
        if isinstance(value, str):
            return value
    parts: list[str] = []
    for key in sorted(args):
        value = args[key]
        if isinstance(value, str | int | float) and not isinstance(value, bool):
            parts.append(str(value))
        elif isinstance(value, list):
            parts.extend(str(v) for v in value if isinstance(v, str | int | float))
    return " ".join(parts)


class ToolCheck(NamedTuple):
    allowed: bool
    warning: str | None
    kind: Literal["tool_calls", "similar_query"] | None = None


class Scratchpad:
    def __init__(
        self,
        run_id: str,
        directory: Path,
        *,
        query: str | None = None,
        max_calls_per_tool: int = 3,
        similarity_threshold: float = 0.7,
    ) -> None:
        self.run_id = run_id
        self.path = directory / f"{run_id}.jsonl"
        self.max_calls_per_tool = max_calls_per_tool
        self.similarity_threshold = similarity_threshold
        self._lock = threading.Lock()
        self._counts: dict[str, int] = {}
        self._queries: dict[str, list[str]] = {}
        self._cleared: set[str] = set()
        self._compaction_summary: str | None = None
        self._compaction_boundary = 0
        directory.mkdir(parents=True, exist_ok=True)
        existing = self.path.exists()
        if existing:
            self._rebuild_counts()
        if query is not None and not existing:
            self.append("init", content=query)

    # -- write ------------------------------------------------------------------------

    def append(self, type_: EntryType, **fields: Any) -> dict[str, Any]:
        entry = {"type": type_, "ts": datetime.now(UTC).isoformat(), **fields}
        line = json.dumps(to_jsonable_python(entry, fallback=str), ensure_ascii=False)
        with self._lock, self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
        return entry

    def add_tool_result(
        self,
        call_id: str,
        tool: str,
        args: dict[str, Any],
        result: Any,
        *,
        status: str = "ok",
        provenance_ids: list[str] | None = None,
        duration_ms: int = 0,
        parent_call_id: str | None = None,
        counted: bool = True,
    ) -> None:
        """``counted=False`` marks a call that never executed (invalid args, limited, denied)."""
        self.append(
            "tool_result",
            call_id=call_id,
            tool=tool,
            args=args,
            result=result,
            status=status,
            provenance_ids=provenance_ids or [],
            duration_ms=duration_ms,
            parent_call_id=parent_call_id,
            counted=counted,
        )

    def add_thinking(self, text: str) -> None:
        self.append("thinking", content=text)

    def add_note(self, text: str, **fields: Any) -> None:
        self.append("note", content=text, **fields)

    # -- read -------------------------------------------------------------------------

    def entries(self) -> list[dict[str, Any]]:
        return read_entries(self.path)

    def tool_results(self) -> list[dict[str, Any]]:
        return [e for e in self.entries() if e.get("type") == "tool_result"]

    def _rebuild_counts(self) -> None:
        for entry in self.tool_results():
            tool = str(entry.get("tool"))
            if entry.get("counted") is False:
                continue
            self._counts[tool] = self._counts.get(tool, 0) + 1
            self._queries.setdefault(tool, []).append(query_from_args(entry.get("args") or {}))

    # -- tool limits (D5) -------------------------------------------------------------------

    def check_tool(self, tool: str, query: str | None, *, max_calls: int | None = None) -> ToolCheck:
        limit = max_calls if max_calls is not None else self.max_calls_per_tool
        count = self._counts.get(tool, 0)
        if count >= limit:
            return ToolCheck(
                False,
                f"Tool '{tool}' was already called {count}/{limit} times this run. Use a different "
                "tool, change the approach, or answer with what you have and state what is missing.",
                "tool_calls",
            )
        if query:
            words = tokenize(query)
            for previous in self._queries.get(tool, []):
                score = jaccard(words, tokenize(previous))
                if score >= self.similarity_threshold:
                    return ToolCheck(
                        False,
                        f"This '{tool}' call is very similar (similarity {score:.2f}) to an earlier one "
                        f"({previous!r}). Its result is already in the scratchpad; do not repeat it.",
                        "similar_query",
                    )
        if count == limit - 1:
            return ToolCheck(True, f"Last allowed call of '{tool}' this run ({count + 1}/{limit}).")
        return ToolCheck(True, None)

    def can_call_tool(
        self, tool: str, query: str | None, *, max_calls: int | None = None
    ) -> tuple[bool, str | None]:
        check = self.check_tool(tool, query, max_calls=max_calls)
        return check.allowed, check.warning

    def record_tool_call(self, tool: str, query: str | None) -> None:
        with self._lock:
            self._counts[tool] = self._counts.get(tool, 0) + 1
            if query:
                self._queries.setdefault(tool, []).append(query)

    def tool_call_count(self, tool: str) -> int:
        return self._counts.get(tool, 0)

    def tool_usage_status(self) -> str | None:
        if not self._counts:
            return None
        lines = [f"- {tool}: {n}/{self.max_calls_per_tool} calls" for tool, n in sorted(self._counts.items())]
        return "## Tool usage this run\n" + "\n".join(lines)

    # -- clearing / compaction bookkeeping (P06) ---------------------------------------------

    def mark_cleared(self, call_ids: list[str]) -> None:
        self._cleared.update(call_ids)

    def is_cleared(self, call_id: str) -> bool:
        return call_id in self._cleared

    @property
    def compaction_summary(self) -> str | None:
        return self._compaction_summary

    def set_compaction_summary(self, summary: str) -> None:
        """``setCompactionSummary``: every tool result so far is now covered by ``summary``."""
        self._compaction_summary = summary
        self._compaction_boundary = len(self.tool_results())
        self._cleared.clear()

    def tool_results_since_compaction(self) -> list[dict[str, Any]]:
        return self.tool_results()[self._compaction_boundary :]

    def active_tool_result_count(self) -> int:
        return sum(1 for e in self.tool_results_since_compaction() if e.get("call_id") not in self._cleared)

    def compaction_input(self) -> str:
        """What a summarizer reads (``getToolResults``): prior summary + full results after it."""
        body = format_tool_results(self.tool_results_since_compaction())
        if self._compaction_summary is None:
            return body
        if not body:
            return self._compaction_summary
        return f"{self._compaction_summary}\n\n---\n\nNew data retrieved after compaction:\n\n{body}"

    def has_executed_skill(self, skill: str) -> bool:
        return any(
            e.get("tool") == "skill" and (e.get("args") or {}).get("skill") == skill
            for e in self.tool_results()
        )

    def build_final_context(self) -> str:
        """Every tool result in full (never compacted) for the final-answer call."""
        return format_tool_results(self.tool_results())


def format_tool_result(entry: dict[str, Any]) -> str:
    args = ", ".join(f"{k}={v}" for k, v in (entry.get("args") or {}).items())
    result = entry.get("result")
    text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
    prov = entry.get("provenance_ids") or []
    header = f"### {entry.get('tool')}({args}) [call_id={entry.get('call_id')}]"
    status = entry.get("status")
    if status and status != "ok":
        header += f" status={status}"
    if prov:
        header += f" provenance: {', '.join(prov)}"
    return f"{header}\n{text}"


def format_tool_results(entries: list[dict[str, Any]]) -> str:
    return "\n\n".join(format_tool_result(e) for e in entries)


def read_entries(path: Path) -> list[dict[str, Any]]:
    """All valid entries; malformed/partial lines are skipped rather than crashing."""
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict) and "type" in entry:
                out.append(entry)
    return out
