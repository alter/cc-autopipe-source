#!/usr/bin/env python3
"""disk.py — disk-space probe + checkpoint cleanup.

Refs: PROMPT_v1.3-FULL.md GROUP C2.

ML R&D projects accumulate checkpoints (often 100MB+ per epoch). On
a 14-day autonomous run, disk fills before quota does. This module:
  - check_disk_space:        free-space query with min_free_gb threshold
  - cleanup_old_checkpoints: keep K newest `checkpoint_epoch_*.pt` per
                             experiment dir, never touch final
                             checkpoints (no `epoch_` token) or
                             norm_stats files.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

DEFAULT_MIN_FREE_GB = 5.0
DEFAULT_KEEP_PER_DIR = 3
CHECKPOINT_DIR_REL = "data/models"
EPOCH_PATTERN = re.compile(r"checkpoint_epoch_(\d+)\.pt$")


def check_disk_space(
    project_dir: Path, min_free_gb: float = DEFAULT_MIN_FREE_GB
) -> dict:
    """Return {free_gb, used_pct, ok}.

    `used_pct` is total filesystem usage; `free_gb` is space available
    to the user. Both reported even when ok=True so the operator can
    log trends.
    """
    project_dir = Path(project_dir)
    try:
        usage = shutil.disk_usage(project_dir if project_dir.exists() else "/")
    except OSError:
        return {"free_gb": 0.0, "used_pct": 1.0, "ok": False}
    free_gb = usage.free / (1024**3)
    used_pct = (usage.total - usage.free) / usage.total if usage.total else 1.0
    return {
        "free_gb": round(free_gb, 2),
        "used_pct": round(used_pct, 4),
        "ok": free_gb >= min_free_gb,
    }


def _list_epoch_checkpoints(exp_dir: Path) -> list[Path]:
    """Return checkpoint_epoch_*.pt files in exp_dir, sorted ascending
    by epoch number."""
    out: list[tuple[int, Path]] = []
    try:
        for f in exp_dir.iterdir():
            if not f.is_file():
                continue
            m = EPOCH_PATTERN.search(f.name)
            if m:
                try:
                    out.append((int(m.group(1)), f))
                except ValueError:
                    continue
    except OSError:
        return []
    out.sort(key=lambda x: x[0])
    return [p for _, p in out]


def cleanup_old_checkpoints(
    project_dir: Path,
    keep_per_dir: int = DEFAULT_KEEP_PER_DIR,
    dry_run: bool = False,
) -> list[str]:
    """Walk `data/models/<exp>/`, keep K newest `checkpoint_epoch_*.pt`
    per experiment directory.

    NEVER touches:
      - files without `checkpoint_epoch_<int>.pt` shape (final ckpts
        named with timestamp, named exports, norm_stats, etc.)

    Returns the list of removed paths (str). With dry_run=True nothing
    is removed but the candidate list is still returned, so the caller
    can log what WOULD have been freed.
    """
    project_dir = Path(project_dir)
    base = project_dir / CHECKPOINT_DIR_REL
    if not base.exists() or not base.is_dir():
        return []

    removed: list[str] = []
    try:
        exps = [p for p in base.iterdir() if p.is_dir()]
    except OSError:
        return removed

    keep = max(0, int(keep_per_dir))
    for exp in exps:
        ckpts = _list_epoch_checkpoints(exp)
        if len(ckpts) <= keep:
            continue
        # Keep newest `keep` (last in ascending sort), drop the rest.
        to_remove = ckpts[: len(ckpts) - keep]
        for f in to_remove:
            removed.append(str(f))
            if dry_run:
                continue
            try:
                f.unlink()
            except OSError:
                # Best-effort; continue with the rest.
                pass
    return removed


def total_freed_bytes(removed_paths: list[str]) -> int:
    """Return the total size (bytes) of removed paths from a previous
    cleanup_old_checkpoints invocation. Used by the disk_cleanup event
    log to surface what was freed.

    NOTE: caller should call this BEFORE the unlink (so on real run we
    pre-compute via dry_run, log expected freed, then unlink). Here for
    the dry_run case where files still exist.
    """
    total = 0
    for p in removed_paths:
        try:
            total += os.path.getsize(p)
        except OSError:
            pass
    return total
