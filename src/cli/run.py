#!/usr/bin/env python3
"""run.py — implements `cc-autopipe run <project> [--once]` per SPEC.md §12.6.

Single-cycle execution path: bypasses the singleton orchestrator lock,
acquires the per-project lock normally, runs ONE cycle, exits.

Used by:
  - Roman during development (test a project without starting the loop)
  - Stage G hello-fullstack smoke test
  - tests/integration/test_cli.py and Stage F smoke

Reuses orchestrator.process_project so behaviour is identical to a
loop-driven cycle (pre-flight quota check, hooks, state transitions).
The orchestrator file has no .py extension; we import it via
importlib.

Exit codes:
  0 — cycle completed (phase ∈ {active, done, paused})
  1 — project missing, uninitialized, or per-project lock held elsewhere
  2 — phase ended in failed (3+ consecutive failures triggered)

Refs: SPEC.md §12.6, §6.1
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent
_ORCHESTRATOR_PATH = _SRC / "orchestrator"


def _import_orchestrator():
    """Load the executable orchestrator script as a module.

    The file has no .py extension, so importlib's default extension-based
    finder doesn't recognize it. Pass an explicit SourceFileLoader.
    """
    loader = SourceFileLoader("orchestrator", str(_ORCHESTRATOR_PATH))
    spec = importlib.util.spec_from_loader("orchestrator", loader)
    if spec is None:
        raise RuntimeError(f"could not import orchestrator from {_ORCHESTRATOR_PATH}")
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="cc-autopipe run",
        description="Run a single cycle for one project (bypasses singleton lock).",
    )
    parser.add_argument("project", help="Project path")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one cycle and exit (currently the only supported mode)",
    )
    args = parser.parse_args(argv)

    if not args.once:
        sys.stderr.write(
            "run: only --once is supported in v0.5. "
            "Use `cc-autopipe start` for the continuous loop.\n"
        )
        return 64

    project = Path(args.project).resolve()
    if not project.exists():
        sys.stderr.write(f"run: project path does not exist: {project}\n")
        return 1

    orch = _import_orchestrator()
    phase = orch.process_project(project)

    if phase == "failed":
        return 2
    if phase in ("missing", "uninit", "locked"):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
