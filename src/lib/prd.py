"""prd.py — PRD phase parser for cc-autopipe v1.0.

Refs: SPEC-v1.md §2.3.2

A v1.0 PRD may declare named phases via `### Phase N: <name>` headers:

    ### Phase 1: Foundation
    **Acceptance:** ...
    - [ ] Item 1.1
    - [x] Item 1.2

    ### Phase 2: API
    - [ ] Item 2.1

This module parses such PRDs into Phase records the orchestrator uses
to drive the phase-transition state machine. PRDs without `### Phase N:`
headers parse to an empty list — callers must fall back to the v0.5
"single-phase" semantics (the whole prd.md treated as one phase, with
verify.sh's prd_complete flag being the completion signal).

The parser is line-oriented and tolerates trailing whitespace, missing
colon, mixed case ("Phase" / "phase" / "PHASE"), and arbitrary text
between the header and the first `- [ ]` item.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# Matches "### Phase 12: Whatever" or "### Phase 12 Whatever" or "### phase 12".
# Non-greedy on title text so trailing whitespace is dropped naturally.
_PHASE_HEADER_RE = re.compile(r"^###\s+Phase\s+(\d+)\s*:?\s*(.*)$", re.IGNORECASE)
_ITEM_OPEN_RE = re.compile(r"^- \[ \]")
_ITEM_CHECKED_RE = re.compile(r"^- \[x\]", re.IGNORECASE)


@dataclass
class Phase:
    """One `### Phase N:` block, including everything up to the next phase header."""

    number: int
    name: str
    body: str = ""  # Full block text (header + acceptance + items + blank lines)
    items: list[str] = field(default_factory=list)

    @property
    def unchecked_count(self) -> int:
        return sum(1 for it in self.items if _ITEM_OPEN_RE.match(it))

    @property
    def checked_count(self) -> int:
        return sum(1 for it in self.items if _ITEM_CHECKED_RE.match(it))

    @property
    def total_items(self) -> int:
        return self.unchecked_count + self.checked_count

    @property
    def is_complete(self) -> bool:
        """A phase is complete when every item is checked AND there is at
        least one item — an empty phase is not silently passable."""
        return self.total_items > 0 and self.unchecked_count == 0


def parse_phases(prd_text: str) -> list[Phase]:
    """Parse a PRD body into ordered phase records.

    Returns an empty list when no `### Phase N:` headers are present —
    that signals "single-phase PRD" to callers. Phase numbers in the
    output preserve the order the headers appear in (which is normally
    1..N but the parser doesn't enforce strict numbering — gaps and
    duplicates are passed through so the operator notices in the
    next render).
    """
    phases: list[Phase] = []
    current: Phase | None = None
    current_lines: list[str] = []

    def _flush() -> None:
        nonlocal current, current_lines
        if current is None:
            return
        current.body = "".join(current_lines)
        for ln in current_lines:
            stripped = ln.rstrip("\n")
            if _ITEM_OPEN_RE.match(stripped) or _ITEM_CHECKED_RE.match(stripped):
                current.items.append(stripped)
        phases.append(current)
        current = None
        current_lines = []

    for raw_line in prd_text.splitlines(keepends=True):
        m = _PHASE_HEADER_RE.match(raw_line.rstrip("\n"))
        if m:
            _flush()
            current = Phase(number=int(m.group(1)), name=m.group(2).strip())
            current_lines = [raw_line]
        else:
            if current is not None:
                current_lines.append(raw_line)

    _flush()
    return phases


def has_phases(prd_text: str) -> bool:
    """Returns True iff the PRD declares at least one `### Phase N:` header."""
    return bool(parse_phases(prd_text))


def get_phase(prd_text: str, phase_number: int) -> Phase | None:
    """Return the Phase with the given number, or None if absent."""
    for p in parse_phases(prd_text):
        if p.number == phase_number:
            return p
    return None


def read_phases(prd_path: Path) -> list[Phase]:
    """Read prd.md from disk and parse. Returns [] on missing/unreadable."""
    try:
        text = prd_path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return []
    return parse_phases(text)
