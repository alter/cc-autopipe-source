"""recovery_revert_fake_closures.py — operator rollback for the v1.5.7
gate gap. v1.5.8 MAY-13-RECOVERY-SCRIPT.

v1.5.7 BACKLOG-WRITE-GATE only ran from the phase-done sweep + at
startup; during phase=active agents could close ~hundreds of tasks per
hour without the gate firing, AND any pre-existing
`CAND_*_PROMOTION.md` stub from a prior fabrication run satisfied the
v1.5.7 `Path.exists()` check. AI-trade 2026-05-13 produced ~351 such
closures in 3 hours.

v1.5.8's GATE-ALWAYS-RUNS + STALE-PROMOTION-REJECTED close the gap
going forward, but the closures already landed are still `[x]` on disk.
This module reverts those `[x]` rows that lack a "fresh" PROMOTION
relative to a caller-supplied `since` timestamp.

Logic for one project:
  - Walk every `- [x] [<type>] [P<n>] vec_<id>` row in backlog.md.
  - For each, find the corresponding `CAND_<id>_PROMOTION.md` or
    `CAND_<short_id>_PROMOTION.md` (AI-trade short-name variant).
  - If a PROMOTION file exists AND its mtime >= since: keep the closure.
  - Otherwise: revert to `[ ]` (operator-driven cleanup).

Dry-run by default; `--apply` actually rewrites backlog.md and emits
one summary event on the per-project progress log.

Operator usage (after deploying v1.5.8):
    python3 state.py revert-fake-closures \\
        /mnt/c/claude/artifacts/repos/AI-trade 2026-05-13T00:00:00Z
    # review output
    python3 state.py revert-fake-closures \\
        /mnt/c/claude/artifacts/repos/AI-trade 2026-05-13T00:00:00Z --apply
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Match the canonical `- [x] [<type>] [P<n>] vec_<id>` shape used
# everywhere else in the engine. Meta-task IDs (meta_expand_backlog_*,
# phase_gate_*) are intentionally NOT matched — they have their own
# lifecycles and never have PROMOTION files.
TASK_RE = re.compile(
    r"^- \[x\]\s+\[[^\]]+\]\s+\[P\d+\]\s+(vec_\w+)"
)


def _parse_since(since_iso: str) -> float:
    """Parse a `--since` ISO timestamp into a UNIX float. Accepts both
    `Z` and `+00:00` offset suffixes."""
    dt = datetime.fromisoformat(since_iso.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _has_fresh_promotion(
    debug_dir: Path, task_id: str, since_ts: float
) -> bool:
    """True iff one of the candidate PROMOTION filenames exists and was
    written/modified at or after `since_ts`. Mirrors the freshness
    semantic of `backlog_gate._is_fresh_promotion` but anchored to a
    caller-supplied timestamp instead of the snapshot mtime."""
    candidates = [debug_dir / f"CAND_{task_id}_PROMOTION.md"]
    if task_id.startswith("vec_"):
        short = task_id[len("vec_"):]
        candidates.append(debug_dir / f"CAND_{short}_PROMOTION.md")
    for p in candidates:
        try:
            if p.exists() and p.stat().st_mtime >= since_ts:
                return True
        except OSError:
            continue
    return False


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def revert_fake_closures(
    project_path: Path, since_iso: str, apply: bool = False
) -> dict[str, object]:
    """Identify `[x]` rows in `project_path/backlog.md` that lack a
    PROMOTION file mtime >= `since_iso`. Returns a result dict with
    counts and the per-task_id sample.

    With `apply=False` (default): no filesystem mutation, no event.
    With `apply=True`: rewrites backlog.md and emits one
    `revert_fake_closures_applied` event via state.log_event so the
    operation is observable in aggregate.jsonl.
    """
    since_ts = _parse_since(since_iso)
    backlog_candidates = [
        project_path / "backlog.md",
        project_path / ".cc-autopipe" / "backlog.md",
    ]
    backlog = next((p for p in backlog_candidates if p.exists()), None)
    result: dict[str, object] = {
        "project": str(project_path),
        "since_iso": since_iso,
        "backlog_path": str(backlog) if backlog else None,
        "open_before": 0,
        "closed_before": 0,
        "candidates": [],
        "reverted": 0,
        "applied": bool(apply),
    }
    if backlog is None:
        return result

    debug_dir = project_path / "data" / "debug"
    text = backlog.read_text(encoding="utf-8")
    lines = text.splitlines()
    result["open_before"] = text.count("- [ ]")
    result["closed_before"] = text.count("- [x]")

    indices: list[int] = []
    candidates: list[str] = []
    for i, line in enumerate(lines):
        m = TASK_RE.match(line)
        if not m:
            continue
        task_id = m.group(1)
        if _has_fresh_promotion(debug_dir, task_id, since_ts):
            continue
        indices.append(i)
        candidates.append(task_id)
    result["candidates"] = candidates

    if not apply:
        return result

    if not indices:
        result["reverted"] = 0
        return result

    for i in indices:
        lines[i] = lines[i].replace("- [x]", "- [ ]", 1)
    new_text = "\n".join(lines)
    if text.endswith("\n"):
        new_text += "\n"
    _atomic_write(backlog, new_text)
    result["reverted"] = len(indices)

    # Best-effort event — never crash the operator's recovery script
    # because state.log_event failed. The CLI's exit code carries the
    # actual outcome.
    try:
        import state  # noqa: PLC0415

        state.log_event(
            project_path,
            "revert_fake_closures_applied",
            since_iso=since_iso,
            reverted=len(indices),
            sample=",".join(candidates[:10]),
        )
    except Exception:  # noqa: BLE001
        pass
    return result


def _print_report(result: dict[str, object]) -> None:
    """Operator-facing report (mirrors the v1.5.5 rebuild-leaderboard
    style: human-readable header + a sample list). For machine-readable
    output, callers should consume `revert_fake_closures` directly
    rather than parsing stdout."""
    print(f"project:             {result['project']}")
    print(f"since:               {result['since_iso']}")
    print(f"backlog path:        {result['backlog_path']}")
    print(f"open before:         {result['open_before']}")
    print(f"closed before:       {result['closed_before']}")
    candidates = result.get("candidates") or []
    print(f"revert candidates:   {len(candidates)}")
    if candidates:
        print("sample (first 10):")
        for tid in candidates[:10]:
            print(f"  {tid}")
    if result["applied"]:
        print(f"reverted:            {result['reverted']}")
    else:
        print(
            f"\ndry-run. re-run with --apply to revert "
            f"{len(candidates)} task(s)."
        )


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="recovery_revert_fake_closures.py",
        description=(
            "v1.5.8 MAY-13-RECOVERY: revert [x] rows in backlog.md that "
            "lack a PROMOTION file mtime >= the supplied --since "
            "timestamp. Dry-run by default; pass --apply to mutate."
        ),
    )
    ap.add_argument("project_path", type=Path)
    ap.add_argument(
        "since_iso",
        help="ISO timestamp; [x] rows with no PROMOTION file at or "
             "after this point will be reverted",
    )
    ap.add_argument(
        "--apply",
        action="store_true",
        help="Actually rewrite backlog.md (omit for dry-run)",
    )
    args = ap.parse_args(argv)

    if not args.project_path.exists():
        print(f"project path not found: {args.project_path}", file=sys.stderr)
        return 2
    result = revert_fake_closures(
        args.project_path, args.since_iso, apply=args.apply
    )
    if result["backlog_path"] is None:
        print(
            f"backlog.md not found under {args.project_path}",
            file=sys.stderr,
        )
        return 2
    _print_report(result)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
