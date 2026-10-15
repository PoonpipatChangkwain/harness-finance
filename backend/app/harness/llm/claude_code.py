"""Provider ``claude_code``: Claude through the user's own subscription (P16).

Every call spawns the locally installed, unmodified Claude Code CLI in
headless mode (``claude -p --output-format json``), the way the Agent SDK
docs describe running it as a subprocess. The prompt goes in on stdin and
the system prompt in a file, so neither shows up in the process list; the
OAuth token from ``claude setup-token`` (``ANTHROPIC_API_SUB_KEY``) is
passed only through the child's environment and is never logged, put in
an exception or in ``response_metadata``.

**Personal use on the user's own machine only.** Claude Code's legal terms
allow the subscription OAuth token for ordinary use of Claude Code; a
deployment serving other people must use provider ``anthropic`` with their
own API key.

What the CLI cannot do natively is emulated:

- tool calling: the tool list is appended to the system prompt and the
  reply is forced into the ``--json-schema`` envelope
  ``{"text": str, "tool_calls": [{"name": str, "args": object}]}``, which
  becomes ``AIMessage.tool_calls``;
- structured output: ``--json-schema <schema>`` → ``structured_output`` of
  the result (else the first JSON object in ``result``), returned as
  ``{"raw", "parsed", "parsing_error"}`` like LangChain's ``include_raw``.

Cost: ``usage`` from the result is priced with the normal model catalog
(API list prices) so the run budget guard keeps working, even though a
subscription is not billed per token. Streaming is not supported yet: the
model answers in one chunk.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import weakref
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any
from uuid import uuid4

from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models import LanguageModelInput
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.messages.ai import UsageMetadata
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable, RunnableLambda
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from app.harness.llm.errors import ErrorKind, LLMError, LLMNotConfigured
from app.harness.llm.messages import split_content
from app.harness.structured.invoke import extract_json_object
from app.settings import Settings

logger = logging.getLogger(__name__)

PROVIDER_ID = "claude_code"
CLI_NOT_FOUND = "Claude Code CLI not found; install Claude Code and run `claude setup-token`"
NO_LOGIN = (
    "Claude Code has no login on this machine; run `claude setup-token` and set "
    "ANTHROPIC_API_SUB_KEY in backend/.env (or log in once with `claude`)"
)
DEFAULT_SYSTEM = "You are a helpful, concise assistant."

# Parent-environment variables the child must not inherit: API keys would switch the CLI
# from the subscription to API billing, and a parent Claude Code session's own variables
# (CLAUDECODE, CLAUDE_CODE_SESSION_ID, messaging socket, ...) would make the child act as
# its sub-session.
_DROP_ENV = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "CLAUDECODE",
        "CLAUDE_CODE_ENTRYPOINT",
        "CLAUDE_PID",
        "CLAUDE_EFFORT",
        "CLAUDE_AGENT_SDK_VERSION",
    }
)
_KEEP_ENV = frozenset({"CLAUDE_CODE_GIT_BASH_PATH"})  # Windows: where git-bash lives
# Keep the project's / user's CLAUDE.md and the repo's git status out of the model's context.
_FORCED_ENV = {"CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1", "CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS": "1"}

TOOL_ENVELOPE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "text": {"type": "string"},
        "tool_calls": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "args": {"type": "object"}},
                "required": ["name", "args"],
            },
        },
    },
    "required": ["text", "tool_calls"],
}

TRANSCRIPT_INSTRUCTION = (
    "The conversation so far is above, one block per message ([user], [assistant] with any "
    "tool calls it made, [tool_result] with what a tool returned). Write the next [assistant] "
    "turn: answer the last message. Do not repeat the block labels."
)


# -- CLI discovery / login ------------------------------------------------------


def find_cli(settings: Settings) -> str | None:
    """``CLAUDE_CODE_BIN`` if set, else ``claude`` on PATH (``claude.cmd`` / ``claude.exe`` on Windows)."""
    configured = (settings.claude_code_bin or "").strip()
    if configured:
        path = Path(configured).expanduser()
        if path.is_file():
            return str(path)
        return shutil.which(configured)
    for name in ("claude", "claude.cmd", "claude.exe"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _config_dirs() -> list[Path]:
    dirs = [Path.home() / ".claude"]
    custom = os.environ.get("CLAUDE_CONFIG_DIR")
    if custom:
        dirs.insert(0, Path(custom).expanduser())
    return dirs


def has_local_login() -> bool:
    """A Claude Code login here: ``~/.claude/.credentials.json`` or ``oauthAccount`` in ``~/.claude.json``."""
    if any((d / ".credentials.json").is_file() for d in _config_dirs()):
        return True
    state = Path.home() / ".claude.json"
    try:
        return state.is_file() and '"oauthAccount"' in state.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False


def _token(settings: Settings) -> str | None:
    secret = settings.anthropic_api_sub_key
    value = secret.get_secret_value().strip() if secret is not None else ""
    return value or None


def claude_code_status(settings: Settings) -> tuple[bool, str | None]:
    """(configured, reason): the CLI is found **and** (a token is set **or** a local login exists)."""
    if find_cli(settings) is None:
        return False, CLI_NOT_FOUND
    if _token(settings) is not None or has_local_login():
        return True, None
    return False, NO_LOGIN


def cli_command(path: str) -> list[str]:
    """argv prefix that runs the CLI.

    An npm ``claude.cmd`` shim would route every argument through ``cmd.exe``, which
    mangles quotes and newlines (``--json-schema``), so run the package's native
    ``bin/claude.exe`` (newer releases) or its ``cli.js`` with node (older ones) directly.
    """
    p = Path(path)
    if p.suffix.lower() in (".cmd", ".bat"):
        package = p.parent / "node_modules" / "@anthropic-ai" / "claude-code"
        native = package / "bin" / "claude.exe"
        if native.is_file():
            return [str(native)]
        script = package / "cli.js"
        if script.is_file():
            local_node = p.parent / "node.exe"
            node = str(local_node) if local_node.is_file() else shutil.which("node")
            if node:
                return [node, str(script)]
        logger.warning("claude_code: running %s through cmd.exe (no claude.exe or cli.js next to it)", p.name)
    return [path]


def child_env(token: str | None, base: Mapping[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    for key in list(env):
        upper = key.upper()
        if upper in _KEEP_ENV:
            continue
        if upper in _DROP_ENV or upper.startswith("CLAUDE_CODE_"):
            del env[key]
    env.update(_FORCED_ENV)
    if token:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    return env


# -- prompt rendering -----------------------------------------------------------


def _text(content: Any) -> str:
    return split_content(content)[0]


def _tool_calls_json(message: AIMessage) -> str:
    calls = [{"id": c.get("id"), "name": c["name"], "args": c.get("args") or {}} for c in message.tool_calls]
    return json.dumps(calls, ensure_ascii=False, sort_keys=True)


def render_transcript(messages: Sequence[BaseMessage]) -> str:
    """Non-system messages as one stdin prompt. A lone user message is sent as-is."""
    turns = [m for m in messages if not isinstance(m, SystemMessage)]
    if len(turns) == 1 and isinstance(turns[0], HumanMessage):
        return _text(turns[0].content)
    names: dict[str, str] = {}
    blocks: list[str] = []
    for m in turns:
        if isinstance(m, AIMessage):
            for call in m.tool_calls:
                if call.get("id"):
                    names[str(call["id"])] = call["name"]
            body = _text(m.content)
            if m.tool_calls:
                body = (body + "\n" if body else "") + f"tool_calls: {_tool_calls_json(m)}"
            blocks.append(f"[assistant]\n{body}")
        elif isinstance(m, ToolMessage):
            name = m.name or names.get(m.tool_call_id, "unknown")
            blocks.append(f"[tool_result name={name} id={m.tool_call_id}]\n{_text(m.content)}")
        else:
            blocks.append(f"[user]\n{_text(m.content)}")
    blocks.append(f"---\n{TRANSCRIPT_INSTRUCTION}")
    return "\n\n".join(blocks)


def _tool_specs(tools: Sequence[Any]) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for tool in tools:
        fn = convert_to_openai_tool(tool)["function"]
        params = dict(fn.get("parameters") or {"type": "object", "properties": {}})
        params.pop("title", None)
        specs.append({"name": fn["name"], "description": fn.get("description", ""), "parameters": params})
    return specs


def tools_section(tools: Sequence[Mapping[str, Any]]) -> str:
    lines = [
        "# Tools available",
        "You cannot run tools yourself. Reply with a JSON object "
        '{"text": string, "tool_calls": [{"name": string, "args": object}]}. To use tools, list '
        'them in "tool_calls" with arguments matching their schema; the harness runs them and '
        "sends the output back as [tool_result] blocks. When you give your final answer, put it "
        'in "text" and leave "tool_calls" empty.',
    ]
    for t in tools:
        lines.append(
            f"\n## {t['name']}\n{t.get('description') or ''}\n"
            f"Args JSON schema: {json.dumps(t['parameters'], ensure_ascii=False, sort_keys=True)}"
        )
    return "\n".join(lines)


def system_prompt(messages: Sequence[BaseMessage], tools: Sequence[Mapping[str, Any]] | None) -> str:
    parts = [_text(m.content) for m in messages if isinstance(m, SystemMessage)]
    text = "\n\n".join(p for p in parts if p.strip()) or DEFAULT_SYSTEM
    if tools:
        text = f"{text}\n\n{tools_section(tools)}"
    return text


# -- result parsing / errors ------------------------------------------------------


def _redact(text: str, token: str | None) -> str:
    return text.replace(token, "***") if token else text


def error_kind(text: str, subtype: str = "") -> ErrorKind:
    lower = f"{subtype} {text}".lower()
    if "budget" in lower:
        return "billing"
    if any(
        p in lower
        for p in (
            "invalid bearer token",
            "authentication",
            "login",
            "oauth",
            "unauthorized",
            "invalid api key",
        )
    ):
        return "auth"
    if any(p in lower for p in ("rate limit", "rate_limit", "usage limit", "too many requests", "429")):
        return "rate_limit"
    if "overloaded" in lower or "529" in lower:
        return "overloaded"
    if "credit balance" in lower:
        return "billing"
    return "server"


def _parse_stdout(stdout: str) -> dict[str, Any] | None:
    text = stdout.strip()
    if not text:
        return None
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        value = None
        for line in reversed(text.splitlines()):  # a stray log line before the result
            try:
                value = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
    return value if isinstance(value, dict) else None


def _int(value: Any) -> int:
    return int(value) if isinstance(value, int | float) else 0


def usage_metadata(result: Mapping[str, Any]) -> UsageMetadata:
    usage = result.get("usage") or {}
    cache_read = _int(usage.get("cache_read_input_tokens"))
    cache_write = _int(usage.get("cache_creation_input_tokens"))
    input_tokens = _int(usage.get("input_tokens")) + cache_read + cache_write
    output_tokens = _int(usage.get("output_tokens"))
    return UsageMetadata(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        input_token_details={"cache_read": cache_read, "cache_creation": cache_write},
    )


def _tool_calls(envelope: Mapping[str, Any], known: set[str], warnings: list[str]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    raw = envelope.get("tool_calls")
    for item in raw if isinstance(raw, list) else []:
        name = item.get("name") if isinstance(item, dict) else None
        args = item.get("args") if isinstance(item, dict) else None
        if not isinstance(name, str) or name not in known:
            warnings.append(f"dropped tool call to unknown tool {name!r}")
            continue
        if not isinstance(args, dict):
            warnings.append(f"dropped tool call {name!r}: args is not an object")
            continue
        calls.append({"id": f"call_{uuid4().hex[:12]}", "name": name, "args": args, "type": "tool_call"})
    return calls


# -- concurrency ------------------------------------------------------------------

_semaphores: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = (
    weakref.WeakKeyDictionary()
)


def _semaphore(limit: int) -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    sem = _semaphores.get(loop)
    if sem is None:
        sem = asyncio.Semaphore(max(1, limit))
        _semaphores[loop] = sem
    return sem


def _kill(proc: Any) -> None:
    """Kill the CLI (and, on Windows, its whole process tree)."""
    if sys.platform == "win32" and isinstance(proc, asyncio.subprocess.Process | subprocess.Popen):
        subprocess.run(  # fixed argv; the pid is our own child's
            ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    with contextlib.suppress(ProcessLookupError):
        proc.kill()


def _make_call_dir(root: Path, system_text: str) -> Path:
    """A fresh empty cwd per call (no CLAUDE.md around it) holding the system prompt file."""
    root.mkdir(parents=True, exist_ok=True)
    cwd = Path(tempfile.mkdtemp(prefix="call-", dir=root))
    (cwd / "system.md").write_text(system_text, encoding="utf-8")
    return cwd


# -- the chat model ---------------------------------------------------------------


class ClaudeCodeChatModel(BaseChatModel):
    """LangChain chat model where every call is one headless ``claude -p`` process."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    model: str
    effort: str | None = None
    timeout_s: float = 120.0
    max_budget_usd: float | None = None
    cli_path: str | None = None
    oauth_token: SecretStr | None = Field(default=None, exclude=True, repr=False)
    work_dir: str = ".data/claude_code"
    max_concurrency: int = 2

    @property
    def _llm_type(self) -> str:
        return "claude-code-cli"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {"model": self.model, "effort": self.effort}

    # -- binding ------------------------------------------------------------------

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, AIMessage]:
        return self.bind(cc_tools=_tool_specs(tools), **kwargs)

    def with_structured_output(
        self,
        schema: dict[str, Any] | type,
        *,
        include_raw: bool = False,
        method: str = "json_schema",
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, dict[str, Any] | BaseModel]:
        is_model = isinstance(schema, type) and issubclass(schema, BaseModel)
        if is_model:
            json_schema = schema.model_json_schema()  # type: ignore[union-attr]
        elif isinstance(schema, dict):
            json_schema = schema
        else:
            raise TypeError(f"unsupported structured output schema {schema!r}")

        def parse(message: AIMessage) -> dict[str, Any] | BaseModel:
            parsed: Any = None
            error: Exception | None = None
            obj = message.response_metadata.get("structured_output")
            if not isinstance(obj, dict):
                obj = extract_json_object(_text(message.content))
            if obj is None:
                error = ValueError("Claude Code returned no JSON object for the structured output")
            elif is_model:
                try:
                    parsed = schema.model_validate(obj)  # type: ignore[union-attr]
                except ValidationError as exc:
                    error = exc
            else:
                parsed = obj
            if include_raw:
                return {"raw": message, "parsed": parsed, "parsing_error": error}
            if error is not None:
                raise error
            return parsed  # type: ignore[no-any-return]

        return self.bind(cc_json_schema=json_schema) | RunnableLambda(parse)

    # -- generation ---------------------------------------------------------------

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self._agenerate(messages, stop, **kwargs))
        raise NotImplementedError("ClaudeCodeChatModel is async-only inside an event loop; use ainvoke")

    def _argv(self, cli: str, system_file: Path, json_schema: dict[str, Any] | None) -> list[str]:
        argv = [
            *cli_command(cli),
            "-p",
            "--output-format",
            "json",
            "--tools",
            "",
            "--no-session-persistence",
            "--disable-slash-commands",
            "--strict-mcp-config",
            "--setting-sources",
            "",
            "--permission-mode",
            "dontAsk",
            "--model",
            self.model,
        ]
        if self.effort:
            argv += ["--effort", self.effort]
        if self.max_budget_usd is not None and self.max_budget_usd > 0:
            argv += ["--max-budget-usd", f"{self.max_budget_usd:g}"]
        argv += ["--system-prompt-file", str(system_file)]
        if json_schema is not None:
            argv += ["--json-schema", json.dumps(json_schema, ensure_ascii=False, separators=(",", ":"))]
        return argv

    def _error(self, message: str, kind: ErrorKind) -> LLMError:
        token = self.oauth_token.get_secret_value() if self.oauth_token else None
        return LLMError(f"claude_code: {_redact(message, token)[:500]}", kind=kind, provider=PROVIDER_ID)

    async def _run_cli(self, argv: list[str], stdin: str, cwd: Path) -> tuple[int, str, str]:
        token = self.oauth_token.get_secret_value() if self.oauth_token else None
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(cwd),
                env=child_env(token),
            )
        except NotImplementedError:
            # Windows SelectorEventLoop (uvicorn --reload) cannot spawn subprocesses.
            return await self._run_cli_threaded(argv, stdin, cwd, child_env(token))
        except FileNotFoundError as exc:
            raise LLMNotConfigured(CLI_NOT_FOUND, provider=PROVIDER_ID) from exc
        try:
            out, err = await asyncio.wait_for(proc.communicate(stdin.encode("utf-8")), self.timeout_s)
        except TimeoutError as exc:
            _kill(proc)
            await proc.wait()
            raise self._error(f"timed out after {self.timeout_s:g}s", "timeout") from exc
        except BaseException:  # cancelled by the caller's own timeout / Stop
            if proc.returncode is None:
                _kill(proc)
                await proc.wait()
            raise
        return (
            proc.returncode if proc.returncode is not None else -1,
            out.decode("utf-8", errors="replace"),
            err.decode("utf-8", errors="replace"),
        )

    async def _run_cli_threaded(
        self, argv: list[str], stdin: str, cwd: Path, env: dict[str, str]
    ) -> tuple[int, str, str]:
        """Blocking ``Popen`` waited on in a worker thread, for loops without subprocess support."""
        try:
            proc = await asyncio.to_thread(
                subprocess.Popen,  # argv is built by _argv, never from user text
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(cwd),
                env=env,
            )
        except FileNotFoundError as exc:
            raise LLMNotConfigured(CLI_NOT_FOUND, provider=PROVIDER_ID) from exc
        try:
            out, err = await asyncio.to_thread(proc.communicate, stdin.encode("utf-8"), self.timeout_s)
        except subprocess.TimeoutExpired as exc:
            _kill(proc)
            await asyncio.to_thread(proc.wait)
            raise self._error(f"timed out after {self.timeout_s:g}s", "timeout") from exc
        except BaseException:  # cancelled by the caller's own timeout / Stop
            if proc.poll() is None:
                _kill(proc)
            raise
        return (
            proc.returncode,
            out.decode("utf-8", errors="replace"),
            err.decode("utf-8", errors="replace"),
        )

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        tools: list[dict[str, Any]] | None = kwargs.get("cc_tools") or None
        structured: dict[str, Any] | None = kwargs.get("cc_json_schema")
        json_schema = TOOL_ENVELOPE_SCHEMA if tools else structured
        cli = self.cli_path
        if not cli:
            raise LLMNotConfigured(CLI_NOT_FOUND, provider=PROVIDER_ID)

        cwd = _make_call_dir(Path(self.work_dir), system_prompt(messages, tools))
        try:
            argv = self._argv(cli, cwd / "system.md", json_schema)
            async with _semaphore(self.max_concurrency):
                code, stdout, stderr = await self._run_cli(argv, render_transcript(messages), cwd)
        finally:
            shutil.rmtree(cwd, ignore_errors=True)

        result = _parse_stdout(stdout)
        if result is None:
            detail = (stderr.strip() or stdout.strip() or f"exit code {code}")[-500:]
            raise self._error(detail, error_kind(detail) if code != 0 else "server")
        if result.get("is_error") or code != 0:
            subtype = str(result.get("subtype") or "")
            errors = result.get("errors")
            detail = " ".join(
                str(x) for x in (subtype, result.get("result") or "", errors or "", stderr.strip()) if x
            )
            raise self._error(detail or f"exit code {code}", error_kind(detail, subtype))
        return ChatResult(generations=[ChatGeneration(message=self._message(result, tools))])

    def _message(self, result: Mapping[str, Any], tools: list[dict[str, Any]] | None) -> AIMessage:
        text = str(result.get("result") or "")
        structured = result.get("structured_output")
        warnings: list[str] = []
        metadata: dict[str, Any] = {
            "model": self.model,
            "model_name": self.model,
            "total_cost_usd": result.get("total_cost_usd"),
            "duration_api_ms": result.get("duration_api_ms"),
            "session_id": result.get("session_id"),
            "stop_reason": result.get("stop_reason"),
        }
        tool_calls: list[dict[str, Any]] = []
        if tools:
            envelope = structured if isinstance(structured, dict) else extract_json_object(text)
            if envelope is None:
                warnings.append("reply was not the tool-call JSON envelope; treated as plain text")
            else:
                text = str(envelope.get("text") or "")
                tool_calls = _tool_calls(envelope, {t["name"] for t in tools}, warnings)
        elif isinstance(structured, dict):
            metadata["structured_output"] = structured
            if not text.strip():
                text = json.dumps(structured, ensure_ascii=False)
        if warnings:
            metadata["warnings"] = warnings
            logger.warning("claude_code: %s", "; ".join(warnings))
        return AIMessage(
            content=text,
            tool_calls=tool_calls,
            usage_metadata=usage_metadata(result),
            response_metadata=metadata,
        )
