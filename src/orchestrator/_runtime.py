#!/usr/bin/env python3
"""orchestrator._runtime — shared helpers and shutdown flag.

Modules in the orchestrator package share a small surface: a logger, a
clock, the user-home resolver, an ISO parser, and a cooperative
shutdown flag wired by main.py's signal handlers. Putting them here
breaks the circular import that would otherwise form between main.py
and cycle.py / subprocess_runner.py.
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Ensure src/lib is importable as bare names (`import state`, `import locking`,
# ...). Submodules import this module first; the side-effect places lib on
# sys.path so bare imports work both when invoked as `python3 -m
# orchestrator` and as `python3 path/to/orchestrator/`.
_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent
_LIB = _SRC / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


# ---------------------------------------------------------------------------
# Cooperative shutdown flag.
# ---------------------------------------------------------------------------
_shutdown = False


def is_shutdown() -> bool:
    """True when SIGTERM / SIGINT requested orchestrator exit."""
    return _shutdown


def set_shutdown(value: bool = True) -> None:
    """Flip the shutdown flag. Called from main.py's signal handlers."""
    global _shutdown
    _shutdown = value


# ---------------------------------------------------------------------------
# Logging + clock helpers.
# ---------------------------------------------------------------------------


def _log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[orchestrator {ts}] {msg}", file=sys.stderr, flush=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso_utc(value: str | None) -> datetime | None:
    """Best-effort parse of `YYYY-MM-DDTHH:MM:SSZ`. Returns None on bad input."""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None


def _user_home() -> Path:
    return Path(
        os.environ.get("CC_AUTOPIPE_USER_HOME", str(Path.home() / ".cc-autopipe"))
    )


def _interruptible_sleep(seconds: float) -> None:
    """Sleep in 0.25s slices so SIGTERM/SIGINT is observed promptly."""
    if seconds <= 0:
        return
    end = time.time() + seconds
    while time.time() < end and not is_shutdown():
        time.sleep(min(0.25, end - time.time()))
