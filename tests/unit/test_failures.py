"""Unit tests for src/lib/failures.py.

Covers SPEC-v1.2.md Bug H pseudocode: smart escalation depends on
the failure category mix, not just consecutive_failures count.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_LIB = REPO_ROOT / "src" / "lib"

sys.path.insert(0, str(SRC_LIB))

import failures  # noqa: E402


def _crash(score: float | None = None) -> dict:
    return {"ts": "2026-05-03T00:00:00Z", "error": "claude_subprocess_failed"}


def _verify(score: float = 0.0) -> dict:
    return {
        "ts": "2026-05-03T00:00:00Z",
        "error": "verify_failed",
        "details": {"score": score},
    }


def _malformed() -> dict:
    return {"ts": "2026-05-03T00:00:00Z", "error": "verify_malformed"}


def _other() -> dict:
    return {"ts": "2026-05-03T00:00:00Z", "error": "weird_engine_fault"}


# ---------------------------------------------------------------------------
# read_recent
# ---------------------------------------------------------------------------


def test_read_recent_missing_file_returns_empty(tmp_path: Path) -> None:
    assert failures.read_recent(tmp_path) == []


def test_read_recent_empty_file_returns_empty(tmp_path: Path) -> None:
    p = tmp_path / ".cc-autopipe" / "memory"
    p.mkdir(parents=True)
    (p / "failures.jsonl").write_text("")
    assert failures.read_recent(tmp_path) == []


def test_read_recent_returns_last_n(tmp_path: Path) -> None:
    p = tmp_path / ".cc-autopipe" / "memory"
    p.mkdir(parents=True)
    lines = [
        json.dumps({"error": "claude_subprocess_failed", "i": i}) for i in range(10)
    ]
    (p / "failures.jsonl").write_text("\n".join(lines))
    out = failures.read_recent(tmp_path, n=3)
    assert len(out) == 3
    assert [f["i"] for f in out] == [7, 8, 9]


def test_read_recent_skips_malformed_lines(tmp_path: Path) -> None:
    """Garbage lines must not abort the read or hide later good entries."""
    p = tmp_path / ".cc-autopipe" / "memory"
    p.mkdir(parents=True)
    (p / "failures.jsonl").write_text(
        "this is not json\n"
        '{"error":"verify_failed","i":1}\n'
        "{not json either\n"
        '{"error":"verify_failed","i":2}\n'
    )
    out = failures.read_recent(tmp_path, n=10)
    assert [f.get("i") for f in out] == [1, 2]


def test_read_recent_handles_blank_lines(tmp_path: Path) -> None:
    p = tmp_path / ".cc-autopipe" / "memory"
    p.mkdir(parents=True)
    (p / "failures.jsonl").write_text(
        '\n\n{"error":"verify_failed","i":1}\n\n{"error":"verify_failed","i":2}\n\n'
    )
    out = failures.read_recent(tmp_path, n=10)
    assert len(out) == 2


# ---------------------------------------------------------------------------
# categorize_recent — escalation path (3+ crashes)
# ---------------------------------------------------------------------------


def test_categorize_three_crashes_recommends_escalation() -> None:
    cat = failures.categorize_recent([_crash(), _crash(), _crash()])
    assert cat["recommend_escalation"] is True
    assert cat["recommend_human_needed"] is False
    assert cat["recommend_failed"] is False
    assert cat["crash_count"] == 3


def test_categorize_two_crashes_below_threshold_no_action() -> None:
    cat = failures.categorize_recent([_crash(), _crash()])
    assert cat["recommend_escalation"] is False
    assert cat["recommend_human_needed"] is False
    assert cat["recommend_failed"] is False


# ---------------------------------------------------------------------------
# categorize_recent — verify pattern (3+ verify_failed)
# ---------------------------------------------------------------------------


def test_categorize_three_verify_recommends_human_needed() -> None:
    cat = failures.categorize_recent([_verify(0.4), _verify(0.5), _verify(0.3)])
    assert cat["recommend_escalation"] is False, "verify pattern must NOT escalate"
    assert cat["recommend_human_needed"] is True
    assert cat["recommend_failed"] is False
    assert cat["verify_count"] == 3
    assert "structural" in cat["reason"]


def test_categorize_verify_malformed_counted_as_verify() -> None:
    cat = failures.categorize_recent([_malformed(), _malformed(), _malformed()])
    assert cat["recommend_human_needed"] is True
    assert cat["verify_count"] == 3


# ---------------------------------------------------------------------------
# categorize_recent — mixed (5+, neither dominant)
# ---------------------------------------------------------------------------


def test_categorize_mixed_5plus_recommends_failed() -> None:
    cat = failures.categorize_recent(
        [_verify(), _crash(), _verify(), _crash(), _other()]
    )
    assert cat["recommend_escalation"] is False
    assert cat["recommend_human_needed"] is False
    assert cat["recommend_failed"] is True
    assert cat["total"] == 5


def test_categorize_5_with_3_crashes_still_escalates() -> None:
    """Mixed pattern is the FALLBACK — if the 3-crash threshold is
    already met, escalate (don't fall through to mixed)."""
    cat = failures.categorize_recent(
        [_crash(), _crash(), _crash(), _verify(), _other()]
    )
    assert cat["recommend_escalation"] is True
    assert cat["recommend_failed"] is False  # fallback is exclusive


def test_categorize_5_with_3_verify_writes_human_needed() -> None:
    cat = failures.categorize_recent(
        [_verify(), _verify(), _verify(), _crash(), _other()]
    )
    assert cat["recommend_human_needed"] is True
    assert cat["recommend_failed"] is False


# ---------------------------------------------------------------------------
# categorize_recent — under thresholds (no action)
# ---------------------------------------------------------------------------


def test_categorize_empty_no_action() -> None:
    cat = failures.categorize_recent([])
    assert cat["total"] == 0
    assert cat["recommend_escalation"] is False
    assert cat["recommend_human_needed"] is False
    assert cat["recommend_failed"] is False


def test_categorize_two_of_each_no_action() -> None:
    cat = failures.categorize_recent([_crash(), _crash(), _verify(), _verify()])
    assert cat["recommend_escalation"] is False
    assert cat["recommend_human_needed"] is False
    assert cat["recommend_failed"] is False  # only 4 total


def test_categorize_unknown_error_counted_as_other() -> None:
    cat = failures.categorize_recent([_other(), _other()])
    assert cat["other_count"] == 2
    assert cat["crash_count"] == 0
    assert cat["verify_count"] == 0


def test_categorize_reason_describes_pattern() -> None:
    """Reason field must contain enough info for a log entry to be
    actionable on its own — counts + which category."""
    cat = failures.categorize_recent([_verify(), _verify(), _verify()])
    assert "verify_failed" in cat["reason"]
    assert "3" in cat["reason"]


# ---------------------------------------------------------------------------
# read_recent + categorize integration
# ---------------------------------------------------------------------------


def test_read_then_categorize_end_to_end(tmp_path: Path) -> None:
    """The two helpers compose cleanly — read produces what categorize
    expects."""
    p = tmp_path / ".cc-autopipe" / "memory"
    p.mkdir(parents=True)
    lines = [
        json.dumps(_verify(0.4)),
        json.dumps(_verify(0.3)),
        json.dumps(_verify(0.2)),
    ]
    (p / "failures.jsonl").write_text("\n".join(lines))
    recent = failures.read_recent(tmp_path, n=3)
    cat = failures.categorize_recent(recent)
    assert cat["recommend_human_needed"] is True
