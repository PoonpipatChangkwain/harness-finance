"""(P17) ``user_memory_search``: the LLM's read-only view of what the user has told us before.

The profile block is already in the system prompt; this tool is for details ("what did they say
about KBANK"). Scoped to the run's ``user_id`` (``usr_default`` when the input has none) and to
active items. Writing memory is never a tool: the ``learn`` node does it after the turn (D2).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.domain.memory.schemas import MemoryCategory
from app.domain.tools.base import ToolContext, ToolOutput, ToolSpec
from app.harness.memory.store import DEFAULT_USER_ID, UserMemoryStore
from app.harness.storage import runs_repo

TOOL_NAME = "user_memory_search"


class UserMemorySearchArgs(BaseModel):
    query: str = Field(
        default="", max_length=300, description="Words to look for; empty = most important items"
    )
    category: MemoryCategory | None = Field(default=None, description="Only this memory category")
    limit: int = Field(default=5, ge=1, le=10)


async def run_user_id(run_id: str) -> str:
    run = await runs_repo.get_run(run_id)
    user_id = run.input.get("user_id") if run is not None else None
    return str(user_id) if isinstance(user_id, str) and user_id else DEFAULT_USER_ID


def user_memory_tool(store: UserMemoryStore) -> ToolSpec:
    async def run(args: UserMemorySearchArgs, ctx: ToolContext) -> ToolOutput:
        user_id = await run_user_id(ctx.run_id)
        if args.query.strip():
            hits = await store.search_fts(
                user_id, args.query, statuses=("active",), category=args.category, limit=args.limit
            )
            items = [item for item, _ in hits]
        else:
            items = await store.list_items(user_id, statuses=["active"], category=args.category)
            items = sorted(items, key=lambda i: -(i.importance * i.confidence))[: args.limit]
        await store.touch(user_id, [i.id for i in items])
        data = [
            {
                "id": i.id,
                "category": i.category,
                "text": i.text,
                "value": i.value_json,
                "source": i.source,
                "confidence": i.confidence,
                "since": i.valid_from.date().isoformat(),
            }
            for i in items
        ]
        return ToolOutput(
            data=data,
            summary=f"{len(items)} memories"
            + (f" for '{args.query.strip()[:40]}'" if args.query.strip() else ""),
        )

    return ToolSpec(
        name=TOOL_NAME,
        description=(
            "Search what this user has told the advisor in earlier conversations (goals, risk tolerance, "
            "constraints, preferences, style). The INVESTOR PROFILE in the prompt already has the key facts; "
            "use this for details. Returns active memories only."
        ),
        args_model=UserMemorySearchArgs,
        category="memory",
        max_calls_per_run=3,
        fn=run,
    )
