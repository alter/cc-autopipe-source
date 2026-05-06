"""Unit tests for v1.3.3 state additions.

Covers:
- Detached.pipeline_log_path / stale_after_sec round-trip (Group L)
- last_verdict_event_at / last_verdict_task_id round-trip (Group N)
- last_detach_resume_reason round-trip (Group L)
- set_detached() clears verdict fields (Group N "fire once per verdict")
- Schema migration: v4 state without new fields reads cleanly with
  defaults; subsequent write persists schema_version=5
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src" / "lib"))

import state  # noqa: E402


def _seed_project(tmp_path: Path) -> Path:
    p = tmp_path / "demo"
    (p / ".cc-autopipe" / "memory").mkdir(parents=True, exist_ok=True)
    return p


def test_detached_round_trip_with_liveness_fields(tmp_path: Path) -> None:
    project = _seed_project(tmp_path)
    state.set_detached(
        project,
        reason="train",
        check_cmd="test -f done",
        check_every_sec=600,
        max_wait_sec=14400,
        pipeline_log_path="/abs/pipeline.log",
        stale_after_sec=1800,
    )
    s = state.read(project)
    assert s.detached is not None
    assert s.detached.pipeline_log_path == "/abs/pipeline.log"
    assert s.detached.stale_after_sec == 1800

    # Persisted JSON carries them through.
    raw = json.loads((project / ".cc-autopipe" / "state.json").read_text())
    assert raw["detached"]["pipeline_log_path"] == "/abs/pipeline.log"
    assert raw["detached"]["stale_after_sec"] == 1800


def test_detached_defaults_none_when_liveness_omitted(tmp_path: Path) -> None:
    project = _seed_project(tmp_path)
    state.set_detached(
        project,
        reason="train",
        check_cmd="true",
        check_every_sec=600,
        max_wait_sec=14400,
    )
    s = state.read(project)
    assert s.detached is not None
    assert s.detached.pipeline_log_path is None
    assert s.detached.stale_after_sec is None


def test_set_detached_clears_verdict_fields(tmp_path: Path) -> None:
    """v1.3.3 Group N: gate fires once per verdict. After a successful
    detach, last_verdict_event_at / last_verdict_task_id are cleared so
    the next detach is unblocked."""
    project = _seed_project(tmp_path)
    s = state.State.fresh("demo")
    s.last_verdict_event_at = "2026-05-06T10:00:00Z"
    s.last_verdict_task_id = "vec_meta"
    state.write(project, s)

    state.set_detached(
        project,
        reason="next",
        check_cmd="true",
        check_every_sec=600,
        max_wait_sec=14400,
    )
    s2 = state.read(project)
    assert s2.last_verdict_event_at is None
    assert s2.last_verdict_task_id is None
    assert s2.phase == "detached"


def test_verdict_fields_round_trip(tmp_path: Path) -> None:
    project = _seed_project(tmp_path)
    s = state.State.fresh("demo")
    s.last_verdict_event_at = "2026-05-06T11:00:00Z"
    s.last_verdict_task_id = "vec_rl"
    state.write(project, s)
    s2 = state.read(project)
    assert s2.last_verdict_event_at == "2026-05-06T11:00:00Z"
    assert s2.last_verdict_task_id == "vec_rl"


def test_last_detach_resume_reason_round_trip(tmp_path: Path) -> None:
    project = _seed_project(tmp_path)
    s = state.State.fresh("demo")
    s.last_detach_resume_reason = "pipeline_stale"
    state.write(project, s)
    s2 = state.read(project)
    assert s2.last_detach_resume_reason == "pipeline_stale"


def test_v4_state_migrates_to_v5_on_read(tmp_path: Path) -> None:
    """A v1.3.2 state.json (schema_version=4, no liveness/verdict fields)
    must read cleanly with defaults, then persist as schema_version=5
    on the next write. Roman's existing detached projects must not break."""
    project = _seed_project(tmp_path)
    legacy = {
        "schema_version": 4,
        "name": "demo",
        "phase": "detached",
        "iteration": 5,
        "session_id": None,
        "last_score": None,
        "last_passed": None,
        "prd_complete": False,
        "consecutive_failures": 0,
        "last_cycle_started_at": None,
        "last_progress_at": "2026-05-05T20:00:00Z",
        "threshold": 0.85,
        "paused": None,
        "detached": {
            "reason": "v1.3.2 detached state",
            "started_at": "2026-05-05T19:00:00Z",
            "check_cmd": "test -f done",
            "check_every_sec": 600,
            "max_wait_sec": 14400,
            "last_check_at": "2026-05-05T19:30:00Z",
            "checks_count": 3,
        },
        "current_phase": 1,
        "phases_completed": [],
    }
    (project / ".cc-autopipe" / "state.json").write_text(json.dumps(legacy))
    s = state.read(project)

    # Migrated cleanly with defaults.
    assert s.schema_version == state.SCHEMA_VERSION == 5
    assert s.detached is not None
    assert s.detached.pipeline_log_path is None
    assert s.detached.stale_after_sec is None
    assert s.last_verdict_event_at is None
    assert s.last_verdict_task_id is None
    assert s.last_detach_resume_reason is None
    # Existing detached fields preserved.
    assert s.detached.reason == "v1.3.2 detached state"
    assert s.detached.checks_count == 3

    # Persists schema_version=5 on write.
    state.write(project, s)
    raw = json.loads((project / ".cc-autopipe" / "state.json").read_text())
    assert raw["schema_version"] == 5
    assert raw["detached"]["pipeline_log_path"] is None
    assert raw["detached"]["stale_after_sec"] is None
