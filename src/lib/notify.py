#!/usr/bin/env python3
"""notify.py — Python wrapper around src/lib/tg.sh + sentinel dedup.

Refs: SPEC-v1.2.md Bug G "Subprocess failure alerting".

v0.5/v1.0 only fired TG alerts on quota events. Real-world test
revealed silent rc!=0 loops can run for minutes/hours before someone
notices. Bug G adds a TG alert on every rc!=0 cycle with rate
limiting (one alert per 10min per project per rc) so the operator
sees the failure immediately but isn't drowned in repeats.

Dedup mechanism: a sentinel file
  <sentinel_dir>/alert-rc<RC>-<project_name>.last
is touched on each alert. A subsequent call within `dedup_window`
seconds (default 600 = 10min) returns False without firing TG.

Public API:
  notify_subprocess_failed_dedup(
      project_name, rc, stderr_tail, sentinel_dir,
      dedup_window=600, dry_run=False
  ) -> bool

Returns True if the alert was sent (or would have been sent in
dry_run); False if it was deduped.

dry_run=True: useful in tests + the gate script — still honours the
sentinel (so subsequent calls within window dedup correctly) but
doesn't shell out to tg.sh.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

DEFAULT_DEDUP_WINDOW_SEC = 600  # 10 minutes

_HERE = Path(__file__).resolve().parent
_TG_SH = _HERE / "tg.sh"


def _sentinel_path(sentinel_dir: str | Path, rc: int, project_name: str) -> Path:
    return Path(sentinel_dir) / f"alert-rc{rc}-{project_name}.last"


def _format_message(project_name: str, rc: int, stderr_tail: str) -> str:
    """Match SPEC-v1.2.md Bug G example payload."""
    tail = (stderr_tail or "")[-300:] or "(empty)"
    return f"[{project_name}] cycle_failed rc={rc}\nstderr_tail: {tail}"


def notify_subprocess_failed_dedup(
    project_name: str,
    rc: int,
    stderr_tail: str,
    sentinel_dir: str | Path,
    dedup_window: int = DEFAULT_DEDUP_WINDOW_SEC,
    dry_run: bool = False,
) -> bool:
    """Fire a TG alert for a subprocess rc != 0 cycle, deduped via
    sentinel file.

    Args:
        project_name:    project basename (used in alert text + sentinel).
        rc:              non-zero exit code from the cycle's claude subprocess.
        stderr_tail:     stderr snippet (last 300 chars used).
        sentinel_dir:    directory for the dedup sentinel files. Typically
                         ~/.cc-autopipe (orchestrator's user_home).
        dedup_window:    seconds; alerts within this window for the same
                         (project, rc) are skipped. Default 600.
        dry_run:         if True, update sentinel + return True/False but
                         skip the actual tg.sh invocation. Tests + gates
                         use this to exercise dedup logic without sending
                         real TG messages.

    Returns:
        True  — alert sent (or would have been in dry_run).
        False — within dedup window, skipped.

    Never raises: file IO errors fall back to "send" (better to alert
    too much than silently swallow a real subprocess failure).
    """
    sentinel = _sentinel_path(sentinel_dir, rc, project_name)
    now = time.time()

    # Check existing sentinel for dedup window.
    try:
        if sentinel.exists():
            age = now - sentinel.stat().st_mtime
            if age <= dedup_window:
                return False
    except OSError:
        # Stat failure → fall through and fire (fail-loud).
        pass

    # Touch the sentinel BEFORE firing TG so concurrent calls dedup
    # correctly even if tg.sh is slow.
    try:
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.touch()
        # Update mtime if file already existed.
        try:
            import os as _os

            _os.utime(sentinel, (now, now))
        except OSError:
            pass
    except OSError:
        # Can't write sentinel → still send (don't suppress on local FS error).
        pass

    if dry_run:
        return True

    # Fire-and-forget TG. tg.sh always exits 0, so capture rc only for
    # diagnostic logging; never re-raise.
    msg = _format_message(project_name, rc, stderr_tail)
    try:
        subprocess.run(
            ["bash", str(_TG_SH), msg],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (subprocess.TimeoutExpired, OSError):
        pass
    return True
