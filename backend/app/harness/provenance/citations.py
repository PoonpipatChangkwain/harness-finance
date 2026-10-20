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
_ID = r"\^?\s*prov_[A-Za-z0-9]+"
# what models actually write: "[ ^prov_x ]", "[prov_x]", "[^prov_x, ^prov_y]", "(^prov_x)"
_LOOSE_MARKER_RE = re.compile(rf"[\[(]\s*{_ID}(?:\s*[,;]\s*{_ID})*\s*[\])]")
_ID_IN_MARKER_RE = re.compile(r"prov_[A-Za-z0-9]+")
_STRAY_ID_RE = re.compile(r"\s?\^?prov_[A-Za-z0-9]{10,}")


def normalize_markers(markdown: str) -> str:
    """Rewrite every loose provenance marker as canonical ``[^prov_x]`` (one per id)."""

    def canon(match: re.Match[str]) -> str:
        return "".join(f"[^{pid}]" for pid in _ID_IN_MARKER_RE.findall(match.group(0)))

    return _LOOSE_MARKER_RE.sub(canon, markdown)


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

    text = _MARKER_RE.sub(replace, normalize_markers(markdown))
    text = re.sub(r"(\[\d+\])(?:\1)+", r"\1", text)  # "[1][1]" when a number was cited twice
    text = _STRAY_ID_RE.sub("", text)  # a bare id outside any marker must never reach the user
    citations = [CitationRef(marker=n, provenance_id=pid) for pid, n in numbers.items()]
    return CitationResult(markdown=text, citations=citations, unknown_ids=unknown)


def strip_citation_markers(markdown: str) -> str:
    """Text with ``[^prov_…]`` and ``[n]`` markers removed (input to the number check)."""
    return re.sub(r"\[\d+\]", "", _MARKER_RE.sub("", markdown))
