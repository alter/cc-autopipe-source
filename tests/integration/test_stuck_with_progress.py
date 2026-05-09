"""Integration tests for v1.3.7 STUCK-WITH-PROGRESS.

Engine should NOT mark a project `stuck_failed` when the filesystem
shows in-cycle progress, even if the engine-internal `last_activity_at`
is stale. Real driver: AI-trade Phase 2 v2.0 iteration=24 closed 4
tasks in a single 5-min cycle but verify.sh rc=1 + 2-day-stale
timestamp pushed phase to failed. v1.3.7 gates the fail by
`_check_in_cycle_progress`:

    new_promotion_files     CAND_*_PROMOTION.md mtime ≥ cycle_start
    backlog_x_delta          `- [x]` count grew vs cycle-start snapshot
    current_task_stages_grew CURRENT_TASK.md mtime ≥ cycle_start AND
                             post-cycle stages_completed non-empty

When ANY of the three fires, the engine refreshes `last_activity_at`
and emits `stuck_check_skipped_progress_detected`; phase stays active.
When all three are zero, the legacy `stuck_failed` path runs.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
LIB = SRC / "lib"
for p in (str(SRC), str(LIB)):
    if p not in sys.path:
        sys.path.insert(0, p)

import state  # noqa: E402

cycle_mod = importlib.import_module("orchestrator.cycle")


# ---------------------------------------------------------------------------
# _check_in_cycle_progress (helper-level coverage of each evidence source)
# ---------------------------------------------------------------------------


def _project(tmp_path: Path) -> Path:
    p = tmp_path / "proj"
    (p / ".cc-autopipe" / "memory").mkdir(parents=True, exist_ok=True)
    return p


def test_check_progress_no_evidence(tmp_path: Path) -> None:
    project = _project(tmp_path)
    s = state.State.fresh(project.name)
    out = cycle_mod._check_in_cycle_progress(project, time.time() - 60, s)
    assert out["any_progress"] is False
    assert out["new_promotion_files"] == 0
    assert out["backlog_x_delta"] == 0
    assert out["current_task_stages_grew"] is False


def test_check_progress_new_promotion_file_detected(tmp_path: Path) -> None:
    project = _project(tmp_path)
    (project / "data" / "debug").mkdir(parents=True)
    (project / "data" / "debug" / "CAND_demo_PROMOTION.md").write_text("x")
    s = state.State.fresh(project.name)
    out = cycle_mod._check_in_cycle_progress(project, time.time() - 30, s)
    assert out["new_promotion_files"] == 1
    assert out["any_progress"] is True


def test_check_progress_stale_promotion_file_ignored(tmp_path: Path) -> None:
    """A PROMOTION.md from a prior cycle (mtime < cycle_start) must not
    trigger `any_progress` — we'd never escape stale state otherwise."""
    project = _project(tmp_path)
    (project / "data" / "debug").mkdir(parents=True)
    p = project / "data" / "debug" / "CAND_demo_PROMOTION.md"
    p.write_text("x")
    # Backdate mtime so it predates the synthetic cycle_start.
    old = time.time() - 3600
    os.utime(p, (old, old))
    s = state.State.fresh(project.name)
    out = cycle_mod._check_in_cycle_progress(project, time.time() - 60, s)
    assert out["new_promotion_files"] == 0
    assert out["any_progress"] is False


def test_check_progress_backlog_x_delta(tmp_path: Path) -> None:
    project = _project(tmp_path)
    (project / "backlog.md").write_text(
        "- [x] [implement] [P1] task_a — done\n"
        "- [x] [implement] [P1] task_b — done\n"
        "- [x] [implement] [P1] task_c — done\n"
    )
    s = state.State.fresh(project.name)
    s.cycle_backlog_x_count_at_start = 1  # snapshot before any work
    out = cycle_mod._check_in_cycle_progress(project, time.time() - 60, s)
    assert out["backlog_x_delta"] == 2
    assert out["any_progress"] is True


def test_check_progress_current_task_stages_grew(tmp_path: Path) -> None:
    project = _project(tmp_path)
    ct = project / ".cc-autopipe" / "CURRENT_TASK.md"
    ct.write_text("id: vec_demo\nstages_completed: stage_a\n")
    s = state.State.fresh(project.name)
    s.current_task = state.CurrentTask(
        id="vec_demo", stages_completed=["stage_a"]
    )
    out = cycle_mod._check_in_cycle_progress(project, time.time() - 60, s)
    assert out["current_task_stages_grew"] is True
    assert out["any_progress"] is True


def test_check_progress_all_three_signals(tmp_path: Path) -> None:
    project = _project(tmp_path)
    (project / "data" / "debug").mkdir(parents=True)
    (project / "data" / "debug" / "CAND_demo_PROMOTION.md").write_text("x")
    (project / "backlog.md").write_text(
        "- [x] [implement] [P1] task_a — done\n"
    )
    (project / ".cc-autopipe" / "CURRENT_TASK.md").write_text(
        "id: vec_demo\nstages_completed: stage_a\n"
    )
    s = state.State.fresh(project.name)
    s.cycle_backlog_x_count_at_start = 0
    s.current_task = state.CurrentTask(
        id="vec_demo", stages_completed=["stage_a"]
    )
    out = cycle_mod._check_in_cycle_progress(project, time.time() - 60, s)
    assert out["new_promotion_files"] == 1
    assert out["backlog_x_delta"] == 1
    assert out["current_task_stages_grew"] is True
    assert out["any_progress"] is True


# ---------------------------------------------------------------------------
# process_project end-to-end stuck-gate behaviour
# ---------------------------------------------------------------------------


def _bootstrap_stuck_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Create a project whose `last_activity_at` is 65 min stale so
    `evaluate_stuck` returns 'fail' on the next cycle. Disables quota
    preflight + network probe so the cycle reaches the stuck-check
    block deterministically."""
    user_home = tmp_path / "uhome"
    user_home.mkdir(parents=True)
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    monkeypatch.setenv("CC_AUTOPIPE_QUOTA_DISABLED", "1")
    monkeypatch.setenv("CC_AUTOPIPE_NETWORK_PROBE_DISABLED", "1")

    project = _project(tmp_path)
    s = state.State.fresh(project.name)
    s.phase = "active"
    s.iteration = 23
    stale = datetime.now(timezone.utc) - timedelta(minutes=65)
    s.last_activity_at = stale.strftime("%Y-%m-%dT%H:%M:%SZ")
    state.write(project, s)
    return project


def _read_aggregate(user_home: Path) -> list[dict]:
    p = user_home / "log" / "aggregate.jsonl"
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]


def _patch_run_claude(
    monkeypatch: pytest.MonkeyPatch,
    side_effect=None,
    rc: int = 0,
):
    """Replace cycle._run_claude with a stub that runs `side_effect`
    (mutating the project filesystem) and returns rc/empty-streams.
    The orchestrator's own bookkeeping (state mutation, event emission)
    runs as normal around the stub."""

    def _fake(project_path: Path, cmd, timeout):  # noqa: ANN001
        if side_effect is not None:
            side_effect(project_path)
        return rc, "", ""

    monkeypatch.setattr(cycle_mod, "_run_claude", _fake)


def _disable_activity_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub `activity_lib.detect_activity` to is_active=False so the
    legacy v1.3 activity probe doesn't pre-empt the v1.3.7 progress
    gate. Reproduces the real AI-trade scenario where the probe's
    5000-file walk budget skipped fresh artefacts deep in an
    artefact-heavy `data/` tree, leaving stuck-detection to consult
    only the stale state-write timestamp."""

    def _fake(*_a, **_kw):  # noqa: ANN001
        return {
            "has_running_processes": False,
            "recent_artifact_changes": [],
            "stage_changed": False,
            "last_artifact_mtime": None,
            "process_pids": [],
            "is_active": False,
        }

    monkeypatch.setattr(
        cycle_mod.activity_lib, "detect_activity", _fake
    )


def test_stuck_fail_with_no_progress_emits_stuck_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Baseline: stuck timestamp + no filesystem evidence → legacy
    `stuck_failed` path runs (phase=failed, HUMAN_NEEDED.md, TG)."""
    project = _bootstrap_stuck_project(tmp_path, monkeypatch)
    user_home = Path(os.environ["CC_AUTOPIPE_USER_HOME"])

    _disable_activity_probe(monkeypatch)
    _patch_run_claude(monkeypatch, side_effect=None, rc=1)
    monkeypatch.setattr(cycle_mod, "_notify_tg", lambda *a, **kw: None)

    cycle_mod.process_project(project)

    s = state.read(project)
    assert s.phase == "failed"
    events = [e["event"] for e in _read_aggregate(user_home)]
    assert "stuck_failed" in events
    assert "stuck_check_skipped_progress_detected" not in events


def test_stuck_with_new_promotion_file_skips_fail_refreshes_activity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stuck timestamp + 1 new PROMOTION.md → skipped, phase=active,
    last_activity_at advanced past the stale snapshot."""
    project = _bootstrap_stuck_project(tmp_path, monkeypatch)
    user_home = Path(os.environ["CC_AUTOPIPE_USER_HOME"])
    pre_activity = state.read(project).last_activity_at

    def _claude_writes_promotion(p: Path) -> None:
        debug = p / "data" / "debug"
        debug.mkdir(parents=True, exist_ok=True)
        (debug / "CAND_demo_PROMOTION.md").write_text(
            "## Acceptance\n\n✅ criteria met.\n"
        )

    _disable_activity_probe(monkeypatch)
    _patch_run_claude(
        monkeypatch, side_effect=_claude_writes_promotion, rc=1
    )
    monkeypatch.setattr(cycle_mod, "_notify_tg", lambda *a, **kw: None)

    cycle_mod.process_project(project)

    s = state.read(project)
    assert s.phase == "active"
    assert s.last_activity_at != pre_activity, "must refresh last_activity_at"

    events = _read_aggregate(user_home)
    skip_events = [
        e
        for e in events
        if e["event"] == "stuck_check_skipped_progress_detected"
    ]
    assert len(skip_events) == 1
    assert skip_events[0]["new_promotions"] == 1
    assert skip_events[0]["backlog_x_delta"] == 0
    assert skip_events[0]["current_task_grew"] is False
    assert "stuck_failed" not in {e["event"] for e in events}


def test_stuck_with_backlog_x_delta_skips_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stuck timestamp + backlog [x] grew by 1 → skipped, phase=active.
    Cycle starts with 0 [x] in backlog; mock-claude appends one."""
    project = _bootstrap_stuck_project(tmp_path, monkeypatch)
    (project / "backlog.md").write_text(
        "- [ ] [implement] [P1] vec_a — pending\n"
    )
    user_home = Path(os.environ["CC_AUTOPIPE_USER_HOME"])

    def _claude_closes_task(p: Path) -> None:
        bl = p / "backlog.md"
        bl.write_text(
            "- [x] [implement] [P1] vec_a — done\n"
        )

    _disable_activity_probe(monkeypatch)
    _patch_run_claude(monkeypatch, side_effect=_claude_closes_task, rc=1)
    monkeypatch.setattr(cycle_mod, "_notify_tg", lambda *a, **kw: None)

    cycle_mod.process_project(project)

    s = state.read(project)
    assert s.phase == "active"

    events = _read_aggregate(user_home)
    skip_events = [
        e
        for e in events
        if e["event"] == "stuck_check_skipped_progress_detected"
    ]
    assert len(skip_events) == 1
    assert skip_events[0]["backlog_x_delta"] == 1
    assert "stuck_failed" not in {e["event"] for e in events}


def test_stuck_with_current_task_stages_grew_skips_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stuck timestamp + CURRENT_TASK.md mtime advanced AND
    stages_completed non-empty → skipped, phase=active. Mocks the stop
    hook by mutating CURRENT_TASK.md in `_run_claude` and writing the
    matching state.current_task post-run."""
    project = _bootstrap_stuck_project(tmp_path, monkeypatch)
    user_home = Path(os.environ["CC_AUTOPIPE_USER_HOME"])

    def _claude_advances_stage(p: Path) -> None:
        ct = p / ".cc-autopipe" / "CURRENT_TASK.md"
        ct.write_text(
            "id: vec_long_demo\nstage: training\n"
            "stages_completed: data_load, training\n"
        )
        # Stop hook would normally sync this into state.json; emulate
        # that here by writing state.current_task explicitly.
        s2 = state.read(p)
        s2.current_task = state.CurrentTask(
            id="vec_long_demo",
            stage="training",
            stages_completed=["data_load", "training"],
        )
        state.write(p, s2)

    _disable_activity_probe(monkeypatch)
    _patch_run_claude(
        monkeypatch, side_effect=_claude_advances_stage, rc=1
    )
    monkeypatch.setattr(cycle_mod, "_notify_tg", lambda *a, **kw: None)

    cycle_mod.process_project(project)

    s = state.read(project)
    assert s.phase == "active"

    events = _read_aggregate(user_home)
    skip_events = [
        e
        for e in events
        if e["event"] == "stuck_check_skipped_progress_detected"
    ]
    assert len(skip_events) == 1
    assert skip_events[0]["current_task_grew"] is True
    assert "stuck_failed" not in {e["event"] for e in events}


def test_stuck_with_all_three_signals_emits_one_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All three evidence sources fire in the same cycle → one
    `stuck_check_skipped_progress_detected` event with all three fields
    populated. Guards against a per-evidence-type log flood."""
    project = _bootstrap_stuck_project(tmp_path, monkeypatch)
    (project / "backlog.md").write_text(
        "- [ ] [implement] [P1] vec_a — pending\n"
    )
    user_home = Path(os.environ["CC_AUTOPIPE_USER_HOME"])

    def _claude_does_everything(p: Path) -> None:
        debug = p / "data" / "debug"
        debug.mkdir(parents=True, exist_ok=True)
        (debug / "CAND_demo_PROMOTION.md").write_text(
            "## Acceptance\n\n✅ criteria met.\n"
        )
        (p / "backlog.md").write_text(
            "- [x] [implement] [P1] vec_a — done\n"
        )
        ct = p / ".cc-autopipe" / "CURRENT_TASK.md"
        ct.write_text(
            "id: vec_a\nstages_completed: data_load, training\n"
        )
        s2 = state.read(p)
        s2.current_task = state.CurrentTask(
            id="vec_a", stages_completed=["data_load", "training"]
        )
        state.write(p, s2)

    _disable_activity_probe(monkeypatch)
    _patch_run_claude(
        monkeypatch, side_effect=_claude_does_everything, rc=1
    )
    monkeypatch.setattr(cycle_mod, "_notify_tg", lambda *a, **kw: None)

    cycle_mod.process_project(project)

    s = state.read(project)
    assert s.phase == "active"

    events = _read_aggregate(user_home)
    skip_events = [
        e
        for e in events
        if e["event"] == "stuck_check_skipped_progress_detected"
    ]
    assert len(skip_events) == 1, "single combined event, not three"
    e = skip_events[0]
    assert e["new_promotions"] == 1
    assert e["backlog_x_delta"] == 1
    assert e["current_task_grew"] is True
    assert "stuck_failed" not in {ev["event"] for ev in events}
