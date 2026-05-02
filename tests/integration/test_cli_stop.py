"""Integration tests for cc-autopipe stop (Batch a / v0.5.1).

Covers SPEC-v1.md §1.3 acceptance:
- graceful SIGTERM stops a running orchestrator
- SIGKILL escalation when SIGTERM is ignored past --timeout
- idempotent: rc=0 when no orchestrator is running
- corrupt / stale PID file handling
- --help discoverability via the bash dispatcher

The tests spawn a real orchestrator subprocess (mock claude binary,
quota disabled) so we exercise the actual lib/locking → SIGTERM →
graceful-shutdown path, not a mocked stand-in.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
LIB = SRC / "lib"
ORCHESTRATOR = SRC / "orchestrator"
DISPATCHER = SRC / "helpers" / "cc-autopipe"
STOP_PY = SRC / "cli" / "stop.py"

sys.path.insert(0, str(LIB))
import locking  # noqa: E402


def _engine_env(user_home: Path, **overrides: str) -> dict[str, str]:
    env = os.environ.copy()
    env["CC_AUTOPIPE_HOME"] = str(SRC)
    env["CC_AUTOPIPE_USER_HOME"] = str(user_home)
    env["NO_COLOR"] = "1"
    env.update(overrides)
    return env


def _orch_env(user_home: Path, **overrides: str) -> dict[str, str]:
    env = _engine_env(user_home, **overrides)
    # Long-lived orchestrator with no real claude / quota dependency.
    env["CC_AUTOPIPE_COOLDOWN_SEC"] = "1"
    env["CC_AUTOPIPE_IDLE_SLEEP_SEC"] = "1"
    env["CC_AUTOPIPE_CLAUDE_BIN"] = "/usr/bin/true"
    env["CC_AUTOPIPE_QUOTA_DISABLED"] = "1"
    env.pop("CC_AUTOPIPE_MAX_LOOPS", None)
    return env


def _start_reaper(proc: subprocess.Popen) -> threading.Thread:
    """Reap `proc` as soon as it exits.

    Without this, cc-autopipe stop sees the orchestrator's PID lingering
    in the process table as a zombie (the test process is the parent and
    hasn't waited yet). os.kill(pid, 0) returns success for zombies on
    both Linux and macOS, so stop's _is_alive check would never observe
    "dead" and it would falsely escalate to SIGKILL+timeout. In production
    the orchestrator runs under init/launchd which reaps immediately, so
    this is a test-harness concern, not a stop.py bug.
    """
    t = threading.Thread(target=proc.wait, daemon=True)
    t.start()
    return t


def _wait_for_lock_held(user_home: Path, timeout_sec: float = 5.0) -> None:
    """Block until the orchestrator has acquired the singleton lock.

    Necessary because Popen returns before the subprocess has done its
    locking.acquire_singleton() call, and `cc-autopipe stop` against an
    empty PID file would short-circuit with rc=0 ("not running").
    """
    pid_path = user_home / "orchestrator.pid"
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if pid_path.exists():
            payload = locking.read_lock_payload(pid_path)
            if payload and isinstance(payload.get("pid"), int):
                snap = locking.lock_status(pid_path)
                if snap.get("held"):
                    return
        time.sleep(0.05)
    raise AssertionError(f"orchestrator never acquired lock at {pid_path}")


# ---------------------------------------------------------------------------
# --help
# ---------------------------------------------------------------------------


def test_stop_help_via_dispatcher(tmp_path: Path) -> None:
    cp = subprocess.run(
        [str(DISPATCHER), "stop", "--help"],
        capture_output=True,
        text=True,
        env=_engine_env(tmp_path / "uhome"),
        check=True,
    )
    assert "stop" in cp.stdout.lower()
    assert "--timeout" in cp.stdout


def test_stop_listed_in_dispatcher_help(tmp_path: Path) -> None:
    cp = subprocess.run(
        [str(DISPATCHER), "--help"],
        capture_output=True,
        text=True,
        env=_engine_env(tmp_path / "uhome"),
        check=True,
    )
    assert "stop" in cp.stdout


# ---------------------------------------------------------------------------
# Idempotent / no-op cases
# ---------------------------------------------------------------------------


def test_stop_when_no_orchestrator_running_returns_zero(tmp_path: Path) -> None:
    user_home = tmp_path / "uhome"
    user_home.mkdir()
    cp = subprocess.run(
        [sys.executable, str(STOP_PY)],
        capture_output=True,
        text=True,
        env=_engine_env(user_home),
    )
    assert cp.returncode == 0
    assert "not running" in cp.stderr.lower()


def test_stop_with_stale_pid_file_returns_zero(tmp_path: Path) -> None:
    """A PID file left behind from a crashed orchestrator: fcntl
    re-acquires it during lock_status, so stop reports 'not running'."""
    user_home = tmp_path / "uhome"
    user_home.mkdir()
    pid_path = user_home / "orchestrator.pid"
    pid_path.write_text(
        json.dumps({"pid": 999999, "purpose": "orchestrator", "started_at": "x"})
    )
    cp = subprocess.run(
        [sys.executable, str(STOP_PY)],
        capture_output=True,
        text=True,
        env=_engine_env(user_home),
    )
    assert cp.returncode == 0
    assert "not running" in cp.stderr.lower()


def test_stop_with_corrupt_pid_file_returns_one(tmp_path: Path) -> None:
    """A truly held lock file with non-integer pid → rc=1."""
    user_home = tmp_path / "uhome"
    user_home.mkdir()
    pid_path = user_home / "orchestrator.pid"
    # Acquire the fcntl lock from a tiny holder that we control, with a
    # malformed payload — `cc-autopipe stop` must report corruption rc=1.
    holder_src = textwrap.dedent(
        """
        import fcntl, os, sys, time
        fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o644)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.lseek(fd, 0, 0); os.ftruncate(fd, 0)
        os.write(fd, b'{"pid": "not-an-int"}\\n')
        sys.stdout.write("ready\\n"); sys.stdout.flush()
        time.sleep(60)
        """
    )
    holder = subprocess.Popen(
        [sys.executable, "-c", holder_src, str(pid_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        # Wait for "ready" so the lock is acquired and payload written.
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "ready"

        cp = subprocess.run(
            [sys.executable, str(STOP_PY)],
            capture_output=True,
            text=True,
            env=_engine_env(user_home),
        )
        assert cp.returncode == 1
        assert "stale" in cp.stderr.lower() or "corrupt" in cp.stderr.lower()
    finally:
        holder.kill()
        holder.wait(timeout=5)


# ---------------------------------------------------------------------------
# Real orchestrator: graceful SIGTERM
# ---------------------------------------------------------------------------


def test_stop_graceful_terminates_running_orchestrator(tmp_path: Path) -> None:
    user_home = tmp_path / "uhome"
    user_home.mkdir()
    proc = subprocess.Popen(
        [sys.executable, str(ORCHESTRATOR)],
        env=_orch_env(user_home),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    reaper = _start_reaper(proc)
    try:
        _wait_for_lock_held(user_home)
        cp = subprocess.run(
            [sys.executable, str(STOP_PY), "--timeout", "10"],
            capture_output=True,
            text=True,
            env=_engine_env(user_home),
        )
        assert cp.returncode == 0, f"stop failed: {cp.stderr}"
        assert "stopped" in cp.stdout.lower(), cp.stdout
        reaper.join(timeout=5)
        assert proc.returncode == 0, f"orchestrator exited with rc={proc.returncode}"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def test_stop_via_dispatcher(tmp_path: Path) -> None:
    """End-to-end via the bash dispatcher (cc-autopipe stop)."""
    user_home = tmp_path / "uhome"
    user_home.mkdir()
    proc = subprocess.Popen(
        [sys.executable, str(ORCHESTRATOR)],
        env=_orch_env(user_home),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    reaper = _start_reaper(proc)
    try:
        _wait_for_lock_held(user_home)
        cp = subprocess.run(
            [str(DISPATCHER), "stop", "--timeout", "10"],
            capture_output=True,
            text=True,
            env=_engine_env(user_home),
        )
        assert cp.returncode == 0, f"dispatcher stop failed: {cp.stderr}"
        reaper.join(timeout=5)
        assert proc.returncode == 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


# ---------------------------------------------------------------------------
# SIGKILL escalation: process ignores SIGTERM
# ---------------------------------------------------------------------------


def test_stop_escalates_to_sigkill_after_timeout(tmp_path: Path) -> None:
    """A process that holds the orchestrator lock AND ignores SIGTERM
    must be SIGKILLed after --timeout seconds."""
    user_home = tmp_path / "uhome"
    user_home.mkdir()
    pid_path = user_home / "orchestrator.pid"

    # Tiny holder: takes the fcntl lock, writes a normal payload, ignores
    # SIGTERM, and busy-waits. The only way out is SIGKILL.
    holder_src = textwrap.dedent(
        """
        import fcntl, json, os, signal, sys, time
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o644)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        payload = {"pid": os.getpid(), "purpose": "orchestrator",
                   "started_at": "2026-05-02T00:00:00Z",
                   "heartbeat":  "2026-05-02T00:00:00Z"}
        os.lseek(fd, 0, 0); os.ftruncate(fd, 0)
        os.write(fd, (json.dumps(payload) + "\\n").encode())
        sys.stdout.write("ready\\n"); sys.stdout.flush()
        while True:
            time.sleep(0.5)
        """
    )
    holder = subprocess.Popen(
        [sys.executable, "-c", holder_src, str(pid_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    reaper = _start_reaper(holder)
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "ready"

        t0 = time.time()
        cp = subprocess.run(
            [sys.executable, str(STOP_PY), "--timeout", "2"],
            capture_output=True,
            text=True,
            env=_engine_env(user_home),
        )
        elapsed = time.time() - t0

        assert cp.returncode == 0, f"stop failed: {cp.stderr}"
        assert "killed" in cp.stdout.lower() or "stopped" in cp.stdout.lower()
        assert "sigkill" in cp.stderr.lower(), cp.stderr
        # Sanity: we waited at least the timeout, not much more than +5s grace.
        assert 2.0 <= elapsed < 12.0, f"elapsed={elapsed}"

        reaper.join(timeout=5)
        assert holder.returncode is not None, "holder still alive after stop"
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.wait(timeout=5)
