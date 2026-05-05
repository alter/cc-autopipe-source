"""Integration tests for v1.3.2 STDERR-LOGGING.

The daemonized orchestrator must capture stderr + stdout to rotating
log files inside `user_home/log/`. `--foreground` mode keeps streams
attached to the parent (systemd journald, terminal). Without this,
silent crashes during 14-day autonomy leave no diagnostic.

Tests run main.py as a subprocess to exercise the actual fd
duplication path. Pure-unit tests cover the rotation helper.
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
LIB = SRC / "lib"
ORCHESTRATOR = SRC / "orchestrator"
for p in (str(SRC), str(LIB)):
    if p not in sys.path:
        sys.path.insert(0, p)

main_mod = importlib.import_module("orchestrator.main")


# ---------------------------------------------------------------------------
# _rotate_log (pure file ops — fast unit tests)
# ---------------------------------------------------------------------------


def test_rotate_log_basic_shift(tmp_path: Path) -> None:
    """path → path.1 when nothing else exists."""
    p = tmp_path / "x.log"
    p.write_text("v1")
    main_mod._rotate_log(p, keep=3)
    assert not p.exists()
    assert (tmp_path / "x.log.1").read_text() == "v1"


def test_rotate_log_chain_shift(tmp_path: Path) -> None:
    """All existing rotated files shift one slot up; oldest dropped."""
    p = tmp_path / "x.log"
    p.write_text("current")
    (tmp_path / "x.log.1").write_text("rot1")
    (tmp_path / "x.log.2").write_text("rot2")
    (tmp_path / "x.log.3").write_text("rot3")  # oldest, will drop
    main_mod._rotate_log(p, keep=3)
    assert not p.exists()
    assert (tmp_path / "x.log.1").read_text() == "current"
    assert (tmp_path / "x.log.2").read_text() == "rot1"
    assert (tmp_path / "x.log.3").read_text() == "rot2"
    # rot3 was dropped (only 3 kept).
    # No .4 created.
    assert not (tmp_path / "x.log.4").exists()


def test_rotate_log_partial_chain(tmp_path: Path) -> None:
    """Gaps in the chain (e.g., .1 exists but .2 doesn't) are handled."""
    p = tmp_path / "x.log"
    p.write_text("current")
    (tmp_path / "x.log.1").write_text("rot1")
    main_mod._rotate_log(p, keep=3)
    assert (tmp_path / "x.log.1").read_text() == "current"
    assert (tmp_path / "x.log.2").read_text() == "rot1"


def test_rotate_log_missing_path_noop(tmp_path: Path) -> None:
    """Calling rotate when the live log doesn't exist is a no-op."""
    p = tmp_path / "x.log"
    main_mod._rotate_log(p, keep=3)
    # No files anywhere.
    assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------------------
# Subprocess-based redirect tests
# ---------------------------------------------------------------------------


def _run_main_subprocess(
    user_home: Path,
    *,
    foreground: bool,
    no_redirect: bool,
    timeout: float = 10.0,
) -> subprocess.CompletedProcess[str]:
    """Spawn main.py with one outer loop (max=1) and an empty projects.list.

    Captures the parent-side stderr/stdout via subprocess.run. The point
    is to verify whether the orchestrator wrote to user_home/log/ files
    or to the parent pipes.
    """
    user_home.mkdir(parents=True, exist_ok=True)
    (user_home / "projects.list").write_text("")  # empty — fast exit

    env = os.environ.copy()
    env["CC_AUTOPIPE_HOME"] = str(SRC)
    env["CC_AUTOPIPE_USER_HOME"] = str(user_home)
    env["CC_AUTOPIPE_MAX_LOOPS"] = "1"
    env["CC_AUTOPIPE_COOLDOWN_SEC"] = "0"
    env["CC_AUTOPIPE_IDLE_SLEEP_SEC"] = "0"
    env["CC_AUTOPIPE_QUOTA_DISABLED"] = "1"
    if no_redirect:
        env["CC_AUTOPIPE_NO_REDIRECT"] = "1"
    else:
        env.pop("CC_AUTOPIPE_NO_REDIRECT", None)

    cmd = [sys.executable, str(ORCHESTRATOR)]
    if foreground:
        cmd.append("--foreground")

    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )


def test_foreground_mode_does_not_redirect(tmp_path: Path) -> None:
    """--foreground keeps streams attached to the parent (systemd, terminal).
    No log files should be created."""
    user_home = tmp_path / "uhome"
    cp = _run_main_subprocess(user_home, foreground=True, no_redirect=False)
    assert cp.returncode == 0, cp.stderr
    # Orchestrator's own startup logs land in the parent's stderr.
    assert "started" in cp.stderr or "shutdown" in cp.stderr or cp.stderr
    # No redirected log files.
    assert not (user_home / "log" / "orchestrator-stderr.log").exists()
    assert not (user_home / "log" / "orchestrator-stdout.log").exists()


def test_no_redirect_env_disables_redirect(tmp_path: Path) -> None:
    """CC_AUTOPIPE_NO_REDIRECT=1 disables redirection even without
    --foreground (test-harness escape hatch)."""
    user_home = tmp_path / "uhome"
    cp = _run_main_subprocess(user_home, foreground=False, no_redirect=True)
    assert cp.returncode == 0, cp.stderr
    assert cp.stderr  # parent saw the startup log
    assert not (user_home / "log" / "orchestrator-stderr.log").exists()


def test_daemonized_mode_redirects_to_log_files(tmp_path: Path) -> None:
    """Without --foreground or NO_REDIRECT, stderr/stdout land in
    user_home/log/orchestrator-{stderr,stdout}.log."""
    user_home = tmp_path / "uhome"
    cp = _run_main_subprocess(user_home, foreground=False, no_redirect=False)
    assert cp.returncode == 0
    # Parent-side captures should be empty (or near-empty) — output went to files.
    stderr_log = user_home / "log" / "orchestrator-stderr.log"
    stdout_log = user_home / "log" / "orchestrator-stdout.log"
    assert stderr_log.exists(), "stderr log not created"
    assert stdout_log.exists(), "stdout log not created"
    log_text = stderr_log.read_text(encoding="utf-8")
    # Orchestrator's startup `_log("started; ...")` print lands in stderr.
    assert "started" in log_text or "shutdown" in log_text


def test_daemonized_mode_creates_log_dir(tmp_path: Path) -> None:
    """Log dir must be auto-created if missing — fresh install case."""
    user_home = tmp_path / "uhome"
    user_home.mkdir()  # exists, but no log/ subdir
    assert not (user_home / "log").exists()
    cp = _run_main_subprocess(user_home, foreground=False, no_redirect=False)
    assert cp.returncode == 0
    assert (user_home / "log").is_dir()
    assert (user_home / "log" / "orchestrator-stderr.log").exists()


def test_daemonized_mode_appends_across_restarts(tmp_path: Path) -> None:
    """Append-mode preserves history across orchestrator restarts."""
    user_home = tmp_path / "uhome"
    # Pre-seed an existing stderr log with prior history.
    (user_home / "log").mkdir(parents=True)
    seed = "PRIOR_RUN_TRACEBACK_LINE\n"
    (user_home / "log" / "orchestrator-stderr.log").write_text(seed)

    cp = _run_main_subprocess(user_home, foreground=False, no_redirect=False)
    assert cp.returncode == 0
    log_text = (user_home / "log" / "orchestrator-stderr.log").read_text()
    assert log_text.startswith(seed), "prior history was clobbered"
    assert len(log_text) > len(seed), "no new content appended"


def test_daemonized_mode_pre_rotates_oversized_log(tmp_path: Path) -> None:
    """Files > LOG_ROTATE_BYTES rotate to .1 before the new run starts.
    We monkeypatch LOG_ROTATE_BYTES via a tiny seed for speed (real 50MB
    threshold takes too long to fill in tests)."""
    # Direct-call test instead of subprocess to avoid filling 50MB.
    user_home = tmp_path / "uhome"
    log_dir = user_home / "log"
    log_dir.mkdir(parents=True)
    big = "X" * 100  # we'll lower the threshold for the test
    stderr_log = log_dir / "orchestrator-stderr.log"
    stderr_log.write_text(big)

    # Lower the threshold via monkeypatch on the module attr, then
    # invoke _redirect_streams_for_daemon directly. We must restore
    # sys.stderr/stdout afterwards or pytest's capture breaks.
    saved_stderr_attr = sys.stderr
    saved_stdout_attr = sys.stdout
    saved_stderr_fd = os.dup(2)
    saved_stdout_fd = os.dup(1)
    saved_threshold = main_mod.LOG_ROTATE_BYTES
    try:
        main_mod.LOG_ROTATE_BYTES = 50  # 100-byte seed exceeds it
        main_mod._redirect_streams_for_daemon(user_home)
    finally:
        try:
            sys.stderr.flush()
            sys.stdout.flush()
        except Exception:  # noqa: BLE001
            pass
        os.dup2(saved_stderr_fd, 2)
        os.dup2(saved_stdout_fd, 1)
        os.close(saved_stderr_fd)
        os.close(saved_stdout_fd)
        sys.stderr = saved_stderr_attr
        sys.stdout = saved_stdout_attr
        main_mod.LOG_ROTATE_BYTES = saved_threshold

    # Original moved to .1; a fresh empty(ish) live file exists.
    assert (log_dir / "orchestrator-stderr.log.1").read_text() == big
    assert stderr_log.exists()
    assert stderr_log.stat().st_size < len(big)
