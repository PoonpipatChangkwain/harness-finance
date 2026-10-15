"""F2: the import-linter contracts in pyproject.toml hold (domain is framework-free, harness ↛ api)."""

from __future__ import annotations

from pathlib import Path

import pytest
from importlinter.cli import lint_imports

BACKEND = Path(__file__).resolve().parents[1]


def test_import_contracts_hold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(BACKEND)
    assert lint_imports(config_filename=str(BACKEND / "pyproject.toml")) == 0
