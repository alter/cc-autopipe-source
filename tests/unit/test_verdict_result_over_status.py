"""Unit tests for v1.4.0 RESULT-OVER-STATUS Tier 4 two-pass split.

Phase 3 PROMOTION.md files commonly carry both `**Status**: PASS ✓`
(BIAS-audit sign-off) and `**Result:** REJECTED — ...` (the actual
verdict). The v1.3.9 single-pass `BOLD_METADATA_VERDICT_RE` returned
the FIRST match in the file scan, so `Status: PASS` (which appears
first) was misclassified as PROMOTED. v1.4.0 splits Tier 4 into two
passes: PRIMARY (Result / Verdict / Outcome / Decision / Conclusion)
wins; STATUS (Status only) is the fallback.

Refs: PROMPT_v1.4.0.md GROUP RESULT-OVER-STATUS.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_LIB = REPO_ROOT / "src" / "lib"

sys.path.insert(0, str(SRC_LIB))

import promotion  # noqa: E402


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "CAND_test_PROMOTION.md"
    p.write_text(body, encoding="utf-8")
    return p


def test_status_pass_then_result_rejected_returns_rejected(tmp_path: Path) -> None:
    """`**Status**: PASS ✓` (first) + `**Result:** REJECTED` (second).
    Without the two-pass split, Status was picked first and the file
    misclassified as PROMOTED. Primary pass must capture Result first."""
    body = (
        "# CAND_nn_liquid_nn_PROMOTION\n\n"
        "**Status**: PASS ✓\n"
        "**Result:** REJECTED — CfC AUC=0.78762 (ΔGBT=-0.06662, DM p=1.0000)\n"
    )
    assert promotion.parse_verdict(_write(tmp_path, body)) == "REJECTED"


def test_status_only_neutral_falls_back_to_status_pass(tmp_path: Path) -> None:
    """File with only `**Status**: NEUTRAL` (no Result/Verdict/Outcome
    field) — primary pass finds nothing, status fallback fires and
    canonicalises to NEUTRAL (v1.5.5 CANONICAL-MAP-FIX identity)."""
    body = "# Phase 3 DA report\n\n**Status**: NEUTRAL\n"
    assert promotion.parse_verdict(_write(tmp_path, body)) == "NEUTRAL"


def test_status_only_pass_falls_back_to_status_promoted(tmp_path: Path) -> None:
    """File with only `**Status**: PASS ✓` (no Result field) — primary
    pass finds nothing, status fallback maps PASS → PROMOTED. This is
    the v1.3.9 measurement-task path; if a project doesn't want it,
    they should use the labelled `## Metrics for leaderboard` block."""
    body = "# CAND_elo_rating_PROMOTION\n\n**Status**: PASS ✓\n"
    assert promotion.parse_verdict(_write(tmp_path, body)) == "PROMOTED"
