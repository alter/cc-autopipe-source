#!/usr/bin/env python3
"""status.py — implements `cc-autopipe status` per SPEC.md §12.4.

Reads ~/.cc-autopipe/projects.list and prints a one-screen overview of
project phases, iteration counts, last verify scores, and last activity.
With --json, emits a machine-readable document instead.

Best-effort reads:
- ~/.cc-autopipe/orchestrator.pid (Stage D)
- ~/.cc-autopipe/quota-cache.json (Stage E)
- ~/.cc-autopipe/log/aggregate.jsonl (recent events tail)
- per-project state.json

Refs: SPEC.md §12.4, §15.1
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LIB = Path(__file__).resolve().parent.parent / "lib"
sys.path.insert(0, str(_LIB))
import locking  # noqa: E402
import state  # noqa: E402

RECENT_EVENTS_DEFAULT = 5


def _engine_home() -> Path:
    env = os.environ.get("CC_AUTOPIPE_HOME")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent


def _user_home() -> Path:
    return Path(
        os.environ.get("CC_AUTOPIPE_USER_HOME", str(Path.home() / ".cc-autopipe"))
    )


def _engine_version(engine_home: Path) -> str:
    vfile = engine_home / "VERSION"
    try:
        return vfile.read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"


def _read_projects_list(user_home: Path) -> list[Path]:
    list_path = user_home / "projects.list"
    if not list_path.exists():
        return []
    return [
        Path(ln.strip())
        for ln in list_path.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]


def _orchestrator_status(user_home: Path) -> dict[str, Any]:
    """Read the singleton lock and report whether an orchestrator is running.

    Stage D writes ~/.cc-autopipe/orchestrator.pid as a one-line JSON
    payload via lib/locking; lock_status uses fcntl to distinguish
    "lock truly held" (live orchestrator) from "stale file content"
    (process died, fcntl auto-released).
    """
    snap = locking.lock_status(user_home / "orchestrator.pid")
    if not snap["held"]:
        return {
            "running": False,
            "pid": None,
            "started_at": None,
            "uptime_sec": None,
        }
    started_at = snap.get("started_at")
    uptime_sec: float | None = None
    if started_at:
        try:
            started = datetime.strptime(started_at, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc
            )
            uptime_sec = (datetime.now(timezone.utc) - started).total_seconds()
        except ValueError:
            uptime_sec = None
    return {
        "running": True,
        "pid": snap.get("pid"),
        "started_at": started_at,
        "heartbeat": snap.get("heartbeat"),
        "uptime_sec": uptime_sec,
    }


def _quota_summary(user_home: Path) -> dict[str, Any]:
    """Read the quota cache if Stage E has populated it; otherwise n/a."""
    cache = user_home / "quota-cache.json"
    if not cache.exists():
        return {"available": False}
    try:
        raw = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"available": False}
    return {
        "available": True,
        "five_hour_pct": (raw.get("five_hour", {}) or {}).get("utilization"),
        "five_hour_resets_at": (raw.get("five_hour", {}) or {}).get("resets_at"),
        "seven_day_pct": (raw.get("seven_day", {}) or {}).get("utilization"),
        "seven_day_resets_at": (raw.get("seven_day", {}) or {}).get("resets_at"),
    }


def _last_activity_iso(project: Path) -> str | None:
    sjson = project / ".cc-autopipe" / "state.json"
    if not sjson.exists():
        return None
    try:
        s = state.read(project)
    except OSError:
        return None
    if s.last_progress_at:
        return s.last_progress_at
    if s.last_cycle_started_at:
        return s.last_cycle_started_at
    try:
        ts = sjson.stat().st_mtime
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    except OSError:
        return None


def _humanize_age(iso_ts: str | None) -> str:
    if not iso_ts:
        return "—"
    try:
        dt = datetime.strptime(iso_ts, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return iso_ts
    delta = (datetime.now(timezone.utc) - dt).total_seconds()
    if delta < 0:
        return "in the future"
    if delta < 60:
        return f"{int(delta)}s ago"
    if delta < 3600:
        return f"{int(delta / 60)}m ago"
    if delta < 86400:
        return f"{int(delta / 3600)}h ago"
    return f"{int(delta / 86400)}d ago"


def _humanize_resume_in(resume_at: str | None) -> str | None:
    if not resume_at:
        return None
    try:
        dt = datetime.strptime(resume_at, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None
    delta = (dt - datetime.now(timezone.utc)).total_seconds()
    if delta <= 0:
        return "ready"
    if delta < 60:
        return f"resume in {int(delta)}s"
    if delta < 3600:
        return f"resume in {int(delta / 60)}m"
    if delta < 86400:
        return f"resume in {int(delta / 3600)}h"
    return f"resume in {int(delta / 86400)}d"


def _project_row(project: Path) -> dict[str, Any]:
    cca = project / ".cc-autopipe"
    if not project.exists():
        return {
            "path": str(project),
            "name": project.name,
            "phase": "MISSING",
            "iteration": 0,
            "last_score": None,
            "last_activity": None,
            "resume_in": None,
        }
    if not cca.exists():
        return {
            "path": str(project),
            "name": project.name,
            "phase": "UNINIT",
            "iteration": 0,
            "last_score": None,
            "last_activity": None,
            "resume_in": None,
        }

    s = state.read(project)
    resume_in = None
    if s.phase == "paused" and s.paused is not None:
        resume_in = s.paused.resume_at
    return {
        "path": str(project),
        "name": s.name or project.name,
        "phase": s.phase.upper(),
        "iteration": s.iteration,
        "last_score": s.last_score,
        "last_activity": _last_activity_iso(project),
        "resume_in": resume_in,
    }


def _recent_events(user_home: Path, limit: int) -> list[dict[str, Any]]:
    log = user_home / "log" / "aggregate.jsonl"
    if not log.exists():
        return []
    lines = log.read_text(encoding="utf-8").splitlines()
    out: list[dict[str, Any]] = []
    for ln in lines[-limit * 4 :]:  # over-read; not every line may be parseable
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out[-limit:]


def _build_report(user_home: Path, engine_home: Path, recent_n: int) -> dict[str, Any]:
    projects = [_project_row(p) for p in _read_projects_list(user_home)]
    return {
        "engine_version": _engine_version(engine_home),
        "orchestrator": _orchestrator_status(user_home),
        "quota": _quota_summary(user_home),
        "projects": projects,
        "recent_events": _recent_events(user_home, recent_n),
    }


def _format_quota(q: dict[str, Any]) -> str:
    if not q.get("available"):
        return "5h quota: n/a | 7d quota: n/a (Stage E)"

    def pct(v: Any) -> str:
        try:
            return f"{int(float(v) * 100)}%"
        except (TypeError, ValueError):
            return "n/a"

    return (
        f"5h quota: {pct(q.get('five_hour_pct'))} "
        f"(resets {q.get('five_hour_resets_at') or '?'}) | "
        f"7d quota: {pct(q.get('seven_day_pct'))} "
        f"(resets {q.get('seven_day_resets_at') or '?'})"
    )


def _format_orchestrator(o: dict[str, Any]) -> str:
    if not o.get("running"):
        return "Orchestrator: not running"
    pid = o.get("pid")
    uptime = o.get("uptime_sec")
    if uptime is None:
        return f"Orchestrator: running (PID {pid})"
    if uptime < 60:
        upt = f"{int(uptime)}s"
    elif uptime < 3600:
        upt = f"{int(uptime / 60)}m"
    elif uptime < 86400:
        h = int(uptime / 3600)
        m = int((uptime % 3600) / 60)
        upt = f"{h}h {m}m" if m else f"{h}h"
    else:
        upt = f"{int(uptime / 86400)}d"
    return f"Orchestrator: running (PID {pid}, uptime {upt})"


def _format_human(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(
        f"cc-autopipe v{report['engine_version']} | {_format_orchestrator(report['orchestrator'])}"
    )
    lines.append(_format_quota(report["quota"]))
    lines.append("")
    if not report["projects"]:
        lines.append(
            "No projects registered. Run `cc-autopipe init` in a project root."
        )
    else:
        lines.append(
            f"{'PROJECT':<24} {'PHASE':<8} {'ITER':>5} {'SCORE':>6}  LAST ACTIVITY"
        )
        for p in report["projects"]:
            score = (
                "n/a" if p["last_score"] is None else f"{float(p['last_score']):.2f}"
            )
            activity = _humanize_age(p["last_activity"])
            if p["phase"] == "PAUSED":
                rem = _humanize_resume_in(p["resume_in"])
                if rem:
                    activity = f"{activity} ({rem})"
            lines.append(
                f"{p['name'][:23]:<24} {p['phase']:<8} {p['iteration']:>5} {score:>6}  {activity}"
            )

    if report["recent_events"]:
        lines.append("")
        lines.append(f"Recent events (last {len(report['recent_events'])}):")
        for ev in report["recent_events"]:
            ts = (ev.get("ts") or "")[11:19]  # HH:MM:SS
            project = ev.get("project", "")
            event = ev.get("event") or ev.get("error") or "?"
            extra = ", ".join(
                f"{k}={v}"
                for k, v in ev.items()
                if k not in {"ts", "project", "event", "error"}
            )
            lines.append(
                f"  {ts} {project:<22} {event}" + (f" ({extra})" if extra else "")
            )

    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="cc-autopipe status")
    parser.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON"
    )
    parser.add_argument(
        "--recent",
        type=int,
        default=RECENT_EVENTS_DEFAULT,
        help=f"how many recent events to show (default: {RECENT_EVENTS_DEFAULT})",
    )
    args = parser.parse_args(argv)

    report = _build_report(_user_home(), _engine_home(), args.recent)

    if args.json:
        json.dump(report, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(_format_human(report))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
