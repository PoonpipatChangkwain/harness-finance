from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.domain.agents.library import LIBRARY, get_role, role_profile
from app.harness.runtime.prompts import build_system_prompt
from app.harness.skills.loader import parse_skill_file, resolve_links, scan
from app.harness.skills.registry import SkillRegistry
from app.harness.skills.tool import skill_tool
from tests.harness.fake_tools import all_tools

EXPECTED = {
    "earnings-review",
    "macro-outlook",
    "sector-scan",
    "news-digest",
    "company-valuation",
    "technical-analysis",
    "sentiment-check",
    "compare-companies",
    "investment-memo",
    "x-research",
    "portfolio-risk-review",
    "portfolio-weakness",
}


def test_all_builtin_skills_load_with_valid_roles() -> None:
    reg = SkillRegistry(provider_ok=lambda p: True)
    assert set(reg.names()) == EXPECTED
    for name in reg.names():
        skill = reg.get(name)
        assert skill is not None and skill.role in LIBRARY, name
        assert "compute" in skill.body or "tool" in skill.body.lower()


def test_links_become_attachments_and_stay_in_folder(tmp_path: Path) -> None:
    folder = tmp_path / "s"
    folder.mkdir()
    (folder / "extra.md").write_text("EXTRA BODY", encoding="utf-8")
    (tmp_path / "secret.md").write_text("SECRET", encoding="utf-8")
    (folder / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: a demo skill for tests\n---\n"
        "See [extra](extra.md) twice [again](extra.md), not [up](../secret.md) or [web](https://x.io/a.md).",
        encoding="utf-8",
    )
    skill = parse_skill_file(folder / "SKILL.md")
    assert skill is not None
    text = resolve_links(skill)
    assert text.count("EXTRA BODY") == 1 and "SECRET" not in text


@pytest.mark.parametrize(
    "content",
    [
        "no frontmatter",
        "---\nname: Bad Name!\ndescription: long enough description\n---\nbody",
        "---\nname: ok-name\n---\nmissing description",
        "---\n- a list\n---\nbody",
        "---\nname: [unclosed\n---\nbody",
    ],
)
def test_invalid_frontmatter_is_skipped(tmp_path: Path, content: str) -> None:
    path = tmp_path / "x" / "SKILL.md"
    path.parent.mkdir()
    path.write_text(content, encoding="utf-8")
    assert parse_skill_file(path) is None
    assert scan(tmp_path) == []


def test_unreadable_skill_is_skipped(tmp_path: Path) -> None:
    assert parse_skill_file(tmp_path / "missing" / "SKILL.md") is None


def test_duplicate_names_keep_first(tmp_path: Path) -> None:
    for d in ("a", "b"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "SKILL.md").write_text(
            f"---\nname: same\ndescription: dup from {d} folder\n---\nbody {d}", encoding="utf-8"
        )
    reg = SkillRegistry(tmp_path, provider_ok=lambda p: True)
    skill = reg.get("same")
    assert skill is not None and skill.body == "body a"


def test_availability_and_metadata_section(tmp_path: Path) -> None:
    reg = SkillRegistry(provider_ok=lambda p: p != "x_api")
    assert not reg.is_available("x-research") and reg.is_available("macro-outlook")
    assert not reg.is_available("nope")
    section = reg.metadata_section()
    assert "x-research" not in section and "**company-valuation** (analysis)" in section
    assert SkillRegistry(tmp_path).metadata_section() == "No skills available."
    body = reg.instructions("company-valuation", "PTT")
    assert body.startswith("## Skill: company-valuation") and "**Arguments provided:** PTT" in body
    assert "## Attached: sector-wacc.md" in body and "Utilities" in body


async def test_skill_tool_runs_and_rejects_unknown() -> None:
    reg = SkillRegistry(provider_ok=lambda p: p != "x_api")
    spec = skill_tool(reg)
    assert spec.limit_by == "skill" and spec.max_calls_per_run == 1
    assert spec.limit_key({"skill": "a"}) != spec.limit_key({"skill": "b"})
    out = await spec.fn(spec.args_model(skill="news-digest"), None)  # type: ignore[arg-type]
    assert "News Digest" in out.data
    with pytest.raises(ValueError, match="not available"):
        await spec.fn(spec.args_model(skill="x-research"), None)  # type: ignore[arg-type]


def test_every_role_has_a_prompt_and_prompt_has_sections() -> None:
    for role in LIBRARY.values():
        assert len(role_profile(role)) > 200, role.name
    assert get_role("unknown").name == "research_lead"
    reg = SkillRegistry(provider_ok=lambda p: True)
    tools: list[Any] = [*all_tools(), skill_tool(reg)]
    prompt = build_system_prompt(get_role("valuation_specialist"), tools, reg)
    assert "You are a Valuation Specialist" in prompt
    assert "## Available Skills" in prompt and "### get_prices (data, max 3 calls/run)" in prompt
    assert "### skill (skill, once per skill)" in prompt
    no_skill = build_system_prompt(get_role(None), all_tools(), reg)
    assert "## Available Skills" not in no_skill and "## Memory" not in no_skill
