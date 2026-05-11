"""Unit tests for v1.4.0 DAILY-SHARPE cascade in parse_metrics.

Phase 3 LA reports carry an inflated `**Per-bar Sharpe 90.8** = ...`
line BEFORE the true `**True daily Sharpe = 18.33**` value. The v1.3.13
single-pass `re.search` returned the first match → captured the
per-bar inflation. v1.4.0 fixes via a two-priority cascade: explicit
daily form first, then bare Sharpe with per-bar context exclusion.

Refs: PROMPT_v1.4.0.md GROUP DAILY-SHARPE.
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


def test_daily_form_wins_over_per_bar_inflation(tmp_path: Path) -> None:
    """`**Per-bar Sharpe 90.8**` (inflated) appears first; `**True daily
    Sharpe = 18.33**` is the real value. Priority 1 catches the daily
    form and wins."""
    body = (
        "# Phase 3 LA report\n\n"
        "**Per-bar Sharpe 90.8** = inflated ~5x (fixed-frequency bars)\n"
        "**True daily Sharpe = 18.33**\n"
    )
    metrics = promotion.parse_metrics(_write(tmp_path, body))
    assert metrics["sharpe"] == 18.33


def test_only_per_bar_returns_none(tmp_path: Path) -> None:
    """File has only `**Per-bar Sharpe 90.8**` — no daily form, no other
    bare Sharpe. Priority 1 misses; priority 2 sees the bare `Sharpe`
    but the per-bar context exclusion skips it. Result: None."""
    body = (
        "# Phase 3 LA report\n\n"
        "**Per-bar Sharpe 90.8** = inflated; no daily value supplied.\n"
    )
    metrics = promotion.parse_metrics(_write(tmp_path, body))
    assert metrics["sharpe"] is None


def test_bare_sharpe_ratio_captured(tmp_path: Path) -> None:
    """`**Sharpe ratio**: 1.45` (no daily qualifier, no per-bar prefix).
    Priority 2 captures bare Sharpe with the v1.3.13 markdown-bold-close
    + `ratio` tolerance preserved."""
    body = "# Phase 2 report\n\n**Sharpe ratio**: 1.45\n"
    metrics = promotion.parse_metrics(_write(tmp_path, body))
    assert metrics["sharpe"] == 1.45
