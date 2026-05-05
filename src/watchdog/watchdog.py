#!/usr/bin/env python3
"""watchdog.py — separate process that pings the orchestrator and
restarts it if dead.

Refs: PROMPT_v1.3-FULL.md GROUP C4.

Loop (default 5 min):
  1. Read orchestrator PID from <user_home>/orchestrator.pid (singleton
     lock file).
  2. kill -0 <pid> to check liveness. Owned-by-another-user is treated
     as alive (kernel still tracks it).
  3. If dead:
       - log_event daemon_dead
       - exec `cc-autopipe start --foreground &` to spawn fresh
  4. If alive:
       - append heartbeat record to <user_home>/log/watchdog.jsonl
  5. Sleep DEFAULT_INTERVAL_SEC, repeat.

Test-friendliness:
  - check_orchestrator_alive(pid_path) returns bool, exposed for tests
  - read_pid(pid_path) returns int|None
  - main() honours CC_AUTOPIPE_WATCHDOG_INTERVAL_SEC and
    CC_AUTOPIPE_WATCHDOG_MAX_LOOPS env vars.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_INTERVAL_SEC = 300


def _user_home() -> Path:
    return Path(
        os.environ.get("CC_AUTOPIPE_USER_HOME", str(Path.home() / ".cc-autopipe"))
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _log(msg: str) -> None:
    print(f"[watchdog {_now_iso()}] {msg}", file=sys.stderr, flush=True)


def read_pid(pid_path: Path) -> int | None:
    """Return the PID stored in <pid_path> as an int, or None.

    The PID file is JSON written by lib/locking.acquire_singleton; we
    accept legacy plain-int contents too for forward-compat.
    """
    if not pid_path.exists():
        return None
    try:
        text = pid_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text:
        return None
    # Try JSON first (current format), fall back to bare int.
    try:
        data = json.loads(text)
        if isinstance(data, dict) and isinstance(data.get("pid"), int):
            return data["pid"]
    except (json.JSONDecodeError, ValueError):
        pass
    try:
        return int(text)
    except ValueError:
        return None


def check_orchestrator_alive(pid_path: Path) -> bool:
    """True if the PID stored in pid_path is alive."""
    pid = read_pid(pid_path)
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but is owned by another user.
        return True
    except OSError:
        return False


def _heartbeat_path(user_home: Path) -> Path:
    return user_home / "log" / "watchdog.jsonl"


def _append_heartbeat(user_home: Path, alive: bool, pid: int | None) -> None:
    p = _heartbeat_path(user_home)
    p.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": _now_iso(),
        "alive": alive,
        "pid": pid,
    }
    try:
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


def restart_orchestrator(user_home: Path) -> bool:
    """Spawn `cc-autopipe start --foreground &` (detached). Returns True
    on successful spawn (subprocess returns immediately because it's a
    background dispatcher invocation)."""
    helper = (
        Path(__file__).resolve().parent.parent / "helpers" / "cc-autopipe"
    )
    if not helper.exists():
        _log(f"helper not found at {helper}; cannot restart")
        return False
    env = os.environ.copy()
    env.setdefault("CC_AUTOPIPE_USER_HOME", str(user_home))
    try:
        subprocess.Popen(
            ["bash", str(helper), "start", "--foreground"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=env,
        )
        return True
    except OSError as exc:
        _log(f"restart failed: {exc!r}")
        return False


def run_one_iteration(user_home: Path) -> dict:
    """One pass: probe orchestrator, log heartbeat, restart if dead.

    Returns a dict {alive, pid, restarted} so tests can assert on the
    decision path without subprocess coupling.
    """
    pid_path = user_home / "orchestrator.pid"
    pid = read_pid(pid_path)
    alive = check_orchestrator_alive(pid_path) if pid_path.exists() else False
    restarted = False
    if alive:
        _append_heartbeat(user_home, True, pid)
    else:
        _append_heartbeat(user_home, False, pid)
        _log(f"orchestrator not alive (pid={pid}); attempting restart")
        if restart_orchestrator(user_home):
            restarted = True
    return {"alive": alive, "pid": pid, "restarted": restarted}


_shutdown = False


def _install_signal_handlers() -> None:
    def handler(_signum, _frame):
        global _shutdown
        _shutdown = True

    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="cc-autopipe-watchdog",
        description="Monitor cc-autopipe orchestrator and restart on death.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single check pass and exit.",
    )
    args = parser.parse_args(argv)

    _install_signal_handlers()
    user_home = _user_home()
    interval = float(
        os.environ.get("CC_AUTOPIPE_WATCHDOG_INTERVAL_SEC", DEFAULT_INTERVAL_SEC)
    )
    max_loops = int(os.environ.get("CC_AUTOPIPE_WATCHDOG_MAX_LOOPS", "0"))

    if args.once:
        run_one_iteration(user_home)
        return 0

    loops = 0
    while not _shutdown:
        run_one_iteration(user_home)
        loops += 1
        if max_loops and loops >= max_loops:
            return 0
        # Coarse sleep — interruptible by SIGTERM via _shutdown flag.
        end = time.time() + interval
        while time.time() < end and not _shutdown:
            time.sleep(min(1.0, end - time.time()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
