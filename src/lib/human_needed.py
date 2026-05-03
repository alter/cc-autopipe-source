#!/usr/bin/env python3
"""human_needed.py — write HUMAN_NEEDED.md to a project.

Refs: SPEC-v1.2.md Bug H. The orchestrator already had an inline
HUMAN_NEEDED writer (`_write_human_needed` in src/orchestrator) for
the 3-consecutive-failures path. Bug H needs additional templated
messages — verify-pattern and mixed-pattern — that explain WHY
escalation was skipped, otherwise the operator may try to escalate
manually and burn opus quota on a structural problem.

Public API:
  write(project_path, title, body) -> None
  write_verify_pattern(project_path, recent) -> None

Atomic write via tmpfile + replace; never raises (file IO errors
are swallowed, matching the orchestrator's hook contract).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

HUMAN_NEEDED_FILENAME = "HUMAN_NEEDED.md"


def _target(project_path: str | Path) -> Path:
    return Path(project_path) / ".cc-autopipe" / HUMAN_NEEDED_FILENAME


def write(project_path: str | Path, title: str, body: str) -> None:
    """Write HUMAN_NEEDED.md atomically with the given title + body.

    Format:
        # {title}

        {body}
    """
    path = _target(project_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(f"# {title}\n\n{body}\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        # Hook-helper contract: never raise.
        pass


def write_verify_pattern(
    project_path: str | Path, recent: list[dict[str, Any]]
) -> None:
    """Bug H verify-failed pattern: 3 consecutive verify_failed without
    a crash signal. The operator needs to look at verify.sh
    expectations vs. what Claude is producing — escalating to opus
    will not help.
    """
    last_n = (
        "\n".join(
            f"  - ts={f.get('ts')} score={(f.get('details') or {}).get('score')!r}"
            for f in recent[-3:]
        )
        or "  (none)"
    )
    body = (
        "verify.sh returned passed=false 3 cycles in a row.\n"
        "\n"
        "Likely causes:\n"
        "- verify.sh expectations don't match what Claude is producing\n"
        "  (paths, JSON shape, prd.md acceptance items)\n"
        "- Claude is making real progress but verify can't see it —\n"
        "  consider adding `in_progress: true` to verify output during\n"
        "  long operations (Bug B)\n"
        "- Real failure: Claude can't make work pass acceptance criteria\n"
        "\n"
        "Engine did NOT auto-escalate to opus because the failure pattern\n"
        "is verify-driven, not subprocess-driven. Throwing opus at a\n"
        "structural mismatch burns quota without solving the issue.\n"
        "\n"
        "Last 3 verify failures:\n"
        f"{last_n}\n"
        "\n"
        "After fixing verify.sh (or breaking the task into smaller PRD\n"
        "items), run `cc-autopipe resume <project>` to restart."
    )
    write(project_path, "Human attention required (verify pattern)", body)


def write_mixed_pattern(project_path: str | Path, total: int) -> None:
    """Bug H mixed pattern: 5+ failures with no dominant category."""
    body = (
        f"Project has accumulated {total} consecutive failures with no\n"
        "dominant category — mix of claude crashes and verify rejections.\n"
        "Engine has marked it FAILED.\n"
        "\n"
        "Inspect `.cc-autopipe/memory/failures.jsonl` to spot the pattern.\n"
        "Either the task is too ill-defined, the verify gates are\n"
        "miscalibrated, or claude is genuinely struggling. After the\n"
        "underlying cause is addressed, run `cc-autopipe resume <project>`."
    )
    write(project_path, "Human attention required (mixed pattern)", body)
