"""GET /api/skills — research scenarios for the UI picker, with availability (P07 §7.7)."""

from __future__ import annotations

from fastapi import APIRouter

from app.harness.runtime.services import get_services
from app.harness.skills.registry import SkillInfo

router = APIRouter(tags=["skills"])


@router.get("/skills", response_model=list[SkillInfo])
async def list_skills() -> list[SkillInfo]:
    return get_services().skills.info()
