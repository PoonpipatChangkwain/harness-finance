"""The ``skill`` tool: returns a skill's instructions for the agent to follow (D4).

Ported from ``../dexter/src/tools/skill.ts`` (``SKILL_TOOL_DESCRIPTION``,
"don't invoke twice"). Each skill runs at most once per query: the executor
counts calls per ``(skill tool, skill name)`` via ``limit_by="skill"``.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.domain.tools.base import ToolContext, ToolOutput, ToolSpec
from app.harness.skills.registry import SkillRegistry

SKILL_TOOL_DESCRIPTION = """Load a skill: step-by-step instructions for a complex task
(earnings review, valuation, memo…).

When to use:
- The user's request matches an available skill's description
- Invoke it IMMEDIATELY as your first action when relevant

When NOT to use:
- Simple questions that need one or two data tools
- A skill already loaded for this query (each skill loads once)

Use the skill name exactly as listed in Available Skills; pass tickers or other context via args."""


class SkillArgs(BaseModel):
    skill: str = Field(description="Skill name exactly as listed, e.g. 'company-valuation'")
    args: str | None = Field(default=None, description="Optional context, e.g. the ticker")


def skill_tool(registry: SkillRegistry) -> ToolSpec:
    async def run_skill(args: SkillArgs, ctx: ToolContext) -> ToolOutput:
        if not registry.is_available(args.skill):
            names = ", ".join(s.name for s in registry.available()) or "none"
            raise ValueError(f"skill '{args.skill}' is not available. Available skills: {names}")
        body = registry.instructions(args.skill, args.args)
        return ToolOutput(data=body, summary=f"loaded skill {args.skill}")

    return ToolSpec(
        name="skill",
        description=SKILL_TOOL_DESCRIPTION,
        args_model=SkillArgs,
        category="skill",
        max_calls_per_run=1,
        limit_by="skill",
        fn=run_skill,
    )
