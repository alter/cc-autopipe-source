"""Unit tests for src/lib/prd.py — phase parser (Stage J).

Covers SPEC-v1.md §2.3.2 acceptance:
- Recognises `### Phase N:` headers (case-insensitive, optional colon)
- Returns empty list for PRDs without phase headers (single-phase fallback)
- Counts unchecked / checked items per phase
- is_complete iff items > 0 AND no unchecked
- Body preserves the full block text (header → next header)
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src" / "lib"))

import prd  # noqa: E402


def test_no_phase_headers_returns_empty_list() -> None:
    text = """# PRD

Just a list of items, no phase headers.

- [ ] Item one
- [ ] Item two
"""
    assert prd.parse_phases(text) == []
    assert not prd.has_phases(text)


def test_single_phase_parsed() -> None:
    text = """# PRD

### Phase 1: Foundation

- [ ] Item 1.1
- [x] Item 1.2
"""
    phases = prd.parse_phases(text)
    assert len(phases) == 1
    p = phases[0]
    assert p.number == 1
    assert p.name == "Foundation"
    assert p.unchecked_count == 1
    assert p.checked_count == 1
    assert p.total_items == 2
    assert not p.is_complete


def test_multiple_phases_in_order() -> None:
    text = """# PRD

### Phase 1: Foundation
- [x] Item 1.1
- [x] Item 1.2

### Phase 2: API
- [ ] Item 2.1
- [ ] Item 2.2

### Phase 3: Frontend
- [ ] Item 3.1
"""
    phases = prd.parse_phases(text)
    assert [p.number for p in phases] == [1, 2, 3]
    assert [p.name for p in phases] == ["Foundation", "API", "Frontend"]
    assert phases[0].is_complete
    assert not phases[1].is_complete
    assert not phases[2].is_complete


def test_phase_complete_only_when_items_present_and_all_checked() -> None:
    """A phase with zero items must NOT be marked complete — that's an
    operator authoring bug, not a green signal."""
    text = """### Phase 1: Empty
**Acceptance:** TBD
"""
    p = prd.parse_phases(text)[0]
    assert p.total_items == 0
    assert not p.is_complete


def test_case_insensitive_phase_keyword() -> None:
    text = """### phase 1: lowercase
- [ ] x
### PHASE 2: SHOUTING
- [x] y
"""
    phases = prd.parse_phases(text)
    assert [p.number for p in phases] == [1, 2]


def test_missing_colon_after_phase_n() -> None:
    text = """### Phase 1 No Colon Title
- [ ] item
"""
    phases = prd.parse_phases(text)
    assert len(phases) == 1
    assert phases[0].name == "No Colon Title"


def test_acceptance_text_between_header_and_items_preserved_in_body() -> None:
    text = """### Phase 1: Foundation
**Acceptance:** All items checked AND verify >= 0.85.

Some narrative.

- [ ] Task one
- [ ] Task two
"""
    phases = prd.parse_phases(text)
    assert "Acceptance" in phases[0].body
    assert "narrative" in phases[0].body
    assert phases[0].unchecked_count == 2


def test_get_phase_by_number() -> None:
    text = """### Phase 1: A
- [ ] x
### Phase 2: B
- [x] y
### Phase 7: Skipped numbering
- [ ] z
"""
    p = prd.get_phase(text, 7)
    assert p is not None and p.name == "Skipped numbering"
    assert prd.get_phase(text, 99) is None


def test_read_phases_from_path(tmp_path: Path) -> None:
    f = tmp_path / "prd.md"
    f.write_text("### Phase 1: T\n- [ ] item\n")
    phases = prd.read_phases(f)
    assert len(phases) == 1


def test_read_phases_missing_file_returns_empty(tmp_path: Path) -> None:
    assert prd.read_phases(tmp_path / "nope.md") == []


def test_first_header_consumes_following_lines_until_next_header() -> None:
    """Lines BEFORE the first phase header are intentionally dropped —
    that block is presumably PRD intro / TOC text, not phase content."""
    text = """# PRD
Some intro that should NOT count toward phase 1.

### Phase 1: Real
- [ ] real item
"""
    phases = prd.parse_phases(text)
    assert len(phases) == 1
    assert "intro" not in phases[0].body
    assert phases[0].unchecked_count == 1
