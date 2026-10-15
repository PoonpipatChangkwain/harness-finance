"""Provider ``claude_code`` (P16 §16.1): the CLI is never spawned here (fake subprocess)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import BaseModel, SecretStr

from app.harness.llm import claude_code as cc
from app.harness.llm.base import BaseLLMClient
from app.harness.llm.claude_code import TOOL_ENVELOPE_SCHEMA, ClaudeCodeChatModel, render_transcript
from app.harness.llm.config import (
    PROVIDERS,
    LLMTierConfig,
    canonical_provider,
    is_provider_configured,
    provider_status,
)
from app.harness.llm.errors import LLMError, LLMNotConfigured, LLMUnavailable, classify_error
from app.harness.llm.factory import create_client
from app.harness.llm.failover import FailoverLLM
from app.harness.llm.model_catalog import CATALOG, lookup
from app.harness.llm.usage import extract_usage
from app.harness.structured.invoke import invoke_structured
from app.settings import Settings
from tests.harness.llm.stubs import fake_client

FIXTURE = Path(__file__).parents[2] / "fixtures" / "llm" / "claude_code_result.json"
TOKEN = "sk-ant-oat01-SECRET-test-token-xyz"
REAL_FIND_CLI = cc.find_cli  # captured before the autouse fixture hides it
CLI = "/opt/claude/bin/claude"

GET_PRICE = {
    "type": "function",
    "function": {
        "name": "get_price",
        "description": "Latest price of a symbol",
        "parameters": {
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
        },
    },
}


def _result(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    data.update(overrides)
    return data


class FakeProc:
    def __init__(self, stdout: bytes, stderr: bytes = b"", code: int = 0, *, hang: bool = False) -> None:
        self.pid = 999_999
        self.returncode: int | None = None
        self._stdout, self._stderr, self._code, self._hang = stdout, stderr, code, hang
        self.stdin: bytes | None = None
        self.killed = False

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
        self.stdin = input
        if self._hang:
            await asyncio.sleep(3600)
        await asyncio.sleep(0)
        self.returncode = self._code
        return self._stdout, self._stderr

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode if self.returncode is not None else 0


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _exists(path: Path) -> bool:
    return path.exists()


class Spawner:
    """Stands in for ``asyncio.create_subprocess_exec``; records each call."""

    def __init__(self, *procs: FakeProc) -> None:
        self.procs = list(procs)
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, *argv: str, **kwargs: Any) -> FakeProc:
        system_file = Path(argv[argv.index("--system-prompt-file") + 1])
        proc = self.procs.pop(0) if len(self.procs) > 1 else self.procs[0]
        self.calls.append(
            {
                "argv": list(argv),
                "env": kwargs["env"],
                "cwd": kwargs["cwd"],
                "system": _read(system_file),
                "proc": proc,
            }
        )
        return proc


def _ok(data: dict[str, Any] | None = None) -> FakeProc:
    return FakeProc(json.dumps(data or _result()).encode())


@pytest.fixture
def spawn(monkeypatch: pytest.MonkeyPatch) -> Spawner:
    spawner = Spawner(_ok())
    monkeypatch.setattr(cc.asyncio, "create_subprocess_exec", spawner)
    return spawner


def _settings(tmp_path: Path, **kwargs: Any) -> Settings:
    base: dict[str, Any] = {"anthropic_api_key": None, "anthropic_api_sub_key": None, "data_dir": tmp_path}
    base.update(kwargs)
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]


def _model(tmp_path: Path, **kwargs: Any) -> ClaudeCodeChatModel:
    base: dict[str, Any] = {
        "model": "claude-haiku-5-5",
        "cli_path": CLI,
        "oauth_token": SecretStr(TOKEN),
        "work_dir": str(tmp_path / "claude_code"),
        "timeout_s": 5,
    }
    base.update(kwargs)
    return ClaudeCodeChatModel(**base)


def _client(tmp_path: Path, **kwargs: Any) -> BaseLLMClient:
    cfg = LLMTierConfig(provider="claude_code", model="claude-haiku-5-5")
    return BaseLLMClient(
        cfg, PROVIDERS["claude_code"], _model(tmp_path, **kwargs), lookup("claude-haiku-5-5")
    )


# -- registry / configuration ------------------------------------------------------


@pytest.mark.parametrize("alias", ["claude_code", "claude_sub", "claude-subscription", "claude_code_cli"])
def test_aliases(alias: str) -> None:
    assert canonical_provider(alias) == "claude_code"


@pytest.mark.parametrize(
    ("cli", "token", "expected"),
    [(True, True, True), (True, False, False), (False, True, False), (False, False, False)],
)
def test_is_provider_configured_cli_x_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, cli: bool, token: bool, expected: bool
) -> None:
    monkeypatch.setattr(cc, "find_cli", REAL_FIND_CLI)
    monkeypatch.setattr(cc.shutil, "which", lambda name: CLI if cli else None)
    monkeypatch.setattr(cc, "has_local_login", lambda: False)
    s = _settings(tmp_path, anthropic_api_sub_key=SecretStr(TOKEN) if token else None)
    assert is_provider_configured("claude_code", s) is expected
    configured, reason = provider_status("claude_code", s)
    assert configured is expected
    if not cli:
        assert reason is not None and "CLI not found" in reason
    elif not token:
        assert reason is not None and "setup-token" in reason
    assert reason is None or TOKEN not in reason


def test_local_login_counts_without_token(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cc, "find_cli", lambda settings: CLI)
    monkeypatch.setattr(cc, "has_local_login", lambda: True)
    assert is_provider_configured("claude_code", _settings(tmp_path))


def test_has_local_login_reads_claude_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr(cc.Path, "home", lambda: tmp_path)
    assert not cc.has_local_login()
    (tmp_path / ".claude.json").write_text('{"oauthAccount": {"emailAddress": "x"}}', encoding="utf-8")
    assert cc.has_local_login()
    (tmp_path / ".claude.json").unlink()
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / ".credentials.json").write_text("{}", encoding="utf-8")
    assert cc.has_local_login()


def test_find_cli_tries_windows_names_and_explicit_bin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cc, "find_cli", REAL_FIND_CLI)
    seen: list[str] = []

    def which(name: str) -> str | None:
        seen.append(name)
        return r"C:\npm\claude.cmd" if name == "claude.cmd" else None

    monkeypatch.setattr(cc.shutil, "which", which)
    assert cc.find_cli(_settings(tmp_path)) == r"C:\npm\claude.cmd"
    assert seen == ["claude", "claude.cmd"]
    binary = tmp_path / "claude.exe"
    binary.write_text("", encoding="utf-8")
    assert cc.find_cli(_settings(tmp_path, claude_code_bin=str(binary))) == str(binary)


def test_cmd_shim_runs_cli_js_with_node(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    shim = tmp_path / "claude.cmd"
    shim.write_text("@echo off", encoding="utf-8")
    script = tmp_path / "node_modules" / "@anthropic-ai" / "claude-code" / "cli.js"
    script.parent.mkdir(parents=True)
    script.write_text("", encoding="utf-8")
    monkeypatch.setattr(cc.shutil, "which", lambda name: "/usr/bin/node" if name == "node" else None)
    assert cc.cli_command(str(shim)) == ["/usr/bin/node", str(script)]
    assert cc.cli_command("/usr/local/bin/claude") == ["/usr/local/bin/claude"]


def test_cmd_shim_prefers_native_exe(tmp_path: Path) -> None:
    shim = tmp_path / "claude.cmd"
    shim.write_text("@echo off", encoding="utf-8")
    package = tmp_path / "node_modules" / "@anthropic-ai" / "claude-code"
    native = package / "bin" / "claude.exe"
    native.parent.mkdir(parents=True)
    native.write_bytes(b"")
    (package / "cli.js").write_text("", encoding="utf-8")
    assert cc.cli_command(str(shim)) == [str(native)]


def test_factory_builds_client_when_cli_found(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cc, "find_cli", lambda settings: CLI)
    s = _settings(tmp_path, anthropic_api_sub_key=SecretStr(TOKEN))
    client = create_client(LLMTierConfig(provider="claude_sub", model="opus", temperature=0.5), s)
    assert isinstance(client.chat_model, ClaudeCodeChatModel)
    assert client.chat_model.cli_path == CLI
    assert client.chat_model.max_budget_usd == s.run_max_cost_usd
    assert client.info == CATALOG["claude-opus-5-5"]  # alias priced as the latest Opus
    assert TOKEN not in repr(client.chat_model)


def test_factory_uses_claude_code_timeout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cc, "find_cli", lambda settings: CLI)
    s = _settings(tmp_path, anthropic_api_sub_key=SecretStr(TOKEN), claude_code_timeout_s=240.0)
    client = create_client(LLMTierConfig(provider="claude_code", model="opus", timeout_s=120.0), s)
    assert client.cfg.timeout_s == 240.0  # outer wait_for in BaseLLMClient
    assert isinstance(client.chat_model, ClaudeCodeChatModel)
    assert client.chat_model.timeout_s == 240.0  # the CLI process itself


def test_factory_not_configured_without_cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cc, "find_cli", REAL_FIND_CLI)
    monkeypatch.setattr(cc.shutil, "which", lambda name: None)
    with pytest.raises(LLMNotConfigured, match="CLI not found"):
        create_client(
            LLMTierConfig(provider="claude_code", model="claude-haiku-5-5"),
            _settings(tmp_path, anthropic_api_sub_key=SecretStr(TOKEN)),
        )


def test_catalog_models_and_aliases() -> None:
    assert lookup("claude-sonnet-5-5").input_per_m == 2.0
    assert lookup("claude-sonnet-5-5").cache_read_per_m == 0.20
    assert lookup("claude-haiku-5-5").output_per_m == 0.50
    assert lookup("sonnet") == CATALOG["claude-sonnet-5-5"]
    assert lookup("haiku") == CATALOG["claude-haiku-5-5"]
    assert lookup("Opus") == CATALOG["claude-opus-5-5"]


def test_prepare_leaves_system_prompt_plain(tmp_path: Path) -> None:
    msgs = _client(tmp_path).prepare([SystemMessage(content="sys"), HumanMessage(content="hi")])
    assert msgs[0].content == "sys"


# -- one call ----------------------------------------------------------------------


async def test_argv_env_and_stdin(spawn: Spawner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api-should-not-leak")
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "parent-session")
    model = _model(tmp_path, effort="low", max_budget_usd=1.0)
    msg = await model.ainvoke([SystemMessage(content="You are terse."), HumanMessage(content="ตอบว่า pong")])

    assert msg.content == "pong"
    call = spawn.calls[0]
    argv: list[str] = call["argv"]
    assert argv[0] == CLI and "-p" in argv
    assert argv[argv.index("--model") + 1] == "claude-haiku-5-5"
    assert argv[argv.index("--tools") + 1] == ""
    assert argv[argv.index("--output-format") + 1] == "json"
    assert argv[argv.index("--effort") + 1] == "low"
    assert argv[argv.index("--max-budget-usd") + 1] == "1"
    assert argv[argv.index("--setting-sources") + 1] == ""
    assert "--no-session-persistence" in argv and "--strict-mcp-config" in argv
    assert "--bare" not in argv and "--json-schema" not in argv
    assert call["system"] == "You are terse."
    assert call["proc"].stdin == "ตอบว่า pong".encode()
    assert not any(TOKEN in a or "pong" in a for a in argv)  # prompt + token never in the process list

    env: dict[str, str] = call["env"]
    assert "ANTHROPIC_API_KEY" not in env and "CLAUDECODE" not in env
    assert "CLAUDE_CODE_SESSION_ID" not in env
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == TOKEN
    assert env["CLAUDE_CODE_DISABLE_CLAUDE_MDS"] == "1"

    cwd = Path(call["cwd"])
    assert cwd.parent == tmp_path / "claude_code"
    assert not _exists(cwd)  # per-call temp dir is cleaned up


async def test_no_token_relies_on_local_login(spawn: Spawner, tmp_path: Path) -> None:
    await _model(tmp_path, oauth_token=None).ainvoke([HumanMessage(content="hi")])
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in spawn.calls[0]["env"]
    assert spawn.calls[0]["system"] == cc.DEFAULT_SYSTEM


async def test_plain_text_usage_matches_fixture(spawn: Spawner, tmp_path: Path) -> None:
    client = _client(tmp_path)
    msg = await client.ainvoke([HumanMessage(content="Reply with exactly: pong")])
    assert isinstance(msg, AIMessage) and msg.content == "pong" and msg.tool_calls == []
    usage = extract_usage(msg, client.info)
    assert (usage.input_tokens, usage.output_tokens) == (4 + 29575, 4)
    assert (usage.cache_read_tokens, usage.cache_write_tokens) == (0, 29575)
    assert usage.cost_usd > 0  # priced from the catalog so the budget guard still works
    meta = msg.response_metadata
    assert meta["total_cost_usd"] == pytest.approx(0.11097825)
    assert meta["model"] == "claude-haiku-5-5" and meta["stop_reason"] == "end_turn"
    assert meta["duration_api_ms"] == 2327
    assert TOKEN not in json.dumps(meta)


async def test_astream_yields_one_chunk(spawn: Spawner, tmp_path: Path) -> None:
    chunks = [c async for c in _client(tmp_path).astream([HumanMessage(content="hi")])]
    assert len(chunks) == 1 and chunks[0].content == "pong"


def test_sync_invoke_outside_event_loop(spawn: Spawner, tmp_path: Path) -> None:
    assert _model(tmp_path).invoke([HumanMessage(content="hi")]).content == "pong"


def test_transcript_is_labelled_and_deterministic() -> None:
    msgs = [
        SystemMessage(content="sys"),
        HumanMessage(content="price of PTT?"),
        AIMessage(
            content="checking",
            tool_calls=[
                {"id": "call_1", "name": "get_price", "args": {"symbol": "PTT.BK"}, "type": "tool_call"}
            ],
        ),
        ToolMessage(content='{"price": 33.5}', tool_call_id="call_1"),
    ]
    text = render_transcript(msgs)
    assert text == render_transcript(msgs)
    assert "sys" not in text
    assert text.index("[user]\nprice of PTT?") < text.index("[assistant]\nchecking\ntool_calls: ")
    assert '"name": "get_price"' in text
    assert '[tool_result name=get_price id=call_1]\n{"price": 33.5}' in text
    assert text.endswith(cc.TRANSCRIPT_INSTRUCTION)


# -- tools / structured --------------------------------------------------------------


async def test_bind_tools_envelope_and_unknown_tools_dropped(
    spawn: Spawner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    envelope = {
        "text": "checking the price",
        "tool_calls": [
            {"name": "get_price", "args": {"symbol": "PTT.BK"}},
            {"name": "rm_rf", "args": {}},
            {"name": "get_price", "args": "PTT"},
        ],
    }
    spawn.procs = [_ok(_result(result="", structured_output=envelope))]
    msg = await _client(tmp_path).ainvoke([HumanMessage(content="price of PTT?")], tools=[GET_PRICE])

    call = spawn.calls[0]
    assert json.loads(call["argv"][call["argv"].index("--json-schema") + 1]) == TOOL_ENVELOPE_SCHEMA
    assert "# Tools available" in call["system"] and "## get_price" in call["system"]
    assert '"symbol"' in call["system"]
    assert msg.content == "checking the price"
    assert [(c["name"], c["args"]) for c in msg.tool_calls] == [("get_price", {"symbol": "PTT.BK"})]
    assert msg.tool_calls[0]["id"].startswith("call_")
    assert len(msg.response_metadata["warnings"]) == 2


async def test_bind_tools_envelope_parsed_from_result_text(spawn: Spawner, tmp_path: Path) -> None:
    text = 'ok ```json\n{"text": "", "tool_calls": [{"name": "get_price", "args": {"symbol": "AAPL"}}]}\n```'
    spawn.procs = [_ok(_result(result=text))]
    msg = await _client(tmp_path).ainvoke([HumanMessage(content="AAPL?")], tools=[GET_PRICE])
    assert msg.tool_calls[0]["args"] == {"symbol": "AAPL"}


async def test_bind_tools_plain_reply_kept_as_text(spawn: Spawner, tmp_path: Path) -> None:
    spawn.procs = [_ok(_result(result="final answer, no JSON"))]
    msg = await _client(tmp_path).ainvoke([HumanMessage(content="hi")], tools=[GET_PRICE])
    assert msg.content == "final answer, no JSON" and msg.tool_calls == []
    assert msg.response_metadata["warnings"]


class Count(BaseModel):
    n: int


async def test_structured_native(spawn: Spawner, tmp_path: Path) -> None:
    spawn.procs = [_ok(_result(result="", structured_output={"n": 3}))]
    result = await invoke_structured(_client(tmp_path), [HumanMessage(content="count")], Count)
    assert result.mode == "native" and result.value == Count(n=3)
    assert result.usage.input_tokens == 29579
    argv = spawn.calls[0]["argv"]
    assert json.loads(argv[argv.index("--json-schema") + 1]) == Count.model_json_schema()


async def test_structured_falls_back_to_result_text(spawn: Spawner, tmp_path: Path) -> None:
    spawn.procs = [_ok(_result(result='Here: ```json\n{"n": 5}\n```'))]
    raw = await _model(tmp_path).with_structured_output(Count, include_raw=True).ainvoke("count")
    assert isinstance(raw, dict)
    assert raw["parsed"] == Count(n=5) and raw["parsing_error"] is None
    assert isinstance(raw["raw"], AIMessage)


async def test_structured_parse_error_is_reported(spawn: Spawner, tmp_path: Path) -> None:
    spawn.procs = [_ok(_result(result="no json at all"))]
    raw = await _model(tmp_path).with_structured_output(Count, include_raw=True).ainvoke("count")
    assert isinstance(raw, dict) and raw["parsed"] is None and raw["parsing_error"] is not None


# -- errors ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("subtype", "text", "kind"),
    [
        ("success", f"Invalid bearer token {TOKEN} · Please run /login", "auth"),
        ("success", "Claude AI usage limit reached|1760000000", "rate_limit"),
        ("success", 'API Error: 529 {"type":"overloaded_error"}', "overloaded"),
        ("error_max_budget_usd", "", "billing"),
        ("error_during_execution", "something unexpected", "server"),
    ],
)
async def test_is_error_maps_to_kind(
    spawn: Spawner, tmp_path: Path, subtype: str, text: str, kind: str
) -> None:
    spawn.procs = [
        FakeProc(json.dumps(_result(is_error=True, subtype=subtype, result=text)).encode(), code=1)
    ]
    with pytest.raises(LLMError) as info:
        await _model(tmp_path).ainvoke([HumanMessage(content="hi")])
    assert classify_error(info.value) == kind
    assert info.value.provider == "claude_code"
    assert TOKEN not in str(info.value)


async def test_crash_without_json_reads_stderr(spawn: Spawner, tmp_path: Path) -> None:
    spawn.procs = [FakeProc(b"", stderr=f"Error: Invalid API key {TOKEN}".encode(), code=1)]
    with pytest.raises(LLMError) as info:
        await _model(tmp_path).ainvoke([HumanMessage(content="hi")])
    assert classify_error(info.value) == "auth"
    assert TOKEN not in str(info.value)


async def test_timeout_kills_process(spawn: Spawner, tmp_path: Path) -> None:
    proc = FakeProc(b"", hang=True)
    spawn.procs = [proc]
    with pytest.raises(LLMError) as info:
        await _model(tmp_path, timeout_s=0.05).ainvoke([HumanMessage(content="hi")])
    assert classify_error(info.value) == "timeout"
    assert proc.killed


async def test_cancel_kills_process(spawn: Spawner, tmp_path: Path) -> None:
    proc = FakeProc(b"", hang=True)
    spawn.procs = [proc]
    task = asyncio.create_task(_model(tmp_path).ainvoke([HumanMessage(content="hi")]))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert proc.killed


async def test_missing_binary_is_not_configured(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    async def missing(*argv: str, **kwargs: Any) -> FakeProc:
        raise FileNotFoundError(argv[0])

    monkeypatch.setattr(cc.asyncio, "create_subprocess_exec", missing)
    with pytest.raises(LLMNotConfigured):
        await _model(tmp_path).ainvoke([HumanMessage(content="hi")])


class FakePopen:
    """Stands in for ``subprocess.Popen`` (the thread fallback)."""

    def __init__(self, argv: list[str], **kwargs: Any) -> None:
        self.argv, self.kwargs = argv, kwargs
        self.pid = 999_999
        self.returncode: int | None = None
        self.stdin: bytes | None = None

    def communicate(self, input: bytes | None = None, timeout: float | None = None) -> tuple[bytes, bytes]:
        self.stdin = input
        self.returncode = 0
        return json.dumps(_result()).encode(), b""

    def poll(self) -> int | None:
        return self.returncode


async def test_loop_without_subprocess_support_falls_back_to_thread(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def unsupported(*argv: str, **kwargs: Any) -> FakeProc:
        raise NotImplementedError  # Windows SelectorEventLoop (uvicorn --reload)

    spawned: list[FakePopen] = []

    def popen(argv: list[str], **kwargs: Any) -> FakePopen:
        spawned.append(FakePopen(argv, **kwargs))
        return spawned[-1]

    monkeypatch.setattr(cc.asyncio, "create_subprocess_exec", unsupported)
    monkeypatch.setattr(cc.subprocess, "Popen", popen)
    reply = await _model(tmp_path).ainvoke([HumanMessage(content="hi")])
    assert reply.content
    assert spawned[0].stdin == b"hi"
    assert spawned[0].kwargs["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == TOKEN


async def test_concurrency_is_capped(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    running = 0
    peak = 0

    class SlowProc(FakeProc):
        async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0.02)
            running -= 1
            self.returncode = 0
            return json.dumps(_result()).encode(), b""

    async def spawn_slow(*argv: str, **kwargs: Any) -> FakeProc:
        return SlowProc(b"")

    monkeypatch.setattr(cc.asyncio, "create_subprocess_exec", spawn_slow)
    model = _model(tmp_path, max_concurrency=1)
    monkeypatch.setattr(cc, "_semaphores", type(cc._semaphores)())
    await asyncio.gather(*(model.ainvoke([HumanMessage(content=str(i))]) for i in range(3)))
    assert peak == 1


async def _no_sleep(seconds: float) -> None:
    return None


async def test_failover_claude_code_to_groq(spawn: Spawner, tmp_path: Path) -> None:
    spawn.procs = [
        FakeProc(json.dumps(_result(is_error=True, result="usage limit reached")).encode(), code=1)
    ]
    warnings: list[tuple[str, str]] = []
    configs = [
        LLMTierConfig(provider="claude_code", model="claude-haiku-5-5"),
        LLMTierConfig(provider="groq", model="openai/gpt-oss-120b"),
    ]

    def factory(cfg: LLMTierConfig) -> BaseLLMClient:
        if cfg.provider == "claude_code":
            return _client(tmp_path)
        return fake_client([AIMessage(content="from groq")], provider="groq", model=cfg.model)

    chain = FailoverLLM(
        configs, factory=factory, on_warning=lambda c, m: warnings.append((c, m)), sleep=_no_sleep
    )

    async def op(client: BaseLLMClient) -> AIMessage:
        return await client.ainvoke([HumanMessage(content="hi")])

    msg = await chain.run(op)
    assert msg.content == "from groq"
    assert len(spawn.calls) == 3  # rate limit is retried on claude_code before switching
    assert warnings and warnings[0][0] == "llm_failover" and "rate_limit" in warnings[0][1]


async def test_failover_exhausted_raises_unavailable(spawn: Spawner, tmp_path: Path) -> None:
    spawn.procs = [
        FakeProc(json.dumps(_result(is_error=True, result="Invalid bearer token")).encode(), code=1)
    ]
    chain = FailoverLLM(
        [LLMTierConfig(provider="claude_code", model="claude-haiku-5-5")],
        factory=lambda cfg: _client(tmp_path),
        sleep=_no_sleep,
    )

    async def op(client: BaseLLMClient) -> AIMessage:
        return await client.ainvoke([HumanMessage(content="hi")])

    with pytest.raises(LLMUnavailable) as info:
        await chain.run(op)
    assert info.value.kind == "auth"
    assert len(spawn.calls) == 1  # auth is not retried
