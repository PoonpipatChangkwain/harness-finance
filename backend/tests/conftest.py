from __future__ import annotations

import pytest


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """``live`` tests hit real vendor APIs: run them only with ``-m live``."""
    if "live" in (config.getoption("-m") or ""):
        return
    skip = pytest.mark.skip(reason="live test; run with -m live")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _hide_local_claude_code(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit tests must not see this machine's Claude Code CLI / login (P16).

    Otherwise provider ``claude_code`` counts as configured on a developer machine and
    changes LLM auto-pick and the Settings catalog. Tests that need it patch ``find_cli``.
    """
    if request.node.get_closest_marker("live") is not None:
        return
    monkeypatch.setattr("app.harness.llm.claude_code.find_cli", lambda settings: None)
    monkeypatch.delenv("ANTHROPIC_API_SUB_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
