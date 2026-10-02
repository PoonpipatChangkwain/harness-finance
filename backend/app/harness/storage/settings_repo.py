"""Repository for Settings-page overrides (P13 §13.4): ``{section: {field: value}}``."""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select

from app.harness.storage.db import session_scope
from app.harness.storage.models import SettingRow


async def load_all() -> dict[str, dict[str, Any]]:
    async with session_scope() as s:
        return {row.key: dict(row.value or {}) for row in (await s.execute(select(SettingRow))).scalars()}


async def save_section(key: str, value: dict[str, Any]) -> None:
    """Replace one section; an empty section deletes the row (back to defaults)."""
    async with session_scope() as s:
        row = await s.get(SettingRow, key)
        if not value:
            if row is not None:
                await s.execute(delete(SettingRow).where(SettingRow.key == key))
        elif row is None:
            s.add(SettingRow(key=key, value=value))
        else:
            row.value = value
        await s.commit()
