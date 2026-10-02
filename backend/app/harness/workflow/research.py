"""Harness entry point of the ``research`` workflow (P07, graph-compiled since P09):
wire services, run the spec on ``GraphRunner``.

Builds everything one run needs — node-scoped emitter, LLM bound to the run
(events + usage per purpose), executor, context manager, ``ResearchAgent`` —
then runs ``RESEARCH_WORKFLOW`` on LangGraph (scope ``_workflow_research``,
one checkpoint thread per run — see ``runner._default_date``). Afterwards
the answer is stored as the session's assistant message and a limit that
ended the research early marks the run ``limited``. ``research_workflow``
(fresh start) and ``research_workflow_resume`` (after a crash) share the
setup in ``_build``/``_finish``.
"""

from __future__ import annotations

from typing import Any, Protocol

from app.domain.ports import EventPort, LLMUsage
from app.domain.workflows.research import RESEARCH_WORKFLOW, ResearchInput, output_of
from app.harness.context.config import ContextConfig
from app.harness.context.manager import ContextManager
from app.harness.executor.executor import ToolExecutor
from app.harness.graph.context import make_node_context_factory
from app.harness.runtime.context import RunContext
from app.harness.runtime.research_agent import HistoryItem, ResearchAgent, history_messages
from app.harness.storage import sessions_repo
from app.harness.workflow.runner import GraphRunner
from app.harness.workflow.sequential import ScopedEmitter

LIMIT_KINDS = ("iterations", "tool_calls", "similar_query", "timeout", "budget")


class BindableLLM(Protocol):
    def bind(
        self, *, emit: EventPort | None = None, on_usage: Any = None, node: str | None = None
    ) -> Any: ...

    def primary(self, tier: Any) -> tuple[str, str]: ...


def _runner(ctx: RunContext) -> GraphRunner:
    settings = ctx.services.settings

    async def after_node(node_id: str, state: dict[str, Any]) -> None:
        if node_id != "plan" or ctx.session_id is None:
            return
        title = (state.get("plan") or {}).get("title")
        session = await sessions_repo.get_session(ctx.session_id)
        if title and session is not None and not session.title:
            await sessions_repo.set_title(ctx.session_id, title)

    return GraphRunner(
        RESEARCH_WORKFLOW,
        checkpoints=ctx.services.checkpoints,
        scope="_workflow_research",
        keep_checkpoints_on_success=settings.graph_keep_checkpoints_on_success,
        recursion_limit=settings.graph_recursion_limit,
        on_node_finished=after_node,
    )


def _build(ctx: RunContext) -> tuple[Any, ResearchAgent, ContextManager]:
    services = ctx.services
    scoped = ScopedEmitter(ctx.emit)
    ctx.emit = scoped

    def on_usage(usage: LLMUsage, purpose: str) -> None:
        ctx.record_llm_usage(usage, purpose)

    choice = ctx.input.get("llm") if isinstance(ctx.input.get("llm"), dict) else None
    base_llm: BindableLLM = (
        services.get_llm_for_choice(choice["provider"], choice["model"]) if choice else services.get_llm()
    )
    llm = base_llm.bind(emit=scoped, on_usage=on_usage)
    deep_model = base_llm.primary("deep")[1]
    quick_model = base_llm.primary("quick")[1]
    config = ContextConfig.from_settings(services.settings)
    context = ContextManager(
        llm,
        services.tools,
        model=deep_model,
        summary_model=quick_model,
        config=config,
        memory=services.memory if config.memory_flush else None,
    )
    executor = ToolExecutor(services.tools, ctx, provenance=services.provenance)
    inp = ResearchInput.model_validate({k: v for k, v in ctx.input.items() if k != "llm"})
    agent = ResearchAgent(
        ctx,
        llm,
        services.tools,
        services.skills,
        services.provenance,
        context,
        executor,
        query=inp.text,
        history=history_messages([HistoryItem(role=h.role, content=h.content) for h in inp.history]),
        portfolio_ids=inp.portfolio_ids,
    )
    node_ctx_factory = make_node_context_factory(
        llm=llm,
        tools=executor,
        agent=agent,
        run_id=ctx.run_id,
        config={"skills": [s.model_dump() for s in services.skills.info()]},
    )
    return node_ctx_factory, agent, context


async def _finish(
    ctx: RunContext, agent: ResearchAgent, context: ContextManager, state: dict[str, Any]
) -> dict[str, Any]:
    out = output_of(state)
    if ctx.session_id is not None:
        await sessions_repo.add_message(
            ctx.session_id,
            "assistant",
            out.markdown,
            run_id=ctx.run_id,
            meta={
                "citations": out.citations,
                "unverified_numbers": out.unverified_numbers,
                "skill": out.skill,
                "role": out.role,
                "stop_reason": out.stop_reason,
            },
        )
    kind = (out.stop_reason or "").split(":", 1)[0]
    if kind in LIMIT_KINDS:
        ctx.outcome_status, ctx.outcome_reason = "limited", out.stop_reason
    return {**out.model_dump(), "prompt_versions": agent.prompt_versions, "context_window": context.window}


async def research_workflow(ctx: RunContext) -> dict[str, Any]:
    node_ctx_factory, agent, context = _build(ctx)
    state = await _runner(ctx).start(ctx, node_ctx_factory)
    assert state is not None, "research has no approval node; it cannot pause"
    return await _finish(ctx, agent, context, state)


async def research_workflow_resume(ctx: RunContext, resume_value: Any) -> dict[str, Any] | None:
    node_ctx_factory, agent, context = _build(ctx)
    state = await _runner(ctx).resume(ctx, node_ctx_factory, resume_value=resume_value)
    if state is None:
        return None
    return await _finish(ctx, agent, context, state)
