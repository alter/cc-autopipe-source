"""orchestrator.backlog_gate — physical guarantee that `[x]` requires a
verify stamp OR a matching PROMOTION file on disk.

v1.5.7 BACKLOG-WRITE-GATE. AI-trade audit 2026-05-13 showed 947 of 953
closed tasks lacked an engine-side `verify_completed` event in
aggregate.jsonl — subagents had edited backlog.md directly via
Edit/MultiEdit/Write and bypassed the verify pipeline. Result: 32% of
closures were self-admitted DEFERRED, Phase 4 NN2 7/8 stubs, Phase 4
NN3 + multi-asset entirely fictitious.

The gate is structural, not prompt-based: on every sweep tick (and at
startup), `audit_and_revert` diffs `backlog.md` against a per-project
snapshot. Any task that transitioned from `[ ]` or `[~]` to `[x]` since
the last audit must be backed by either:

  (a) a `verify_completed task_id=X passed=true` event in the user-home
      aggregate.jsonl from the last 24h, OR
  (b) a `data/debug/CAND_<task>_PROMOTION.md` file on disk (or its
      `vec_`-stripped alias for the AI-trade short-name convention).

A line that satisfies neither is rewritten in place from `[x]` back to
`[ ]` and an `unverified_close_blocked` event is appended to
aggregate.jsonl so the operator can grep the trail.

First-deploy legacy amnesty: when no snapshot exists, all current
`[x]` lines are treated as pre-v1.5.7 history. Only NEW closures
(snapshot says `[ ]`/`[~]` and current says `[x]`) trigger the gate.
This avoids forcing operators to manually re-verify hundreds of
historically-closed tasks the first time the engine runs after upgrade.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import state  # noqa: E402

# v1.5.7 BACKLOG-WRITE-GATE: how far back into aggregate.jsonl to look
# for a matching verify_completed event. 24h handles overnight runs
# without forcing the engine to scan the entire log every sweep.
VERIFY_WINDOW_HOURS = 24

# v1.5.8 STALE-PROMOTION-REJECTED: grace window when comparing PROMOTION
# mtime against the snapshot mtime. Agents can legitimately write the
# PROMOTION file ~immediately before flipping the row to [x] in the same
# cycle, and a noisy clock skew between filesystem stat and the
# snapshot's own write moment can place those events microseconds apart.
# 60s tolerates that race without opening a loophole large enough to
# resurrect a long-stale PROMOTION as "fresh evidence".
PROMOTION_FRESHNESS_GRACE_SEC = 60

# Recognises backlog rows that carry a vec_-prefixed canonical task id.
# Format: `- [<state>] [<type>] [P<n>] vec_<rest> — description`.
# Meta-tasks (meta_expand_backlog_*) and phase_gate_* rows are
# intentionally NOT matched — they have their own lifecycles and never
# have PROMOTION files.
TASK_ID_RE = re.compile(
    r"^- \[([ ~x])\]\s+\[[^\]]+\]\s+\[P\d+\]\s+(vec_\w+)"
)


def _read_recent_verify_events(user_home: Path, cutoff: datetime) -> set[str]:
    """Scan aggregate.jsonl for `verify_completed passed=true` events
    newer than cutoff and return their task_ids.

    Cheap textual prefilter (`"event":"verify_completed"` substring)
    avoids JSON-parsing every line in a multi-month log. The full
    parse only runs on candidate lines.
    """
    log = user_home / "log" / "aggregate.jsonl"
    if not log.exists():
        return set()
    verified: set[str] = set()
    try:
        with log.open("r", encoding="utf-8") as f:
            for line in f:
                if '"event":"verify_completed"' not in line:
                    continue
                if '"passed":true' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts_raw = rec.get("ts")
                if not ts_raw:
                    continue
                try:
                    ts = datetime.fromisoformat(
                        ts_raw.replace("Z", "+00:00")
                    )
                except ValueError:
                    continue
                if ts < cutoff:
                    continue
                tid = rec.get("task_id")
                if tid:
                    verified.add(tid)
    except OSError:
        return set()
    return verified


def _find_promotion_file(debug_dir: Path, task_id: str) -> Path | None:
    """Locate the PROMOTION file for `task_id`, accepting either the
    full `CAND_<task_id>_PROMOTION.md` filename or the AI-trade short
    variant with the `vec_` prefix stripped.

    The AI-trade convention historically wrote PROMOTION filenames
    without the `vec_` prefix even when the backlog id carries it.
    v1.5.5 ORPHAN-RESCAN-FIX taught the leaderboard path to canonicalise
    via the body `**Task:**` field; the gate only needs a presence + mtime
    check, so accepting either filename form is enough.

    Returns the first matching path that exists, or None.
    """
    candidates = [debug_dir / f"CAND_{task_id}_PROMOTION.md"]
    if task_id.startswith("vec_"):
        short = task_id[len("vec_"):]
        candidates.append(debug_dir / f"CAND_{short}_PROMOTION.md")
    for p in candidates:
        if p.exists():
            return p
    return None


def _is_fresh_promotion(
    promotion_path: Path,
    snapshot_mtime: float,
    grace_seconds: int = PROMOTION_FRESHNESS_GRACE_SEC,
) -> bool:
    """v1.5.8 STALE-PROMOTION-REJECTED: True iff the PROMOTION file was
    written AFTER (or within `grace_seconds` of) the snapshot that
    observed the task as open.

    Pre-v1.5.8 the gate accepted any existing `CAND_*_PROMOTION.md` as
    proof. AI-trade had ~520 PROMOTION files from earlier fabrication
    runs in `data/debug/` before May 13; cleanup re-opened the
    corresponding rows, and the agent re-closed them on the next sweep.
    The stale file satisfied the v1.5.7 `Path.exists()` check, the gate
    passed, and ~351 fake closures went unchecked.

    Comparing PROMOTION mtime against snapshot mtime forces the
    PROMOTION to have been (re-)written in the same window as the new
    closure. The grace covers a legitimate close-race where the agent
    writes PROMOTION ~immediately before setting [x] in the same cycle.
    """
    try:
        return promotion_path.stat().st_mtime >= (snapshot_mtime - grace_seconds)
    except OSError:
        return False


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def audit_and_revert(
    project_path: Path, user_home: Path
) -> dict[str, int]:
    """Diff `backlog.md` against the per-project snapshot; revert
    unverified `[x]` transitions back to `[ ]` and emit
    `unverified_close_blocked` events.

    Returns a counters dict:
      - scanned: total backlog rows matched by TASK_ID_RE
      - reverted: NEW [x] transitions without verify proof — rewritten
      - ok_verified: NEW [x] transitions with verify proof — left alone
      - ok_orphan_pre_v157: rows that were already [x] in the snapshot
        (legacy amnesty; never touched)

    Side effects:
      - backlog.md rewritten if reverted > 0
      - snapshot refreshed unconditionally so the NEXT call sees the
        post-revert state as baseline
      - one `unverified_close_blocked` event per reverted row in both
        the per-project progress.jsonl and user-home aggregate.jsonl
    """
    counts = {
        "scanned": 0,
        "reverted": 0,
        "ok_verified": 0,
        "ok_orphan_pre_v157": 0,
    }

    backlog_candidates = [
        project_path / "backlog.md",
        project_path / ".cc-autopipe" / "backlog.md",
    ]
    backlog = next((p for p in backlog_candidates if p.exists()), None)
    if backlog is None:
        return counts

    snap = project_path / ".cc-autopipe" / "backlog_snapshot.md"
    try:
        current = backlog.read_text(encoding="utf-8")
    except OSError:
        return counts
    prev = ""
    if snap.exists():
        try:
            prev = snap.read_text(encoding="utf-8")
        except OSError:
            prev = ""

    cutoff = datetime.now(timezone.utc) - timedelta(hours=VERIFY_WINDOW_HOURS)
    verified_ids = _read_recent_verify_events(user_home, cutoff)
    debug_dir = project_path / "data" / "debug"

    # v1.5.8 STALE-PROMOTION-REJECTED: snapshot mtime anchors freshness.
    # If the snapshot file is missing (first deploy / legacy amnesty
    # path), snap_mtime stays 0 so every PROMOTION counts as fresh —
    # the legacy-amnesty branch below catches those rows separately,
    # so no freshness regression is possible on first run.
    try:
        snap_mtime = snap.stat().st_mtime if snap.exists() else 0.0
    except OSError:
        snap_mtime = 0.0

    prev_state: dict[str, str] = {}
    for line in prev.splitlines():
        m = TASK_ID_RE.match(line)
        if m:
            prev_state[m.group(2)] = m.group(1)

    new_lines: list[str] = []
    for line in current.splitlines():
        m = TASK_ID_RE.match(line)
        if not m:
            new_lines.append(line)
            continue
        state_char, task_id = m.group(1), m.group(2)
        counts["scanned"] += 1
        if state_char != "x":
            new_lines.append(line)
            continue
        prev_char = prev_state.get(task_id)
        if prev_char == "x":
            # Already closed in the snapshot — legacy or previously
            # verified. Leave it alone (legacy amnesty).
            counts["ok_orphan_pre_v157"] += 1
            new_lines.append(line)
            continue
        # NEW [x] transition: must have verify proof or FRESH PROMOTION file.
        has_verify = task_id in verified_ids
        pfile = _find_promotion_file(debug_dir, task_id)
        promotion_stale = False
        if pfile is not None:
            if _is_fresh_promotion(pfile, snap_mtime):
                has_promotion = True
            else:
                has_promotion = False
                promotion_stale = True
        else:
            has_promotion = False
        if has_verify or has_promotion:
            counts["ok_verified"] += 1
            new_lines.append(line)
            continue
        # No proof — revert in place and log.
        counts["reverted"] += 1
        reverted = line.replace("- [x]", "- [ ]", 1)
        new_lines.append(reverted)
        # v1.5.8 STALE-PROMOTION-REJECTED: surface stale-promotion sub-case
        # in the event so operators / analytics can distinguish "agent
        # wrote nothing" from "agent re-closed a row whose PROMOTION was
        # from a prior fabrication run."
        if promotion_stale:
            reason = (
                "no verify_completed event AND PROMOTION file is stale "
                "(mtime < snapshot mtime - grace)"
            )
        else:
            reason = "no verify_completed event AND no PROMOTION file"
        try:
            pfile_mtime: float | None = None
            if pfile is not None:
                try:
                    pfile_mtime = pfile.stat().st_mtime
                except OSError:
                    pfile_mtime = None
            state.log_event(
                project_path,
                "unverified_close_blocked",
                task_id=task_id,
                reason=reason,
                prev_state=prev_char or "absent_from_snapshot",
                pfile_mtime=pfile_mtime,
                snap_mtime=snap_mtime,
                stale=promotion_stale,
            )
        except Exception:  # noqa: BLE001 — gate must not crash sweep
            pass

    if counts["reverted"] > 0:
        new_text = "\n".join(new_lines)
        if current.endswith("\n"):
            new_text += "\n"
        try:
            _atomic_write(backlog, new_text)
        except OSError:
            # Fail safe — don't crash, don't refresh snapshot from a
            # broken write; next sweep retries.
            return counts

    # Refresh snapshot from the post-revert backlog state so future
    # audits don't re-flag the same rows.
    try:
        snap.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(snap, backlog.read_text(encoding="utf-8"))
    except OSError:
        pass
    return counts
