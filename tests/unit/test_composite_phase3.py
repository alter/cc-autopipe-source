"""Unit tests for v1.3.13 leaderboard._composite phase detection.

Phase 2 formula (sum_fixed non-None):
    0.5 * (sum_fixed/1000) + 0.3 * (1 - regime_parity) + 0.2 * (max_dd / -100)
Phase 3 formula (sum_fixed None, ML classification):
    0.6 * auc_adj + 0.3 * sharpe_adj + 0.1 * dm_adj

Refs: PROMPT_v1.3.13-hotfix.md GROUP PHASE3-METRICS.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_LIB = REPO_ROOT / "src" / "lib"

sys.path.insert(0, str(SRC_LIB))

import leaderboard  # noqa: E402


def test_composite_phase2_full() -> None:
    m = {"sum_fixed": 245.5, "regime_parity": 0.18, "max_dd": -8.2}
    # 0.5 * 0.2455 + 0.3 * 0.82 + 0.2 * 0.082 = 0.12275 + 0.246 + 0.0164 = 0.38515
    assert leaderboard._composite(m) == round(
        0.5 * 0.2455 + 0.3 * (1 - 0.18) + 0.2 * (-8.2 / -100), 4
    )


def test_composite_phase3_full() -> None:
    m = {"auc": 0.86, "sharpe": 1.2, "dm_p_value": 0.021}
    # auc_adj = (0.86 - 0.5) * 2 = 0.72
    # sharpe_adj = clamp(1.2/3, 0, 1) = 0.4
    # dm_adj = max(0, 1 - 0.021*10) = 0.79
    # composite = 0.6 * 0.72 + 0.3 * 0.4 + 0.1 * 0.79 = 0.432 + 0.12 + 0.079 = 0.631
    assert leaderboard._composite(m) == round(
        0.6 * 0.72 + 0.3 * 0.4 + 0.1 * 0.79, 4
    )


def test_composite_phase3_random_chance() -> None:
    """AUC=0.5, Sharpe=0, no DM — Phase 3 random-chance model, no negative
    penalty beyond floor of 0.0."""
    m = {"auc": 0.5, "sharpe": 0.0, "dm_p_value": None}
    assert leaderboard._composite(m) == 0.0


def test_composite_phase3_all_none() -> None:
    """No Phase-2 OR Phase-3 metrics → Phase 3 branch with all-zero
    contributions."""
    m = {}
    assert leaderboard._composite(m) == 0.0


def test_composite_phase2_zero_sumfixed() -> None:
    """sum_fixed=0.0 (explicit) is non-None → still Phase 2 branch, not
    Phase 3 fallback."""
    m = {"sum_fixed": 0.0, "regime_parity": 0.5, "max_dd": -5.0}
    # 0.5*0 + 0.3*0.5 + 0.2*0.05 = 0 + 0.15 + 0.01 = 0.16
    assert leaderboard._composite(m) == round(
        0.5 * 0.0 + 0.3 * (1 - 0.5) + 0.2 * (-5.0 / -100), 4
    )
