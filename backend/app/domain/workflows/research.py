"""``research`` workflow (F5): plan → retrieve_analyze → verify → synthesize_report.

- plan (quick, structured T5): classify the question, pick a skill (unless
  the user chose one) and an expert role, propose a session title
- retrieve_analyze: the harness agent loop (D1-D5) with that role/skill
- verify: number-check the agent's brief; if more than 3 numbers have no
  provenance, run the loop once more (≤ 2 iterations) to confirm them with tools
- synthesize_report: final answer in a separate LLM call with no tools,
  citations resolved and unverified numbers flagged (F3)
A node may return ``_summary`` (one line for ``node.finished``); it is not state.
"""

from __future__ import annotations

from typing import Any, TypedDict

from pydantic import BaseModel, Field

from app.domain.agents.library import LIBRARY, PIPELINE_ROLE_PREFIXES
from app.domain.agents.schemas import ResearchPlan
from app.domain.ports import AgentRunRequest, LLMMessage
from app.domain.workflows.spec import NodeContext, NodeKind, NodeSpec, WorkflowSpec

MAX_UNVERIFIED_BEFORE_RECHECK = 3
VERIFY_INSTRUCTION = (
    "Before the final answer: these numbers from your brief are not backed by any tool result yet: "
    "{numbers}. Confirm each one with a tool call (or drop it). Do not repeat calls whose results you "
    "already have; then reply with a corrected brief."
)


class HistoryTurn(BaseModel):
    role: str
    content: str


class ResearchInput(BaseModel):
    text: str = Field(min_length=1)
    skill: str | None = None
    history: list[HistoryTurn] = Field(default_factory=list)
    # (P12 §12.7) portfolios whose snapshot goes into the context; empty = do not use portfolio data
    portfolio_ids: list[str] = Field(default_factory=list, max_length=20)


class ResearchOutput(BaseModel):
    markdown: str
    citations: list[dict[str, Any]] = Field(default_factory=list)
    unverified_numbers: list[str] = Field(default_factory=list)
    skill: str | None = None
    role: str
    stop_reason: str | None = None
    title: str | None = None


class ResearchState(TypedDict, total=False):
    input: dict[str, Any]
    plan: dict[str, Any]
    loop: dict[str, Any]
    verify: dict[str, Any]
    answer: dict[str, Any]


def fallback_title(text: str, max_words: int = 6, max_chars: int = 48) -> str:
    words = text.strip().split()
    title = " ".join(words[:max_words])
    return title if len(title) <= max_chars else title[: max_chars - 1].rstrip() + "…"


def _plan_prompt(text: str, skills: list[dict[str, Any]]) -> list[LLMMessage]:
    skill_lines = "\n".join(
        f"- {s['name']} ({s.get('category')}, role {s.get('role') or 'research_lead'}): {s['description']}"
        for s in skills
    )
    roles = "\n".join(
        f"- {r.name}: {r.title}" for r in LIBRARY.values() if not r.name.startswith(PIPELINE_ROLE_PREFIXES)
    )
    system = (
        "You route a financial research question. Choose the single best skill (or null when no skill "
        "fits), the expert role, the tickers mentioned, the question's language, and a session title of "
        "at most 6 words in the same language. Return only the structured fields."
    )
    user = f"Question:\n{text}\n\nSkills:\n{skill_lines or '(none)'}\n\nRoles:\n{roles}"
    return [LLMMessage(role="system", content=system), LLMMessage(role="user", content=user)]


async def plan(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
    inp = ResearchInput.model_validate(state["input"])
    skills: list[dict[str, Any]] = [s for s in ctx.config.get("skills", []) if s.get("available")]
    by_name = {s["name"]: s for s in skills}
    result = await ctx.llm.structured(
        _plan_prompt(inp.text, skills), ResearchPlan, tier="quick", purpose="structured"
    )
    chosen = result.value or ResearchPlan()
    if inp.skill:
        chosen.skill = inp.skill
    elif chosen.skill not in by_name:
        chosen.skill = None
    skill_role = by_name.get(chosen.skill or "", {}).get("role")
    if skill_role in LIBRARY:
        chosen.role = skill_role
    if chosen.role.startswith(PIPELINE_ROLE_PREFIXES):  # workflow-pipeline roles are not chat experts
        chosen.role = "research_lead"
    if not chosen.title.strip():
        chosen.title = fallback_title(inp.text)
    value = {**chosen.model_dump(), "mode": result.mode, "user_selected": bool(inp.skill)}
    return {"plan": value, "_summary": f"skill {chosen.skill or 'auto'} · role {chosen.role}"}


async def retrieve_analyze(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
    p = state["plan"]
    needs_data = bool(p.get("skill") or p.get("symbols"))
    result = await ctx.agent.run_loop(
        AgentRunRequest(role=p["role"], skill=p.get("skill"), require_data=needs_data)
    )
    summary = f"{result.iterations} iterations · {result.tool_calls} tool calls"
    if result.stop_reason:
        summary += f" · stopped: {result.stop_reason}"
    return {"loop": result.model_dump(), "_summary": summary}


async def verify(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
    loop = state["loop"]
    check = await ctx.agent.check(loop.get("draft") or "")
    unverified = check.unverified_numbers
    out: dict[str, Any] = {"unverified": unverified, "reloop": False}
    if len(unverified) > MAX_UNVERIFIED_BEFORE_RECHECK and loop.get("stop_reason") != "cancelled":
        second = await ctx.agent.run_loop(
            AgentRunRequest(
                role=state["plan"]["role"],
                instruction=VERIFY_INSTRUCTION.format(numbers=", ".join(unverified[:12])),
                max_iterations=2,
            )
        )
        out.update(reloop=True, second=second.model_dump())
    summary = f"{len(unverified)} unverified in brief" + (" · re-checked once" if out["reloop"] else "")
    return {"verify": out, "_summary": summary}


async def synthesize_report(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
    second = state.get("verify", {}).get("second") or {}
    stop = second.get("stop_reason") or state["loop"].get("stop_reason")
    answer = await ctx.agent.final_answer(role=state["plan"]["role"], stop_reason=stop)
    value = {**answer.model_dump(), "stop_reason": stop}
    return {
        "answer": value,
        "_summary": f"{len(answer.citations)} citations · {len(answer.unverified_numbers)} unverified",
    }


def output_of(state: dict[str, Any]) -> ResearchOutput:
    answer = state.get("answer", {})
    plan_ = state.get("plan", {})
    return ResearchOutput(
        markdown=answer.get("markdown", ""),
        citations=answer.get("citations", []),
        unverified_numbers=answer.get("unverified_numbers", []),
        skill=plan_.get("skill"),
        role=plan_.get("role", "research_lead"),
        stop_reason=answer.get("stop_reason"),
        title=plan_.get("title"),
    )


RESEARCH_WORKFLOW = WorkflowSpec(
    id="research",
    version="1",
    input_model=ResearchInput,
    output_model=ResearchOutput,
    state_model=ResearchState,
    reducers={
        "input": "replace",
        "plan": "replace",
        "loop": "replace",
        "verify": "replace",
        "answer": "replace",
    },
    nodes=[
        NodeSpec(
            id="plan",
            label="Plan",
            kind=NodeKind.AGENT,
            phase="retrieve",
            fn=plan,
            writes=frozenset({"plan"}),
        ),
        NodeSpec(
            id="retrieve_analyze",
            label="Research",
            kind=NodeKind.AGENT,
            phase="analyze",
            fn=retrieve_analyze,
            writes=frozenset({"loop"}),
        ),
        NodeSpec(
            id="verify",
            label="Verify",
            kind=NodeKind.COMPUTE,
            phase="verify",
            fn=verify,
            writes=frozenset({"verify"}),
        ),
        NodeSpec(
            id="synthesize_report",
            label="Answer",
            kind=NodeKind.REPORT,
            phase="synthesize",
            fn=synthesize_report,
            writes=frozenset({"answer"}),
        ),
    ],
    edges=[("plan", "retrieve_analyze"), ("retrieve_analyze", "verify"), ("verify", "synthesize_report")],
    entry="plan",
)
