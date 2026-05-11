"""Unit tests for v1.4.1 TABLE-CELL-HARDENING — `_parse_table_metrics`
exercised against real AI-trade Phase 3 table shapes.

Two cases:
  1. Phase 3 NN bold-cell table (mirrors `CAND_nn_liquid_nn_PROMOTION.md`):
     the data row's cells are wrapped in `**bold**`. Before v1.4.1 the
     hardened cell coercion was inline `float()` and silently dropped
     every bold cell, returning `{}`. After the fix, the AUC column is
     extracted as 0.78762.

  2. Date-format header `| Date (dd-MM) | Result |`: before v1.4.1 the
     bare `"dd"` alias in `_TABLE_COLUMN_ALIASES` would not match this
     header (header text is `date (dd-mm)`, not `dd`), but a sibling
     header `| dd | Result |` would have wrongly mapped to `max_dd`.
     With the alias removed, neither shape false-positives. This test
     pins the contract: a date-format-derived two-letter header MUST
     NOT match any metric.

Refs: PROMPT_v1.4.1-hotfix.md GROUP TABLE-CELL-HARDENING.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_LIB = REPO_ROOT / "src" / "lib"

sys.path.insert(0, str(SRC_LIB))

import promotion  # noqa: E402


def test_phase3_nn_bold_cells_extract_auc() -> None:
    """Mirrors `CAND_nn_liquid_nn_PROMOTION.md` — single bold-cell data
    row. Header `AUC` matches the alias, bold-stripped cell coerces to
    the float value, table parser returns {"auc": 0.78762, ...}."""
    text = (
        "# Phase 3 NN report\n\n"
        "| Model | AUC | DM p | Status |\n"
        "|-------|-----|------|--------|\n"
        "| **CfC (LiquidNN)** | **0.78762** | **1.0000** | **REJECTED** |\n"
    )
    result = promotion._parse_table_metrics(text)
    assert result.get("auc") == 0.78762
    # DM p alias also matches and the bold cell coerces cleanly.
    assert result.get("dm_p_value") == 1.0


def test_dd_date_header_no_false_positive() -> None:
    """`| dd | Result |` would have mapped `dd` → `max_dd` under v1.4.0.
    After dropping the bare `dd` alias the column is unrecognised; with
    no other recognised header cells the parser returns an empty dict,
    preventing leaderboard corruption from date-numeral values."""
    text = (
        "# Calendar reference table\n\n"
        "| dd | Result |\n"
        "|----|--------|\n"
        "| 11 | OK |\n"
    )
    result = promotion._parse_table_metrics(text)
    assert result == {}
