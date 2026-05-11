"""Unit tests for v1.4.0 TABLE-METRICS markdown-table fallback in
parse_metrics.

Phase 3 LA / NN reports include data tables with column headers like
`| sf | Sharpe(bar) | Sharpe(daily) |` that the v1.3.13 free-form
regex extractors can't read. The v1.4.0 fallback parses the first
recognised table cell-by-cell — only filling slots that the labelled
block + legacy regex + Sharpe cascade left None, so priority order is
preserved.

Refs: PROMPT_v1.4.0.md GROUP TABLE-METRICS.
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


def test_phase3_la_table_no_block(tmp_path: Path) -> None:
    """Phase 3 LA `| sf | Sharpe(bar) | Sharpe(daily) |` table with
    data row `| 692.84 | 90.8 | 18.33 |` and no labelled block.
    Table fallback fills sum_fixed and the daily-Sharpe cascade fills
    sharpe (priority 1 catches `Sharpe(daily)` in the table header
    line via the daily-form regex)."""
    body = (
        "# Phase 3 LA report\n\n"
        "| sf | Sharpe(bar) | Sharpe(daily) |\n"
        "|----|-------------|---------------|\n"
        "| 692.84 | 90.8 | 18.33 |\n"
    )
    metrics = promotion.parse_metrics(_write(tmp_path, body))
    assert metrics["sum_fixed"] == 692.84
    assert metrics["sharpe"] == 18.33


def test_metrics_block_wins_over_table(tmp_path: Path) -> None:
    """Labelled block `**sum_fixed**: 245.5` must win over the table's
    `| sf | ... | 692.84 | ... |` cell — block is Tier 0 / primary."""
    body = (
        "## Metrics for leaderboard\n"
        "- **verdict**: PROMOTED\n"
        "- **sum_fixed**: 245.5\n"
        "\n"
        "## Table\n\n"
        "| sf | Sharpe(daily) |\n"
        "|----|---------------|\n"
        "| 692.84 | 18.33 |\n"
    )
    metrics = promotion.parse_metrics(_write(tmp_path, body))
    assert metrics["sum_fixed"] == 245.5


def test_phase3_nn_auc_table(tmp_path: Path) -> None:
    """Phase 3 NN `| Model | AUC | Sharpe(daily) |` with data row
    `| TFT | 0.86003 | 1.45 |`. The Sharpe cascade catches the daily
    form first; the table fallback fills auc (which the v1.3.13 regex
    actually catches inline too — table is defense-in-depth)."""
    body = (
        "# Phase 3 NN report\n\n"
        "| Model | AUC | Sharpe(daily) |\n"
        "|-------|-----|---------------|\n"
        "| TFT | 0.86003 | 1.45 |\n"
    )
    metrics = promotion.parse_metrics(_write(tmp_path, body))
    assert metrics["auc"] == 0.86003
    assert metrics["sharpe"] == 1.45


def test_no_table_no_block_legacy_unchanged(tmp_path: Path) -> None:
    """File with no tables and no labelled block — `parse_metrics`
    behaves identically to v1.3.13: legacy regexes fill what they can,
    everything else stays None."""
    body = (
        "# Legacy report\n\n"
        "sum_fixed: 200.5%\n"
        "**Sharpe**: 2.1\n"
    )
    metrics = promotion.parse_metrics(_write(tmp_path, body))
    assert metrics["sum_fixed"] == 200.5
    assert metrics["sharpe"] == 2.1
    assert metrics["auc"] is None
    assert metrics["regime_parity"] is None
