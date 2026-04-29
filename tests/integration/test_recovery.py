"""Integration tests for SPEC §8.4 recovery scenarios.

Covers Stage D DoD:
- "kill -9 mid-cycle, restart resumes correctly"
- "tests/integration/test_recovery.py passes (simulates kill -9)"

The fcntl lock semantics make most of recovery automatic — these tests
prove the automatic behaviour holds end-to-end through the orchestrator.
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


def _passing_verify(project: Path) -> None:
    v = project / ".cc-autopipe" / "verify.sh"
    v.write_text(
        '#!/bin/bash\necho \'{"passed":true,"score":0.9,"prd_complete":false,"details":{}}\'\n'
    )
    v.chmod(0o755)


# ---------------------------------------------------------------------------
# Singleton recovery
# ---------------------------------------------------------------------------


def test_kill_minus_9_singleton_recovers_immediately(tmp_path: Path) -> None:
    """SPEC §8.4: kill -9 the orchestrator → next start succeeds without
    intervention. fcntl auto-releases on process death."""
    user_home = tmp_path / "uhome"
    user_home.mkdir(parents=True, exist_ok=True)

    env = _orch_env(user_home)
    env["CC_AUTOPIPE_COOLDOWN_SEC"] = "10"
    env["CC_AUTOPIPE_IDLE_SLEEP_SEC"] = "10"

    proc = subprocess.Popen(
        [sys.executable, str(ORCHESTRATOR)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Wait for the singleton lock to be acquired.
    deadline = time.time() + 5
    while time.time() < deadline:
        if (user_home / "orchestrator.pid").exists():
            if locking.lock_status(user_home / "orchestrator.pid")["held"]:
                break
        time.sleep(0.1)
    assert locking.lock_status(user_home / "orchestrator.pid")["held"]

    # SIGKILL — no chance for cleanup or release.
    proc.kill()
    proc.wait(timeout=5)

    # Lock is auto-released by fcntl. Time the recovery: per SPEC quality
    # bar, restart should resume within 60s. We assert <5s here because
    # nothing in our path actually needs that long.
    started = time.time()
    env2 = _orch_env(user_home, CC_AUTOPIPE_MAX_LOOPS="1")
    cp = subprocess.run(
        [sys.executable, str(ORCHESTRATOR)],
        env=env2,
        capture_output=True,
        text=True,
        timeout=10,
    )
    elapsed = time.time() - started
    assert cp.returncode == 0, cp.stderr
    assert elapsed < 5.0, f"recovery too slow: {elapsed:.2f}s"


def test_kill_minus_9_leaves_pid_file_with_dead_pid(tmp_path: Path) -> None:
    """The lock file payload still names the dead PID after SIGKILL,
    but lock_status reports held=False because fcntl released."""
    user_home = tmp_path / "uhome"
    user_home.mkdir(parents=True, exist_ok=True)
    env = _orch_env(user_home)
    env["CC_AUTOPIPE_COOLDOWN_SEC"] = "10"

    proc = subprocess.Popen(
        [sys.executable, str(ORCHESTRATOR)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    deadline = time.time() + 5
    while time.time() < deadline:
        if (user_home / "orchestrator.pid").exists():
            payload = locking.read_lock_payload(user_home / "orchestrator.pid")
            if payload and payload.get("pid"):
                break
        time.sleep(0.1)

    payload_alive = locking.read_lock_payload(user_home / "orchestrator.pid")
    assert payload_alive is not None
    dead_pid = payload_alive["pid"]

    proc.kill()
    proc.wait(timeout=5)

    # File still exists, payload still names the dead pid.
    payload_dead = locking.read_lock_payload(user_home / "orchestrator.pid")
    assert payload_dead is not None
    assert payload_dead["pid"] == dead_pid
    # But lock_status correctly reports not held.
    assert locking.lock_status(user_home / "orchestrator.pid")["held"] is False


# ---------------------------------------------------------------------------
# Per-project recovery
# ---------------------------------------------------------------------------


def test_kill_minus_9_releases_per_project_lock(tmp_path: Path) -> None:
    """SPEC §8.4: a SIGKILL'd orchestrator with a held per-project lock
    should leave the lock acquirable on the next start."""
    user_home = tmp_path / "uhome"
    project = tmp_path / "proj"
    _init_project(project, user_home)
    _passing_verify(project)

    env = _orch_env(user_home)
    env["CC_AUTOPIPE_COOLDOWN_SEC"] = "10"
    env["CC_AUTOPIPE_IDLE_SLEEP_SEC"] = "10"
    env["CC_AUTOPIPE_CLAUDE_BIN"] = str(MOCK_CLAUDE)
    env["CC_AUTOPIPE_HOOKS_DIR"] = str(HOOKS_DIR)
    env["CC_AUTOPIPE_MOCK_SLEEP_SEC"] = "5"  # cycle takes >>1s
    env["CC_AUTOPIPE_CYCLE_TIMEOUT_SEC"] = "30"

    proc = subprocess.Popen(
        [sys.executable, str(ORCHESTRATOR)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Wait for per-project lock to be acquired (cycle entered).
    lock_path = project / ".cc-autopipe" / "lock"
    deadline = time.time() + 5
    while time.time() < deadline:
        if lock_path.exists() and locking.lock_status(lock_path)["held"]:
            break
        time.sleep(0.1)
    assert locking.lock_status(lock_path)["held"], (
        "orchestrator never acquired per-project lock"
    )

    # SIGKILL while the cycle is mid-flight.
    proc.kill()
    proc.wait(timeout=5)

    # fcntl released both locks automatically.
    assert not locking.lock_status(lock_path)["held"]
    assert not locking.lock_status(user_home / "orchestrator.pid")["held"]

    # A fresh orchestrator can claim the per-project lock without forcing.
    contender = locking.try_acquire(lock_path, purpose="contender")
    assert contender is not None
    contender.release()


def test_state_after_crash_is_recoverable(tmp_path: Path) -> None:
    """state.json may have iteration bumped without a matching cycle_end
    in aggregate.jsonl. The next cycle should continue cleanly — no
    crash, no integrity error, just one extra iteration in the count.
    SPEC §8.4: 'Iteration count may be off by one. Acceptable.'"""
    user_home = tmp_path / "uhome"
    project = tmp_path / "proj"
    _init_project(project, user_home)
    _passing_verify(project)

    env = _orch_env(user_home)
    env["CC_AUTOPIPE_COOLDOWN_SEC"] = "10"
    env["CC_AUTOPIPE_IDLE_SLEEP_SEC"] = "10"
    env["CC_AUTOPIPE_CLAUDE_BIN"] = str(MOCK_CLAUDE)
    env["CC_AUTOPIPE_HOOKS_DIR"] = str(HOOKS_DIR)
    env["CC_AUTOPIPE_MOCK_SLEEP_SEC"] = "5"
    env["CC_AUTOPIPE_CYCLE_TIMEOUT_SEC"] = "30"

    proc = subprocess.Popen(
        [sys.executable, str(ORCHESTRATOR)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    # Wait until iteration has been bumped and lock is held.
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            s = json.loads((project / ".cc-autopipe" / "state.json").read_text())
            if s["iteration"] == 1:
                break
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        time.sleep(0.1)

    s_pre_crash = json.loads((project / ".cc-autopipe" / "state.json").read_text())
    assert s_pre_crash["iteration"] == 1

    proc.kill()
    proc.wait(timeout=5)

    # State stays as-is on disk — the in-flight cycle had no chance to
    # write cycle_end. The next orchestrator must read this and continue.
    env2 = _orch_env(user_home, CC_AUTOPIPE_MAX_LOOPS="1")
    cp = subprocess.run(
        [sys.executable, str(ORCHESTRATOR)],
        env=env2,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert cp.returncode == 0, cp.stderr

    s_post = json.loads((project / ".cc-autopipe" / "state.json").read_text())
    # The new run bumped iteration to 2 (the "off by one" SPEC mentions).
    assert s_post["iteration"] == 2


# ---------------------------------------------------------------------------
# Claude subprocess crash (non-zero rc, no Stop hook)
# ---------------------------------------------------------------------------


def test_claude_subprocess_nonzero_rc_does_not_kill_orchestrator(
    tmp_path: Path,
) -> None:
    """SPEC §8.4: claude subprocess crashes → orchestrator detects via
    poll(), logs error, continues. State.json unchanged from pre-cycle
    (no Stop hook fired)."""
    user_home = tmp_path / "uhome"
    project = tmp_path / "proj"
    _init_project(project, user_home)

    # /usr/bin/false exits 1 immediately. No hooks fire.
    env = _orch_env(user_home, CC_AUTOPIPE_CLAUDE_BIN="/usr/bin/false")
    env["CC_AUTOPIPE_MAX_LOOPS"] = "2"
    cp = subprocess.run(
        [sys.executable, str(ORCHESTRATOR)],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert cp.returncode == 0, cp.stderr

    s = json.loads((project / ".cc-autopipe" / "state.json").read_text())
    # Iteration was bumped — the orchestrator did try, claude just failed.
    # consecutive_failures stays 0 here because no Stop hook ran to bump
    # it (SPEC §8.4 actually says orchestrator should bump it; v0.5
    # leaves that for Stage F's verify-driven path. Document this gap.)
    assert s["iteration"] == 2

    # Aggregate.jsonl has cycle_start + cycle_end with rc=1 for both
    # cycles, but no hook_session_start (false doesn't fire hooks).
    log = (user_home / "log" / "aggregate.jsonl").read_text().splitlines()
    events = [json.loads(ln) for ln in log if ln.strip()]
    assert sum(1 for e in events if e.get("event") == "cycle_start") == 2
    assert sum(1 for e in events if e.get("event") == "cycle_end") == 2
    assert all(e["rc"] != 0 for e in events if e.get("event") == "cycle_end")


# ---------------------------------------------------------------------------
# SIGTERM still works after Stage D wiring
# ---------------------------------------------------------------------------


def test_graceful_shutdown_releases_singleton(tmp_path: Path) -> None:
    """Sanity: SIGTERM during sleep cleans up the singleton lock."""
    user_home = tmp_path / "uhome"
    user_home.mkdir(parents=True, exist_ok=True)
    env = _orch_env(user_home)
    env["CC_AUTOPIPE_COOLDOWN_SEC"] = "10"
    env["CC_AUTOPIPE_IDLE_SLEEP_SEC"] = "10"

    proc = subprocess.Popen(
        [sys.executable, str(ORCHESTRATOR)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    deadline = time.time() + 5
    while time.time() < deadline:
        if locking.lock_status(user_home / "orchestrator.pid")["held"]:
            break
        time.sleep(0.1)
    assert locking.lock_status(user_home / "orchestrator.pid")["held"]

    proc.send_signal(signal.SIGTERM)
    proc.wait(timeout=10)
    assert not locking.lock_status(user_home / "orchestrator.pid")["held"]
