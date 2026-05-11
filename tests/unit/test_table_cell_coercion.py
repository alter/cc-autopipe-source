"""Unit tests for v1.4.1 TABLE-CELL-HARDENING — `_coerce_table_cell`.

The v1.4.0 inline `float(raw.rstrip('%').lstrip('+'))` cell coercion
silently dropped the most common Phase 3 PROMOTION.md cell shapes:
bold markers (Phase 3 NN tables), Unicode minus (Phase 3 LA Δ rows),
em-dash placeholders, trailing emoji. v1.4.1 routes every data cell
through `_coerce_table_cell` which normalises those shapes before
attempting `float()`.

Refs: PROMPT_v1.4.1-hotfix.md GROUP TABLE-CELL-HARDENING.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_LIB = REPO_ROOT / "src" / "lib"

sys.path.insert(0, str(SRC_LIB))

import promotion  # noqa: E402


def test_bold_markers_stripped() -> None:
    """`**0.78762**` (Phase 3 NN bold-cell shape) → 0.78762."""
    assert promotion._coerce_table_cell("**0.78762**") == 0.78762


def test_unicode_minus_normalised() -> None:
    """`−3.32` (U+2212, Phase 3 LA Δ-row shape) → -3.32."""
    assert promotion._coerce_table_cell("−3.32") == -3.32


def test_em_dash_placeholder_returns_none() -> None:
    """`—` (em-dash standalone) is a "no value" placeholder, not 0."""
    assert promotion._coerce_table_cell("—") is None


def test_trailing_emoji_dropped() -> None:
    """`0.86003 ✓` — strip the trailing PASS marker, keep the value."""
    assert promotion._coerce_table_cell("0.86003 ✓") == 0.86003


def test_bold_plus_percent_combo() -> None:
    """`**+692.84%**` — bold markers + leading plus + trailing percent."""
    assert promotion._coerce_table_cell("**+692.84%**") == 692.84
