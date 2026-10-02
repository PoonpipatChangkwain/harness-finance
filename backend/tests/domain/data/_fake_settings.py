"""Shared SecretStr-shaped test double for monkeypatching provider `get_settings`."""

from __future__ import annotations

from typing import Any


class FakeSecret:
    def __init__(self, value: str) -> None:
        self._value = value

    def get_secret_value(self) -> str:
        return self._value


def make_settings(**overrides: str | None) -> Any:
    class _FakeSettings:
        def __init__(self) -> None:
            for key, value in overrides.items():
                setattr(self, key, FakeSecret(value) if value else None)

        def __getattr__(self, name: str) -> None:
            return None

    return _FakeSettings()
