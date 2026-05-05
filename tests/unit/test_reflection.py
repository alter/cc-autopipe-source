"""Unit tests for src/orchestrator/reflection.py — PROMPT_v1.3-FULL.md H."""

from __future__ import annotations

import importlib
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
LIB = SRC / "lib"
for p in (str(SRC), str(LIB)):
    if p not in sys.path:
        sys.path.insert(0, p)

import state  # noqa: E402

reflection = importlib.import_module("orchestrator.reflection")


def _project(tmp_path: Path) -> Path:
    p = tmp_path / "demo"
    (p / ".cc-autopipe").mkdir(parents=True)
    return p


# ---------------------------------------------------------------------------
# write_meta_reflect
# ---------------------------------------------------------------------------


def test_write_meta_reflect_creates_file(tmp_path: Path) -> None:
    p = _project(tmp_path)
    target = reflection.write_meta_reflect(
        p,
        task_id="vec_meta",
        stage="stage_e_verdict",
        failures=[
            {"ts": "2026-05-04T10:00:00Z", "error": "verify_failed", "details": "auc=0.51"},
            {"ts": "2026-05-04T11:00:00Z", "error": "verify_failed"},
        ],
        findings_excerpt="- 2026-05-03 stage_d_verdict: REJECT auc=0.5",
        knowledge_excerpt="- transformers don't help on 8-feat",
        attempt=1,
    )
    assert target.exists()
    body = target.read_text()
    assert "Meta-reflection: vec_meta stage stage_e_verdict" in body
    assert "Attempt:** 1" in body
    assert "vec_meta" in body
    assert "MANDATORY ANALYSIS" in body
    assert "META_DECISION_vec_meta_stage_e_verdict" in body


def test_write_meta_reflect_sanitises_filename(tmp_path: Path) -> None:
    p = _project(tmp_path)
    target = reflection.write_meta_reflect(
        p, task_id="task with spaces!", stage="stage/x", failures=[]
    )
    # No spaces or slashes in the filename.
    assert " " not in target.name
    assert "/" not in target.name


# ---------------------------------------------------------------------------
# read_meta_decision
# ---------------------------------------------------------------------------


def test_read_decision_parses_continue(tmp_path: Path) -> None:
    p = _project(tmp_path)
    target = reflection.write_meta_reflect(p, "vec_a", "s1", failures=[])
    decision_path = target.parent / "META_DECISION_vec_a_s1_xyz.md"
    decision_path.write_text(
        "decision: continue\nreason: trying a different layer\n"
        "new_approach: swap to mamba\n"
    )
    out = reflection.read_meta_decision(p, target)
    assert out is not None
    assert out["decision"] == "continue"
    assert "different layer" in out["reason"]
    assert "mamba" in out["new_approach"]


def test_read_decision_parses_skip(tmp_path: Path) -> None:
    p = _project(tmp_path)
    target = reflection.write_meta_reflect(p, "v", "s", failures=[])
    (target.parent / "META_DECISION_v_s_a.md").write_text(
        "decision: skip\nreason: structurally impossible\n"
    )
    out = reflection.read_meta_decision(p, target)
    assert out["decision"] == "skip"


def test_read_decision_picks_newest(tmp_path: Path) -> None:
    p = _project(tmp_path)
    target = reflection.write_meta_reflect(p, "v", "s", failures=[])
    older = target.parent / "META_DECISION_v_s_old.md"
    older.write_text("decision: continue\nreason: old\n")
    older_mtime = time.time() - 100
    import os

    os.utime(older, (older_mtime, older_mtime))
    newer = target.parent / "META_DECISION_v_s_new.md"
    newer.write_text("decision: skip\nreason: new\n")
    out = reflection.read_meta_decision(p, target)
    assert out["decision"] == "skip"


def test_read_decision_returns_none_when_missing(tmp_path: Path) -> None:
    p = _project(tmp_path)
    target = reflection.write_meta_reflect(p, "v", "s", failures=[])
    assert reflection.read_meta_decision(p, target) is None


def test_read_decision_returns_none_for_invalid_value(tmp_path: Path) -> None:
    p = _project(tmp_path)
    target = reflection.write_meta_reflect(p, "v", "s", failures=[])
    (target.parent / "META_DECISION_v_s_a.md").write_text(
        "decision: maybe\nreason: bla\n"
    )
    assert reflection.read_meta_decision(p, target) is None


# ---------------------------------------------------------------------------
# apply_meta_decision
# ---------------------------------------------------------------------------


def test_apply_skip_marks_backlog_and_clears_task(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    p = _project(tmp_path)
    (p / "backlog.md").write_text(
        "- [ ] [P1] vec_x — broken task\n- [ ] [P2] vec_y — other\n"
    )
    s = state.State.fresh(p.name)
    s.current_task = state.CurrentTask(id="vec_x", stage="s")
    state.write(p, s)

    reflection.apply_meta_decision(
        p,
        {"decision": "skip", "reason": "structurally impossible"},
        "vec_x",
    )
    backlog = (p / "backlog.md").read_text()
    assert "[~won't-fix]" in backlog
    assert "[ ] [P2] vec_y" in backlog  # other task untouched
    assert state.read(p).current_task is None


def test_apply_defer_marks_backlog(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    p = _project(tmp_path)
    (p / "backlog.md").write_text("- [ ] [P1] vec_x — needs data\n")
    s = state.State.fresh(p.name)
    s.current_task = state.CurrentTask(id="vec_x", stage="s")
    state.write(p, s)

    reflection.apply_meta_decision(
        p, {"decision": "defer", "reason": "waiting for data"}, "vec_x"
    )
    backlog = (p / "backlog.md").read_text()
    assert "[~deferred]" in backlog


def test_apply_continue_no_backlog_change(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    p = _project(tmp_path)
    (p / "backlog.md").write_text("- [ ] [P1] vec_x — task\n")
    s = state.State.fresh(p.name)
    s.current_task = state.CurrentTask(id="vec_x", stage="s")
    state.write(p, s)

    reflection.apply_meta_decision(
        p,
        {
            "decision": "continue",
            "reason": "different approach",
            "new_approach": "swap arch",
        },
        "vec_x",
    )
    backlog = (p / "backlog.md").read_text()
    assert "[ ] [P1] vec_x" in backlog  # untouched
    assert state.read(p).current_task is not None  # unchanged


# ---------------------------------------------------------------------------
# trigger_meta_reflect + detect_and_apply_decision
# ---------------------------------------------------------------------------


def test_trigger_meta_reflect_writes_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    p = _project(tmp_path)
    s = state.State.fresh(p.name)
    s.current_task = state.CurrentTask(id="vec_x", stage="stage_b")
    s.consecutive_failures = 3
    state.write(p, s)

    action, target = reflection.trigger_meta_reflect(
        p, s, [{"error": "verify_failed", "ts": "x"}]
    )
    assert action == "triggered"
    assert target is not None
    assert target.exists()
    s2 = state.read(p)
    assert s2.meta_reflect_pending is True
    assert s2.meta_reflect_attempts == 1
    assert s2.consecutive_failures == 0  # reset


def test_trigger_meta_reflect_falls_back_after_two_attempts(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    p = _project(tmp_path)
    s = state.State.fresh(p.name)
    s.current_task = state.CurrentTask(id="vec_x", stage="stage_b")
    s.meta_reflect_attempts = 2
    state.write(p, s)
    action, _ = reflection.trigger_meta_reflect(p, s, [])
    assert action == "fallback"


def test_trigger_skipped_when_no_current_task(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    p = _project(tmp_path)
    s = state.State.fresh(p.name)
    state.write(p, s)
    action, _ = reflection.trigger_meta_reflect(p, s, [])
    assert action == "skipped"


def test_detect_and_apply_clears_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    p = _project(tmp_path)
    s = state.State.fresh(p.name)
    s.current_task = state.CurrentTask(id="vec_x", stage="stage_b")
    state.write(p, s)
    target = reflection.write_meta_reflect(p, "vec_x", "stage_b", failures=[])
    s.meta_reflect_pending = True
    s.meta_reflect_target = str(target)
    s.meta_reflect_attempts = 1
    state.write(p, s)
    # Drop a continue decision.
    (target.parent / "META_DECISION_vec_x_stage_b_x.md").write_text(
        "decision: continue\nreason: trying different params\n"
    )

    applied = reflection.detect_and_apply_decision(p, s)
    assert applied is True
    s2 = state.read(p)
    assert s2.meta_reflect_pending is False
    assert s2.meta_reflect_target is None
    assert s2.meta_reflect_attempts == 0


def test_build_block_when_pending_no_decision(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    p = _project(tmp_path)
    s = state.State.fresh(p.name)
    s.meta_reflect_pending = True
    s.meta_reflect_target = "/path/to/META_REFLECT_v_s_xx.md"
    state.write(p, s)
    block = reflection.build_meta_reflect_block(p)
    assert "MANDATORY META-REFLECTION" in block
    assert "META_REFLECT_v_s_xx.md" in block


def test_build_block_empty_when_not_pending(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    p = _project(tmp_path)
    s = state.State.fresh(p.name)
    state.write(p, s)
    assert reflection.build_meta_reflect_block(p) == ""
