"""locking.py — fcntl-based singleton + per-project locks.

Refs: SPEC.md §8.3, §8.4, OPEN_QUESTIONS.md Q8, Q11

Why fcntl over shell flock(1):
- fcntl.flock is in Python stdlib — works identically on Linux + macOS
  with no brew dependency (Q8 resolution).
- POSIX advisory locks held via fcntl auto-release when the holding
  process dies. This makes SPEC §8.4's kill -9 recovery automatic:
  the next orchestrator's flock(LOCK_NB) succeeds without manual
  PID checks or force-release dances.

Design:
- Each lock is backed by a small file whose content is JSON:
    {pid, purpose, started_at, heartbeat}
  The fcntl lock provides mutual exclusion; the file content is purely
  informational (status.py reads it, hung-process detection reads it).
- A "stale" lock per SPEC §8.3 has two flavours:
    a. PID dead: fcntl auto-releases → next acquire succeeds. Nothing
       to do here.
    b. PID alive but heartbeat >120s old (hung): we log + refuse the
       acquire. v0.5 leaves recovery to the operator (kill the hung
       process). True force-recovery would mean killing the holder,
       which is too aggressive for an autonomous build pipeline.
- HeartbeatThread runs in the orchestrator process and refreshes the
  heartbeat timestamp every 10s while a claude subprocess is alive.
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

DEFAULT_HEARTBEAT_INTERVAL_SEC = 10.0
DEFAULT_HEARTBEAT_STALE_SEC = 120.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _log(msg: str) -> None:
    print(f"[locking] {msg}", file=sys.stderr, flush=True)


@dataclass
class Lock:
    """An acquired fcntl lock, holding an open file descriptor.

    The lock is released when .release() is called OR when the holding
    process dies (fcntl semantics). Tests can simulate the latter with
    SIGKILL.
    """

    path: Path
    fd: int
    pid: int
    purpose: str
    started_at: str

    def _write_payload(self, payload: dict[str, Any]) -> None:
        """Atomically rewrite the lock file's content. The fcntl lock is
        held on the file descriptor, so concurrent rewrites by other
        processes are impossible by construction."""
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n"
        os.lseek(self.fd, 0, os.SEEK_SET)
        os.ftruncate(self.fd, 0)
        os.write(self.fd, encoded)
        try:
            os.fsync(self.fd)
        except OSError:
            pass

    def initialize(self) -> None:
        """Write the initial PID + started_at record."""
        self._write_payload(
            {
                "pid": self.pid,
                "purpose": self.purpose,
                "started_at": self.started_at,
                "heartbeat": self.started_at,
            }
        )

    def heartbeat(self) -> None:
        """Refresh the heartbeat timestamp."""
        self._write_payload(
            {
                "pid": self.pid,
                "purpose": self.purpose,
                "started_at": self.started_at,
                "heartbeat": _now_iso(),
            }
        )

    def release(self) -> None:
        """Release the lock by closing the fd. fcntl auto-clears the lock."""
        try:
            # Truncate to zero so a subsequent reader doesn't see stale data
            # while we close.
            os.lseek(self.fd, 0, os.SEEK_SET)
            os.ftruncate(self.fd, 0)
        except OSError:
            pass
        try:
            os.close(self.fd)
        except OSError:
            pass


def read_lock_payload(path: Path) -> dict[str, Any] | None:
    """Read the JSON payload from a lock file without touching the lock.

    Returns None if the file is missing, empty, or not parseable.
    """
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, OSError):
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return data


def heartbeat_age_sec(path: Path) -> Optional[float]:
    """Returns seconds since the last heartbeat, or None if unknown."""
    payload = read_lock_payload(path)
    if not payload:
        return None
    hb = _parse_iso(payload.get("heartbeat"))
    if hb is None:
        return None
    return (datetime.now(timezone.utc) - hb).total_seconds()


def try_acquire(
    path: Path,
    *,
    purpose: str,
    heartbeat_stale_sec: float = DEFAULT_HEARTBEAT_STALE_SEC,
) -> Lock | None:
    """Acquire an exclusive non-blocking flock on `path`.

    Behaviour matrix:
    - File missing or unowned (previous holder died): success — fcntl
      grants the lock immediately.
    - File held by another live process with fresh heartbeat: failure
      (returns None silently).
    - File held by another live process with heartbeat >120s old: failure
      with a stderr log line. v0.5 does not force-release; the operator
      must intervene.

    The caller is responsible for calling .release() (or letting the
    process die — fcntl auto-releases).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        # EWOULDBLOCK / EAGAIN: held by another live process.
        if exc.errno not in (errno.EWOULDBLOCK, errno.EAGAIN):
            os.close(fd)
            raise
        os.close(fd)
        age = heartbeat_age_sec(path)
        if age is not None and age > heartbeat_stale_sec:
            _log(
                f"{path}: lock held by hung process (heartbeat age {age:.0f}s); "
                f"refusing to force-release. Manual intervention required: "
                f"kill the holder then retry."
            )
        return None

    started_at = _now_iso()
    lock = Lock(
        path=path,
        fd=fd,
        pid=os.getpid(),
        purpose=purpose,
        started_at=started_at,
    )
    try:
        lock.initialize()
    except OSError as exc:
        # Couldn't write initial payload — release and surface the error.
        _log(f"{path}: failed to write initial payload: {exc!r}")
        lock.release()
        return None
    return lock


# Convenience wrappers used by the orchestrator.


def acquire_singleton(
    user_home: Path,
    *,
    heartbeat_stale_sec: float = DEFAULT_HEARTBEAT_STALE_SEC,
) -> Lock | None:
    """Acquire ~/.cc-autopipe/orchestrator.pid (singleton)."""
    return try_acquire(
        user_home / "orchestrator.pid",
        purpose="orchestrator",
        heartbeat_stale_sec=heartbeat_stale_sec,
    )


def acquire_project(
    project_path: Path,
    *,
    heartbeat_stale_sec: float = DEFAULT_HEARTBEAT_STALE_SEC,
) -> Lock | None:
    """Acquire <project>/.cc-autopipe/lock."""
    return try_acquire(
        project_path / ".cc-autopipe" / "lock",
        purpose="project",
        heartbeat_stale_sec=heartbeat_stale_sec,
    )


# ---------------------------------------------------------------------------
# Heartbeat updater thread.
# ---------------------------------------------------------------------------


class HeartbeatThread:
    """Background thread that refreshes a Lock's heartbeat timestamp.

    Started while a claude subprocess is alive; stopped before subprocess
    cleanup. The thread is a daemon so an orchestrator crash doesn't
    leave it dangling — fcntl auto-releases the lock on process death,
    which is the only correctness requirement.
    """

    def __init__(
        self,
        lock: Lock,
        interval_sec: float = DEFAULT_HEARTBEAT_INTERVAL_SEC,
    ) -> None:
        self.lock = lock
        self.interval = interval_sec
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name=f"heartbeat-{lock.path.name}",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.lock.heartbeat()
            except OSError as exc:
                _log(f"heartbeat write failed for {self.lock.path}: {exc!r}")
            # Wait either the full interval or until stop() flips the flag.
            if self._stop.wait(self.interval):
                break


# ---------------------------------------------------------------------------
# Status helpers (used by status.py).
# ---------------------------------------------------------------------------


def is_holder_alive(payload: dict[str, Any]) -> bool:
    """Best-effort: send signal 0 to the holder. Linux+macOS both honor this."""
    pid = payload.get("pid")
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # PID exists but is owned by another user — treat as alive.
        return True


def lock_status(path: Path) -> dict[str, Any]:
    """Return a status snapshot for status.py to render.

    Keys: held (bool), pid, started_at, heartbeat, age_sec, alive.
    """
    payload = read_lock_payload(path)
    if not payload:
        return {"held": False, "pid": None, "started_at": None, "heartbeat": None}

    # Try to acquire the lock to determine if a previous holder died
    # (fcntl auto-released). If we acquire, the file content is stale —
    # release immediately so we don't actually claim the lock.
    held = True
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        held = False
    else:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # Got the lock — previous holder is gone.
            fcntl.flock(fd, fcntl.LOCK_UN)
            held = False
        except OSError as exc:
            if exc.errno not in (errno.EWOULDBLOCK, errno.EAGAIN):
                raise
        finally:
            os.close(fd)

    age = heartbeat_age_sec(path)
    return {
        "held": held,
        "pid": payload.get("pid"),
        "started_at": payload.get("started_at"),
        "heartbeat": payload.get("heartbeat"),
        "age_sec": age,
        "alive": is_holder_alive(payload) if held else False,
    }
