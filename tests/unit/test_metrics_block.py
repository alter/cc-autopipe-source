"""Unit tests for v1.4.0 METRICS-BLOCK-CONVENTION.

Covers the labelled `## Metrics for leaderboard` block as the primary
source for both `parse_verdict` (Tier 0) and `parse_metrics` (pre-fill
before the v1.3.x regex extractors).

Refs: PROMPT_v1.4.0.md GROUP METRICS-BLOCK-CONVENTION.
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


def test_metrics_block_all_fields(tmp_path: Path) -> None:
    body = (
        "# CAND_test_PROMOTION\n\n"
        "## Metrics for leaderboard\n"
        "- **verdict**: PROMOTED\n"
        "- **sum_fixed**: 245.5%\n"
        "- **regime_parity**: 0.18\n"
        "- **max_dd**: -8.2\n"
        "- **dm_p_value**: 0.003\n"
        "- **dsr**: 1.12\n"
        "- **auc**: 0.86\n"
        "- **sharpe**: 18.33\n"
        "\n"
        "## Free-form summary\nDetail prose.\n"
    )
    p = _write(tmp_path, body)
    assert promotion.parse_verdict(p) == "PROMOTED"
    metrics = promotion.parse_metrics(p)
    assert metrics["sum_fixed"] == 245.5
    assert metrics["regime_parity"] == 0.18
    assert metrics["max_dd"] == -8.2
    assert metrics["dm_p_value"] == 0.003
    assert metrics["dsr"] == 1.12
    assert metrics["auc"] == 0.86
    assert metrics["sharpe"] == 18.33


def test_metrics_block_wins_over_bold_metadata(tmp_path: Path) -> None:
    """Tier 0 (block) fires before Tier 4 (bold metadata) — labelled
    `**verdict**: PROMOTED` overrides a conflicting `**Status**: REJECTED`
    elsewhere in the file."""
    body = (
        "# Report\n\n"
        "**Status**: REJECTED\n\n"
        "## Metrics for leaderboard\n"
        "- **verdict**: PROMOTED\n"
        "- **sum_fixed**: 100.0\n"
    )
    assert promotion.parse_verdict(_write(tmp_path, body)) == "PROMOTED"


def test_metrics_block_without_verdict_falls_through(tmp_path: Path) -> None:
    """Block present but `**verdict**:` field missing → Tier 0 returns
    None; engine falls through to Tier 1 and parses the verdict from the
    `## Verdict` heading."""
    body = (
        "## Verdict\n\nPROMOTED — looks good\n\n"
        "## Metrics for leaderboard\n"
        "- **sum_fixed**: 50.0\n"
        "- **sharpe**: 1.5\n"
    )
    p = _write(tmp_path, body)
    assert promotion.parse_verdict(p) == "PROMOTED"
    metrics = promotion.parse_metrics(p)
    assert metrics["sum_fixed"] == 50.0
    assert metrics["sharpe"] == 1.5


def test_metrics_block_na_coerces_to_none(tmp_path: Path) -> None:
    """`**sharpe**: N/A` → out["sharpe"] is None (NOT 0)."""
    body = (
        "## Metrics for leaderboard\n"
        "- **verdict**: NEUTRAL\n"
        "- **sharpe**: N/A\n"
        "- **auc**: 0.5\n"
    )
    p = _write(tmp_path, body)
    metrics = promotion.parse_metrics(p)
    assert metrics["sharpe"] is None
    assert metrics["auc"] == 0.5
    assert promotion.parse_verdict(p) == "CONDITIONAL"


def test_no_block_backward_compat(tmp_path: Path) -> None:
    """File without the labelled block parses identically to v1.3.13:
    legacy regex extractors fill `sum_fixed` / `sharpe`; bold-metadata
    `**Status**: PASS` resolves the verdict via Tier 4."""
    body = (
        "# Legacy report\n\n"
        "**Status**: PASS ✓\n\n"
        "sum_fixed: 200.5%\n"
        "**Sharpe**: 2.1\n"
    )
    p = _write(tmp_path, body)
    assert promotion.parse_verdict(p) == "PROMOTED"
    metrics = promotion.parse_metrics(p)
    assert metrics["sum_fixed"] == 200.5
    assert metrics["sharpe"] == 2.1
