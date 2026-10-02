"""SkillRegistry: discover skills once, report availability, build prompt sections (D4).

Ported from ``../dexter/src/skills/registry.ts`` (discover, cache by name,
``buildSkillMetadataSection``). Availability: a skill whose ``requires``
lists data providers is only offered when each of them is configured.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel

from app.harness.skills.loader import Skill, resolve_links, scan

logger = logging.getLogger(__name__)

DEFAULT_SKILLS_ROOT = Path(__file__).resolve().parents[2] / "domain" / "skills"

ProviderCheck = Callable[[str], bool]


def provider_configured(provider_id: str) -> bool:
    from app.domain.data.registry import get_router

    provider = get_router().providers().get(provider_id)
    return provider is not None and provider.is_configured()


class SkillInfo(BaseModel):
    name: str
    description: str
    category: str
    role: str | None
    inputs: list[str]
    available: bool
    missing: list[str]


class SkillRegistry:
    def __init__(self, root: Path = DEFAULT_SKILLS_ROOT, *, provider_ok: ProviderCheck = provider_configured):
        self.root = root
        self._provider_ok = provider_ok
        self._skills: dict[str, Skill] = {}
        for skill in scan(root):
            if skill.name in self._skills:
                logger.warning("duplicate skill %r at %s; keeping the first", skill.name, skill.path)
                continue
            self._skills[skill.name] = skill

    def names(self) -> list[str]:
        return list(self._skills)

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def missing(self, skill: Skill) -> list[str]:
        return [p for p in skill.requires if not self._provider_ok(p)]

    def is_available(self, name: str) -> bool:
        skill = self._skills.get(name)
        return skill is not None and not self.missing(skill)

    def available(self) -> list[Skill]:
        return [s for s in self._skills.values() if not self.missing(s)]

    def info(self) -> list[SkillInfo]:
        out = []
        for s in self._skills.values():
            missing = self.missing(s)
            out.append(
                SkillInfo(
                    name=s.name,
                    description=s.description,
                    category=s.category,
                    role=s.role,
                    inputs=s.inputs,
                    available=not missing,
                    missing=missing,
                )
            )
        return out

    def instructions(self, name: str, args: str | None = None) -> str:
        skill = self._skills[name]
        head = f"## Skill: {skill.name}\n\n"
        if args:
            head += f"**Arguments provided:** {args}\n\n"
        return head + resolve_links(skill)

    def metadata_section(self) -> str:
        skills = self.available()
        if not skills:
            return "No skills available."
        return "\n".join(f"- **{s.name}** ({s.category}): {s.description}" for s in skills)
