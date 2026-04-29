"""Integration tests for src/orchestrator framework behaviour.

Covers Stage B DoD items that test the main loop / phase handling
without depending on hook execution:
- reads projects.list and iterates FIFO
- exits cleanly on SIGTERM
- skips done/failed projects
- transitions paused→active when resume_at has passed

Tests use CC_AUTOPIPE_CLAUDE_BIN=/usr/bin/true so each cycle exits
zero immediately without firing hooks. The Stage C dedicated
test_orchestrator_claude.py covers the actual claude+hooks integration.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
ORCHESTRATOR = SRC / "orchestrator"


def _seed_project(
    base: Path,
    name: str,
    *,
    phase: str = "active",
    iteration: int = 0,
    paused_resume_at: str | None = None,
) -> Path:
    p = base / name
    (p / ".cc-autopipe" / "memory").mkdir(parents=True, exist_ok=True)
    state_doc: dict[str, object] = {
        "schema_version": 1,
        "name": name,
        "phase": phase,
        "iteration": iteration,
        "session_id": None,
        "last_score": None,
        "last_passed": None,
        "prd_complete": False,
        "consecutive_failures": 0,
        "last_cycle_started_at": None,
        "last_progress_at": None,
        "threshold": 0.85,
        "paused": (
            {"resume_at": paused_resume_at, "reason": "rate_limit_5h"}
            if paused_resume_at
            else None
        ),
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
    cooldown: float = 0.0,
    idle_sleep: float = 0.0,
    timeout: float = 10.0,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["CC_AUTOPIPE_HOME"] = str(SRC)
    env["CC_AUTOPIPE_USER_HOME"] = str(user_home)
    env["CC_AUTOPIPE_COOLDOWN_SEC"] = str(cooldown)
    env["CC_AUTOPIPE_IDLE_SLEEP_SEC"] = str(idle_sleep)
    env["CC_AUTOPIPE_MAX_LOOPS"] = str(max_loops)
    # Existing Stage B framework tests don't care about hook execution —
    # they verify FIFO/paused/skip/signal behavior. Use /bin/true as a
    # zero-effect stand-in for the claude binary so the orchestrator
    # treats each cycle as "claude returned cleanly". The dedicated
    # test_orchestrator_claude.py uses the real mock-claude.sh.
    env["CC_AUTOPIPE_CLAUDE_BIN"] = "/usr/bin/true"
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


# --- main loop -----------------------------------------------------------


def test_one_loop_active_project_logs_cycle_events(
    env_paths: tuple[Path, Path],
) -> None:
    user_home, root = env_paths
    p = _seed_project(root, "alpha", phase="active", iteration=0)
    _write_projects_list(user_home, [p])

    cp = _run_orch(user_home, max_loops=1)
    assert cp.returncode == 0, cp.stderr

    s = _read_state(p)
    assert s["iteration"] == 1
    assert s["last_cycle_started_at"] is not None

    events = _read_aggregate(user_home)
    starts = [e for e in events if e.get("event") == "cycle_start"]
    ends = [e for e in events if e.get("event") == "cycle_end"]
    assert len(starts) == 1 and len(ends) == 1
    assert starts[0]["project"] == "alpha"
    assert starts[0]["iteration"] == 1
    assert ends[0]["rc"] == 0


def test_fifo_ordering_across_multiple_projects(
    env_paths: tuple[Path, Path],
) -> None:
    user_home, root = env_paths
    a = _seed_project(root, "alpha", phase="active")
    b = _seed_project(root, "bravo", phase="active")
    c = _seed_project(root, "charlie", phase="active")
    _write_projects_list(user_home, [a, b, c])

    _run_orch(user_home, max_loops=1)
    starts = [
        e for e in _read_aggregate(user_home) if e.get("event") == "cycle_start"
    ]
    assert [e["project"] for e in starts] == ["alpha", "bravo", "charlie"]


def test_skips_done_and_failed_projects(env_paths: tuple[Path, Path]) -> None:
    user_home, root = env_paths
    a = _seed_project(root, "alpha", phase="done")
    b = _seed_project(root, "bravo", phase="failed")
    c = _seed_project(root, "charlie", phase="active")
    _write_projects_list(user_home, [a, b, c])

    _run_orch(user_home, max_loops=1)

    assert _read_state(a)["iteration"] == 0  # untouched
    assert _read_state(b)["iteration"] == 0
    assert _read_state(c)["iteration"] == 1  # incremented

    events = [
        e for e in _read_aggregate(user_home) if e.get("event") == "cycle_start"
    ]
    assert {e["project"] for e in events} == {"charlie"}


def test_paused_project_not_due_yet_is_skipped(
    env_paths: tuple[Path, Path],
) -> None:
    user_home, root = env_paths
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    p = _seed_project(root, "alpha", phase="paused", paused_resume_at=future)
    _write_projects_list(user_home, [p])

    _run_orch(user_home, max_loops=1)
    s = _read_state(p)
    assert s["phase"] == "paused"
    assert s["iteration"] == 0  # untouched


def test_paused_project_due_resumes_then_runs(env_paths: tuple[Path, Path]) -> None:
    user_home, root = env_paths
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    p = _seed_project(root, "alpha", phase="paused", paused_resume_at=past)
    _write_projects_list(user_home, [p])

    _run_orch(user_home, max_loops=1)
    s = _read_state(p)
    assert s["phase"] == "active"
    assert s["paused"] is None
    assert s["iteration"] == 1

    events = _read_aggregate(user_home)
    assert any(e.get("event") == "resumed_from_pause" for e in events)
    assert any(e.get("event") == "cycle_start" for e in events)


def test_uninit_project_is_skipped_not_crashed(
    env_paths: tuple[Path, Path],
) -> None:
    user_home, root = env_paths
    bare = root / "bare"
    bare.mkdir()
    _write_projects_list(user_home, [bare])

    cp = _run_orch(user_home, max_loops=1)
    assert cp.returncode == 0, cp.stderr
    assert "not initialized" in cp.stderr


def test_missing_project_path_is_skipped_not_crashed(
    env_paths: tuple[Path, Path],
) -> None:
    user_home, root = env_paths
    ghost = root / "ghost"  # never created
    _write_projects_list(user_home, [ghost])

    cp = _run_orch(user_home, max_loops=1)
    assert cp.returncode == 0, cp.stderr


def test_no_projects_list_does_not_crash(env_paths: tuple[Path, Path]) -> None:
    user_home, _ = env_paths
    user_home.mkdir(parents=True, exist_ok=True)
    cp = _run_orch(user_home, max_loops=2, idle_sleep=0)
    assert cp.returncode == 0, cp.stderr


def test_multiple_loops_increment_iteration(env_paths: tuple[Path, Path]) -> None:
    user_home, root = env_paths
    p = _seed_project(root, "alpha", phase="active")
    _write_projects_list(user_home, [p])

    _run_orch(user_home, max_loops=3)
    assert _read_state(p)["iteration"] == 3

    events = [
        e for e in _read_aggregate(user_home) if e.get("event") == "cycle_start"
    ]
    assert len(events) == 3


# --- SIGTERM -------------------------------------------------------------


def test_sigterm_exits_cleanly(env_paths: tuple[Path, Path]) -> None:
    user_home, root = env_paths
    p = _seed_project(root, "alpha", phase="active")
    _write_projects_list(user_home, [p])

    env = os.environ.copy()
    env["CC_AUTOPIPE_HOME"] = str(SRC)
    env["CC_AUTOPIPE_USER_HOME"] = str(user_home)
    env["CC_AUTOPIPE_COOLDOWN_SEC"] = "10"  # long enough to catch us mid-sleep
    env["CC_AUTOPIPE_IDLE_SLEEP_SEC"] = "10"
    env["CC_AUTOPIPE_MAX_LOOPS"] = "0"

    proc = subprocess.Popen(
        [sys.executable, str(ORCHESTRATOR)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Give it a moment to start and complete one cycle attempt.
    time.sleep(1.0)
    proc.send_signal(signal.SIGTERM)
    try:
        stdout, stderr = proc.communicate(timeout=5.0)
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate()
        pytest.fail("orchestrator did not exit within 5s of SIGTERM")

    assert proc.returncode == 0, stderr.decode()
    assert b"shutdown gracefully" in stderr
    # Mid-write safety: state.json must still be valid JSON.
    s = _read_state(p)
    assert s["phase"] == "active"
    assert s["iteration"] >= 1
