"""Unit tests for NEUTRAL verdict recognition.

NEUTRAL is the Phase 3 DA-track / Phase 4 inconclusive-outcome marker
(no exploitable edge, no clear bug). It must be recognised by all three
keyword-bearing parse_verdict tiers.

v1.3.13 added NEUTRAL recognition across all tiers but mapped it to
CONDITIONAL. v1.5.5 CANONICAL-MAP-FIX promotes NEUTRAL to a distinct
fourth canonical verdict — these assertions now expect "NEUTRAL".

Refs: PROMPT_v1.3.13-hotfix.md GROUP NEUTRAL-VERDICT,
      PROMPT-v1.5.5.md GROUP CANONICAL-MAP-FIX.
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
    v1.5.5 CANONICAL_MAP NEUTRAL → NEUTRAL (identity)."""
    body = "**Status**: NEUTRAL\n"
    assert promotion.parse_verdict(_write(tmp_path, body)) == "NEUTRAL"


def test_neutral_verdict_heading_tier1(tmp_path: Path) -> None:
    """Verdict heading + NEUTRAL keyword in body — tier 1
    VERDICT_KEYWORD_RE captures NEUTRAL; v1.5.5 preserves identity."""
    body = (
        "## Verdict\n\n"
        "### NEUTRAL — DA information ceiling\n\n"
        "12 DA-track features produced no exploitable edge.\n"
    )
    assert promotion.parse_verdict(_write(tmp_path, body)) == "NEUTRAL"


def test_neutral_acceptance_tier3(tmp_path: Path) -> None:
    """Acceptance heading + 'neutral' prose — tier 3
    ACCEPTANCE_KEYWORD_RE group 3 matches `neutral`. v1.5.5: tier 3
    keyword-class collapses NEUTRAL/CONDITIONAL/PARTIAL into a single
    group-3 return value; that group's canonical result is CONDITIONAL
    (the group represents documentation-style 'partial-pass' acceptance
    where NEUTRAL and CONDITIONAL are not distinguished at the regex
    grouping level). NEUTRAL via the labelled metrics block or via
    `**Status**: NEUTRAL` (tier-4) does pin to canonical NEUTRAL."""
    body = (
        "## Acceptance\n\n"
        "Result is neutral — no edge found.\n"
    )
    assert promotion.parse_verdict(_write(tmp_path, body)) == "CONDITIONAL"


def test_neutral_canonical_map() -> None:
    """v1.5.5: CANONICAL_MAP["NEUTRAL"] is identity."""
    assert promotion.CANONICAL_MAP.get("NEUTRAL") == "NEUTRAL"
