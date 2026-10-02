"""Citation markers in final answers (F3).

The final-answer prompt asks the LLM to put ``[^prov_xxx]`` right after a
number. ``resolve_citations`` turns those into numbered ``[n]`` markers
for the UI (same provenance id → same n) and returns the citation list
for ``answer.final``. Markers pointing at ids the run never produced are
dropped and reported, so the UI can't link to a record that doesn't exist.
"""

from __future__ import annotations

import re

from pydantic import BaseModel

_MARKER_RE = re.compile(r"\[\^(prov_[A-Za-z0-9]+)\]")


class CitationRef(BaseModel):
    marker: int
    provenance_id: str


class CitationResult(BaseModel):
    markdown: str
    citations: list[CitationRef]
    unknown_ids: list[str]


def resolve_citations(markdown: str, known_ids: set[str] | None = None) -> CitationResult:
    numbers: dict[str, int] = {}
    unknown: list[str] = []

    def replace(match: re.Match[str]) -> str:
        pid = match.group(1)
        if known_ids is not None and pid not in known_ids:
            if pid not in unknown:
                unknown.append(pid)
            return ""
        if pid not in numbers:
            numbers[pid] = len(numbers) + 1
        return f"[{numbers[pid]}]"

    text = _MARKER_RE.sub(replace, markdown)
    citations = [CitationRef(marker=n, provenance_id=pid) for pid, n in numbers.items()]
    return CitationResult(markdown=text, citations=citations, unknown_ids=unknown)


def strip_citation_markers(markdown: str) -> str:
    """Text with ``[^prov_…]`` and ``[n]`` markers removed (input to the number check)."""
    return re.sub(r"\[\d+\]", "", _MARKER_RE.sub("", markdown))
