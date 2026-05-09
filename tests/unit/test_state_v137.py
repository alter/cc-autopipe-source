"""Unit tests for v1.3.7 STUCK-WITH-PROGRESS state addition.

Adds ONE field to State per PROMPT_v1.3.7-hotfix.md §"Don't" (single
field, well-bounded):
  - cycle_backlog_x_count_at_start: Optional[int]  (default None)

Schema version stays at 6 — additive on top of v1.3.4's bump. v6 state
files written before v1.3.7 read cleanly via dataclass defaults; the
next write persists the new key as null until cycle_start populates it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src" / "lib"))

import state  # noqa: E402


def _init_project(tmp_path: Path) -> Path:
    project = tmp_path / "proj"
    (project / ".cc-autopipe").mkdir(parents=True)
    return project


def test_schema_version_unchanged_at_6() -> None:
    """v1.3.7 is purely additive on the v1.3.4 schema bump."""
    assert state.SCHEMA_VERSION == 6


def test_fresh_state_defaults_cycle_backlog_count_to_none() -> None:
    s = state.State.fresh("demo")
    assert s.cycle_backlog_x_count_at_start is None


def test_cycle_backlog_count_round_trips(tmp_path: Path) -> None:
    project = _init_project(tmp_path)
    s = state.State.fresh("demo")
    s.cycle_backlog_x_count_at_start = 12
    state.write(project, s)
    s2 = state.read(project)
    assert s2.cycle_backlog_x_count_at_start == 12


def test_pre_v137_v6_state_file_migrates_with_default(tmp_path: Path) -> None:
    """A v1.3.4-era state.json (schema_version=6, no
    cycle_backlog_x_count_at_start key) reads cleanly and defaults the
    new field to None. Round-tripping persists the key explicitly so
    later reads see it materialised."""
    project = _init_project(tmp_path)
    state_path = project / ".cc-autopipe" / "state.json"
    legacy = {
        "schema_version": 6,
        "name": "proj",
        "phase": "active",
        "iteration": 11,
        "session_id": None,
        "last_score": 0.9,
        "last_passed": True,
        "prd_complete": False,
        "consecutive_failures": 0,
        "last_cycle_started_at": "2026-05-08T09:00:00Z",
        "last_progress_at": "2026-05-08T09:00:00Z",
        "threshold": 0.85,
        "paused": None,
        "detached": None,
        "consecutive_transient_failures": 0,
        "last_transient_at": None,
    }
    state_path.write_text(json.dumps(legacy), encoding="utf-8")

    s = state.read(project)
    assert s.schema_version == 6
    assert s.cycle_backlog_x_count_at_start is None
    assert s.iteration == 11

    state.write(project, s)
    raw = json.loads(state_path.read_text(encoding="utf-8"))
    assert raw["schema_version"] == 6
    assert raw["cycle_backlog_x_count_at_start"] is None
