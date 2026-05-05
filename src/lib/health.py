#!/usr/bin/env python3
"""health.py — append cycle-level health metrics to ~/.cc-autopipe/log/health.jsonl.

Refs: PROMPT_v1.3-FULL.md GROUP F2.

One line per cycle (orchestrator emits via cycle.process_project):

    {"ts": "...", "project": "...", "iteration": ..., "phase": "...",
     "5h_pct": 0.34, "7d_pct": 0.50, "disk_free_gb": 45.2,
     "cycles_last_hour": 12, "recoveries_today": 2,
     "meta_reflects_today": 1}

The CLI in src/cli/health.py reads this file and surfaces summary
metrics for `cc-autopipe health`.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HEALTH_LOG_REL = "log/health.jsonl"


def _user_home() -> Path:
    return Path(
        os.environ.get("CC_AUTOPIPE_USER_HOME", str(Path.home() / ".cc-autopipe"))
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _health_path() -> Path:
    return _user_home() / HEALTH_LOG_REL


def append_health(record: dict[str, Any]) -> bool:
    """Append a JSON-encoded record. Best-effort; returns True on success."""
    p = _health_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return True
    except OSError as exc:
        print(f"[health] could not write {p}: {exc}", file=sys.stderr)
        return False


def emit_cycle_health(
    project_name: str,
    iteration: int,
    phase: str,
    five_hour_pct: float | None = None,
    seven_day_pct: float | None = None,
    disk_free_gb: float | None = None,
    cycles_last_hour: int | None = None,
    recoveries_today: int | None = None,
    meta_reflects_today: int | None = None,
) -> bool:
    record: dict[str, Any] = {
        "ts": _now_iso(),
        "project": project_name,
        "iteration": iteration,
        "phase": phase,
    }
    if five_hour_pct is not None:
        record["5h_pct"] = round(float(five_hour_pct), 4)
    if seven_day_pct is not None:
        record["7d_pct"] = round(float(seven_day_pct), 4)
    if disk_free_gb is not None:
        record["disk_free_gb"] = round(float(disk_free_gb), 2)
    if cycles_last_hour is not None:
        record["cycles_last_hour"] = int(cycles_last_hour)
    if recoveries_today is not None:
        record["recoveries_today"] = int(recoveries_today)
    if meta_reflects_today is not None:
        record["meta_reflects_today"] = int(meta_reflects_today)
    return append_health(record)


def read_recent_health(since_seconds: int = 3600) -> list[dict[str, Any]]:
    """Return health records with ts within last `since_seconds`."""
    p = _health_path()
    if not p.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=since_seconds)
    out: list[dict[str, Any]] = []
    try:
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                ts = rec.get("ts", "")
                try:
                    dt = datetime.strptime(
                        ts, "%Y-%m-%dT%H:%M:%SZ"
                    ).replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
                if dt >= cutoff:
                    out.append(rec)
    except OSError:
        return []
    return out


def summarise(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per-project counts/percentiles for CLI display."""
    by_project: dict[str, dict[str, Any]] = {}
    for rec in records:
        proj = rec.get("project", "?")
        d = by_project.setdefault(proj, {"cycles": 0, "phases": set()})
        d["cycles"] += 1
        d["phases"].add(rec.get("phase", "?"))
        for k in ("5h_pct", "7d_pct", "disk_free_gb"):
            if k in rec:
                d[k] = rec[k]
    # Convert phase sets to sorted lists for stable display.
    for d in by_project.values():
        d["phases"] = sorted(d["phases"])
    return {
        "total_records": len(records),
        "by_project": by_project,
    }
