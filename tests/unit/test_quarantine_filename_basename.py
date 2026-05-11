"""Unit tests for v1.4.1 QUARANTINE-FILENAME-CONSISTENCY.

v1.4.0 MULTI-PREFIX-STRIP made the engine read CAND files from a
Form-1 (canonical: `vec_` stripped) path. `quarantine_invalid` was
left using the raw task_id for both the marker filename and the
operator-facing CAND reference, so the operator was directed to a
file path the engine never reads. v1.4.1 routes both write-side
paths through `_promotion_basename(task_id)` so the marker and the
CAND reference match the engine's read-side resolution.

Refs: PROMPT_v1.4.1-hotfix.md GROUP QUARANTINE-FILENAME-CONSISTENCY.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_LIB = REPO_ROOT / "src" / "lib"

sys.path.insert(0, str(SRC_LIB))

import promotion  # noqa: E402


@dataclass
class _StubItem:
    id: str
    priority: int = 1


def test_phase3_task_id_uses_form1_basename(tmp_path: Path) -> None:
    """`vec_p3_meta_test` → marker at `UNVALIDATED_PROMOTION_p3_meta_test.md`,
    body references `data/debug/CAND_p3_meta_test_PROMOTION.md`
    (Form 1: only `vec_` stripped — canonical engine-emit path)."""
    item = _StubItem(id="vec_p3_meta_test")
    promotion.quarantine_invalid(
        tmp_path, item, ["Long-only verification"]
    )
    marker = tmp_path / "data" / "debug" / "UNVALIDATED_PROMOTION_p3_meta_test.md"
    assert marker.exists()
    body = marker.read_text(encoding="utf-8")
    # Heading retains the full task_id for operator readability.
    assert "# Unvalidated promotion: vec_p3_meta_test" in body
    # CAND reference uses the Form 1 basename so the operator opens the
    # file the engine actually reads.
    assert "`data/debug/CAND_p3_meta_test_PROMOTION.md`" in body
    # The raw-task-id form must NOT leak into the CAND reference.
    assert "CAND_vec_p3_meta_test_PROMOTION.md" not in body


def test_legacy_long_task_id_uses_form1_basename(tmp_path: Path) -> None:
    """`vec_long_synth_v3` → marker at
    `UNVALIDATED_PROMOTION_long_synth_v3.md`, body references
    `CAND_long_synth_v3_PROMOTION.md`. Mirrors the Phase 2 legacy
    convention while still matching the new Form 1 contract — both
    code paths converge on the same `vec_`-stripped basename."""
    item = _StubItem(id="vec_long_synth_v3")
    promotion.quarantine_invalid(
        tmp_path, item, ["Regime-stratified PnL"]
    )
    marker = tmp_path / "data" / "debug" / "UNVALIDATED_PROMOTION_long_synth_v3.md"
    assert marker.exists()
    body = marker.read_text(encoding="utf-8")
    assert "# Unvalidated promotion: vec_long_synth_v3" in body
    assert "`data/debug/CAND_long_synth_v3_PROMOTION.md`" in body
    assert "CAND_vec_long_synth_v3_PROMOTION.md" not in body
