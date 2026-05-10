"""Unit tests for v1.3.13 NEUTRAL verdict recognition.

NEUTRAL is the Phase 3 DA-track inconclusive-outcome marker (no
exploitable edge, no clear bug). It must be recognised by all three
keyword-bearing parse_verdict tiers and canonicalise to CONDITIONAL.

Refs: PROMPT_v1.3.13-hotfix.md GROUP NEUTRAL-VERDICT.
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


def test_neutral_bold_metadata_tier4(tmp_path: Path) -> None:
    """Bare `**Status**: NEUTRAL` — tier 4 bold-metadata path,
    CANONICAL_MAP NEUTRAL → CONDITIONAL."""
    body = "**Status**: NEUTRAL\n"
    assert promotion.parse_verdict(_write(tmp_path, body)) == "CONDITIONAL"


def test_neutral_verdict_heading_tier1(tmp_path: Path) -> None:
    """Verdict heading + NEUTRAL keyword in body — tier 1
    VERDICT_KEYWORD_RE captures NEUTRAL."""
    body = (
        "## Verdict\n\n"
        "### NEUTRAL — DA information ceiling\n\n"
        "12 DA-track features produced no exploitable edge.\n"
    )
    assert promotion.parse_verdict(_write(tmp_path, body)) == "CONDITIONAL"


def test_neutral_acceptance_tier3(tmp_path: Path) -> None:
    """Acceptance heading + 'neutral' prose — tier 3
    ACCEPTANCE_KEYWORD_RE group 3 matches `neutral`."""
    body = (
        "## Acceptance\n\n"
        "Result is neutral — no edge found.\n"
    )
    assert promotion.parse_verdict(_write(tmp_path, body)) == "CONDITIONAL"


def test_neutral_canonical_map() -> None:
    """Defensive: confirm CANONICAL_MAP contains NEUTRAL → CONDITIONAL."""
    assert promotion.CANONICAL_MAP.get("NEUTRAL") == "CONDITIONAL"
