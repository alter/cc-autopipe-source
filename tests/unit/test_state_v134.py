"""Unit tests for v1.3.4 state additions (Group R2).

Adds two fields to State:
  - consecutive_transient_failures: int
  - last_transient_at: Optional[str]

SCHEMA_VERSION bumped 5 → 6. Existing v5 state files migrate via the
dataclass-defaults path already used for every prior bump.
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


def test_schema_version_is_at_least_6() -> None:
    # v1.3.4 introduced schema 6; later bumps (e.g. v1.3.12 → 7) keep the
    # transient fields intact, so this test pins the v1.3.4 invariant
    # without locking the version to a specific number.
    assert state.SCHEMA_VERSION >= 6


def test_fresh_state_defaults_transient_fields() -> None:
    s = state.State.fresh("demo")
    assert s.consecutive_transient_failures == 0
    assert s.last_transient_at is None


def test_transient_fields_round_trip(tmp_path: Path) -> None:
    project = _init_project(tmp_path)
    s = state.State.fresh("demo")
    s.consecutive_transient_failures = 3
    s.last_transient_at = "2026-05-06T10:00:00Z"
    state.write(project, s)
    s2 = state.read(project)
    assert s2.consecutive_transient_failures == 3
    assert s2.last_transient_at == "2026-05-06T10:00:00Z"


def test_v5_state_file_migrates_with_defaults(tmp_path: Path) -> None:
    """v1.3.3 produced schema_version=5 with the v1.3.3 fields but no
    transient counter. The engine must read it cleanly, supplying
    defaults; the next write persists the current SCHEMA_VERSION
    (>= 6 — v1.3.12 bumped to 7)."""
    project = _init_project(tmp_path)
    state_path = project / ".cc-autopipe" / "state.json"
    legacy = {
        "schema_version": 5,
        "name": "proj",
        "phase": "active",
        "iteration": 7,
        "session_id": None,
        "last_score": 0.9,
        "last_passed": True,
        "prd_complete": False,
        "consecutive_failures": 0,
        "last_cycle_started_at": "2026-05-06T09:00:00Z",
        "last_progress_at": "2026-05-06T09:00:00Z",
        "threshold": 0.85,
        "paused": None,
        "detached": None,
        "last_verdict_event_at": "2026-05-05T12:00:00Z",
        "last_verdict_task_id": "T-001",
    }
    state_path.write_text(json.dumps(legacy), encoding="utf-8")

    s = state.read(project)
    # Migrated up: schema bump on read, defaults supplied for new fields.
    assert s.schema_version == state.SCHEMA_VERSION
    assert s.consecutive_transient_failures == 0
    assert s.last_transient_at is None
    # Pre-existing fields preserved verbatim.
    assert s.iteration == 7
    assert s.last_verdict_task_id == "T-001"

    # Persist and re-read to verify the current schema is now on disk.
    state.write(project, s)
    raw = json.loads(state_path.read_text(encoding="utf-8"))
    assert raw["schema_version"] == state.SCHEMA_VERSION
    assert raw["consecutive_transient_failures"] == 0
    assert raw["last_transient_at"] is None
