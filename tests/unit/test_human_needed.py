"""Unit tests for src/lib/human_needed.py."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_LIB = REPO_ROOT / "src" / "lib"

sys.path.insert(0, str(SRC_LIB))

import human_needed  # noqa: E402


def _project(tmp_path: Path) -> Path:
    p = tmp_path / "demo"
    (p / ".cc-autopipe").mkdir(parents=True)
    return p


# ---------------------------------------------------------------------------
# write
# ---------------------------------------------------------------------------


def test_write_creates_file_with_title_and_body(tmp_path: Path) -> None:
    p = _project(tmp_path)
    human_needed.write(p, "Test", "Body content")
    text = (p / ".cc-autopipe" / "HUMAN_NEEDED.md").read_text(encoding="utf-8")
    assert text.startswith("# Test\n")
    assert "Body content" in text


def test_write_overwrites_existing(tmp_path: Path) -> None:
    p = _project(tmp_path)
    human_needed.write(p, "First", "old body")
    human_needed.write(p, "Second", "new body")
    text = (p / ".cc-autopipe" / "HUMAN_NEEDED.md").read_text(encoding="utf-8")
    assert "old body" not in text
    assert "new body" in text


def test_write_atomic_no_tmp_leftover(tmp_path: Path) -> None:
    p = _project(tmp_path)
    human_needed.write(p, "T", "B")
    leftovers = list((p / ".cc-autopipe").glob("*.tmp"))
    assert leftovers == []


def test_write_creates_cca_dir_if_missing(tmp_path: Path) -> None:
    """Defensive: write() should mkdir parents on its own — useful
    when invoked against a fresh tmp project layout."""
    p = tmp_path / "fresh"
    p.mkdir()
    human_needed.write(p, "T", "B")
    assert (p / ".cc-autopipe" / "HUMAN_NEEDED.md").exists()


def test_write_swallows_oserror(tmp_path: Path) -> None:
    """Hook-helper contract: never raise. Pass a path that can't be
    created (a file where a dir should be) and assert no exception."""
    blocked = tmp_path / "blocked"
    blocked.write_text("im a file")  # not a directory
    # write tries blocked / .cc-autopipe / HUMAN_NEEDED.md — mkdir fails.
    human_needed.write(blocked, "T", "B")  # must not raise


# ---------------------------------------------------------------------------
# write_verify_pattern
# ---------------------------------------------------------------------------


def test_write_verify_pattern_message_distinct(tmp_path: Path) -> None:
    """Bug H requires the verify-pattern message to explicitly tell
    the operator escalation was skipped on purpose, otherwise they
    might manually escalate and burn opus quota."""
    p = _project(tmp_path)
    recent = [
        {
            "ts": "2026-05-03T10:00:00Z",
            "error": "verify_failed",
            "details": {"score": 0.4},
        },
        {
            "ts": "2026-05-03T10:10:00Z",
            "error": "verify_failed",
            "details": {"score": 0.5},
        },
        {
            "ts": "2026-05-03T10:20:00Z",
            "error": "verify_failed",
            "details": {"score": 0.3},
        },
    ]
    human_needed.write_verify_pattern(p, recent)
    text = (p / ".cc-autopipe" / "HUMAN_NEEDED.md").read_text(encoding="utf-8")
    assert "verify pattern" in text.lower()
    assert "did NOT auto-escalate" in text
    assert "verify.sh expectations" in text
    assert "in_progress: true" in text  # Bug B cross-reference
    # Last-3 timestamps surface so the operator can correlate with logs.
    assert "2026-05-03T10:00:00Z" in text
    assert "2026-05-03T10:20:00Z" in text
    # Score values rendered.
    assert "0.4" in text
    assert "0.3" in text


def test_write_verify_pattern_with_empty_recent(tmp_path: Path) -> None:
    """If categorize_recent saw an empty-but-still-recommend pattern,
    we shouldn't crash. write_verify_pattern handles []."""
    p = _project(tmp_path)
    human_needed.write_verify_pattern(p, [])
    text = (p / ".cc-autopipe" / "HUMAN_NEEDED.md").read_text(encoding="utf-8")
    assert "(none)" in text


# ---------------------------------------------------------------------------
# write_mixed_pattern
# ---------------------------------------------------------------------------


def test_write_mixed_pattern_message(tmp_path: Path) -> None:
    p = _project(tmp_path)
    human_needed.write_mixed_pattern(p, total=5)
    text = (p / ".cc-autopipe" / "HUMAN_NEEDED.md").read_text(encoding="utf-8")
    assert "mixed pattern" in text.lower()
    assert "5 consecutive failures" in text
    assert "FAILED" in text
    assert "failures.jsonl" in text


# ---------------------------------------------------------------------------
# All three message variants are visually distinct
# ---------------------------------------------------------------------------


def test_three_variants_have_distinct_titles(tmp_path: Path) -> None:
    """Operator should be able to glance at the file's first heading
    and immediately know which pattern fired."""
    titles_seen = set()
    for variant in ("write", "write_verify_pattern", "write_mixed_pattern"):
        p = tmp_path / variant
        (p / ".cc-autopipe").mkdir(parents=True)
        if variant == "write":
            human_needed.write(p, "Custom title", "body")
        elif variant == "write_verify_pattern":
            human_needed.write_verify_pattern(p, [])
        else:
            human_needed.write_mixed_pattern(p, total=5)
        first_line = (
            (p / ".cc-autopipe" / "HUMAN_NEEDED.md").read_text().splitlines()[0]
        )
        titles_seen.add(first_line)
    assert len(titles_seen) == 3
