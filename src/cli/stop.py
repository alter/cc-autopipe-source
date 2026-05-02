#!/usr/bin/env python3
"""stop.py — implements `cc-autopipe stop` per SPEC.md §12.3, SPEC-v1.md §1.3.

Sends SIGTERM to the running orchestrator (PID read from
~/.cc-autopipe/orchestrator.pid). Waits up to --timeout seconds for
graceful shutdown via the orchestrator's existing SIGTERM handler
(src/orchestrator:_install_signal_handlers — flips _shutdown flag,
exits at next safe point). Escalates to SIGKILL if the timeout expires.

Exit codes:
  0  — orchestrator stopped (or wasn't running, which is not an error
       per the SPEC-v1 sketch: `cc-autopipe stop` is idempotent)
  1  — PID file present but unreadable (corrupt content), OR the PID
       belongs to another user (PermissionError on os.kill)

Refs: SPEC.md §12.3, SPEC-v1.md §1.3, src/lib/locking.py (singleton lock)
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent / "lib"
sys.path.insert(0, str(_LIB))
import locking  # noqa: E402

DEFAULT_TIMEOUT_SEC = 60
POLL_INTERVAL_SEC = 0.5


def _user_home() -> Path:
    return Path(
        os.environ.get("CC_AUTOPIPE_USER_HOME", str(Path.home() / ".cc-autopipe"))
    )


def _is_alive(pid: int) -> bool:
    """Best-effort: signal 0 probes liveness without delivering anything."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # PID exists but is owned by another user — alive from kernel's POV.
        return True


def _wait_for_exit(pid: int, timeout_sec: float) -> bool:
    """Return True if the process exits within timeout_sec, else False."""
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if not _is_alive(pid):
            return True
        time.sleep(POLL_INTERVAL_SEC)
    return not _is_alive(pid)


def _resolve_pid(user_home: Path) -> tuple[int | None, str]:
    """Locate the orchestrator PID via the singleton lock file.

    Returns (pid, status) where status is one of:
      "running"   — lock currently held by `pid`
      "not_running" — lock file absent, or content stale and fcntl re-acquired
      "corrupt"   — lock file present but unparseable
    """
    pid_path = user_home / "orchestrator.pid"
    if not pid_path.exists():
        return None, "not_running"

    snap = locking.lock_status(pid_path)
    if not snap.get("held"):
        # fcntl re-acquired during lock_status → previous holder is gone.
        return None, "not_running"

    pid = snap.get("pid")
    if not isinstance(pid, int) or pid <= 0:
        return None, "corrupt"
    return pid, "running"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="cc-autopipe stop",
        description="Stop the running orchestrator gracefully via SIGTERM.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_SEC,
        help=(
            f"Seconds to wait for graceful exit before SIGKILL "
            f"(default: {DEFAULT_TIMEOUT_SEC})."
        ),
    )
    args = parser.parse_args(argv)

    user_home = _user_home()
    pid, status = _resolve_pid(user_home)

    if status == "not_running":
        # Idempotent per SPEC-v1.md §1.3 — `stop` succeeds when there's
        # nothing to stop. Print to stderr so scripted callers can grep
        # stdout for the success line they emit themselves.
        print("orchestrator: not running", file=sys.stderr)
        return 0

    if status == "corrupt" or pid is None:
        pid_path = user_home / "orchestrator.pid"
        print(
            f"orchestrator: stale or corrupt PID file at {pid_path}",
            file=sys.stderr,
        )
        return 1

    # Send SIGTERM. Failures here distinguish:
    #   ProcessLookupError → kernel already reaped between our check and now.
    #   PermissionError    → PID belongs to another user.
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        print(
            f"orchestrator: PID {pid} not running (stale lock file)",
            file=sys.stderr,
        )
        return 0
    except PermissionError:
        print(
            f"orchestrator: PID {pid} owned by another user — refusing",
            file=sys.stderr,
        )
        return 1

    print(f"orchestrator: SIGTERM sent to PID {pid}, waiting up to {args.timeout}s")

    if _wait_for_exit(pid, args.timeout):
        print(f"orchestrator: stopped (PID {pid})")
        return 0

    # Graceful shutdown didn't happen — escalate.
    print(
        f"orchestrator: SIGTERM timeout after {args.timeout}s, sending SIGKILL",
        file=sys.stderr,
    )
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        # Raced — exited just as we were about to SIGKILL. Either way: gone.
        pass
    except PermissionError:
        print(
            f"orchestrator: cannot SIGKILL PID {pid} — owned by another user",
            file=sys.stderr,
        )
        return 1

    # Brief grace for the kernel to reap before we report success.
    if _wait_for_exit(pid, 5):
        print(f"orchestrator: killed (PID {pid})")
        return 0

    print(
        f"orchestrator: PID {pid} did not exit even after SIGKILL — "
        "manual investigation needed",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
