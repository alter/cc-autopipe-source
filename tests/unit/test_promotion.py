"""Unit tests for src/lib/promotion.py.

v1.3.5 Group PROMOTION-PARSER.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_LIB = REPO_ROOT / "src" / "lib"

sys.path.insert(0, str(SRC_LIB))

import promotion  # noqa: E402


PROMOTED_FULL = """\
# CAND vec_long_lgbm — PROMOTION

**Verdict: PROMOTED**

## Acceptance
sum_fixed: +268.99%
regime_parity: 0.18
max_DD: -8.20%
DM_p_value: 0.003
DSR: 1.12

## Long-only verification
no shorts.

## Regime-stratified PnL
all 5 regimes positive.

## Statistical significance
DM p<0.01.

## Walk-forward stability
3 of 4 windows positive.

## No-lookahead audit
no leak.
"""

PROMOTED_MISSING_TWO = """\
**Verdict: PROMOTED**

## Long-only verification
yes
## Regime-stratified PnL
yes
## Statistical significance
yes
"""

REJECTED = """\
**Verdict: REJECTED**

## Long-only verification
no shorts.
## Regime-stratified PnL
fails parity.
## Statistical significance
DM p>0.1.
## Walk-forward stability
1 of 4.
## No-lookahead audit
maybe leak.
"""


def _write_promo(project: Path, task_id: str, body: str) -> Path:
    p = promotion.promotion_path(project, task_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def test_parse_verdict_promoted(tmp_path: Path) -> None:
    p = _write_promo(tmp_path, "vec_long_lgbm", PROMOTED_FULL)
    assert promotion.parse_verdict(p) == "PROMOTED"


def test_parse_verdict_rejected(tmp_path: Path) -> None:
    p = _write_promo(tmp_path, "vec_long_lgbm", REJECTED)
    assert promotion.parse_verdict(p) == "REJECTED"


def test_parse_verdict_no_line_returns_none(tmp_path: Path) -> None:
    p = _write_promo(tmp_path, "x", "no verdict here, just text")
    assert promotion.parse_verdict(p) is None


def test_parse_verdict_missing_file(tmp_path: Path) -> None:
    p = promotion.promotion_path(tmp_path, "x")
    assert promotion.parse_verdict(p) is None


def test_validate_v2_sections_all_present(tmp_path: Path) -> None:
    p = _write_promo(tmp_path, "x", PROMOTED_FULL)
    ok, missing = promotion.validate_v2_sections(p)
    assert ok
    assert missing == []


def test_validate_v2_sections_two_missing(tmp_path: Path) -> None:
    p = _write_promo(tmp_path, "x", PROMOTED_MISSING_TWO)
    ok, missing = promotion.validate_v2_sections(p)
    assert not ok
    assert "Walk-forward stability" in missing
    assert "No-lookahead audit" in missing
    assert "Long-only verification" not in missing


def test_parse_metrics_extracts_full_set(tmp_path: Path) -> None:
    p = _write_promo(tmp_path, "x", PROMOTED_FULL)
    m = promotion.parse_metrics(p)
    assert m["sum_fixed"] == 268.99
    assert m["regime_parity"] == 0.18
    assert m["max_dd"] == -8.20
    assert abs(m["dm_p_value"] - 0.003) < 1e-6
    assert m["dsr"] == 1.12


def test_parse_metrics_missing_field_returns_none(tmp_path: Path) -> None:
    p = _write_promo(tmp_path, "x", "**Verdict: PROMOTED**\nsum_fixed: +12.5%\n")
    m = promotion.parse_metrics(p)
    assert m["sum_fixed"] == 12.5
    assert m["regime_parity"] is None
    assert m["max_dd"] is None
    assert m["dm_p_value"] is None
    assert m["dsr"] is None


# v1.3.6 lenient verdict parser tests — Phase 1/2 PROMOTION.md formats
# observed in the AI-trade Phase 2 v2.1 run.


def test_parse_verdict_inline_long_loses_money(tmp_path: Path) -> None:
    """Heading + colon + custom verdict keyword (as seen in
    CAND_long_only_baseline_PROMOTION.md)."""
    p = _write_promo(tmp_path, "x", "## Verdict: LONG_LOSES_MONEY\n")
    assert promotion.parse_verdict(p) == "REJECTED"


def test_parse_verdict_stage_subheading_reject(tmp_path: Path) -> None:
    """`## Stage X: Verdict` heading with `### KEYWORD` body
    (CAND_rl_PROMOTION.md format)."""
    p = _write_promo(
        tmp_path, "x", "## Stage D: Verdict\n\n### REJECT — vec_rl (PPO)\n"
    )
    assert promotion.parse_verdict(p) == "REJECTED"


def test_parse_verdict_subheading_stable_promoted(tmp_path: Path) -> None:
    """`## Verdict\n### STABLE — ...` (CAND_dr_regime_classifier_check)."""
    p = _write_promo(
        tmp_path, "x", "## Verdict\n\n### STABLE — M-Regime ready\n"
    )
    assert promotion.parse_verdict(p) == "PROMOTED"


def test_parse_verdict_conditional_partial_pass(tmp_path: Path) -> None:
    """CONDITIONAL is a distinct third state for partial passes
    (CAND_dr_synth_v1_PROMOTION.md format)."""
    p = _write_promo(
        tmp_path, "x", "## Verdict\n\n### CONDITIONAL — Passes 3/4 PRD criteria\n"
    )
    assert promotion.parse_verdict(p) == "CONDITIONAL"


def test_parse_verdict_subheading_pass_promoted(tmp_path: Path) -> None:
    """`## Verdict\n### PASS — ...` (CAND_q_compressed_partial_filter)."""
    p = _write_promo(
        tmp_path, "x", "## Verdict\n\n### PASS — Acceptance criteria met\n"
    )
    assert promotion.parse_verdict(p) == "PROMOTED"


def test_parse_verdict_legacy_strict_pattern_still_works(tmp_path: Path) -> None:
    """v1.3.5 `**Verdict: PROMOTED**` form must still parse so existing
    fixtures and old AI-trade reports keep working."""
    p = _write_promo(tmp_path, "x", "**Verdict: PROMOTED**\n")
    assert promotion.parse_verdict(p) == "PROMOTED"


def test_parse_verdict_next_heading_boundary_respected(tmp_path: Path) -> None:
    """A keyword in the NEXT same-level section must not be picked up.
    `## Verdict` body ends at the next `##` heading — `## Next Section`
    here — so the PROMOTED in body is out of scope and parser returns
    None."""
    p = _write_promo(
        tmp_path,
        "x",
        "## Verdict\n\n## Next Section\nPROMOTED here doesn't count\n",
    )
    assert promotion.parse_verdict(p) is None


def test_parse_verdict_partial_keyword_canonicalizes_to_conditional(
    tmp_path: Path,
) -> None:
    """PARTIAL maps to CONDITIONAL alongside the explicit keyword."""
    p = _write_promo(
        tmp_path, "x", "## Verdict\n\n### PARTIAL — see notes\n"
    )
    assert promotion.parse_verdict(p) == "CONDITIONAL"
