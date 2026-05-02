#!/usr/bin/env python3
"""session_start_helper.py — context blocks emitted by SessionStart hook.

Refs: SPEC-v1.2.md Bug A, Bug C, Bug D, Bug F; AGENTS-v1.2.md §5 (Batch 1)
+ §7 (Batch 3).

Architecture note: hooks remain bash dispatchers (Q-V12-2 decision,
2026-05-02). All injection-block content is composed here in Python
and printed by `src/hooks/session-start.sh` via:

    python3 "$CC_AUTOPIPE_HOME/lib/session_start_helper.py" \
        current-task <project>

In Batch 1 this module emits ONLY the current_task block (Bug A).
Batch 3 will extend with:
  - top-3 backlog tasks (Bug D)
  - long-operation guidance (Bug C)
  - stages_completed progress (Bug F)
under additional CLI subcommands.

CLI:

    python3 session_start_helper.py current-task <project_path>

Always exits 0 — failures here must not abort the session, matching
the broader hook contract.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import state  # noqa: E402


def _format_relative(started_iso: str | None) -> str:
    """Turn an ISO 8601 UTC timestamp into a coarse 'N {minutes,hours,days}
    ago' string. Returns 'just now' for <60s and the raw string back if
    parsing fails — never raises."""
    if not started_iso:
        return ""
    try:
        # Accept both "Z" suffix and "+00:00" forms.
        s = started_iso.rstrip("Z")
        if "+" not in s and "-" not in s[10:]:
            dt = datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
        else:
            dt = datetime.fromisoformat(started_iso.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - dt
        seconds = int(delta.total_seconds())
    except (ValueError, TypeError):
        return started_iso
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    hours = minutes // 60
    if hours < 48:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = hours // 24
    return f"{days} day{'s' if days != 1 else ''} ago"


def build_current_task_block(project_path: str | Path) -> str:
    """Format the current_task injection block from state.json.

    Returns the multi-line string the SessionStart hook should print.
    Never raises — returns a minimal "no current task" block on errors.
    """
    try:
        s = state.read(project_path)
    except Exception:  # noqa: BLE001 — hook contract: never raise
        s = state.State.fresh(Path(project_path).name)

    ct = s.current_task
    if ct is None or not ct.id:
        return (
            "=== Current task ===\n"
            "No current task tracked. When you start work on a backlog\n"
            "item, write .cc-autopipe/CURRENT_TASK.md with at minimum:\n"
            "    task: <id from backlog.md>\n"
            "    stage: <free-form, e.g. setup, training, review>\n"
            "    artifact: <path Claude will write to>\n"
            "    notes: <one-line context>\n"
            "==="
        )

    rel = _format_relative(ct.started_at)
    started_line = (
        f"Started: {rel} ({ct.started_at})" if ct.started_at else "Started: unknown"
    )

    stages_str = ", ".join(ct.stages_completed) if ct.stages_completed else "(none)"
    artifacts_str = (
        "\n".join(f"  - {a}" for a in ct.artifact_paths)
        if ct.artifact_paths
        else "  (none declared)"
    )

    notes = ct.claude_notes.strip() if ct.claude_notes else "(none)"

    lines = [
        "=== Current task ===",
        f"Task: {ct.id}",
        f"Stage: {ct.stage or 'unspecified'}",
        started_line,
        f"Stages completed: {stages_str}",
        "Artifacts:",
        artifacts_str,
        f"Notes: {notes}",
        "",
        "Continue this task. Update CURRENT_TASK.md when stage changes,",
        "stages_completed grows, or you switch tasks. Engine tracks",
        "current_task and treats artifacts that don't match it as",
        "out-of-scope.",
        "===",
    ]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="session_start_helper.py")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_ct = sub.add_parser(
        "current-task",
        help="Print the current_task injection block (Bug A).",
    )
    p_ct.add_argument("project")

    args = parser.parse_args(argv)

    if args.cmd == "current-task":
        try:
            block = build_current_task_block(args.project)
            print(block)
        except Exception as exc:  # noqa: BLE001 — hook contract
            print(f"[session_start_helper] failed: {exc}", file=sys.stderr)
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
