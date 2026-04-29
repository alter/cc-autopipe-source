#!/usr/bin/env python3
"""ratelimit.py — backoff ladder for cases where quota.py returns None.

Refs: SPEC.md §6.4, §9.3

Strategy: when 429 hits and we don't have a precise resets_at from
quota.py, climb a 5min/15min/1h ladder. After 6h with no further 429,
the counter resets — a single transient throttle shouldn't drag the
project into hour-long pauses for the rest of the build.

State persisted at ~/.cc-autopipe/ratelimit.json:
    {"count": 2, "last_429_ts": 1714363800.0}

CLI surface (used by stop-failure.sh per SPEC §9.3):
    python3 ratelimit.py register-429    Print wait_sec to stdout, advance ladder
    python3 ratelimit.py state           Print current state JSON
    python3 ratelimit.py reset           Force count → 0
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

LADDER_SEC = [300, 900, 3600]  # 5min, 15min, 1h
RESET_AFTER_SEC = 21600  # 6h with no 429 → reset count

STATE_FILENAME = "ratelimit.json"


def _user_home() -> Path:
    return Path(
        os.environ.get("CC_AUTOPIPE_USER_HOME", str(Path.home() / ".cc-autopipe"))
    )


def _state_path() -> Path:
    return _user_home() / STATE_FILENAME


def _log(msg: str) -> None:
    print(f"[ratelimit] {msg}", file=sys.stderr, flush=True)


def load_state() -> dict[str, Any]:
    """Read ladder state, returning defaults on missing/corrupt file."""
    path = _state_path()
    if not path.exists():
        return {"count": 0, "last_429_ts": 0.0}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        _log(f"state read failed, resetting: {exc!r}")
        return {"count": 0, "last_429_ts": 0.0}
    if not isinstance(data, dict):
        return {"count": 0, "last_429_ts": 0.0}
    # Coerce types — anything weird → defaults.
    try:
        count = int(data.get("count", 0))
    except (TypeError, ValueError):
        count = 0
    try:
        last_429 = float(data.get("last_429_ts", 0.0))
    except (TypeError, ValueError):
        last_429 = 0.0
    return {"count": max(0, count), "last_429_ts": max(0.0, last_429)}


def save_state(state: dict[str, Any]) -> None:
    """Atomic write via tmpfile + os.replace."""
    path = _state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".tmp.{os.getpid()}")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(state, f)
        os.replace(tmp, path)
    except OSError as exc:
        _log(f"state write failed: {exc!r}")


def register_429(now: float | None = None) -> int:
    """Advance the ladder by one step. Returns wait_sec.

    Resets count to 0 if >6h has elapsed since the last 429 — a
    transient throttle six hours ago shouldn't poison the next backoff.
    """
    if now is None:
        now = time.time()
    state = load_state()
    if now - state["last_429_ts"] > RESET_AFTER_SEC:
        state["count"] = 0
    idx = min(state["count"], len(LADDER_SEC) - 1)
    wait_sec = LADDER_SEC[idx]
    state["count"] += 1
    state["last_429_ts"] = now
    save_state(state)
    return wait_sec


def get_resume_at(quota_resume_at: datetime | None = None) -> datetime:
    """Returns the absolute UTC time at which the project should resume.

    Prefers the precise resets_at from quota.py when available; falls
    back to advancing the ladder. SPEC §9.4 mandates a 60s safety
    margin to avoid hitting the limit immediately on resume.
    """
    if quota_resume_at is not None:
        return quota_resume_at + timedelta(seconds=60)
    wait_sec = register_429()
    return datetime.now(timezone.utc) + timedelta(seconds=wait_sec)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    if not argv:
        _log("usage: ratelimit.py {register-429|state|reset}")
        return 2
    cmd = argv[0]
    if cmd == "register-429":
        wait_sec = register_429()
        sys.stdout.write(f"{wait_sec}\n")
        return 0
    if cmd == "state":
        json.dump(load_state(), sys.stdout)
        sys.stdout.write("\n")
        return 0
    if cmd == "reset":
        save_state({"count": 0, "last_429_ts": 0.0})
        return 0
    _log(f"unknown command: {cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
