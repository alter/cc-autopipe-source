#!/usr/bin/env python3
"""activity.py — detect whether a project is "doing work" right now.

Refs: PROMPT_v1.3-FULL.md GROUP B1.

Three signals:
  1. Running processes whose argv mentions the project name or path
  2. Recent file mtimes under data/{models,backtest,debug}/ (and any
     additional paths the caller declares)
  3. CURRENT_TASK.md stage changed since the engine last observed it

Any signal → `is_active=True`. False positives (a stranger process
matching by name) are safe — they mean "still active, give it more
time" which is the right side of the trade-off when deciding whether
to mark a project stuck.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any

DEFAULT_SINCE_SECONDS = 1800  # 30 min — caller can override
DEFAULT_ACTIVITY_DIRS = ("data/models", "data/backtest", "data/debug")
PS_TIMEOUT_SEC = 5
WALK_FILE_LIMIT = 5000  # cap walk so a huge tree doesn't stall a cycle


def _scan_processes(project_name: str, project_dir: Path) -> list[int]:
    """Return PIDs whose argv mentions the project name or directory.

    Uses `ps -e -o pid=,command=`. Matching is substring-based; misses
    nothing important and false-positives mean "still active". On any
    OS error returns an empty list — caller treats that as "no
    process signal".
    """
    needles = [project_name]
    project_dir_str = str(project_dir)
    if project_dir_str:
        needles.append(project_dir_str)
    needles = [n for n in needles if n]

    try:
        cp = subprocess.run(
            ["ps", "-e", "-o", "pid=,command="],
            capture_output=True,
            text=True,
            timeout=PS_TIMEOUT_SEC,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return []
    if cp.returncode != 0:
        return []

    pids: list[int] = []
    self_pid = os.getpid()
    for line in cp.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            pid_str, _, cmd = line.partition(" ")
            pid = int(pid_str)
        except ValueError:
            continue
        if pid == self_pid:
            continue
        # Skip the ps invocation itself.
        if "ps -e" in cmd:
            continue
        if any(n in cmd for n in needles):
            pids.append(pid)
    return pids


def _walk_recent_files(
    base_dirs: list[Path], cutoff_ts: float, file_limit: int = WALK_FILE_LIMIT
) -> tuple[list[str], float | None]:
    """Walk base_dirs and return paths with mtime >= cutoff_ts (capped at
    file_limit visited files), plus the most-recent mtime overall.

    Stops early once file_limit files are inspected so a huge tree (e.g.
    a model directory with 100k checkpoints) doesn't stall a cycle.
    """
    recent: list[str] = []
    visited = 0
    last_mtime: float | None = None
    for base in base_dirs:
        if not base.exists() or not base.is_dir():
            continue
        for root, _, files in os.walk(base):
            for f in files:
                if visited >= file_limit:
                    return recent, last_mtime
                visited += 1
                p = Path(root) / f
                try:
                    mt = p.stat().st_mtime
                except OSError:
                    continue
                if last_mtime is None or mt > last_mtime:
                    last_mtime = mt
                if mt >= cutoff_ts:
                    recent.append(str(p))
    return recent, last_mtime


def detect_activity(
    project_dir: Path,
    project_name: str,
    since_seconds: int = DEFAULT_SINCE_SECONDS,
    extra_dirs: list[str] | None = None,
    last_observed_stage: str | None = None,
    current_stage: str | None = None,
) -> dict[str, Any]:
    """Detect activity signals for the project.

    Returns:
        {
            'has_running_processes': bool,
            'recent_artifact_changes': list[str],
            'stage_changed': bool,
            'last_artifact_mtime': float | None,
            'process_pids': list[int],
            'is_active': bool,
        }

    `last_observed_stage` and `current_stage` are passed by the caller
    (orchestrator) which has authoritative access to state.json and
    CURRENT_TASK.md. Stage transition is treated as activity.
    """
    project_dir = Path(project_dir)
    pids = _scan_processes(project_name, project_dir)

    base_dirs = [project_dir / d for d in DEFAULT_ACTIVITY_DIRS]
    if extra_dirs:
        base_dirs.extend(project_dir / d for d in extra_dirs)

    cutoff = time.time() - max(0, int(since_seconds))
    recent, last_mtime = _walk_recent_files(base_dirs, cutoff)

    stage_changed = bool(
        current_stage
        and last_observed_stage is not None
        and current_stage != last_observed_stage
    )

    has_running = bool(pids)
    is_active = has_running or bool(recent) or stage_changed

    return {
        "has_running_processes": has_running,
        "recent_artifact_changes": recent,
        "stage_changed": stage_changed,
        "last_artifact_mtime": last_mtime,
        "process_pids": pids,
        "is_active": is_active,
    }
