"""Integration tests for v1.3.6 PHASE-DONE-RECOVERY sweep.

`sweep_done_projects` flips `phase=done` projects back to `active` when
their backlog gains open `[ ]` tasks (operator added new work). Mirrors
`sweep_failed_projects` but targets the done state. Required for 3-4
month autonomous runs where backlog cycles drain → reopen multiple
times — without this, every cycle requires a manual state.json edit.

Covers:
- DONE + 0 open tasks → skipped (prd_still_complete)
- DONE + new open task → resumed (phase=active, prd_complete=False,
  current_task=None)
- DONE + open tasks + meta_reflect_pending → skipped (enforcement)
- DONE + open tasks + knowledge_update_pending → skipped (enforcement)
- second sweep on already-active project → no re-trigger
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
LIB = SRC / "lib"
for p in (str(SRC), str(LIB)):
    if p not in sys.path:
        sys.path.insert(0, p)

import state  # noqa: E402

recovery = importlib.import_module("orchestrator.recovery")


def _project(tmp_path: Path, name: str = "demo") -> Path:
    p = tmp_path / name
    (p / ".cc-autopipe").mkdir(parents=True)
    return p


def _write_done_state(project: Path) -> None:
    s = state.State.fresh(project.name)
    s.phase = "done"
    s.prd_complete = True
    s.prd_complete_detected = True
    s.last_score = 0.95
    s.last_passed = True
    state.write(project, s)


def _read_aggregate(user_home: Path) -> list[dict]:
    p = user_home / "log" / "aggregate.jsonl"
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]


def test_done_with_no_open_tasks_skipped(tmp_path: Path, monkeypatch) -> None:
    """A done project whose backlog is genuinely empty should NOT
    auto-resume — there's nothing to resume to. Engine logs
    `phase_done_resume_skipped reason=prd_still_complete` and leaves
    state untouched."""
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    p = _project(tmp_path)
    (p / "backlog.md").write_text(
        "- [x] [implement] [P1] vec_long_baseline — done\n"
        "## Done\n",
        encoding="utf-8",
    )
    _write_done_state(p)

    assert recovery.maybe_resume_done(p) is False
    assert state.read(p).phase == "done"
    events = [
        e
        for e in _read_aggregate(user_home)
        if e.get("event") == "phase_done_resume_skipped"
    ]
    assert len(events) == 1
    assert events[0]["reason"] == "prd_still_complete"


def test_done_with_new_open_task_resumes(tmp_path: Path, monkeypatch) -> None:
    """Operator appended a new `[ ]` line. Engine should flip
    phase=done → active, clear PRD-complete flags + current_task,
    log phase_done_to_active with open_tasks count."""
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    p = _project(tmp_path)
    (p / "backlog.md").write_text(
        "- [x] [implement] [P1] vec_long_baseline — done\n"
        "- [ ] [implement] [P1] vec_new_idea — operator-added\n"
        "## Done\n",
        encoding="utf-8",
    )
    s = state.State.fresh(p.name)
    s.phase = "done"
    s.prd_complete = True
    s.prd_complete_detected = True
    s.last_score = 0.92
    s.last_passed = True
    s.current_task = state.CurrentTask(id="vec_old", stage="closed")
    state.write(p, s)

    assert recovery.maybe_resume_done(p) is True

    s2 = state.read(p)
    assert s2.phase == "active"
    assert s2.prd_complete is False
    assert s2.prd_complete_detected is False
    assert s2.current_task is None
    assert s2.last_score is None
    assert s2.last_passed is None

    events = [
        e
        for e in _read_aggregate(user_home)
        if e.get("event") == "phase_done_to_active"
    ]
    assert len(events) == 1
    assert events[0]["reason"] == "backlog_reopened"
    assert events[0]["open_tasks"] == 1


def test_done_meta_reflect_pending_blocks_resume(
    tmp_path: Path, monkeypatch
) -> None:
    """Enforcement loops outrank reopen. A done project with
    meta_reflect_pending=True should NOT resume — the meta_reflect
    contract pins state.json shape until the reflection completes."""
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    p = _project(tmp_path)
    (p / "backlog.md").write_text(
        "- [ ] [implement] [P1] vec_new — operator-added\n",
        encoding="utf-8",
    )
    s = state.State.fresh(p.name)
    s.phase = "done"
    s.meta_reflect_pending = True
    state.write(p, s)

    assert recovery.maybe_resume_done(p) is False
    assert state.read(p).phase == "done"
    events = [
        e
        for e in _read_aggregate(user_home)
        if e.get("event") == "phase_done_resume_skipped"
    ]
    assert events and events[-1]["reason"] == "meta_reflect_in_progress"


def test_done_knowledge_update_pending_blocks_resume(
    tmp_path: Path, monkeypatch
) -> None:
    """knowledge_update_pending similarly outranks reopen — the
    knowledge.md sentinel must arm and disarm cleanly before any
    state-shape mutation."""
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    p = _project(tmp_path)
    (p / "backlog.md").write_text(
        "- [ ] [implement] [P1] vec_new — operator-added\n",
        encoding="utf-8",
    )
    s = state.State.fresh(p.name)
    s.phase = "done"
    s.knowledge_update_pending = True
    state.write(p, s)

    assert recovery.maybe_resume_done(p) is False
    assert state.read(p).phase == "done"
    events = [
        e
        for e in _read_aggregate(user_home)
        if e.get("event") == "phase_done_resume_skipped"
    ]
    assert events and events[-1]["reason"] == "knowledge_update_in_progress"


def test_done_research_plan_required_blocks_resume(
    tmp_path: Path, monkeypatch
) -> None:
    """research_plan_required also outranks — research mode is in flight."""
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    p = _project(tmp_path)
    (p / "backlog.md").write_text(
        "- [ ] [implement] [P1] vec_new — operator-added\n",
        encoding="utf-8",
    )
    s = state.State.fresh(p.name)
    s.phase = "done"
    s.research_plan_required = True
    state.write(p, s)

    assert recovery.maybe_resume_done(p) is False
    assert state.read(p).phase == "done"


def test_second_sweep_on_active_project_does_not_re_trigger(
    tmp_path: Path, monkeypatch
) -> None:
    """After the first sweep flips done → active, a second sweep iterating
    past the same project must NOT log a skip event (the project isn't
    `phase=done` anymore — the boring not_done path is silent)."""
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    p = _project(tmp_path)
    (p / "backlog.md").write_text(
        "- [ ] [implement] [P1] vec_new — operator-added\n",
        encoding="utf-8",
    )
    s = state.State.fresh(p.name)
    s.phase = "done"
    state.write(p, s)

    assert recovery.maybe_resume_done(p) is True
    assert state.read(p).phase == "active"

    # Second sweep iteration — should not flip anything, should not log
    # a skip event because the project is no longer phase=done.
    pre_count = len(_read_aggregate(user_home))
    assert recovery.maybe_resume_done(p) is False
    post_count = len(_read_aggregate(user_home))
    assert post_count == pre_count, "no extra event for now-active project"


def test_sweep_done_projects_aggregate_count(
    tmp_path: Path, monkeypatch
) -> None:
    """sweep_done_projects iterates the project list and returns a
    revival count. Mixed scenarios: one done+reopen, one done+still
    complete, one already active."""
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))

    # done + reopened
    p1 = _project(tmp_path, "p1")
    (p1 / "backlog.md").write_text("- [ ] [implement] [P1] vec_a\n")
    s1 = state.State.fresh(p1.name)
    s1.phase = "done"
    state.write(p1, s1)

    # done + still complete
    p2 = _project(tmp_path, "p2")
    (p2 / "backlog.md").write_text("- [x] [implement] [P1] vec_b\n## Done\n")
    s2 = state.State.fresh(p2.name)
    s2.phase = "done"
    s2.prd_complete = True
    state.write(p2, s2)

    # active — sweep iterates past silently
    p3 = _project(tmp_path, "p3")
    (p3 / "backlog.md").write_text("- [ ] [implement] [P1] vec_c\n")
    s3 = state.State.fresh(p3.name)
    s3.phase = "active"
    state.write(p3, s3)

    revived = recovery.sweep_done_projects([p1, p2, p3])
    assert revived == 1
    assert state.read(p1).phase == "active"
    assert state.read(p2).phase == "done"
    assert state.read(p3).phase == "active"
