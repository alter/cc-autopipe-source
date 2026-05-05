#!/usr/bin/env python3
"""orchestrator.daily_report — generate per-project daily summary.

Refs: PROMPT_v1.3-FULL.md GROUP F1.

Reads aggregate.jsonl (engine event log) + per-project state to
produce <project>/.cc-autopipe/daily_<YYYY-MM-DD>.md every 24 hours.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from orchestrator._runtime import _user_home
import state  # noqa: E402


def _today_date() -> date:
    return datetime.now(timezone.utc).date()


def _aggregate_path() -> Path:
    return _user_home() / "log" / "aggregate.jsonl"


def _read_events_for_project(
    project_name: str, day: date
) -> list[dict[str, Any]]:
    """Filter aggregate.jsonl for events from `project_name` on `day` (UTC)."""
    p = _aggregate_path()
    if not p.exists():
        return []
    out: list[dict[str, Any]] = []
    day_str = day.isoformat()
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
                if rec.get("project") != project_name:
                    continue
                ts = rec.get("ts", "")
                if not ts.startswith(day_str):
                    continue
                out.append(rec)
    except OSError:
        return []
    return out


def _count_events(events: list[dict[str, Any]], event_name: str) -> int:
    return sum(1 for e in events if e.get("event") == event_name)


def _findings_for_day(project_path: Path, day: date) -> list[str]:
    """Read findings_index.md and return entries from `day`. Cheap parse."""
    f = project_path / ".cc-autopipe" / "findings_index.md"
    if not f.exists():
        return []
    try:
        text = f.read_text(encoding="utf-8")
    except OSError:
        return []
    out: list[str] = []
    day_str = day.isoformat()
    for line in text.splitlines():
        if line.startswith("## ") and day_str in line:
            out.append(line[3:].strip())
    return out


def render_daily_report(
    project_path: Path, day: date | None = None
) -> str:
    """Compose the markdown body of the daily summary."""
    project_path = Path(project_path)
    project_name = project_path.name
    day = day or _today_date()
    events = _read_events_for_project(project_name, day)
    s = state.read(project_path)

    cycle_starts = _count_events(events, "cycle_start")
    cycle_ends = [e for e in events if e.get("event") == "cycle_end"]
    successful = sum(1 for e in cycle_ends if e.get("rc") == 0)
    failed = sum(1 for e in cycle_ends if e.get("rc") not in (0, None))

    auto_recoveries = _count_events(events, "auto_recovery_attempted")
    meta_reflects = _count_events(events, "meta_reflect_triggered")
    research_iters = _count_events(events, "research_mode_active")
    closed_today = [
        e
        for e in events
        if e.get("event") == "stage_completed"
    ]
    findings_lines = _findings_for_day(project_path, day)

    quota_pct = ""
    try:
        import quota as quota_lib  # noqa: WPS433

        q = quota_lib.read_cached()
        if q is not None:
            quota_pct = (
                f"- 5h: current {int(q.five_hour_pct * 100)}%\n"
                f"- 7d: current {int(q.seven_day_pct * 100)}%\n"
            )
    except Exception:  # noqa: BLE001
        quota_pct = "- (quota unavailable)\n"

    lines: list[str] = []
    lines.append(f"# Daily summary — {day.isoformat()}")
    lines.append("")
    lines.append("## Cycles")
    lines.append(f"- Total: {cycle_starts}")
    lines.append(f"- Successful (rc=0): {successful}")
    lines.append(f"- Failed: {failed}")
    lines.append(f"- Auto-recoveries: {auto_recoveries}")
    lines.append(f"- Meta-reflections triggered: {meta_reflects}")
    lines.append(f"- Research mode iterations: {research_iters}")
    lines.append("")
    lines.append("## Tasks")
    if s.current_task is not None and s.current_task.id:
        lines.append(f"- In progress: {s.current_task.id} (stage {s.current_task.stage})")
    else:
        lines.append("- In progress: (none)")
    lines.append(f"- Stage transitions today: {len(closed_today)}")
    lines.append("")
    lines.append("## Findings")
    if findings_lines:
        for ln in findings_lines:
            lines.append(f"- {ln}")
    else:
        lines.append("- (no findings recorded today)")
    lines.append("")
    lines.append("## Quota")
    lines.append(quota_pct.rstrip())
    lines.append("")
    lines.append("## Health")
    lines.append(f"- Recovery attempts (lifetime): {s.recovery_attempts}")
    lines.append(f"- Phase: {s.phase}")
    lines.append(f"- Iteration: {s.iteration}")
    return "\n".join(lines) + "\n"


def write_daily_report(
    project_path: Path, day: date | None = None
) -> Path | None:
    """Render and write <project>/.cc-autopipe/daily_<YYYY-MM-DD>.md.

    Returns the path written, or None if the project isn't initialized
    (no .cc-autopipe/ directory). The function intentionally does NOT
    create .cc-autopipe/ — that would falsely flag uninit projects as
    initialized for downstream code (process_project).
    """
    project_path = Path(project_path)
    day = day or _today_date()
    cca = project_path / ".cc-autopipe"
    if not cca.exists():
        return None
    body = render_daily_report(project_path, day)
    out = cca / f"daily_{day.isoformat()}.md"
    out.write_text(body, encoding="utf-8")
    return out


def maybe_write_for_all(
    projects: list[Path], last_run_at: float, now_ts: float
) -> tuple[float, list[Path]]:
    """Caller (main.py) invokes once per outer loop. Skips when less
    than 24h elapsed. Returns (next_last_run_at, written_paths)."""
    if last_run_at and (now_ts - last_run_at) < (24 * 3600):
        return last_run_at, []
    written: list[Path] = []
    for p in projects:
        try:
            out = write_daily_report(p)
            if out is not None:
                written.append(out)
        except Exception:  # noqa: BLE001
            continue
    return now_ts, written
