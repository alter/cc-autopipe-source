#!/usr/bin/env python3
"""orchestrator.alerts — Telegram fire-and-forget + dedup helpers."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from orchestrator._runtime import _user_home

# TG dedup window for the 7d broadcast — within this many seconds we
# only send one alert across all projects.
SEVEN_DAY_TG_DEDUP_SEC = 300


def _notify_tg(message: str) -> None:
    """Fire-and-forget Telegram. Failures are swallowed by tg.sh itself."""
    tg_sh = Path(__file__).resolve().parent.parent / "lib" / "tg.sh"
    try:
        subprocess.run(
            ["bash", str(tg_sh), message],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError):
        pass


def _should_send_7d_alert() -> bool:
    """Returns True if we should broadcast the 7d-quota TG message.

    Dedup window: SEVEN_DAY_TG_DEDUP_SEC (5min) — if multiple projects
    trigger pre-flight pause within that window, only the first sends.
    Backed by a sentinel file at $CC_AUTOPIPE_USER_HOME/7d-tg.last.
    """
    sentinel = _user_home() / "7d-tg.last"
    try:
        if sentinel.exists():
            age = time.time() - sentinel.stat().st_mtime
            if age < SEVEN_DAY_TG_DEDUP_SEC:
                return False
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.touch()
        return True
    except OSError:
        return True  # if we can't write the sentinel, err on the side of alerting
