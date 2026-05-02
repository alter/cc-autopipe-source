#!/usr/bin/env python3
"""stop_helper.py — Python logic invoked from src/hooks/stop.sh.

Refs: SPEC-v1.2.md Bug A "Mechanism" (Stop hook reads CURRENT_TASK.md,
updates state.json.current_task), AGENTS-v1.2.md §5.

Architecture note: hooks remain bash dispatchers (Roman's Q-V12-2
decision, 2026-05-02). All logic that touches state.json or parses
files lives in Python helpers under src/lib/ and is invoked from
bash via this CLI.

CLI:

    python3 stop_helper.py sync <project_path>

Reads <project>/.cc-autopipe/CURRENT_TASK.md (if present) and projects
its contents into state.json.current_task. Missing/empty CURRENT_TASK.md
leaves state.current_task unchanged — the file is Claude's authoritative
input channel; absence means "no new instruction".

Always exits 0 on recoverable errors (corrupted file, lock contention,
etc.) to match the hook contract: hook helpers must not abort the
parent session.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# src/lib is on sys.path when invoked via `python3 src/lib/stop_helper.py`
# from a hook; importing siblings works directly.
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import current_task  # noqa: E402
import state  # noqa: E402


def sync_current_task_from_md(project_path: str | Path) -> bool:
    """Read CURRENT_TASK.md, update state.json.current_task.

    Returns True if state was modified (and written), False otherwise.
    """
    project = Path(project_path)
    md_path = project / ".cc-autopipe" / "CURRENT_TASK.md"
    md_data = current_task.parse_file(md_path)
    if not md_data:
        # Missing or empty file → nothing to sync. Caller should not
        # treat this as an error; it's the normal path before Claude
        # has written anything.
        return False

    s = state.read(project)
    new_task = state.CurrentTask.from_dict(md_data)
    # Claude is authoritative — overwrite state.current_task. Preserves
    # stages_completed exactly as Claude listed them (so a stage that
    # disappears from CURRENT_TASK.md is intentionally dropped).
    s.current_task = new_task
    state.write(project, s)
    return True


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="stop_helper.py")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_sync = sub.add_parser(
        "sync",
        help="Read CURRENT_TASK.md, project into state.json.current_task",
    )
    p_sync.add_argument("project")

    args = parser.parse_args(argv)

    if args.cmd == "sync":
        try:
            sync_current_task_from_md(args.project)
        except Exception as exc:  # noqa: BLE001 — hook helper must not abort
            print(f"[stop_helper] sync failed: {exc}", file=sys.stderr)
            # Match the always-exit-0 contract for hooks.
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
