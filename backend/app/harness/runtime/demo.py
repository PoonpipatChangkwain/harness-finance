"""``echo_tools``: dev-only workflow that exercises the executor end to end without an LLM.

Calls ``get_prices`` and ``compute_indicators`` concurrently through the
executor so every event (run.started → 2 tool.started → 2 tool.finished →
run.finished), the scratchpad and provenance lineage can be checked by hand.
Used only by ``POST /api/dev/echo-run`` (APP_ENV=dev) and tests.
"""

from __future__ import annotations

from typing import Any

from ulid import ULID

from app.domain.ports import ToolCallReq
from app.harness.executor.executor import ToolExecutor
from app.harness.runtime.context import RunContext


def new_call_id() -> str:
    return f"call_{ULID()}"


async def echo_tools(ctx: RunContext) -> dict[str, Any]:
    services = ctx.services
    symbol = str(ctx.input.get("symbol", "PTT"))
    executor = ToolExecutor(services.tools, ctx, provenance=services.provenance)
    results = await executor.run_calls(
        [
            ToolCallReq(id=new_call_id(), name="get_prices", args={"symbol": symbol}),
            ToolCallReq(id=new_call_id(), name="compute_indicators", args={"symbol": symbol}),
        ]
    )
    return {"results": [r.model_dump(exclude={"content"}) for r in results]}
