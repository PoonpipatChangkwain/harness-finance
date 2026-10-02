"""Run ``alembic upgrade head`` from code (API startup).

Alembic's ``env.py`` drives the async engine with ``asyncio.run``, so this
runs in a worker thread with its own event loop.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from alembic.config import Config

from alembic import command

_BACKEND_DIR = Path(__file__).resolve().parents[3]


def _config(url: str | None = None) -> Config:
    cfg = Config(str(_BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    if url:
        cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def upgrade_head(url: str | None = None) -> None:
    command.upgrade(_config(url), "head")


async def upgrade_head_async(url: str | None = None) -> None:
    await asyncio.to_thread(upgrade_head, url)
