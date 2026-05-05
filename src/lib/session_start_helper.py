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

import backlog as backlog_lib  # noqa: E402
import findings as findings_lib  # noqa: E402
import knowledge as knowledge_lib  # noqa: E402
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


def build_backlog_top3_block(project_path: str | Path) -> str:
    """Bug D: top 3 OPEN backlog tasks injected into the prompt so the
    agent sees the operator's prioritisation up front.

    Reads the project's backlog.md (NOT .cc-autopipe/backlog.md — the
    convention is that backlog.md lives at the project root next to
    PRD.md). Falls back to .cc-autopipe/backlog.md if root copy is
    absent (some Stage I projects placed it there).

    Also surfaces the current_task.id from state.json so the agent can
    immediately see whether it should continue an existing task or pick
    one of the top 3.
    """
    project = Path(project_path)
    candidates = [
        project / "backlog.md",
        project / ".cc-autopipe" / "backlog.md",
    ]
    backlog_path = next((p for p in candidates if p.exists()), None)
    if backlog_path is None:
        return ""  # No backlog → no block; existing recent-failures block
        #  in session-start.sh handles the no-tasks case.

    try:
        items = backlog_lib.parse_top_open(backlog_path, n=3)
    except Exception:  # noqa: BLE001 — hook contract
        return ""

    if not items:
        return ""

    # Best-effort current_task lookup — fall through to "(none)" on any
    # read failure, never raise.
    current_id = "(none — pick one of the above)"
    try:
        s = state.read(project)
        if s.current_task is not None and s.current_task.id:
            current_id = s.current_task.id
    except Exception:  # noqa: BLE001
        pass

    lines = [
        "=== Backlog directive ===",
        "Top 3 OPEN tasks (DO NOT skip these for others):",
    ]
    for it in items:
        marker = "[~]" if it.status == "~" else "[ ]"
        lines.append(
            f"  {marker} P{it.priority} {it.id} — {it.description or '(no description)'}"
        )
    lines.extend(
        [
            "",
            f"CURRENT TASK (per state.json): {current_id}",
            "",
            "If the current task is open, continue it. If you need to switch,",
            "write CURRENT_TASK.md with the new task and explain why in",
            "claude_notes — engine logs task_switched events and treats",
            "off-current artifacts as out-of-scope.",
            "===",
        ]
    )
    return "\n".join(lines)


def build_long_op_block() -> str:
    """Bug C: long-operation guidance reminding the agent to use
    cc-autopipe-detach for >5min operations instead of holding the
    orchestrator slot synchronously."""
    return "\n".join(
        [
            "=== Long operation guidance ===",
            "If you are about to run an operation expected to take >5 minutes",
            "(model training, large data processing, batch inference,",
            "multi-period backtests):",
            "",
            "  1. Launch with nohup in background:",
            "       nohup bash scripts/run_<task>.sh > logs/<task>.log 2>&1 &",
            "  2. Immediately call cc-autopipe-detach with:",
            '       --reason "<short label>"',
            '       --check-cmd "<one-liner that exits 0 when done>"',
            "       --check-every 600",
            "       --max-wait 14400",
            "  3. End your turn. Engine will resume you when check-cmd",
            "     succeeds (or max-wait elapses).",
            "",
            "Do NOT block the cycle waiting for long operations. Each second",
            "you wait synchronously is a second of cycle budget burned for",
            "every other project in projects.list.",
            "===",
        ]
    )


def build_findings_block(project_path: str | Path, top_n: int = 20) -> str:
    """v1.3 A3: top-N most recent findings_index entries (newest first)."""
    try:
        items = findings_lib.read_findings(Path(project_path), top_n=top_n)
    except Exception:  # noqa: BLE001 — hook contract
        return ""
    return findings_lib.format_findings_for_injection(items)


def build_knowledge_block(project_path: str | Path) -> str:
    """v1.3 A3: full knowledge.md (or last 5KB tail if larger)."""
    try:
        text = knowledge_lib.read_knowledge(Path(project_path))
    except Exception:  # noqa: BLE001 — hook contract
        return ""
    return knowledge_lib.format_for_injection(text)


def build_full_block(project_path: str | Path) -> str:
    """All v1.2 + v1.3 SessionStart blocks composed in one call. Empty
    sub-blocks (no backlog, no current_task, no findings, no knowledge)
    are omitted cleanly.

    Order (per PROMPT_v1.3-FULL.md A3 "after existing context, before
    long-op guidance"):

        current_task → backlog → findings → knowledge → long-op
    """
    parts: list[str] = []
    ct_block = build_current_task_block(project_path)
    if ct_block:
        parts.append(ct_block)
    bl_block = build_backlog_top3_block(project_path)
    if bl_block:
        parts.append(bl_block)
    fb = build_findings_block(project_path)
    if fb:
        parts.append(fb)
    kb = build_knowledge_block(project_path)
    if kb:
        parts.append(kb)
    parts.append(build_long_op_block())
    return "\n\n".join(parts)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="session_start_helper.py")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_ct = sub.add_parser(
        "current-task",
        help="Print the current_task injection block (Bug A).",
    )
    p_ct.add_argument("project")

    p_bl = sub.add_parser(
        "backlog-top3",
        help="Print top-3 OPEN backlog tasks block (Bug D).",
    )
    p_bl.add_argument("project")

    sub.add_parser(
        "long-op",
        help="Print long-operation guidance block (Bug C).",
    )

    p_findings = sub.add_parser(
        "findings",
        help="Print recent findings injection block (v1.3 A3).",
    )
    p_findings.add_argument("project")
    p_findings.add_argument("--top-n", type=int, default=20)

    p_kn = sub.add_parser(
        "knowledge",
        help="Print knowledge.md injection block (v1.3 A3).",
    )
    p_kn.add_argument("project")

    p_all = sub.add_parser(
        "all",
        help="Print all v1.2 + v1.3 SessionStart blocks.",
    )
    p_all.add_argument("project")

    args = parser.parse_args(argv)

    try:
        if args.cmd == "current-task":
            print(build_current_task_block(args.project))
        elif args.cmd == "backlog-top3":
            print(build_backlog_top3_block(args.project))
        elif args.cmd == "long-op":
            print(build_long_op_block())
        elif args.cmd == "findings":
            out = build_findings_block(args.project, top_n=args.top_n)
            if out:
                print(out)
        elif args.cmd == "knowledge":
            out = build_knowledge_block(args.project)
            if out:
                print(out)
        elif args.cmd == "all":
            print(build_full_block(args.project))
        else:
            return 2
    except Exception as exc:  # noqa: BLE001 — hook contract
        print(f"[session_start_helper] failed: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
