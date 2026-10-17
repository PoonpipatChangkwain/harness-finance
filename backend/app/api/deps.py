"""Shared FastAPI dependencies."""

from __future__ import annotations

import re
from typing import Annotated

from fastapi import Header, HTTPException

from app.harness.memory.store import DEFAULT_USER_ID, USER_ID_PATTERN

_USER_ID_RE = re.compile(USER_ID_PATTERN)


def current_user_id(
    x_user_id: Annotated[
        str | None, Header(alias="X-User-Id", description="User id (usr_…); missing = usr_default")
    ] = None,
) -> str:
    """(P17) The user the request is for: header ``X-User-Id`` (single-user app: default ``usr_default``)."""
    if x_user_id is None or not x_user_id.strip():
        return DEFAULT_USER_ID
    value = x_user_id.strip()
    if not _USER_ID_RE.fullmatch(value):
        raise HTTPException(status_code=400, detail="X-User-Id must match ^usr_[A-Za-z0-9_-]{1,40}$")
    return value
