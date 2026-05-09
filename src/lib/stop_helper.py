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
import findings as findings_lib  # noqa: E402
import knowledge as knowledge_lib  # noqa: E402
import state  # noqa: E402


def maybe_clear_knowledge_update_flag(project_path: str | Path) -> bool:
    """v1.3 I4: clear knowledge_update_pending if knowledge.md mtime
    has moved past the recorded baseline since the verdict was emitted.

    v1.3.8 SENTINEL-RACE-FIX: emits the cleared baseline + current mtime
    in the `knowledge_updated_detected` event payload so the v1.3.6/8
    arm→clear→re-arm race is observable from aggregate.jsonl. The reset
    of `knowledge_baseline_mtime` to None (which the v1.3 code already
    does) makes the next arming start fresh — the v1.3.8 fix is the
    arming-side `_safe_baseline_mtime` snapshot, this just emits the
    diagnostic state alongside.

    Returns True iff the flag was cleared this call.
    """
    project = Path(project_path)
    s = state.read(project)
    if not s.knowledge_update_pending:
        return False
    if s.knowledge_baseline_mtime is None:
        return False
    baseline_was = s.knowledge_baseline_mtime
    current_mtime = knowledge_lib.get_mtime_or_zero(project)
    if current_mtime <= baseline_was:
        return False
    s.knowledge_update_pending = False
    s.knowledge_baseline_mtime = None
    s.knowledge_pending_reason = None
    state.write(project, s)
    state.log_event(
        project,
        "knowledge_updated_detected",
        baseline_was=baseline_was,
        current_mtime=current_mtime,
    )
    return True


def _diff_new_stages(prev: list[str], new: list[str]) -> list[str]:
    """Return entries in `new` that aren't in `prev`, preserving order."""
    seen = set(prev)
    out: list[str] = []
    for s in new:
        if s and s not in seen:
            out.append(s)
            seen.add(s)
    return out


def sync_current_task_from_md(project_path: str | Path) -> bool:
    """Read CURRENT_TASK.md, update state.json.current_task.

    Returns True if state was modified (and written), False otherwise.
    Side effect (v1.3 A1): for every NEW entry in stages_completed, append
    a corresponding finding to .cc-autopipe/findings_index.md so the
    SessionStart hook can re-inject it after a context compaction.
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
    prev_stages: list[str] = []
    if s.current_task is not None:
        prev_stages = list(s.current_task.stages_completed)

    new_task = state.CurrentTask.from_dict(md_data)
    # Claude is authoritative — overwrite state.current_task. Preserves
    # stages_completed exactly as Claude listed them (so a stage that
    # disappears from CURRENT_TASK.md is intentionally dropped).
    s.current_task = new_task
    state.write(project, s)

    # v1.3 A1 — auto-append findings for stage_completed transitions.
    new_stages = _diff_new_stages(prev_stages, new_task.stages_completed)
    if new_stages and new_task.id:
        for stage in new_stages:
            try:
                findings_lib.append_finding(
                    project_dir=project,
                    task_id=new_task.id,
                    stage=stage,
                    notes=new_task.claude_notes or "",
                    artifact_paths=list(new_task.artifact_paths),
                )
            except Exception as exc:  # noqa: BLE001 — hook contract
                print(
                    f"[stop_helper] findings append failed: {exc}",
                    file=sys.stderr,
                )

    # v1.3 I4: clear the knowledge_update_pending flag if Claude has
    # touched knowledge.md since the last verdict. Best-effort.
    try:
        maybe_clear_knowledge_update_flag(project)
    except Exception as exc:  # noqa: BLE001
        print(
            f"[stop_helper] knowledge clear failed: {exc}",
            file=sys.stderr,
        )
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
