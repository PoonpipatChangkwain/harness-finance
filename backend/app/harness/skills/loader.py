"""SKILL.md loader: YAML frontmatter + markdown body (D4).

Ported from ``../dexter/src/skills/loader.ts``. A file with bad or
incomplete frontmatter is logged and skipped rather than breaking startup.
Relative ``[label](file.md)`` links are resolved by appending the linked
file (confined to the skill's own folder) to the body, since our agent has
no ``read_file`` tool (Dexter rewrites them to absolute paths instead).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+\.md)\)")

SkillCategory = str


class SkillMeta(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,63}$")
    description: str = Field(min_length=10)
    category: str = "research"
    role: str | None = None
    inputs: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    requires: list[str] = Field(default_factory=list)  # data provider ids
    path: Path


class Skill(SkillMeta):
    body: str


def parse_skill_file(path: Path) -> Skill | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("skill %s unreadable: %s", path, exc)
        return None
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        logger.warning("skill %s has no YAML frontmatter; skipped", path)
        return None
    try:
        front: Any = yaml.safe_load(match.group(1)) or {}
        if not isinstance(front, dict):
            raise ValueError("frontmatter is not a mapping")
        return Skill(**front, path=path, body=match.group(2).strip())
    except (yaml.YAMLError, ValidationError, ValueError, TypeError) as exc:
        logger.warning("skill %s has invalid frontmatter (%s); skipped", path, exc)
        return None


def resolve_links(skill: Skill) -> str:
    """Body + every linked sibling ``.md`` appended once (links outside the folder are left alone)."""
    folder = skill.path.parent.resolve()
    attached: list[str] = []
    seen: set[Path] = set()
    for _label, rel in _LINK_RE.findall(skill.body):
        if rel.startswith(("http://", "https://", "/")):
            continue
        target = (folder / rel).resolve()
        if target in seen or not target.is_relative_to(folder) or not target.is_file():
            continue
        seen.add(target)
        attached.append(f"\n\n---\n\n## Attached: {rel}\n\n{target.read_text(encoding='utf-8').strip()}")
    return skill.body + "".join(attached)


def scan(root: Path) -> list[Skill]:
    """Every ``**/SKILL.md`` under ``root`` (sorted for stable prompts)."""
    return [s for p in sorted(root.rglob("SKILL.md")) if (s := parse_skill_file(p)) is not None]
