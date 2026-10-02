"""ToolRegistry: name → ToolSpec, filtered by which data capabilities are configured.

Ported from ``../dexter/src/tools/registry.ts``: a tool is only offered to
the LLM when what it depends on is available (Dexter checks env keys; we
check that some configured provider serves each required capability).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from typing import Any

from app.domain.data.base import Capability
from app.domain.tools.base import ToolSpec

CapabilityCheck = Callable[[Capability], bool]

_GENERIC_TOOL_NAMES = frozenset({"function", "functions", "tool", "tool_call", "call", "run"})


def data_capability_available(cap: Capability) -> bool:
    from app.domain.data.registry import get_router

    return any(cap in p.capabilities and p.is_configured() for p in get_router().providers().values())


class ToolRegistry:
    def __init__(
        self,
        specs: Iterable[ToolSpec] = (),
        *,
        capability_available: CapabilityCheck = data_capability_available,
    ) -> None:
        self._specs: dict[str, ToolSpec] = {}
        self._available = capability_available
        for spec in specs:
            self.register(spec)

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._specs:
            raise ValueError(f"tool {spec.name!r} registered twice")
        self._specs[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def resolve_name(self, name: str, args: dict[str, Any]) -> str:
        """Repair a malformed tool name from a weak model; unchanged when it can't be fixed safely.

        Handles ``functions.get_news`` style prefixes, and generic placeholders (``function``,
        ``tool`` …) by picking the one enabled tool whose args model accepts every arg key.
        """
        if name in self._specs:
            return name
        stripped = re.split(r"[.:/]", name.strip())[-1]
        if stripped in self._specs:
            return stripped
        if stripped.lower() in _GENERIC_TOOL_NAMES and args:
            keys = set(args)
            matches = [s.name for s in self.enabled() if keys <= set(s.args_model.model_fields)]
            if len(matches) == 1:
                return matches[0]
        return name

    def names(self) -> list[str]:
        return sorted(self._specs)

    def is_enabled(self, spec: ToolSpec) -> bool:
        return all(self._available(cap) for cap in spec.requires)

    def enabled(self) -> list[ToolSpec]:
        """Tools an LLM may be offered (``internal`` workflow tools are left out)."""
        return [s for s in self._specs.values() if self.is_enabled(s) and not s.internal]

    def for_node(self, names: Iterable[str]) -> list[ToolSpec]:
        """The enabled subset of ``names`` (unknown and internal names are ignored)."""
        out = []
        for name in names:
            spec = self._specs.get(name)
            if spec is not None and self.is_enabled(spec) and not spec.internal:
                out.append(spec)
        return out
