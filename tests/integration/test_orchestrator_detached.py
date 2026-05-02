"""Integration tests for orchestrator DETACHED handling (Stage H).

Covers SPEC-v1.md §2.1 acceptance:
- DETACHED → ACTIVE on check_cmd success (rc=0)
- DETACHED → FAILED on max_wait_sec timeout (+ HUMAN_NEEDED.md + TG)
- check_cmd skipped when check_every_sec not yet elapsed
- check_cmd timeout (>30s) treated as failed check, not project failure
- Phase=detached with detached=None defensively recovers to active
- Orchestrator does NOT hold the slot for other projects while one
  project is DETACHED-and-waiting

Tests use CC_AUTOPIPE_CLAUDE_BIN=/usr/bin/true so when an ACTIVE cycle
runs, claude exits cleanly without firing hooks. These tests exercise
the orchestrator's state machine, not the claude/hook integration.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
ORCHESTRATOR = SRC / "orchestrator"


def _now_iso(offset_sec: int = 0) -> str:
    t = datetime.now(timezone.utc) + timedelta(seconds=offset_sec)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _seed_detached_project(
    base: Path,
    name: str,
    *,
    started_offset_sec: int = 0,
    check_cmd: str = "true",
    check_every_sec: int = 0,
    max_wait_sec: int = 14400,
    last_check_offset_sec: int | None = None,
    checks_count: int = 0,
) -> Path:
    p = base / name
    (p / ".cc-autopipe" / "memory").mkdir(parents=True, exist_ok=True)
    state_doc: dict[str, object] = {
        "schema_version": 2,
        "name": name,
        "phase": "detached",
        "iteration": 0,
        "session_id": None,
        "last_score": None,
        "last_passed": None,
        "prd_complete": False,
        "consecutive_failures": 0,
        "last_cycle_started_at": None,
        "last_progress_at": None,
        "threshold": 0.85,
        "paused": None,
        "detached": {
            "reason": "test detached op",
            "started_at": _now_iso(started_offset_sec),
            "check_cmd": check_cmd,
            "check_every_sec": check_every_sec,
            "max_wait_sec": max_wait_sec,
            "last_check_at": (
                _now_iso(last_check_offset_sec)
                if last_check_offset_sec is not None
                else None
            ),
            "checks_count": checks_count,
        },
        "current_phase": 1,
        "phases_completed": [],
    }
    (p / ".cc-autopipe" / "state.json").write_text(json.dumps(state_doc))
    return p


def _seed_active_project(base: Path, name: str) -> Path:
    p = base / name
    (p / ".cc-autopipe" / "memory").mkdir(parents=True, exist_ok=True)
    state_doc = {
        "schema_version": 2,
        "name": name,
        "phase": "active",
        "iteration": 0,
        "session_id": None,
        "last_score": None,
        "last_passed": None,
        "prd_complete": False,
        "consecutive_failures": 0,
        "last_cycle_started_at": None,
        "last_progress_at": None,
        "threshold": 0.85,
        "paused": None,
        "detached": None,
        "current_phase": 1,
        "phases_completed": [],
    }
    (p / ".cc-autopipe" / "state.json").write_text(json.dumps(state_doc))
    return p


def _write_projects_list(user_home: Path, projects: list[Path]) -> None:
    user_home.mkdir(parents=True, exist_ok=True)
    (user_home / "projects.list").write_text(
        "\n".join(str(p.resolve()) for p in projects) + "\n"
    )


def _run_orch(
    user_home: Path,
    *,
    max_loops: int = 1,
    timeout: float = 15.0,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["CC_AUTOPIPE_HOME"] = str(SRC)
    env["CC_AUTOPIPE_USER_HOME"] = str(user_home)
    env["CC_AUTOPIPE_COOLDOWN_SEC"] = "0"
    env["CC_AUTOPIPE_IDLE_SLEEP_SEC"] = "0"
    env["CC_AUTOPIPE_MAX_LOOPS"] = str(max_loops)
    env["CC_AUTOPIPE_CLAUDE_BIN"] = "/usr/bin/true"
    env["CC_AUTOPIPE_QUOTA_DISABLED"] = "1"
    return subprocess.run(
        [sys.executable, str(ORCHESTRATOR)],
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )


def _read_state(project: Path) -> dict[str, object]:
    return json.loads((project / ".cc-autopipe" / "state.json").read_text())


def _read_aggregate(user_home: Path) -> list[dict[str, object]]:
    log = user_home / "log" / "aggregate.jsonl"
    if not log.exists():
        return []
    return [json.loads(ln) for ln in log.read_text().splitlines() if ln.strip()]


@pytest.fixture
def env_paths(tmp_path: Path) -> tuple[Path, Path]:
    user_home = tmp_path / "uhome"
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    return user_home, projects_root


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_detached_check_cmd_success_transitions_to_active(
    env_paths: tuple[Path, Path],
) -> None:
    user_home, root = env_paths
    p = _seed_detached_project(
        root,
        "alpha",
        check_cmd="true",  # always succeeds
        check_every_sec=0,  # poll on first pass
    )
    _write_projects_list(user_home, [p])
    cp = _run_orch(user_home, max_loops=1)
    assert cp.returncode == 0, cp.stderr

    s = _read_state(p)
    # Transitioned to active and ran the actual cycle (iteration bumped).
    assert s["phase"] == "active"
    assert s["detached"] is None
    assert s["iteration"] == 1

    events = _read_aggregate(user_home)
    assert any(e.get("event") == "detach_resumed" for e in events), events
    assert any(e.get("event") == "cycle_start" for e in events), events


def test_detached_check_cmd_failure_stays_detached(
    env_paths: tuple[Path, Path],
) -> None:
    user_home, root = env_paths
    p = _seed_detached_project(
        root,
        "alpha",
        check_cmd="false",  # always fails
        check_every_sec=0,
    )
    _write_projects_list(user_home, [p])
    _run_orch(user_home, max_loops=1)

    s = _read_state(p)
    assert s["phase"] == "detached"
    assert s["iteration"] == 0
    assert isinstance(s["detached"], dict)
    detached = s["detached"]
    assert isinstance(detached, dict)
    assert detached["checks_count"] == 1
    assert detached["last_check_at"] is not None

    events = _read_aggregate(user_home)
    assert any(e.get("event") == "detach_check_failed" for e in events), events
    assert not any(e.get("event") == "detach_resumed" for e in events)


# ---------------------------------------------------------------------------
# Throttling: check_every_sec gates polling
# ---------------------------------------------------------------------------


def test_detached_skips_check_when_interval_not_elapsed(
    env_paths: tuple[Path, Path],
) -> None:
    user_home, root = env_paths
    p = _seed_detached_project(
        root,
        "alpha",
        check_cmd="true",
        check_every_sec=3600,  # 1h cadence — far longer than test wall clock
    )
    _write_projects_list(user_home, [p])
    _run_orch(user_home, max_loops=1)

    s = _read_state(p)
    assert s["phase"] == "detached"
    detached = s["detached"]
    assert isinstance(detached, dict)
    # No check should have run.
    assert detached["checks_count"] == 0
    assert detached["last_check_at"] is None


# ---------------------------------------------------------------------------
# max_wait_sec timeout
# ---------------------------------------------------------------------------


def test_detached_max_wait_timeout_transitions_to_failed(
    env_paths: tuple[Path, Path],
) -> None:
    user_home, root = env_paths
    # Seed started_at far in the past so elapsed > max_wait immediately.
    p = _seed_detached_project(
        root,
        "alpha",
        started_offset_sec=-7200,  # 2h ago
        check_cmd="false",
        check_every_sec=0,
        max_wait_sec=3600,  # 1h cap → timed out
    )
    _write_projects_list(user_home, [p])
    _run_orch(user_home, max_loops=1)

    s = _read_state(p)
    assert s["phase"] == "failed"

    # HUMAN_NEEDED.md should have been written.
    hn = p / ".cc-autopipe" / "HUMAN_NEEDED.md"
    assert hn.exists()
    body = hn.read_text()
    assert "DETACHED" in body
    assert "test detached op" in body  # echoes reason

    events = _read_aggregate(user_home)
    timeouts = [e for e in events if e.get("event") == "detached_timeout"]
    assert len(timeouts) == 1
    assert timeouts[0]["reason"] == "test detached op"


# ---------------------------------------------------------------------------
# Slot release: detached project shouldn't starve active siblings
# ---------------------------------------------------------------------------


def test_detached_project_does_not_block_active_sibling(
    env_paths: tuple[Path, Path],
) -> None:
    user_home, root = env_paths
    detached = _seed_detached_project(
        root,
        "long-op",
        check_cmd="false",
        check_every_sec=3600,  # never checks during the test
    )
    active = _seed_active_project(root, "fast-job")
    _write_projects_list(user_home, [detached, active])
    _run_orch(user_home, max_loops=1)

    s_d = _read_state(detached)
    s_a = _read_state(active)
    # The detached project stayed put.
    assert s_d["phase"] == "detached"
    assert s_d["iteration"] == 0
    # The active project ran a normal cycle in the same outer pass.
    assert s_a["phase"] == "active"
    assert s_a["iteration"] == 1


# ---------------------------------------------------------------------------
# Defensive corruption recovery
# ---------------------------------------------------------------------------


def test_detached_phase_with_null_payload_recovers_to_active(
    env_paths: tuple[Path, Path],
) -> None:
    user_home, root = env_paths
    p = root / "alpha"
    (p / ".cc-autopipe" / "memory").mkdir(parents=True, exist_ok=True)
    bad_state = {
        "schema_version": 2,
        "name": "alpha",
        "phase": "detached",
        "iteration": 0,
        "session_id": None,
        "last_score": None,
        "last_passed": None,
        "prd_complete": False,
        "consecutive_failures": 0,
        "last_cycle_started_at": None,
        "last_progress_at": None,
        "threshold": 0.85,
        "paused": None,
        "detached": None,  # the corruption
        "current_phase": 1,
        "phases_completed": [],
    }
    (p / ".cc-autopipe" / "state.json").write_text(json.dumps(bad_state))
    _write_projects_list(user_home, [p])
    _run_orch(user_home, max_loops=1)

    s = _read_state(p)
    assert s["phase"] == "active"
    # And the recovery event was logged.
    events = _read_aggregate(user_home)
    assert any(e.get("event") == "detach_corrupt_recovery" for e in events), events


# ---------------------------------------------------------------------------
# check_cmd timeout (>30s) is a failed check, not a project failure
# ---------------------------------------------------------------------------


def test_detached_check_cmd_timeout_treated_as_failed_check(
    env_paths: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A check_cmd that hangs past 30s should not mark the project FAILED —
    the operator can fix the slow probe without losing the in-flight task.

    We exercise this with a check_cmd that sleeps 60s; the orchestrator's
    30s subprocess timeout fires, rc=124 is recorded, project stays
    DETACHED. We monkeypatch DETACHED_CHECK_TIMEOUT_SEC down to 1 by
    setting an env var-style override... actually orchestrator uses a
    constant — so we use a real but short check_cmd (sleep 35) and let
    the test wait. Skip if too slow."""
    user_home, root = env_paths
    # 35s exceeds the 30s cap → TimeoutExpired. Harness timeout is 60s.
    p = _seed_detached_project(
        root,
        "alpha",
        check_cmd="sleep 35",
        check_every_sec=0,
        max_wait_sec=14400,
    )
    _write_projects_list(user_home, [p])
    cp = _run_orch(user_home, max_loops=1, timeout=60.0)
    assert cp.returncode == 0, cp.stderr

    s = _read_state(p)
    assert s["phase"] == "detached"
    detached = s["detached"]
    assert isinstance(detached, dict)
    assert detached["checks_count"] == 1

    events = _read_aggregate(user_home)
    failed_checks = [e for e in events if e.get("event") == "detach_check_failed"]
    assert len(failed_checks) == 1
    assert failed_checks[0]["rc"] == 124  # subprocess timeout convention
