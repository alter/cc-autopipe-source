#!/usr/bin/env python3
"""Retroactive PROMOTION.md validation for closed tasks that were missed
because the engine filtered by a hardcoded task prefix.

Usage:
    python3 retroactive_promotion_validate.py <project_path> [--prefix vec_p3_] [--dry-run]

Example:
    python3 retroactive_promotion_validate.py \
        /mnt/c/claude/artifacts/repos/AI-trade \
        --prefix vec_p3_ \
        --dry-run

    python3 retroactive_promotion_validate.py \
        /mnt/c/claude/artifacts/repos/AI-trade \
        --prefix vec_p3_
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _find_cc_autopipe_lib() -> Path:
    """Locate cc-autopipe lib dir from CC_AUTOPIPE_HOME or this script's location."""
    home = os.environ.get("CC_AUTOPIPE_HOME")
    if home:
        p = Path(home) / "lib"
        if p.exists():
            return p
    # Try relative to this script: tools/../src/lib
    here = Path(__file__).resolve().parent
    candidate = here.parent / "src" / "lib"
    if candidate.exists():
        return candidate
    raise RuntimeError(
        "Cannot find cc-autopipe lib. Set CC_AUTOPIPE_HOME or run from the "
        "cc-autopipe-source tree."
    )


def _setup_path() -> None:
    lib = _find_cc_autopipe_lib()
    src = lib.parent
    for p in (str(lib), str(src)):
        if p not in sys.path:
            sys.path.insert(0, p)
    # orchestrator package lives in src/orchestrator — needs src on path
    sys.path.insert(0, str(src))


def _load_already_validated(project_path: Path) -> set[str]:
    """Read aggregate.jsonl and collect task IDs that already have a
    promotion_validated or promotion_rejected event — skip those."""
    agg = Path(os.environ.get("CC_AUTOPIPE_USER_HOME", str(Path.home() / ".cc-autopipe"))) \
        / "log" / "aggregate.jsonl"
    seen: set[str] = set()
    if not agg.exists():
        return seen
    try:
        for line in agg.read_text(encoding="utf-8").splitlines():
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue
            if evt.get("project") != project_path.name:
                continue
            if evt.get("event") in (
                "promotion_validated",
                "promotion_validated_attempt",
                "promotion_rejected",
                "promotion_conditional",
            ):
                tid = evt.get("task_id")
                if tid:
                    seen.add(tid)
    except OSError:
        pass
    return seen


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_path", help="Absolute path to the cc-autopipe project")
    parser.add_argument("--prefix", default="vec_p3_",
                        help="Backlog task ID prefix to scan (default: vec_p3_)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be done without writing anything")
    parser.add_argument(
        "--reprocess",
        action="store_true",
        help="Re-validate tasks that already have a promotion_validated event "
             "(overwrites existing leaderboard entry with corrected metrics)",
    )
    args = parser.parse_args()

    _setup_path()

    import backlog as backlog_lib
    import promotion as promotion_lib
    import state

    project = Path(args.project_path).resolve()
    if not project.exists():
        print(f"ERROR: project path does not exist: {project}", file=sys.stderr)
        sys.exit(1)

    backlog_path = project / "backlog.md"
    if not backlog_path.exists():
        print(f"ERROR: backlog.md not found at {backlog_path}", file=sys.stderr)
        sys.exit(1)

    already_done = set() if args.reprocess else _load_already_validated(project)
    print(f"Project:   {project}")
    print(f"Prefix:    {args.prefix}")
    print(f"Dry-run:   {args.dry_run}")
    print(f"Reprocess: {args.reprocess}")
    print(f"Already validated (skipped): {len(already_done)}")
    print()

    all_tasks = backlog_lib.parse_all_tasks(backlog_path)
    candidates = [
        t for t in all_tasks
        if t.status == "x"
        and t.id.startswith(args.prefix)
        and t.task_type == "implement"
        and t.id not in already_done
    ]
    print(f"Closed {args.prefix}* [implement] tasks to validate: {len(candidates)}")
    print()

    stats = {"promoted": 0, "rejected": 0, "conditional": 0,
             "no_file": 0, "no_verdict": 0, "quarantined": 0, "errors": 0}

    for item in candidates:
        p_path = promotion_lib.promotion_path(project, item.id)
        if not p_path.exists():
            print(f"  SKIP  {item.id}  — PROMOTION.md not found ({p_path.name})")
            stats["no_file"] += 1
            continue

        verdict = promotion_lib.parse_verdict(p_path)
        if verdict is None:
            print(f"  SKIP  {item.id}  — no parseable verdict in {p_path.name}")
            stats["no_verdict"] += 1
            continue

        if verdict == "PROMOTED":
            ok, missing = promotion_lib.validate_v2_sections(p_path, task_id=item.id)
            if args.dry_run:
                sections_status = "sections OK" if ok else f"MISSING: {', '.join(missing)}"
                print(f"  DRY   {item.id}  — PROMOTED  {sections_status}")
            else:
                state.log_event(project, "promotion_validated_attempt",
                                task_id=item.id, origin="retroactive_validate")
                state.log_event(project, "promotion_v2_sections_check",
                                task_id=item.id, all_present=ok,
                                missing=",".join(missing),
                                strict=promotion_lib.requires_full_v2_validation(item.id),
                                origin="retroactive_validate")
                if ok:
                    try:
                        metrics = promotion_lib.parse_metrics(p_path)
                        promotion_lib.on_promotion_success(project, item, metrics)
                        state.log_event(project, "promotion_validated",
                                        task_id=item.id, origin="retroactive_validate",
                                        **{k: v for k, v in metrics.items() if v is not None})
                        print(f"  OK    {item.id}  — PROMOTED + validated")
                        stats["promoted"] += 1
                    except Exception as exc:
                        print(f"  ERR   {item.id}  — on_promotion_success failed: {exc!r}")
                        stats["errors"] += 1
                else:
                    promotion_lib.quarantine_invalid(project, item, missing)
                    print(f"  QUAR  {item.id}  — PROMOTED but missing sections: {missing}")
                    stats["quarantined"] += 1

        elif verdict == "REJECTED":
            if not args.dry_run:
                state.log_event(project, "promotion_rejected",
                                task_id=item.id, origin="retroactive_validate")
            print(f"  REJ   {item.id}  — REJECTED")
            stats["rejected"] += 1

        elif verdict == "CONDITIONAL":
            if not args.dry_run:
                state.log_event(project, "promotion_conditional",
                                task_id=item.id, origin="retroactive_validate")
            print(f"  COND  {item.id}  — CONDITIONAL")
            stats["conditional"] += 1

    print()
    print("=== Summary ===")
    if args.dry_run:
        print("(DRY RUN — nothing written)")
    for k, v in stats.items():
        if v:
            print(f"  {k:12s}: {v}")
    print("Done.")


if __name__ == "__main__":
    main()
