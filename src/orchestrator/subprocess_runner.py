#!/usr/bin/env python3
"""orchestrator.subprocess_runner — claude subprocess + stream stash."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

from orchestrator._runtime import _log, is_shutdown


def _kill_process_group(proc: subprocess.Popen, sig: int) -> None:
    """Kill the entire process group of a Popen started with start_new_session.

    This is necessary because claude (or its mock) may spawn child
    processes that inherit the stdout/stderr pipes; killing only the
    parent leaves orphans holding pipes open and proc.communicate()
    blocks indefinitely.
    """
    try:
        os.killpg(proc.pid, sig)
    except (ProcessLookupError, PermissionError):
        # Fall back to single-process kill if killpg fails for any reason.
        try:
            if sig == signal.SIGTERM:
                proc.terminate()
            else:
                proc.kill()
        except ProcessLookupError:
            pass


def _run_claude(
    project_path: Path, cmd: list[str], timeout_sec: float
) -> tuple[int, str, str]:
    """subprocess.Popen with wall-clock timeout. Returns (rc, stdout, stderr).

    Started in a new session so we can kill the whole process group on
    timeout — claude may spawn child processes that would otherwise
    keep the stdout/stderr pipes open after we kill the parent, blocking
    proc.communicate() indefinitely.
    """
    proc = subprocess.Popen(
        cmd,
        cwd=str(project_path),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    started = time.time()
    timed_out = False
    while True:
        if proc.poll() is not None:
            break
        if time.time() - started > timeout_sec:
            _log(
                f"{project_path.name}: claude timeout {timeout_sec:.0f}s, "
                f"killing pgid={proc.pid}"
            )
            _kill_process_group(proc, signal.SIGKILL)
            timed_out = True
            break
        if is_shutdown():
            _log(f"{project_path.name}: shutdown requested, terminating claude")
            _kill_process_group(proc, signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _kill_process_group(proc, signal.SIGKILL)
            break
        time.sleep(0.25)
    stdout, stderr = proc.communicate()
    rc = -1 if timed_out else (proc.returncode if proc.returncode is not None else -1)
    return rc, stdout, stderr


def _stash_stream(project_path: Path, name: str, content: str) -> None:
    """Overwrite memory/<name> with the cycle's stream content.

    Always writes (even when empty) so a fast rc!=0 exit doesn't leave
    a stale log from a previous cycle. Caps at 64KB so we don't fill
    the disk with stream-json.
    """
    target = project_path / ".cc-autopipe" / "memory" / name
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((content or "")[-65536:], encoding="utf-8")
    except OSError:
        pass
