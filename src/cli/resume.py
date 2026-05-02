#!/usr/bin/env python3
"""resume.py — implements `cc-autopipe resume <project>` per SPEC.md §12.7.

Clears PAUSED/FAILED, resets consecutive_failures to 0, and removes
HUMAN_NEEDED.md if present. The next orchestrator cycle will pick the
project up as ACTIVE.

Refs: SPEC.md §12.7, §6.1 (orchestrator phase machine)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent / "lib"
sys.path.insert(0, str(_LIB))
import state  # noqa: E402


def _resolve_project(path_arg: str) -> Path:
    p = Path(path_arg).resolve()
    if not p.exists():
        sys.stderr.write(f"resume: project path does not exist: {p}\n")
        sys.exit(1)
    if not (p / ".cc-autopipe").exists():
        sys.stderr.write(
            f"resume: {p} is not initialised (run `cc-autopipe init` first)\n"
        )
        sys.exit(1)
    return p


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="cc-autopipe resume",
        description="Clear PAUSED/FAILED on a project, reset failures.",
    )
    parser.add_argument("project", help="Project path to resume")
    args = parser.parse_args(argv)

    project = _resolve_project(args.project)

    s = state.read(project)
    prev_phase = s.phase
    prev_failures = s.consecutive_failures
    prev_escalated = s.escalated_next_cycle

    s.phase = "active"
    s.consecutive_failures = 0
    s.paused = None
    # Stage L: a resume is the operator's "fresh start" — clear the
    # escalation flag too so the resumed cycle uses the default model
    # (sonnet) rather than continuing under opus from a prior burn.
    s.escalated_next_cycle = False
    state.write(project, s)

    human_needed = project / ".cc-autopipe" / "HUMAN_NEEDED.md"
    removed_human = False
    if human_needed.exists():
        try:
            human_needed.unlink()
            removed_human = True
        except OSError as exc:
            sys.stderr.write(f"resume: could not remove HUMAN_NEEDED.md: {exc}\n")

    state.log_event(
        project,
        "resume",
        prev_phase=prev_phase,
        prev_failures=prev_failures,
        removed_human_needed=removed_human,
        prev_escalated=prev_escalated,
    )

    print(f"✓ resumed: {project.name}")
    print(f"  phase: {prev_phase} → active")
    if prev_failures:
        print(f"  consecutive_failures: {prev_failures} → 0")
    if prev_escalated:
        print("  escalated_next_cycle: True → False")
    if removed_human:
        print("  HUMAN_NEEDED.md removed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
