"""memory_get / memory_search / memory_update tools over the persistent MemoryStore (D2).

Pattern from ``../dexter/src/tools/memory/`` and the memory section of
``../dexter/src/agent/prompts.ts``. They live in the harness because the
store is SQLite; writing is an explicit tool call (never automatic), and
writing the ``portfolio`` namespace needs approval (``PermissionPolicy``).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.domain.tools.base import ToolContext, ToolOutput, ToolSpec
from app.harness.state.memory import MemoryStore

NAMESPACE_PATTERN = r"^[a-z][a-z0-9_]{1,40}$"
KNOWN_NAMESPACES = "user_prefs, research_notes, decisions, portfolio"


class MemoryKeyArgs(BaseModel):
    namespace: str = Field(pattern=NAMESPACE_PATTERN, description=f"One of: {KNOWN_NAMESPACES}")
    key: str = Field(min_length=1, max_length=200)


class MemorySearchArgs(BaseModel):
    namespace: str = Field(pattern=NAMESPACE_PATTERN, description=f"One of: {KNOWN_NAMESPACES}")
    prefix: str = Field(default="", max_length=200)
    limit: int = Field(default=20, ge=1, le=50)


class MemoryUpdateArgs(MemoryKeyArgs):
    value: Any = Field(description="JSON value to store (text, number, object)")


def memory_tools(store: MemoryStore) -> list[ToolSpec]:
    async def memory_get(args: MemoryKeyArgs, ctx: ToolContext) -> ToolOutput:
        item = await store.get(args.namespace, args.key)
        if item is None:
            return ToolOutput(data=None, summary=f"{args.namespace}/{args.key}: not found")
        return ToolOutput(data=item.model_dump(mode="json"), summary=f"{args.namespace}/{args.key}")

    async def memory_search(args: MemorySearchArgs, ctx: ToolContext) -> ToolOutput:
        items = await store.search(args.namespace, args.prefix, args.limit)
        return ToolOutput(
            data=[i.model_dump(mode="json") for i in items],
            summary=f"{len(items)} memories in {args.namespace}"
            + (f" '{args.prefix}*'" if args.prefix else ""),
        )

    async def memory_update(args: MemoryUpdateArgs, ctx: ToolContext) -> ToolOutput:
        item = await store.put(args.namespace, args.key, args.value)
        return ToolOutput(data=item.model_dump(mode="json"), summary=f"saved {args.namespace}/{args.key}")

    return [
        ToolSpec(
            name="memory_get",
            description=(
                "Read one stored memory (user preferences, prior decisions, notes) by namespace + key."
            ),
            args_model=MemoryKeyArgs,
            category="memory",
            fn=memory_get,
        ),
        ToolSpec(
            name="memory_search",
            description=(
                "List stored memories in a namespace (optionally by key prefix). Before personalized advice, "
                "check user_prefs and decisions for goals, risk tolerance and prior decisions."
            ),
            args_model=MemorySearchArgs,
            category="memory",
            fn=memory_search,
        ),
        ToolSpec(
            name="memory_update",
            description=(
                "Store a durable fact the user explicitly wants remembered (preference, goal, decision). "
                "Never "
                "store prices or tool output. Writing the portfolio namespace requires user approval."
            ),
            args_model=MemoryUpdateArgs,
            category="memory",
            read_only=False,
            max_calls_per_run=3,
            fn=memory_update,
        ),
    ]
