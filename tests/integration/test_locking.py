"""Integration tests for src/lib/locking.py and orchestrator integration.

Covers Stage D DoD items:
- two `cc-autopipe start` invocations: second exits with "already running"
- per-project lock with heartbeat
- stale detection (heartbeat-age branch + automatic fcntl release on death)
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
LIB = SRC / "lib"
ORCHESTRATOR = SRC / "orchestrator"
DISPATCHER = SRC / "helpers" / "cc-autopipe"
HOOKS_DIR = SRC / "hooks"
MOCK_CLAUDE = REPO_ROOT / "tools" / "mock-claude.sh"

sys.path.insert(0, str(LIB))
import locking  # noqa: E402


def _orch_env(user_home: Path, **overrides: str) -> dict[str, str]:
    env = os.environ.copy()
    env["CC_AUTOPIPE_HOME"] = str(SRC)
    env["CC_AUTOPIPE_USER_HOME"] = str(user_home)
    env["CC_AUTOPIPE_COOLDOWN_SEC"] = "0"
    env["CC_AUTOPIPE_IDLE_SLEEP_SEC"] = "0"
    env["CC_AUTOPIPE_CLAUDE_BIN"] = "/usr/bin/true"
    env.update(overrides)
    return env


def _start_orch_bg(user_home: Path, **env_overrides: str) -> subprocess.Popen[bytes]:
    """Start a long-running orchestrator (no MAX_LOOPS) in the background."""
    env = _orch_env(user_home, **env_overrides)
    env.pop("CC_AUTOPIPE_MAX_LOOPS", None)
    env["CC_AUTOPIPE_COOLDOWN_SEC"] = env.get("CC_AUTOPIPE_COOLDOWN_SEC", "10")
    env["CC_AUTOPIPE_IDLE_SLEEP_SEC"] = env.get("CC_AUTOPIPE_IDLE_SLEEP_SEC", "10")
    return subprocess.Popen(
        [sys.executable, str(ORCHESTRATOR)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


# ---------------------------------------------------------------------------
# Locking primitives
# ---------------------------------------------------------------------------


def test_try_acquire_returns_lock_when_unowned(tmp_path: Path) -> None:
    p = tmp_path / "lock"
    lk = locking.try_acquire(p, purpose="test")
    assert lk is not None
    assert p.exists()
    assert lk.pid == os.getpid()
    payload = json.loads(p.read_text())
    assert payload["pid"] == os.getpid()
    assert payload["purpose"] == "test"
    lk.release()


def test_try_acquire_refuses_when_held(tmp_path: Path) -> None:
    p = tmp_path / "lock"
    lk = locking.try_acquire(p, purpose="test")
    assert lk is not None
    lk2 = locking.try_acquire(p, purpose="test")
    assert lk2 is None
    lk.release()
    # After release the next acquire succeeds.
    lk3 = locking.try_acquire(p, purpose="test")
    assert lk3 is not None
    lk3.release()


def test_heartbeat_updates_timestamp(tmp_path: Path) -> None:
    p = tmp_path / "lock"
    lk = locking.try_acquire(p, purpose="test")
    assert lk is not None
    ts1 = json.loads(p.read_text())["heartbeat"]
    time.sleep(1.1)
    lk.heartbeat()
    ts2 = json.loads(p.read_text())["heartbeat"]
    assert ts1 != ts2
    lk.release()


def test_lock_status_reflects_held_then_released(tmp_path: Path) -> None:
    p = tmp_path / "lock"
    lk = locking.try_acquire(p, purpose="test")
    assert lk is not None
    snap = locking.lock_status(p)
    assert snap["held"] is True
    assert snap["alive"] is True
    assert snap["pid"] == os.getpid()
    lk.release()
    snap = locking.lock_status(p)
    assert snap["held"] is False


def test_lock_status_when_holder_dies_via_subprocess(tmp_path: Path) -> None:
    """A subprocess that acquires a lock and is SIGKILL'd should leave the
    fcntl lock auto-released, so lock_status reports held=False even
    though the file content still names the dead PID."""
    p = tmp_path / "lock"
    helper = (
        f"import sys, time; sys.path.insert(0, {str(LIB)!r}); "
        "import locking; "
        f"lk = locking.try_acquire(__import__('pathlib').Path({str(p)!r}), purpose='test'); "
        "print(lk.pid, flush=True); "
        "time.sleep(60)"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", helper],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    # Wait for the subprocess to print its pid → lock acquired.
    deadline = time.time() + 5
    pid_line = b""
    while time.time() < deadline:
        if proc.stdout is not None and proc.stdout.peek(1):
            pid_line = proc.stdout.readline()
            break
        time.sleep(0.1)
    assert pid_line.strip(), "subprocess never printed pid"
    snap = locking.lock_status(p)
    assert snap["held"] is True

    proc.kill()
    proc.wait(timeout=5)
    # fcntl auto-released the lock when proc died.
    snap = locking.lock_status(p)
    assert snap["held"] is False


# ---------------------------------------------------------------------------
# Singleton: orchestrator integration
# ---------------------------------------------------------------------------


def test_second_start_exits_already_running(tmp_path: Path) -> None:
    user_home = tmp_path / "uhome"
    proc = _start_orch_bg(user_home)
    try:
        # Wait for the first orchestrator to acquire the lock.
        deadline = time.time() + 5
        while time.time() < deadline:
            if (user_home / "orchestrator.pid").exists():
                snap = locking.lock_status(user_home / "orchestrator.pid")
                if snap["held"]:
                    break
            time.sleep(0.1)
        assert locking.lock_status(user_home / "orchestrator.pid")["held"]

        # Now try to start a second orchestrator → must exit with rc=1.
        env = _orch_env(user_home)
        env["CC_AUTOPIPE_MAX_LOOPS"] = "1"
        cp = subprocess.run(
            [sys.executable, str(ORCHESTRATOR)],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert cp.returncode == 1, cp.stderr
        assert "already running" in cp.stderr
    finally:
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=10)


def test_first_start_releases_so_second_succeeds(tmp_path: Path) -> None:
    user_home = tmp_path / "uhome"
    user_home.mkdir(parents=True, exist_ok=True)

    env = _orch_env(user_home, CC_AUTOPIPE_MAX_LOOPS="1")
    cp1 = subprocess.run(
        [sys.executable, str(ORCHESTRATOR)],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert cp1.returncode == 0, cp1.stderr

    # Lock file may still exist on disk but fcntl has released it.
    cp2 = subprocess.run(
        [sys.executable, str(ORCHESTRATOR)],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert cp2.returncode == 0, cp2.stderr


def test_singleton_pid_payload_is_well_formed(tmp_path: Path) -> None:
    user_home = tmp_path / "uhome"
    proc = _start_orch_bg(user_home)
    try:
        deadline = time.time() + 5
        while time.time() < deadline:
            if (user_home / "orchestrator.pid").exists():
                payload = locking.read_lock_payload(user_home / "orchestrator.pid")
                if payload and payload.get("pid"):
                    break
            time.sleep(0.1)
        payload = locking.read_lock_payload(user_home / "orchestrator.pid")
        assert payload is not None
        assert payload["purpose"] == "orchestrator"
        assert isinstance(payload["pid"], int)
        assert payload["pid"] == proc.pid
        assert payload["started_at"]
        assert payload["heartbeat"]
    finally:
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=10)


# ---------------------------------------------------------------------------
# Per-project lock: orchestrator integration
# ---------------------------------------------------------------------------


def _init_project(project: Path, user_home: Path) -> None:
    project.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    env = os.environ.copy()
    env["CC_AUTOPIPE_HOME"] = str(SRC)
    env["CC_AUTOPIPE_USER_HOME"] = str(user_home)
    subprocess.run(
        [str(DISPATCHER), "init", str(project)],
        capture_output=True,
        check=True,
        env=env,
    )


def test_per_project_lock_held_during_cycle(tmp_path: Path) -> None:
    """While a cycle is running with a slow claude, the lock file
    should exist with held=True and the heartbeat should refresh."""
    user_home = tmp_path / "uhome"
    project = tmp_path / "proj"
    _init_project(project, user_home)
    # Make verify pass quickly so the cycle exits cleanly afterwards.
    (project / ".cc-autopipe" / "verify.sh").write_text(
        '#!/bin/bash\necho \'{"passed":true,"score":0.9,"prd_complete":false,"details":{}}\'\n'
    )
    (project / ".cc-autopipe" / "verify.sh").chmod(0o755)

    env = _orch_env(user_home)
    env["CC_AUTOPIPE_MAX_LOOPS"] = "1"
    env["CC_AUTOPIPE_CLAUDE_BIN"] = str(MOCK_CLAUDE)
    env["CC_AUTOPIPE_HOOKS_DIR"] = str(HOOKS_DIR)
    env["CC_AUTOPIPE_MOCK_SLEEP_SEC"] = "2"  # stretch the cycle to ~2s
    env["CC_AUTOPIPE_CYCLE_TIMEOUT_SEC"] = "30"

    proc = subprocess.Popen(
        [sys.executable, str(ORCHESTRATOR)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        # Wait for the lock file to appear (cycle started).
        lock_path = project / ".cc-autopipe" / "lock"
        deadline = time.time() + 5
        while time.time() < deadline:
            if lock_path.exists() and locking.lock_status(lock_path)["held"]:
                break
            time.sleep(0.1)
        assert locking.lock_status(lock_path)["held"]

        # Verify the lock is actually held (try_acquire from this test
        # process must fail).
        contender = locking.try_acquire(lock_path, purpose="contender")
        assert contender is None, "lock should be exclusive while cycle runs"
    finally:
        proc.wait(timeout=15)

    # After cycle exits, fcntl auto-releases.
    assert not locking.lock_status(lock_path)["held"]


def test_per_project_lock_released_after_cycle(tmp_path: Path) -> None:
    user_home = tmp_path / "uhome"
    project = tmp_path / "proj"
    _init_project(project, user_home)
    (project / ".cc-autopipe" / "verify.sh").write_text(
        '#!/bin/bash\necho \'{"passed":true,"score":0.9,"prd_complete":false,"details":{}}\'\n'
    )
    (project / ".cc-autopipe" / "verify.sh").chmod(0o755)

    env = _orch_env(user_home, CC_AUTOPIPE_MAX_LOOPS="1")
    cp = subprocess.run(
        [sys.executable, str(ORCHESTRATOR)],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert cp.returncode == 0, cp.stderr

    # After the orchestrator exits, the lock must be acquirable again.
    lock_path = project / ".cc-autopipe" / "lock"
    contender = locking.try_acquire(lock_path, purpose="contender")
    assert contender is not None
    contender.release()


def test_locked_project_is_skipped_with_log(tmp_path: Path) -> None:
    """If something else holds the per-project lock, the orchestrator
    should log "skip ... per-project lock held" and not advance the
    project's iteration."""
    user_home = tmp_path / "uhome"
    project = tmp_path / "proj"
    _init_project(project, user_home)
    (project / ".cc-autopipe" / "verify.sh").write_text(
        '#!/bin/bash\necho \'{"passed":true,"score":0.9,"prd_complete":false,"details":{}}\'\n'
    )
    (project / ".cc-autopipe" / "verify.sh").chmod(0o755)

    # External holder takes the lock first.
    external = locking.try_acquire(
        project / ".cc-autopipe" / "lock", purpose="external"
    )
    assert external is not None

    try:
        env = _orch_env(user_home, CC_AUTOPIPE_MAX_LOOPS="1")
        cp = subprocess.run(
            [sys.executable, str(ORCHESTRATOR)],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert cp.returncode == 0, cp.stderr
        assert "per-project lock held" in cp.stderr

        # Iteration was not bumped.
        s = json.loads((project / ".cc-autopipe" / "state.json").read_text())
        assert s["iteration"] == 0
    finally:
        external.release()


# ---------------------------------------------------------------------------
# Heartbeat staleness path
# ---------------------------------------------------------------------------


def test_stale_heartbeat_is_logged_but_not_force_released(tmp_path: Path) -> None:
    """An external lock-holder with a heartbeat >120s old must be detected
    (logged) but NOT forcibly released. v0.5 leaves recovery to the
    operator."""
    p = tmp_path / "lock"
    holder = locking.try_acquire(p, purpose="hung")
    assert holder is not None
    # Manually backdate the heartbeat by writing an old timestamp.
    payload = json.loads(p.read_text())
    payload["heartbeat"] = "2026-01-01T00:00:00Z"  # ages ago
    p.write_text(json.dumps(payload))

    # Try to acquire from this same process — fails (we already hold it),
    # but we should observe the "hung process" log line.
    import io
    import contextlib

    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        result = locking.try_acquire(p, purpose="contender", heartbeat_stale_sec=10.0)
    assert result is None
    assert "hung process" in buf.getvalue()

    holder.release()
