"""Unit tests for v1.3 I — enforced knowledge.md updates.

Covers:
  - knowledge.is_verdict_stage heuristic
  - SessionStart build_knowledge_update_block
  - stop_helper.maybe_clear_knowledge_update_flag
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_LIB = REPO_ROOT / "src" / "lib"
sys.path.insert(0, str(SRC_LIB))

import knowledge  # noqa: E402
import session_start_helper  # noqa: E402
import state  # noqa: E402
import stop_helper  # noqa: E402


def _project(tmp_path: Path) -> Path:
    p = tmp_path / "demo"
    (p / ".cc-autopipe").mkdir(parents=True)
    return p


# ---------------------------------------------------------------------------
# is_verdict_stage
# ---------------------------------------------------------------------------


def test_is_verdict_matches_keywords() -> None:
    assert knowledge.is_verdict_stage("stage_e_verdict") is True
    assert knowledge.is_verdict_stage("rejected") is True
    assert knowledge.is_verdict_stage("PROMOTED") is True
    assert knowledge.is_verdict_stage("accepted_final") is True
    assert knowledge.is_verdict_stage("shipped_to_prod") is True


def test_is_verdict_skips_unrelated() -> None:
    assert knowledge.is_verdict_stage("stage_a_hypothesis") is False
    assert knowledge.is_verdict_stage("training") is False
    assert knowledge.is_verdict_stage("backtest") is False
    assert knowledge.is_verdict_stage("") is False
    assert knowledge.is_verdict_stage(None) is False  # type: ignore[arg-type]


def test_is_verdict_v136_broader_vocabulary() -> None:
    """v1.3.6 SENTINEL-PATTERNS: the v1.3.5 set was too narrow ({verdict,
    rejected, promoted, accepted, shipped, phase_gate, ...}). Real
    Claude task-sessions in the AI-trade Phase 2 v2.1 run used stages
    like 'complete', 'analysis_complete', 'reporting_complete' — none
    matched. v1.3.6 broadens the vocabulary so the knowledge.md sentinel
    arms after these too."""
    # Outcome words Claude actually uses
    assert knowledge.is_verdict_stage("complete") is True
    assert knowledge.is_verdict_stage("completed") is True
    assert knowledge.is_verdict_stage("done") is True
    assert knowledge.is_verdict_stage("closed") is True
    assert knowledge.is_verdict_stage("finished") is True
    # Substring patterns for compound stage names
    assert knowledge.is_verdict_stage("analysis_complete") is True
    assert knowledge.is_verdict_stage("reporting_complete") is True
    assert knowledge.is_verdict_stage("implementation_complete") is True
    # `pass` / `fail` / `reject` substring matches
    assert knowledge.is_verdict_stage("pass") is True
    assert knowledge.is_verdict_stage("fail") is True
    assert knowledge.is_verdict_stage("reject") is True


def test_is_verdict_v136_implementation_alone_still_skipped() -> None:
    """`implementation` by itself is NOT a verdict — Claude is still in
    flight. Only `implementation_complete`/`implementation_done` arm the
    sentinel. Pin this so we don't over-fire on every stage that happens
    to mention 'implementation'."""
    assert knowledge.is_verdict_stage("implementation") is False
    assert knowledge.is_verdict_stage("hypothesis") is False
    assert knowledge.is_verdict_stage("foobar") is False


# ---------------------------------------------------------------------------
# build_knowledge_update_block
# ---------------------------------------------------------------------------


def test_block_empty_when_no_pending(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    p = _project(tmp_path)
    s = state.State.fresh(p.name)
    state.write(p, s)
    assert session_start_helper.build_knowledge_update_block(p) == ""


def test_block_emitted_when_pending_no_update(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    p = _project(tmp_path)
    s = state.State.fresh(p.name)
    s.knowledge_update_pending = True
    s.knowledge_baseline_mtime = time.time()  # nothing newer
    s.knowledge_pending_reason = "stage_e_verdict on vec_meta"
    state.write(p, s)
    block = session_start_helper.build_knowledge_update_block(p)
    assert "MANDATORY KNOWLEDGE UPDATE" in block
    assert "stage_e_verdict on vec_meta" in block


def test_block_suppressed_when_mtime_advanced(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    p = _project(tmp_path)
    kn = p / ".cc-autopipe" / "knowledge.md"
    kn.write_text("- old\n")
    old_mtime = kn.stat().st_mtime - 100
    os.utime(kn, (old_mtime, old_mtime))
    s = state.State.fresh(p.name)
    s.knowledge_update_pending = True
    s.knowledge_baseline_mtime = old_mtime - 1  # ANY value < current mtime
    state.write(p, s)
    # mtime > baseline → block suppressed (Stop hook will clear).
    assert session_start_helper.build_knowledge_update_block(p) == ""


def test_block_renders_with_default_reason(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    p = _project(tmp_path)
    s = state.State.fresh(p.name)
    s.knowledge_update_pending = True
    s.knowledge_baseline_mtime = time.time()
    state.write(p, s)
    block = session_start_helper.build_knowledge_update_block(p)
    assert "unspecified verdict" in block


# ---------------------------------------------------------------------------
# stop_helper.maybe_clear_knowledge_update_flag
# ---------------------------------------------------------------------------


def test_clear_skips_when_not_pending(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    p = _project(tmp_path)
    s = state.State.fresh(p.name)
    state.write(p, s)
    assert stop_helper.maybe_clear_knowledge_update_flag(p) is False


def test_clear_skips_when_mtime_unchanged(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    p = _project(tmp_path)
    kn = p / ".cc-autopipe" / "knowledge.md"
    kn.write_text("- old\n")
    mtime = kn.stat().st_mtime
    s = state.State.fresh(p.name)
    s.knowledge_update_pending = True
    s.knowledge_baseline_mtime = mtime  # same as current
    state.write(p, s)
    assert stop_helper.maybe_clear_knowledge_update_flag(p) is False
    assert state.read(p).knowledge_update_pending is True


def test_clear_fires_when_mtime_advances(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    p = _project(tmp_path)
    kn = p / ".cc-autopipe" / "knowledge.md"
    kn.write_text("- old\n")
    baseline = kn.stat().st_mtime - 100  # before knowledge.md mtime
    s = state.State.fresh(p.name)
    s.knowledge_update_pending = True
    s.knowledge_baseline_mtime = baseline
    s.knowledge_pending_reason = "stage_e_verdict on vec_meta"
    state.write(p, s)
    assert stop_helper.maybe_clear_knowledge_update_flag(p) is True
    s2 = state.read(p)
    assert s2.knowledge_update_pending is False
    assert s2.knowledge_baseline_mtime is None
    assert s2.knowledge_pending_reason is None


def test_persistence_when_5_cycles_without_update(
    tmp_path: Path, monkeypatch
) -> None:
    """Block must keep emitting cycle after cycle until knowledge updates."""
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    p = _project(tmp_path)
    s = state.State.fresh(p.name)
    s.knowledge_update_pending = True
    s.knowledge_baseline_mtime = time.time()
    s.knowledge_pending_reason = "stage_e_verdict on vec_meta"
    state.write(p, s)
    for _ in range(5):
        assert "MANDATORY KNOWLEDGE UPDATE" in (
            session_start_helper.build_knowledge_update_block(p)
        )
        # Stop hook tries to clear but mtime hasn't moved → no clear.
        assert stop_helper.maybe_clear_knowledge_update_flag(p) is False
