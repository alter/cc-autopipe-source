#!/usr/bin/env python3
"""failures.py — read + categorize entries from failures.jsonl.

Refs: SPEC-v1.2.md Bug H "Smart escalation".

The v1.0 escalation path (Stage L) escalates to opus after any 3
consecutive cycles where state.consecutive_failures >= 3. Real-world
test on AI-trade ML R&D revealed: most "failures" are actually
verify.sh returning passed=false because Claude's outputs aren't
where verify expects (structural, not capability). Throwing more
opus quota at a structural issue burns quota without solving it.

Bug H replaces the binary "fail count >= 3 → escalate" with a
categorisation:

  - 3 consecutive `claude_subprocess_failed` (claude crashed on rc!=0)
        → escalate to opus (existing behaviour preserved — these
        are the cases more capability *might* help)
  - 3 consecutive `verify_failed` (verify ran cleanly, said
        passed=false)
        → write HUMAN_NEEDED.md + TG; do NOT escalate. The operator
        needs to fix verify.sh expectations, not throw opus at it.
  - 5+ consecutive of any kind, mixed
        → mark phase=failed regardless. Both signals firing at once
        means the project is structurally broken.
  - Otherwise → no action (let the project keep cycling).

Public API:
  read_recent(project_path, n=3)            → list[dict]
  categorize_recent(failures)               → dict[str, Any]
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FAILURES_RELATIVE = ".cc-autopipe/memory/failures.jsonl"

# Error type buckets. Anything not in either set is counted toward
# "other" and only contributes to the 5+ mixed cap.
#
# SPEC-v1.2.md Bug H literal: "3 consecutive verify_failed (score=0)
# → HUMAN_NEEDED, no escalation". Only verify_failed (verify ran
# cleanly and reported passed=false) is the structural-mismatch
# signal. verify_malformed (verify.sh emitted bad JSON) and
# verify_missing (no verify.sh) are different — they could be a
# project setup issue OR real-world bugs Claude can fix, so they
# stay in "other" and only contribute to the 5+ mixed cap. This
# preserves v1.0 escalation semantics for those error types.
CRASH_ERRORS = {
    "claude_subprocess_failed",
    "claude_timeout",  # forward-compat: future error type
}
VERIFY_ERRORS = {
    "verify_failed",
}


def read_recent(project_path: str | Path, n: int = 3) -> list[dict[str, Any]]:
    """Read the last n JSON-line entries from failures.jsonl.

    Missing file → empty list. Malformed lines silently skipped (we
    don't want one corrupt entry to mask the categorisation signal).
    Order: oldest first, newest last (matches file order).
    """
    path = Path(project_path) / FAILURES_RELATIVE
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    # Caller asks for last n; trim from the tail.
    return out[-n:] if n > 0 else out


def categorize_recent(failures: list[dict[str, Any]]) -> dict[str, Any]:
    """Bucket failures by error type and decide what the orchestrator
    should do.

    Returns a dict with the keys:
      crash_count       — int, number of CRASH_ERRORS in input
      verify_count      — int, number of VERIFY_ERRORS in input
      other_count       — int, anything else
      total             — int, len(failures)
      recommend_escalation   — bool
      recommend_human_needed — bool
      recommend_failed       — bool   (5+ mixed; engine should phase=failed)
      reason            — str, short human-readable summary for logs
    """
    crash = [f for f in failures if f.get("error") in CRASH_ERRORS]
    verify = [f for f in failures if f.get("error") in VERIFY_ERRORS]
    other = [
        f
        for f in failures
        if f.get("error") not in CRASH_ERRORS and f.get("error") not in VERIFY_ERRORS
    ]
    total = len(failures)

    recommend_escalation = len(crash) >= 3
    recommend_human_needed = len(verify) >= 3 and not recommend_escalation
    # Mixed-pattern fallback: 5+ failures of any kind without 3 of one
    # category dominating. Project is structurally broken.
    recommend_failed = (
        total >= 5 and not recommend_escalation and not recommend_human_needed
    )

    if recommend_escalation:
        reason = (
            f"{len(crash)}/{total} recent failures are claude_subprocess_failed "
            "— claude is actually crashing, escalating to opus might help"
        )
    elif recommend_human_needed:
        reason = (
            f"{len(verify)}/{total} recent failures are verify_failed — "
            "structural mismatch likely (verify expectations vs. Claude's "
            "outputs), no point escalating"
        )
    elif recommend_failed:
        reason = (
            f"{total} mixed failures with no dominant category — project "
            "is structurally broken, marking failed"
        )
    else:
        reason = (
            f"{total} recent failures (crash={len(crash)} verify={len(verify)} "
            f"other={len(other)}) — under thresholds, no action"
        )

    return {
        "crash_count": len(crash),
        "verify_count": len(verify),
        "other_count": len(other),
        "total": total,
        "recommend_escalation": recommend_escalation,
        "recommend_human_needed": recommend_human_needed,
        "recommend_failed": recommend_failed,
        "reason": reason,
    }
