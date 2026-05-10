"""Unit tests for v1.3.13 parse_metrics Phase 3 fields (auc, sharpe).

Phase 3 ML classification PROMOTION.md reports AUC and Sharpe instead
of Phase-2's sum_fixed / regime_parity / max_DD. parse_metrics must
extract both, and must not regress Phase-2 extraction when only Phase-2
fields are present.

Refs: PROMPT_v1.3.13-hotfix.md GROUP PHASE3-METRICS.
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


def test_parse_metrics_auc_inline(tmp_path: Path) -> None:
    body = "**Verdict: PROMOTED**\n\nAUC: 0.86003\n"
    m = promotion.parse_metrics(_write(tmp_path, body))
    assert m["auc"] == 0.86003


def test_parse_metrics_roc_auc(tmp_path: Path) -> None:
    body = "ROC AUC: 0.860\n"
    m = promotion.parse_metrics(_write(tmp_path, body))
    assert m["auc"] == 0.860


def test_parse_metrics_sharpe_ratio(tmp_path: Path) -> None:
    body = "Sharpe ratio: 1.23\n"
    m = promotion.parse_metrics(_write(tmp_path, body))
    assert m["sharpe"] == 1.23


def test_parse_metrics_sharpe_negative(tmp_path: Path) -> None:
    body = "Sharpe: -0.45\n"
    m = promotion.parse_metrics(_write(tmp_path, body))
    assert m["sharpe"] == -0.45


def test_parse_metrics_bold_wrap_all_three(tmp_path: Path) -> None:
    """AI-trade Phase 3 canonical format wraps the field name in markdown
    bold: `**AUC**: 0.873`, `**Sharpe ratio**: 1.45`,
    `**DM p-value**: 0.031`. All three must extract."""
    body = (
        "## Verdict\n\n### PROMOTED\n\n"
        "**AUC**: 0.873\n"
        "**Sharpe ratio**: 1.45\n"
        "**DM p-value**: 0.031\n"
    )
    m = promotion.parse_metrics(_write(tmp_path, body))
    assert m["auc"] == 0.873
    assert m["sharpe"] == 1.45
    assert abs(m["dm_p_value"] - 0.031) < 1e-9


def test_parse_metrics_phase2_only_leaves_phase3_none(tmp_path: Path) -> None:
    body = (
        "**Verdict: PROMOTED**\n\n"
        "sum_fixed: 245.5%\n"
        "regime_parity: 0.18\n"
        "max_DD: -8.2%\n"
    )
    m = promotion.parse_metrics(_write(tmp_path, body))
    assert m["sum_fixed"] == 245.5
    assert m["regime_parity"] == 0.18
    assert m["max_dd"] == -8.2
    assert m["auc"] is None
    assert m["sharpe"] is None
