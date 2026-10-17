"""ResearchAgent: the harness side of ``AgentPort`` for the research workflow (P07).

Owns one conversation per run. ``run_loop`` builds the role's system prompt,
preloads a chosen skill through the executor (so it shows as a tool row,
lands in the scratchpad and counts against the once-per-skill limit), and
runs the ``AgentLoop``. ``final_answer`` writes the answer from the
scratchpad; ``check`` number-checks a draft against the run's provenance.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel
from ulid import ULID

from app.domain.agents.library import get_role
from app.domain.ports import AgentRunRequest, AgentRunResult, AnswerCheck, LLMMessage, LLMPort, ToolCallReq
from app.domain.tools.base import ToolSpec
from app.harness.context.manager import ContextManager
from app.harness.executor.executor import ToolExecutor
from app.harness.executor.registry import ToolRegistry
from app.harness.portfolio.tools import TOOL_NAME as PORTFOLIO_TOOL
from app.harness.provenance.store import ProvenanceStore
from app.harness.runtime.agent_loop import AgentLoop
from app.harness.runtime.context import RunContext
from app.harness.runtime.final_answer import check_answer, context_budget_chars, write_final_answer
from app.harness.runtime.prompts import build_system_prompt
from app.harness.skills.registry import SkillRegistry

MAX_HISTORY_MESSAGES = 10


class HistoryItem(BaseModel):
    role: str
    content: str


def history_messages(history: list[HistoryItem]) -> list[LLMMessage]:
    out = [
        LLMMessage(role="user" if h.role == "user" else "assistant", content=h.content)
        for h in history[-MAX_HISTORY_MESSAGES:]
        if h.content.strip()
    ]
    while out and out[0].role != "user":
        out.pop(0)
    return out


class ResearchAgent:
    def __init__(
        self,
        ctx: RunContext,
        llm: LLMPort,
        tools: ToolRegistry,
        skills: SkillRegistry | None,
        provenance: ProvenanceStore,
        context: ContextManager,
        executor: ToolExecutor,
        *,
        query: str,
        history: list[LLMMessage],
        portfolio_ids: list[str] | None = None,
    ) -> None:
        self.ctx = ctx
        self.llm = llm
        self.tools = tools
        self.skills = skills
        self.provenance = provenance
        self.context = context
        self.executor = executor
        self.query = query
        self.history = history
        self.portfolio_ids = list(portfolio_ids or [])
        self.loop = AgentLoop(ctx, llm, executor, context)
        self.brief = ""
        self.prompt_versions: dict[str, str] = {}
        self.context_blocks: list[str] = []

    def _tools_for(self, role_tools: list[str] | None) -> list[ToolSpec]:
        tools = self.tools.for_node(role_tools) if role_tools else self.tools.enabled()
        if self.portfolio_ids:  # the selected books are always readable, whatever the role's tool list
            extra = self.tools.for_node([PORTFOLIO_TOOL])
            tools = tools + [t for t in extra if t not in tools]
        else:  # "use portfolio data" is off: the LLM must not reach the user's portfolios at all
            tools = [t for t in tools if t.name != PORTFOLIO_TOOL]
        return tools

    async def _preload_portfolios(self) -> str:
        """(P12 §12.7) Run ``get_portfolio_analytics`` once per selected book before the first
        LLM call, so the snapshot is a real tool result (UI row, scratchpad, provenance)."""
        if self.tools.get(PORTFOLIO_TOOL) is None:
            return ""
        calls = [
            ToolCallReq(id=f"call_{ULID()}", name=PORTFOLIO_TOOL, args={"portfolio_id": pid})
            for pid in self.portfolio_ids
        ]
        blocks = []
        for call, result in zip(calls, await self.executor.run_calls(calls), strict=True):
            pid = call.args["portfolio_id"]
            if not result.ok:
                await self.ctx.emit.emit(
                    "warning", {"code": "portfolio_unavailable", "message": f"{pid}: {result.summary}"}
                )
                continue
            summary = result.data.get("summary") if isinstance(result.data, dict) else None
            blocks.append(f'<portfolio id="{pid}">\n{summary or result.content}\n</portfolio>')
        return "\n\n".join(blocks)

    async def _preload_skill(self, name: str) -> str | None:
        call = ToolCallReq(id=f"call_{ULID()}", name="skill", args={"skill": name})
        [result] = await self.executor.run_calls([call])
        if not result.ok:
            await self.ctx.emit.emit(
                "warning", {"code": "skill_unavailable", "message": f"scenario '{name}': {result.summary}"}
            )
            return None
        return result.content

    async def run_loop(self, req: AgentRunRequest) -> AgentRunResult:
        role = get_role(req.role)
        tools = self._tools_for(role.tools)
        if req.context_blocks:  # (P17) kept for later passes (verify) that may not resend them
            self.context_blocks = list(req.context_blocks)
        system = build_system_prompt(role, tools, self.skills)
        if self.context_blocks:
            system = f"{system}\n\n" + "\n\n".join(self.context_blocks)
        self.prompt_versions[role.name] = role.prompt_id
        if not self.loop.started:
            user = self.query
            if self.portfolio_ids:
                portfolios = await self._preload_portfolios()
                if portfolios:
                    user = (
                        f"{user}\n\nThe user shared these portfolios (computed analytics; cite the "
                        f"[^prov_…] ids):\n{portfolios}"
                    )
            if req.skill:
                body = await self._preload_skill(req.skill)
                if body:
                    user = f'{user}\n\n<scenario name="{req.skill}">\n{body}\n</scenario>'
            self.ctx.scratchpad.add_note(
                "agent start", role=role.name, prompt=role.prompt_id, skill=req.skill
            )
            self.loop.start(system, self.history, user)
        else:
            self.loop.set_system(system)
        if req.instruction:
            self.loop.add_user(req.instruction)
        outcome = await self.loop.run(tools, max_iterations=req.max_iterations, require_data=req.require_data)
        if outcome.draft:
            self.brief = outcome.draft
        return AgentRunResult(
            iterations=outcome.iterations,
            stop_reason=outcome.stop_reason,
            draft=outcome.draft,
            tool_calls=outcome.tool_calls,
        )

    async def check(self, markdown: str) -> AnswerCheck:
        records = await self.provenance.list(self.ctx.run_id)
        return check_answer(markdown, records)[0]

    async def final_answer(
        self,
        *,
        role: str,
        stop_reason: str | None,
        context_blocks: list[str] | None = None,
        closing_question: str | None = None,
        discovery: dict[str, Any] | None = None,
    ) -> AnswerCheck:
        spec = get_role(role)
        return await write_final_answer(
            self.ctx,
            self.llm,
            self.provenance,
            title=spec.title,
            query=self.query,
            history=self.history,
            brief=self.brief,
            stop_reason=stop_reason,
            context_chars=context_budget_chars(self.context.window, self.context.config.chars_per_token),
            context_blocks=self.context_blocks if context_blocks is None else context_blocks,
            closing_question=closing_question,
            discovery=discovery,
        )
