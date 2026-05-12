"""v1.5.5 CANONICAL-MAP-FIX: NEUTRAL is a distinct fourth canonical
verdict, not silently folded into CONDITIONAL.

Pre-v1.5.5 CANONICAL_MAP collapsed NEUTRAL → CONDITIONAL. AI-trade
production 2026-05-12 surfaced 108 of 520 PROMOTION files where the
file body said NEUTRAL but the parsed verdict landed as CONDITIONAL,
corrupting composite scoring and ELO computation for those entries.

These tests pin:
1. Each of the four canonical PRD verdicts is identity-mapped.
2. Historical aliases preserve their canonical target.
3. NEUTRAL aliases (NO_IMPROVEMENT, INCONCLUSIVE, ...) canonicalise to
   NEUTRAL — NOT to CONDITIONAL.
4. Unknown verdict keywords return None (caller falls through to the
   parse_verdict cascade); the labelled-block path also surfaces the
   raw unmapped value as `_unmapped_verdict` for operator visibility.

Refs: PROMPT-v1.5.5.md GROUP CANONICAL-MAP-FIX.
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


def test_neutral_labelled_block_stays_neutral(tmp_path: Path) -> None:
    """`## Metrics for leaderboard` block `**verdict**: NEUTRAL` →
    parsed verdict is NEUTRAL. This is the load-bearing regression
    that motivated v1.5.5 — pre-fix produced CONDITIONAL."""
    body = (
        "## Metrics for leaderboard\n"
        "- **verdict**: NEUTRAL\n"
        "- **sum_fixed**: 0.0\n"
    )
    metrics = promotion.parse_metrics(_write(tmp_path, body))
    assert metrics["verdict"] == "NEUTRAL"
    assert "_unmapped_verdict" not in metrics


def test_four_canonical_verdicts_are_identity() -> None:
    """The four PRD verdicts map to themselves."""
    assert promotion.CANONICAL_MAP.get("PROMOTED") == "PROMOTED"
    assert promotion.CANONICAL_MAP.get("REJECTED") == "REJECTED"
    assert promotion.CANONICAL_MAP.get("CONDITIONAL") == "CONDITIONAL"
    assert promotion.CANONICAL_MAP.get("NEUTRAL") == "NEUTRAL"


def test_promoted_aliases() -> None:
    """Historical PROMOTED synonyms still resolve."""
    for alias in ("PASS", "PASSED", "ACCEPT", "ACCEPTED", "STABLE"):
        assert promotion.CANONICAL_MAP.get(alias) == "PROMOTED", alias


def test_rejected_aliases() -> None:
    """Historical REJECTED synonyms still resolve; DEGENERATE is new
    in v1.5.5 (Phase 3 degenerate-strategy reports)."""
    for alias in (
        "FAIL",
        "FAILED",
        "REJECT",
        "LONG_LOSES_MONEY",
        "DEGENERATE",
    ):
        assert promotion.CANONICAL_MAP.get(alias) == "REJECTED", alias


def test_neutral_aliases() -> None:
    """v1.5.5: inconclusive-outcome aliases land at canonical NEUTRAL,
    NOT at CONDITIONAL (the pre-v1.5.5 collapse)."""
    for alias in (
        "NO_IMPROVEMENT",
        "NO-IMPROVEMENT",
        "NOIMPROVEMENT",
        "INCONCLUSIVE",
    ):
        assert promotion.CANONICAL_MAP.get(alias) == "NEUTRAL", alias


def test_conditional_partial_alias() -> None:
    """PARTIAL is the one CONDITIONAL alias retained from pre-v1.5.5."""
    assert promotion.CANONICAL_MAP.get("PARTIAL") == "CONDITIONAL"


def test_unknown_verdict_returns_none_and_surfaces_unmapped(
    tmp_path: Path,
) -> None:
    """Unknown verdict (e.g. `CLEAN`) returns None from CANONICAL_MAP.
    parse_metrics falls through to parse_verdict's cascade and surfaces
    the raw value as `_unmapped_verdict` so an operator can spot a new
    convention drift. Caller layer logs."""
    assert promotion.CANONICAL_MAP.get("CLEAN") is None
    body = (
        "## Metrics for leaderboard\n"
        "- **verdict**: CLEAN\n"
        "- **sum_fixed**: 0.0\n"
    )
    metrics = promotion.parse_metrics(_write(tmp_path, body))
    # Cascade falls through; with no other verdict keyword in the body
    # the parse_verdict tier scan returns None.
    assert metrics["verdict"] is None
    # The unmapped raw value is preserved verbatim for operator audit.
    assert metrics.get("_unmapped_verdict") == "CLEAN"


def test_each_canonical_verdict_round_trips_via_parse_metrics(
    tmp_path: Path,
) -> None:
    """Each of the four canonical verdicts in a labelled block round-
    trips to itself via parse_metrics."""
    for v in ("PROMOTED", "REJECTED", "CONDITIONAL", "NEUTRAL"):
        body = (
            "## Metrics for leaderboard\n"
            f"- **verdict**: {v}\n"
            "- **sum_fixed**: 0.0\n"
        )
        p = tmp_path / f"CAND_{v.lower()}_PROMOTION.md"
        p.write_text(body, encoding="utf-8")
        metrics = promotion.parse_metrics(p)
        assert metrics["verdict"] == v, (
            f"{v} did not round-trip; got {metrics['verdict']!r}"
        )
