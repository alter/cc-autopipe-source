"""Unit tests for v1.4.0 MULTI-PREFIX-STRIP filename probing.

Phase 3 PROMOTION.md filenames take three different forms depending on
which sub-team / iteration produced them. The engine probes a candidate
chain so all three resolve, with the canonical (`vec_`-stripped) form
tried first.

Refs: PROMPT_v1.4.0.md GROUP MULTI-PREFIX-STRIP.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_LIB = REPO_ROOT / "src" / "lib"

sys.path.insert(0, str(SRC_LIB))

import promotion  # noqa: E402


def _debug_dir(tmp_path: Path) -> Path:
    d = tmp_path / "data" / "debug"
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_canonical_form_wins_when_present(tmp_path: Path) -> None:
    """Form 1 (`vec_`-stripped) takes priority when the file exists at
    that path. AI-trade Phase 3 LA tasks like
    `CAND_p3_la_q10_recompute_train_only_PROMOTION.md` already follow
    this convention."""
    d = _debug_dir(tmp_path)
    canonical = d / "CAND_p3_meta_anti_winner_bias_PROMOTION.md"
    canonical.write_text("body", encoding="utf-8")
    p = promotion.promotion_path(tmp_path, "vec_p3_meta_anti_winner_bias")
    assert p == canonical


def test_phase_stripped_form_resolves_when_canonical_absent(tmp_path: Path) -> None:
    """Phase 3 meta / nn / lv tasks frequently land at the phase-
    stripped Form 2 (no `p3_` prefix). Engine must find them."""
    d = _debug_dir(tmp_path)
    form2 = d / "CAND_meta_anti_winner_bias_PROMOTION.md"
    form2.write_text("body", encoding="utf-8")
    p = promotion.promotion_path(tmp_path, "vec_p3_meta_anti_winner_bias")
    assert p == form2


def test_no_file_returns_canonical_with_no_exists(tmp_path: Path) -> None:
    """When no candidate exists on disk, return the canonical (Form 1)
    path so the engine logs `promotion_verdict_missing` against the
    predictable filename."""
    _debug_dir(tmp_path)
    p = promotion.promotion_path(tmp_path, "vec_p3_meta_anti_winner_bias")
    assert p.name == "CAND_p3_meta_anti_winner_bias_PROMOTION.md"
    assert not p.exists()


def test_basename_candidates_phase2_legacy() -> None:
    """`vec_long_meta_v2` yields three candidates in priority order:
    canonical → phase-stripped → no-strip."""
    candidates = promotion._promotion_basename_candidates("vec_long_meta_v2")
    assert candidates == ["long_meta_v2", "meta_v2", "vec_long_meta_v2"]
