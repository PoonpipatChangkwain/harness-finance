"""Structured output with a free-text fallback that never raises (T5).

Ported from ``../TradingAgents/tradingagents/agents/structured.py``
(``bind_structured`` + ``invoke_structured_or_freetext``):

1. native ``with_structured_output`` → ``mode="native"``
2. on any miss, one free-text call with the JSON schema in the prompt, then
   extract a JSON block and ``schema.model_validate`` → ``"fallback_parsed"``
3. still nothing → ``StructuredResult(value=None, mode="failed", raw_text=...)``

Provider-shaped errors (5xx, overloaded, rate limit, auth) propagate out of
``invoke_structured`` so the failover chain can switch clients; the
adapter turns an exhausted chain into ``mode="failed"``, so the router
downstream always gets a result and routes a failure to REVIEW.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.runnables import Runnable
from pydantic import BaseModel, ValidationError

from app.domain.ports import LLMUsage, StructuredResult
from app.harness.llm.base import BaseLLMClient
from app.harness.llm.errors import classify_error, should_failover
from app.harness.llm.messages import split_content
from app.harness.llm.usage import extract_usage

logger = logging.getLogger(__name__)

# Schema-only structured calls bind no search tools; say so explicitly so a
# model doesn't reach for one and waste the structured attempt.
NO_EXTERNAL_TOOLS = (
    "Use only the evidence provided in this prompt. Do not call external tools "
    "or search the web; if something is missing, say so explicitly."
)

_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)


def bind_structured(client: BaseLLMClient, schema: type[BaseModel]) -> Runnable[Any, Any] | None:
    return client.with_structured(schema)


def _balanced_object(text: str, start: int) -> str | None:
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _as_dict(raw: str) -> dict[str, Any] | None:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def extract_json_object(text: str) -> dict[str, Any] | None:
    """First JSON object in ``text``: a ```json fence, else the first balanced ``{...}`` that parses."""
    if not text:
        return None
    for match in _FENCE_RE.finditer(text):
        if (obj := _as_dict(match.group(1))) is not None:
            return obj
    start = text.find("{")
    while start != -1:
        raw = _balanced_object(text, start)
        if raw is not None and (obj := _as_dict(raw)) is not None:
            return obj
        start = text.find("{", start + 1)
    return None


def parse_structured[S: BaseModel](text: str, schema: type[S]) -> S | None:
    obj = extract_json_object(text)
    if obj is None:
        return None
    try:
        return schema.model_validate(obj)
    except ValidationError:
        return None


def _placeholder(prop: dict[str, Any], defs: dict[str, Any]) -> Any:
    if "$ref" in prop:
        prop = defs.get(prop["$ref"].rsplit("/", 1)[-1], {})
    if "enum" in prop:
        return " | ".join(str(v) for v in prop["enum"])
    if "anyOf" in prop:
        options = [p for p in prop["anyOf"] if p.get("type") != "null"]
        return _placeholder(options[0], defs) if options else None
    return {"string": "<text>", "number": "<number>", "integer": "<integer>", "boolean": "<true|false>",
            "array": [], "object": {}}.get(str(prop.get("type")), "<value>")  # fmt: skip


def fallback_instruction(schema: type[BaseModel]) -> str:
    """Schema plus a filled-in skeleton: weak models otherwise echo the schema back."""
    json_schema = schema.model_json_schema()
    defs = json_schema.get("$defs", {})
    skeleton = {name: _placeholder(prop, defs) for name, prop in json_schema.get("properties", {}).items()}
    return (
        f"{NO_EXTERNAL_TOOLS}\n\n"
        "Answer with ONE JSON object holding your actual answer (an instance, not the schema), "
        "inside a ```json code block, and nothing else. Replace every <placeholder>; for a "
        "choice like A | B | C pick exactly one value.\n"
        f"Shape:\n```json\n{json.dumps(skeleton, ensure_ascii=False)}\n```\n"
        f"JSON schema it must validate against:\n```json\n{json.dumps(json_schema, ensure_ascii=False)}\n```"
    )


def _is_provider_error(exc: BaseException) -> bool:
    kind = classify_error(exc)
    return kind != "unknown" and should_failover(kind)


async def invoke_structured[S: BaseModel](
    client: BaseLLMClient, messages: list[BaseMessage], schema: type[S]
) -> StructuredResult[S]:
    usage = LLMUsage()
    raw_text = ""

    runnable = bind_structured(client, schema)
    if runnable is not None:
        try:
            result = await client.ainvoke_runnable(runnable, messages)
            raw_msg = result.get("raw") if isinstance(result, dict) else None
            parsed = result.get("parsed") if isinstance(result, dict) else result
            if raw_msg is not None:
                usage = usage + extract_usage(raw_msg, client.info)
                raw_text = split_content(raw_msg.content)[0]
            if isinstance(parsed, dict):
                parsed = schema.model_validate(parsed)
            if isinstance(parsed, schema):
                return StructuredResult[S](
                    value=parsed,
                    raw_text=raw_text,
                    mode="native",
                    usage=usage,
                    provider=client.provider,
                    model=client.model,
                )
            error = result.get("parsing_error") if isinstance(result, dict) else None
            logger.warning(
                "%s: native structured output returned no parse (%s); falling back", schema.__name__, error
            )
        except ValidationError as exc:
            logger.warning("%s: native structured output invalid (%s); falling back", schema.__name__, exc)
        except Exception as exc:
            if _is_provider_error(exc):
                raise
            logger.warning("%s: native structured call failed (%s); falling back", schema.__name__, exc)

    response = await client.ainvoke([*messages, HumanMessage(content=fallback_instruction(schema))])
    usage = usage + extract_usage(response, client.info)
    text = split_content(response.content)[0]
    value = parse_structured(text, schema)
    return StructuredResult[S](
        value=value,
        raw_text=text,
        mode="fallback_parsed" if value is not None else "failed",
        usage=usage,
        provider=client.provider,
        model=client.model,
    )
