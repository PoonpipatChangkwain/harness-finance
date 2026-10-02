"""Serialize tool output into the text the LLM reads.

Ported concept from ``formatToolResult`` in ``../dexter/src/tools/types.ts``
and ``stringify_output`` in ``../FinRobot/finrobot/utils.py``: lists of
flat records become a markdown table, everything else compact JSON, then
the text is cut at ``max_chars`` with a pointer to the full result in the
scratchpad.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel
from pydantic_core import to_jsonable_python

DEFAULT_MAX_CHARS = 12_000
_MAX_TABLE_COLUMNS = 12


def to_jsonable(data: Any) -> Any:
    if isinstance(data, BaseModel):
        return data.model_dump(mode="json")
    return to_jsonable_python(data, fallback=str)


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.6g}"
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text.replace("|", "\\|").replace("\n", " ")


def _table(rows: list[dict[str, Any]]) -> str | None:
    columns: list[str] = []
    for row in rows:
        for key, value in row.items():
            if isinstance(value, dict | list):
                return None  # nested → JSON reads better
            if key not in columns:
                columns.append(key)
    if not columns or len(columns) > _MAX_TABLE_COLUMNS:
        return None
    lines = ["| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
    lines += ["| " + " | ".join(_cell(row.get(c)) for c in columns) + " |" for row in rows]
    return "\n".join(lines)


def format_output(data: Any) -> str:
    if isinstance(data, str):
        return data
    jsonable = to_jsonable(data)
    if isinstance(jsonable, list) and jsonable and all(isinstance(r, dict) for r in jsonable):
        table = _table(jsonable)
        if table is not None:
            return table
    if isinstance(jsonable, dict):
        # A dict wrapping one list of records (e.g. an OHLCV frame): header + table.
        list_keys = [k for k, v in jsonable.items() if isinstance(v, list) and v and isinstance(v[0], dict)]
        if len(list_keys) == 1:
            key = list_keys[0]
            table = _table(jsonable[key])
            if table is not None:
                header = {k: v for k, v in jsonable.items() if k != key}
                return json.dumps(header, ensure_ascii=False) + f"\n\n{key}:\n" + table
    return json.dumps(jsonable, ensure_ascii=False, separators=(",", ":"))


def truncate(text: str, max_chars: int, call_id: str) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars] + f"\n…[truncated; full result in scratchpad call_id={call_id}]", True
