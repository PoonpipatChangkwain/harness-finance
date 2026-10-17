"""``research`` workflow v2 (F5, P17 §17.2): memory + 5 Whys around the research loop.

    plan → recall_memory → assess_discovery ─┬─ ask_only          → write_question ──────┐
                                             ├─ research          → retrieve_analyze      │
                                             └─ research_then_ask → retrieve_analyze      ├→ learn → END
                                                retrieve_analyze → verify → synthesize_report ┘

- plan (quick, structured T5): classify the question, pick a skill (unless
  the user chose one) and an expert role, propose a session title
- recall_memory: the user's profile + FTS hits + ≤ 2 ReasoningBank lessons (``memory.recalled``);
  the lessons it injects are recorded as used (settled by ``learn``)
- assess_discovery (quick, structured): the 5-Whys assessment of this message → route by the
  pure ``should_ask`` rule (never blocks: a parse failure or a skip click → ``research``)
- write_question (ask_only): one short reflect + the why-question, no tools, no research cost
- retrieve_analyze: the harness agent loop (D1-D5) with that role/skill; the profile, lessons and
  advice mode go into the system prompt (``context_blocks``)
- verify: number-check the agent's brief; if more than 3 numbers have no
  provenance, run the loop once more (≤ 2 iterations) to confirm them with tools
- synthesize_report: final answer in a separate LLM call with no tools,
  citations resolved and unverified numbers flagged (F3); ``research_then_ask`` ends it with the
  discovery question, verbatim
- learn: the only node that writes memory (D2) — facts from this turn's user message, then a
  rule-based judgement → ≤ 1 ReasoningBank lesson + settle the recalled lessons. A failure here is
  a warning, never a failed run.

Memory is off (``ctx.config["memory"]`` is None, ``MEMORY_ENABLED=false``) → the memory nodes do
nothing (no LLM call, no event) and the run behaves as v1. A node may return ``_summary`` (one line
for ``node.finished``); it is not state.
"""

from __future__ import annotations

from typing import Any, TypedDict

from pydantic import BaseModel, Field, ValidationError

from app.domain.agents.library import LIBRARY, PIPELINE_ROLE_PREFIXES
from app.domain.agents.schemas import ResearchPlan
from app.domain.memory.discovery import advice_mode_of, should_ask
from app.domain.memory.messages import context_blocks, discovery_messages, question_messages
from app.domain.memory.ports import MemoryPort
from app.domain.memory.schemas import DiscoveryAssessment, OutcomeJudgement, RootGoal, WhyStep
from app.domain.memory.tags import market_of
from app.domain.ports import AgentRunRequest, LLMMessage
from app.domain.workflows.spec import NodeContext, NodeKind, NodeSpec, RouterSpec, WorkflowSpec

MAX_UNVERIFIED_BEFORE_RECHECK = 3
VERIFY_INSTRUCTION = (
    "Before the final answer: these numbers from your brief are not backed by any tool result yet: "
    "{numbers}. Confirm each one with a tool call (or drop it). Do not repeat calls whose results you "
    "already have; then reply with a corrected brief."
)
LIMIT_KINDS = ("iterations", "tool_calls", "similar_query", "timeout", "budget")
DEFAULT_USER_ID = "usr_default"
DEFAULT_MAX_WHYS = 5
DEFAULT_DISCOVERY_CONFIDENCE = 0.7
RESET_STOP_REASONS = ("max_whys", "resistance")
ROUTES = ("ask_only", "research", "research_then_ask")
SKIP_NOTE = "ถ้าไม่สะดวกตอบ กด “ข้าม — วิเคราะห์เลย” ได้เลยครับ"


class HistoryTurn(BaseModel):
    role: str
    content: str


class SessionDiscovery(BaseModel):
    """The 5-Whys state a session carries between turns (``session.meta["discovery"]``)."""

    whys_asked: int = Field(default=0, ge=0)
    chain: list[WhyStep] = Field(default_factory=list)
    root_goal: RootGoal | None = None
    root_goal_identified: bool = False
    confidence: float = 0.0
    stop_reason: str | None = None
    advice_mode: str = "normal"


class ResearchInput(BaseModel):
    text: str = Field(min_length=1)
    skill: str | None = None
    history: list[HistoryTurn] = Field(default_factory=list)
    # (P12 §12.7) portfolios whose snapshot goes into the context; empty = do not use portfolio data
    portfolio_ids: list[str] = Field(default_factory=list, max_length=20)
    # (P17) "ข้าม — วิเคราะห์เลย": no why-question this turn; whose memory; the session's 5-Whys state
    skip_discovery: bool = False
    user_id: str = DEFAULT_USER_ID
    discovery: dict[str, Any] | None = None


class ResearchOutput(BaseModel):
    markdown: str
    citations: list[dict[str, Any]] = Field(default_factory=list)
    unverified_numbers: list[str] = Field(default_factory=list)
    skill: str | None = None
    role: str
    stop_reason: str | None = None
    title: str | None = None
    # (P17) the turn's 5-Whys state (None when memory is off) and what ``learn`` wrote
    discovery: dict[str, Any] | None = None
    memory_changes: list[dict[str, Any]] = Field(default_factory=list)


class ResearchState(TypedDict, total=False):
    input: dict[str, Any]
    plan: dict[str, Any]
    memory: dict[str, Any]
    discovery: dict[str, Any]
    loop: dict[str, Any]
    verify: dict[str, Any]
    answer: dict[str, Any]
    question: dict[str, Any]
    learned: dict[str, Any]


def fallback_title(text: str, max_words: int = 6, max_chars: int = 48) -> str:
    words = text.strip().split()
    title = " ".join(words[:max_words])
    return title if len(title) <= max_chars else title[: max_chars - 1].rstrip() + "…"


def _memory(ctx: NodeContext) -> MemoryPort | None:
    memory: MemoryPort | None = ctx.config.get("memory")
    return memory


async def _warn(ctx: NodeContext, code: str, message: str) -> None:
    await ctx.emit.emit("warning", {"code": code, "message": message[:500]})


def _input(state: dict[str, Any]) -> ResearchInput:
    return ResearchInput.model_validate(state["input"])


# -- plan ---------------------------------------------------------------------------------------


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
    inp = _input(state)
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


# -- memory + discovery -------------------------------------------------------------------------


async def recall_memory(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
    memory = _memory(ctx)
    if memory is None:
        return {"memory": {"enabled": False}, "_summary": "memory off"}
    inp = _input(state)
    market = market_of((state.get("plan") or {}).get("symbols") or [])
    try:
        recall = await memory.recall(
            inp.user_id, inp.text, workflow="research", tags={"market": market} if market else None
        )
        if recall.reasoning:
            await memory.bank.record_use([r.id for r in recall.reasoning], ctx.run_id, inp.user_id)
    except Exception as exc:  # memory never breaks a run
        await _warn(ctx, "memory_recall_failed", f"{type(exc).__name__}: {exc}")
        return {"memory": {"enabled": True, "error": str(exc)}, "_summary": "recall failed"}
    items = [{"id": i.id, "category": i.category, "text": i.text, "status": i.status} for i in recall.items]
    reasoning = [
        {"id": r.id, "title": r.title, "description": r.description, "content": r.content}
        for r in recall.reasoning
    ]
    await ctx.emit.emit(
        "memory.recalled",
        {"items": items, "reasoning": [{"id": r["id"], "title": r["title"]} for r in reasoning]},
    )
    value = {
        "enabled": True,
        "profile_text": recall.profile_text,
        "items": items,
        "reasoning": reasoning,
        "market": market,
        "provenance": recall.provenance,
    }
    summary = f"profile {recall.token_estimate} tokens · {len(items)} items · {len(reasoning)} lessons"
    return {"memory": value, "_summary": summary}


def prior_discovery(raw: dict[str, Any] | None) -> SessionDiscovery:
    """The session's 5-Whys state; anything unreadable starts over (never fails the turn)."""
    try:
        return SessionDiscovery.model_validate(raw or {})
    except ValidationError:
        return SessionDiscovery()


def _root_goal_out(goal: RootGoal | dict[str, Any] | None) -> dict[str, Any] | None:
    if goal is None:
        return None
    model = goal if isinstance(goal, RootGoal) else RootGoal.model_validate(goal)
    return model.model_dump(mode="json") if model.text.strip() else None


async def assess_discovery(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
    if _memory(ctx) is None:
        return {"discovery": {"enabled": False, "route": "research", "advice_mode": "normal"},
                "_summary": "memory off"}  # fmt: skip
    inp = _input(state)
    cfg = ctx.config.get("discovery") or {}
    max_whys = int(cfg.get("max_whys", DEFAULT_MAX_WHYS))
    threshold = float(cfg.get("confidence", DEFAULT_DISCOVERY_CONFIDENCE))
    prior = prior_discovery(inp.discovery)
    assessment: DiscoveryAssessment | None = None
    mode = "skipped"
    if not inp.skip_discovery:
        result = await ctx.llm.structured(
            discovery_messages(
                question=inp.text,
                history=[(h.role, h.content) for h in inp.history],
                state=prior.model_dump(mode="json"),
                profile_text=(state.get("memory") or {}).get("profile_text") or "",
                max_whys=max_whys,
            ),
            DiscoveryAssessment,
            tier="quick",
            purpose="memory",
        )
        assessment, mode = result.value, result.mode
        if assessment is None:
            await _warn(ctx, "discovery_unparsed", f"5-Whys assessment not parsed ({result.mode}); answering")
    route = should_ask(
        assessment, prior.whys_asked, inp.skip_discovery, max_whys=max_whys, confidence_threshold=threshold
    )
    question = (assessment.next_question or "").strip() if assessment is not None else ""
    if route != "research" and not question:
        route = "research"  # nothing to ask after all
    chain = [step.model_copy() for step in prior.chain]
    if assessment is not None and assessment.answers_pending_question and chain and not chain[-1].a:
        chain[-1] = WhyStep(q=chain[-1].q, a=inp.text.strip()[:1000])
    if assessment is not None:
        identified = assessment.root_goal_identified
        confidence = assessment.confidence
        stop_reason: str | None = assessment.stop_reason
        root_goal = _root_goal_out(assessment.root_goal) or _root_goal_out(prior.root_goal)
    else:
        identified, confidence, root_goal = (
            prior.root_goal_identified,
            prior.confidence,
            _root_goal_out(prior.root_goal),
        )
        stop_reason = None
    if inp.skip_discovery:
        stop_reason = "resistance"  # the user asked for the analysis without more questions
    elif prior.whys_asked >= max_whys and not identified:
        stop_reason = "max_whys"
    asking = route in ("ask_only", "research_then_ask")
    whys = prior.whys_asked + (1 if asking else 0)
    if asking:
        chain.append(WhyStep(q=question))
    advice_mode = advice_mode_of(assessment)
    value: dict[str, Any] = {
        "enabled": True,
        "route": route,
        "mode": mode,
        "max_whys": max_whys,
        "whys_asked": whys,
        "chain": [s.model_dump() for s in chain],
        "root_goal": root_goal,
        "root_goal_identified": identified,
        "confidence": confidence,
        "next_question": question if asking else None,
        "stop_reason": stop_reason,
        "advice_mode": advice_mode,
        "request_type": assessment.request_type if assessment is not None else None,
        "memory_candidates": [c.model_dump() for c in assessment.memory_candidates]
        if assessment is not None
        else [],
    }
    await ctx.emit.emit(
        "discovery.assessed",
        {
            "whys_asked": whys,
            "root_goal_identified": identified,
            "confidence": confidence,
            "stop_reason": stop_reason,
            "advice_mode": advice_mode,
            "next_question": value["next_question"],
            "root_goal": {"text": root_goal["text"], "category": root_goal.get("category")}
            if root_goal
            else None,
        },
    )
    summary = f"{route} · whys {whys}/{max_whys}" + (f" · {stop_reason}" if stop_reason else "")
    return {"discovery": value, "_summary": summary}


def route_after_assess(state: dict[str, Any]) -> str:
    route = (state.get("discovery") or {}).get("route")
    return route if route in ROUTES else "research"


def discovery_payload(state: dict[str, Any]) -> dict[str, Any] | None:
    """``answer.final.discovery`` when this turn asks a why-question."""
    d = state.get("discovery") or {}
    if not d.get("next_question"):
        return None
    return {
        "whys_asked": int(d.get("whys_asked") or 1),
        "max": int(d.get("max_whys") or DEFAULT_MAX_WHYS),
        "question": d["next_question"],
    }


def memory_context(state: dict[str, Any]) -> list[str]:
    """INVESTOR PROFILE + LESSONS + advice mode for the agent's / final answer's system prompt."""
    mem = state.get("memory") or {}
    return context_blocks(
        profile_text=mem.get("profile_text") or "",
        lessons=mem.get("reasoning") or [],
        advice_mode=(state.get("discovery") or {}).get("advice_mode"),
    )


async def write_question(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
    """ask_only: reflect + the why-question (tool-less quick call), no research this turn."""
    inp = _input(state)
    disc = discovery_payload(state) or {"whys_asked": 1, "max": DEFAULT_MAX_WHYS, "question": ""}
    question = disc["question"]
    parts: list[str] = []
    try:
        async for delta in ctx.llm.stream(
            question_messages(
                question=question,
                user_message=inp.text,
                history=[(h.role, h.content) for h in inp.history],
            ),
            tier="quick",
            purpose="final",
        ):
            await ctx.emit.emit("llm.delta", {"channel": delta.channel, "text": delta.text})
            if delta.channel == "answer":
                parts.append(delta.text)
    except Exception as exc:  # the question itself is enough
        await _warn(ctx, "question_failed", f"{type(exc).__name__}: {exc}")
    markdown = "".join(parts).strip()
    if question and question not in markdown:
        markdown = f"{markdown}\n\n{question}".strip() if markdown else f"{question}\n\n{SKIP_NOTE}"
    await ctx.emit.emit(
        "answer.final",
        {"markdown": markdown, "citations": [], "unverified_numbers": [], "discovery": disc},
    )
    value: dict[str, Any] = {
        "markdown": markdown,
        "citations": [],
        "unverified_numbers": [],
        "stop_reason": None,
    }
    return {"question": value, "_summary": f"why {disc['whys_asked']}/{disc['max']}"}


# -- research -----------------------------------------------------------------------------------


async def retrieve_analyze(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
    p = state["plan"]
    needs_data = bool(p.get("skill") or p.get("symbols"))
    result = await ctx.agent.run_loop(
        AgentRunRequest(
            role=p["role"],
            skill=p.get("skill"),
            require_data=needs_data,
            context_blocks=memory_context(state),
        )
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
                context_blocks=memory_context(state),
            )
        )
        out.update(reloop=True, second=second.model_dump())
    summary = f"{len(unverified)} unverified in brief" + (" · re-checked once" if out["reloop"] else "")
    return {"verify": out, "_summary": summary}


async def synthesize_report(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
    second = state.get("verify", {}).get("second") or {}
    stop = second.get("stop_reason") or state["loop"].get("stop_reason")
    disc = discovery_payload(state)
    answer = await ctx.agent.final_answer(
        role=state["plan"]["role"],
        stop_reason=stop,
        context_blocks=memory_context(state),
        closing_question=disc["question"] if disc else None,
        discovery=disc,
    )
    value = {**answer.model_dump(), "stop_reason": stop}
    return {
        "answer": value,
        "_summary": f"{len(answer.citations)} citations · {len(answer.unverified_numbers)} unverified",
    }


# -- learn --------------------------------------------------------------------------------------


def judge_research(state: dict[str, Any]) -> bool | None:
    """Rule-based outcome (no LLM): failure = a limit stopped it or > 3 unverified numbers;
    success = cited, nothing unverified, and the goal is understood (or it was just information).
    Anything else, and an ask-only turn, is not judged (None)."""
    answer = state.get("answer")
    if not answer:
        return None
    kind = (answer.get("stop_reason") or "").split(":", 1)[0]
    unverified = len(answer.get("unverified_numbers") or [])
    if kind in LIMIT_KINDS or unverified > MAX_UNVERIFIED_BEFORE_RECHECK:
        return False
    disc = state.get("discovery") or {}
    understood = bool(disc.get("root_goal_identified")) or disc.get("request_type") == "info"
    if answer.get("citations") and unverified == 0 and understood:
        return True
    return None


def _run_summary(state: dict[str, Any], success: bool) -> str:
    inp = state["input"]
    p = state.get("plan") or {}
    loop = state.get("loop") or {}
    answer = state.get("answer") or {}
    disc = state.get("discovery") or {}
    return "\n".join(
        [
            f"Question: {str(inp.get('text', ''))[:600]}",
            f"Scenario: {p.get('skill') or 'free-form'} · role {p.get('role')}",
            f"Request type: {disc.get('request_type') or 'unknown'} · advice mode {disc.get('advice_mode')}",
            f"Agent: {loop.get('iterations', 0)} iterations, {loop.get('tool_calls', 0)} tool calls, "
            f"stop {answer.get('stop_reason') or 'none'}",
            f"Answer: {len(answer.get('citations') or [])} citations, "
            f"{len(answer.get('unverified_numbers') or [])} unverified numbers → "
            + ("success" if success else "failure"),
            f"Answer text:\n{str(answer.get('markdown', ''))[:2500]}",
        ]
    )


def _discovery_for_learn(state: dict[str, Any]) -> DiscoveryAssessment | None:
    d = state.get("discovery") or {}
    if not d.get("root_goal_identified"):
        return None
    try:
        return DiscoveryAssessment.model_validate(
            {
                "root_goal_identified": True,
                "root_goal": d.get("root_goal"),
                "confidence": d.get("confidence") or 0.0,
                "memory_candidates": d.get("memory_candidates") or [],
            }
        )
    except ValidationError:
        return None


async def learn(state: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
    memory = _memory(ctx)
    if memory is None:
        return {"learned": {"enabled": False}, "_summary": "memory off"}
    inp = _input(state)
    reply = state.get("answer") or state.get("question") or {}
    out: dict[str, Any] = {"enabled": True, "changes": [], "distilled": [], "judgement": None}
    try:
        source_ref = {"run_id": ctx.run_id, "session_id": ctx.config.get("session_id")}
        changes = await memory.learn(
            inp.user_id,
            user_messages=[inp.text],
            assistant_text=str(reply.get("markdown") or ""),
            discovery=_discovery_for_learn(state),
            source_ref={k: v for k, v in source_ref.items() if v},
            llm=ctx.llm,
            emit=ctx.emit,
            node="learn",
        )
        out["changes"] = [
            {
                "op": c.op,
                "id": c.item.id,
                "category": c.item.category,
                "text": c.item.text,
                "previous_text": c.previous.text if c.previous else None,
                "status": c.item.status,
            }
            for c in changes
            if c.op != "NOOP"
        ]
    except Exception as exc:
        await _warn(ctx, "memory_learn_failed", f"{type(exc).__name__}: {exc}")
    try:
        success = judge_research(state)
        out["judgement"] = success
        if success is not None:
            tags = {
                "workflow": "research",
                "scenario": (state.get("plan") or {}).get("skill"),
                "market": (state.get("memory") or {}).get("market"),
            }
            items = await memory.bank.distill(
                run_summary=_run_summary(state, success),
                outcome=OutcomeJudgement(
                    success=success, score=1.0 if success else 0.0, reason="rule-based research judgement"
                ),
                tags={k: v for k, v in tags.items() if v},
                source_run_id=ctx.run_id,
                user_id=inp.user_id,
                max_items=1,
                llm=ctx.llm,
                emit=ctx.emit,
                node="learn",
            )
            out["distilled"] = [{"id": i.id, "title": i.title, "kind": i.kind} for i in items]
            await memory.bank.settle(ctx.run_id, success)
    except Exception as exc:
        await _warn(ctx, "memory_learn_failed", f"ReasoningBank: {type(exc).__name__}: {exc}")
    summary = f"{len(out['changes'])} memory changes · {len(out['distilled'])} lessons"
    return {"learned": out, "_summary": summary}


# -- output + spec ------------------------------------------------------------------------------


def output_of(state: dict[str, Any]) -> ResearchOutput:
    answer = state.get("answer") or state.get("question") or {}
    plan_ = state.get("plan", {})
    disc = state.get("discovery") or {}
    return ResearchOutput(
        markdown=answer.get("markdown", ""),
        citations=answer.get("citations", []),
        unverified_numbers=answer.get("unverified_numbers", []),
        skill=plan_.get("skill"),
        role=plan_.get("role", "research_lead"),
        stop_reason=answer.get("stop_reason"),
        title=plan_.get("title"),
        discovery=disc if disc.get("enabled") else None,
        memory_changes=(state.get("learned") or {}).get("changes") or [],
    )


def _node(id_: str, label: str, kind: NodeKind, phase: str, fn: Any, writes: set[str], **kw: Any) -> NodeSpec:
    return NodeSpec(id=id_, label=label, kind=kind, phase=phase, fn=fn, writes=frozenset(writes), **kw)


RESEARCH_WORKFLOW = WorkflowSpec(
    id="research",
    version="2",
    input_model=ResearchInput,
    output_model=ResearchOutput,
    state_model=ResearchState,
    reducers={
        "input": "replace",
        "plan": "replace",
        "memory": "replace",
        "discovery": "replace",
        "loop": "replace",
        "verify": "replace",
        "answer": "replace",
        "question": "replace",
        "learned": "replace",
    },
    nodes=[
        _node("plan", "Plan", NodeKind.AGENT, "retrieve", plan, {"plan"}),
        _node("recall_memory", "Recall memory", NodeKind.COMPUTE, "retrieve", recall_memory, {"memory"}),
        _node(
            "assess_discovery", "Understand goal", NodeKind.AGENT, "analyze", assess_discovery, {"discovery"}
        ),
        _node("write_question", "Ask why", NodeKind.REPORT, "synthesize", write_question, {"question"}),
        _node("retrieve_analyze", "Research", NodeKind.AGENT, "analyze", retrieve_analyze, {"loop"}),
        _node("verify", "Verify", NodeKind.COMPUTE, "verify", verify, {"verify"}),
        _node("synthesize_report", "Answer", NodeKind.REPORT, "synthesize", synthesize_report, {"answer"}),
        _node("learn", "Learn", NodeKind.COMPUTE, "report", learn, {"learned"}, idempotent=False),
    ],
    edges=[
        ("plan", "recall_memory"),
        ("recall_memory", "assess_discovery"),
        ("write_question", "learn"),
        ("retrieve_analyze", "verify"),
        ("verify", "synthesize_report"),
        ("synthesize_report", "learn"),
        ("learn", "END"),
    ],
    routers=[
        RouterSpec(
            source="assess_discovery",
            fn=route_after_assess,
            path_map={
                "ask_only": "write_question",
                "research": "retrieve_analyze",
                "research_then_ask": "retrieve_analyze",
                "__fallback__": "retrieve_analyze",
            },
            returns=frozenset(ROUTES),
        )
    ],
    entry="plan",
)
