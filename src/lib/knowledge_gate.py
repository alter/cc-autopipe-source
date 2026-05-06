#!/usr/bin/env python3
"""knowledge_gate.py — v1.3.3 Group N detach-time enforcement gate.

Refuses a detach (exit code 3) when the project has a recorded verdict
event whose timestamp is more recent than `knowledge.md` mtime. This
forces Claude to append a knowledge entry before continuing instead of
losing the lesson on the next REJECT/ACCEPT.

Empirical justification: 2026-05-05 production run on AI-trade —
vec_multihead ran A→D, REJECTED with sum -10.05%, then Claude moved
straight to vec_rl without updating knowledge.md. A second consecutive
REJECT would have lost the lesson permanently.

CLI:

    python3 knowledge_gate.py <project_path>

Exit codes:
    0  — knowledge.md is current (or no verdict has fired yet)
    3  — gate failure: knowledge.md missing or stale; stderr explains
    1  — unexpected error (still treated as "do not block silently"
         by the helper wrapper)
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

# bare-name import via sibling state.py
_LIB = Path(__file__).resolve().parent
sys.path.insert(0, str(_LIB))
import state  # noqa: E402

EXIT_OK = 0
EXIT_FAIL_GENERIC = 1
EXIT_GATE = 3


def _parse_iso_utc(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None


def check(project_path: Path) -> tuple[int, str]:
    """Returns (exit_code, message). Caller writes message to stderr.

    Pure function — no side effects on state. Reading state.json is
    safe (no migration write triggered until something else writes).
    """
    s = state.read(project_path)
    if not s.last_verdict_event_at:
        return EXIT_OK, ""

    verdict_dt = _parse_iso_utc(s.last_verdict_event_at)
    if verdict_dt is None:
        return EXIT_OK, ""

    knowledge = project_path / ".cc-autopipe" / "knowledge.md"
    task_label = s.last_verdict_task_id or "(unknown task)"

    if not knowledge.exists():
        msg = (
            "BLOCKED: detach rejected.\n"
            f"Last verdict ({task_label}) at {s.last_verdict_event_at} "
            "was never recorded in knowledge.md.\n"
            "Action required: append a knowledge entry summarizing what "
            "worked/failed and lessons learned, then retry detach. "
            f"Knowledge file: {knowledge}\n"
        )
        return EXIT_GATE, msg

    try:
        mtime_ts = knowledge.stat().st_mtime
    except OSError as exc:
        return EXIT_FAIL_GENERIC, f"knowledge_gate: stat failed: {exc!r}\n"

    knowledge_dt = datetime.fromtimestamp(mtime_ts, tz=timezone.utc)
    if knowledge_dt < verdict_dt:
        msg = (
            "BLOCKED: detach rejected.\n"
            f"knowledge.md (mtime {knowledge_dt.isoformat()}) is older "
            f"than last verdict event ({s.last_verdict_event_at}).\n"
            f"Action required: append knowledge entry for task "
            f"'{task_label}' verdict, then retry detach.\n"
        )
        return EXIT_GATE, msg

    return EXIT_OK, ""


def main(argv: list[str]) -> int:
    if not argv:
        sys.stderr.write("usage: knowledge_gate.py <project>\n")
        return EXIT_FAIL_GENERIC
    project = Path(argv[0])
    if not project.exists():
        sys.stderr.write(f"knowledge_gate: project missing: {project}\n")
        return EXIT_FAIL_GENERIC
    rc, msg = check(project)
    if msg:
        sys.stderr.write(msg)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
