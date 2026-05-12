#!/usr/bin/env python3
"""orchestrator.recovery — failure routing + HUMAN_NEEDED writers.

Includes:
  - _handle_smart_escalation:   v1.2 Bug H smart-escalation router
  - _write_human_needed:        HUMAN_NEEDED.md after consecutive_failures
  - _write_in_progress_cap_human_needed: HUMAN_NEEDED.md after in_progress cap

GROUP B (B3 auto-recovery) and GROUP H (META_REFLECT) extend this module.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from datetime import datetime, timezone
from typing import Iterable

from orchestrator._runtime import _log, _now_iso, _parse_iso_utc, is_shutdown
from orchestrator.alerts import _notify_tg
import failures as failures_lib  # noqa: E402
import human_needed as human_needed_lib  # noqa: E402
import locking  # noqa: E402
import promotion as promotion_lib  # noqa: E402
import state  # noqa: E402

# v1.3 B2: stuck-detection thresholds. Activity-based: a project that
# shows no filesystem / process / stage activity for STUCK_FAIL_SEC is
# marked failed; the warning band fires earlier so the operator can see
# the burn early.
STUCK_WARN_SEC = 30 * 60  # 30 min
STUCK_FAIL_SEC = 60 * 60  # 60 min
# v1.3 B3: auto-recovery cadence + threshold.
RECOVERY_INTERVAL_SEC = 30 * 60  # scan failed projects every 30 min
RECOVERY_AGE_SEC = 60 * 60  # only recover after 1h of inactivity
# v1.3.8 RECOVERY-SWEEP-SENTINEL-TIMEOUT: escape hatch threshold. When a
# `knowledge_update_pending` sentinel has been armed > 4 hours with no
# knowledge.md mtime advance past baseline, treat it as genuinely stuck
# (not "in progress") and force-clear before recovering. Mirrors the 4h
# autonomous-burn limit and is well past any normal verdict→knowledge
# append turnaround. Set high enough not to interfere with legitimate
# slow-running tasks.
SENTINEL_STUCK_THRESHOLD_SEC = 4 * 3600  # 4 hours


def _handle_smart_escalation(
    project_path: Path,
    s: state.State,
    stderr: str,
    esc_cfg: dict[str, object],
    esc_trigger: int,
) -> None:
    """v1.2 Bug H: smart-escalation router.

    Reads recent failures, categorises them, and routes to one of:
      verify-pattern   → phase=failed + HUMAN_NEEDED + no escalation
      crash-pattern    → escalate to opus
      mixed (5+)       → phase=failed + HUMAN_NEEDED
      fallback         → preserve v1.0 deferred-fail semantics

    Mutates `s` and writes state. Caller invokes only when
    `s.consecutive_failures >= 3` and `s.phase == 'active'`.
    """
    recent = failures_lib.read_recent(project_path, n=5)
    cat = failures_lib.categorize_recent(recent)
    _log(f"{project_path.name}: failure category — {cat['reason']}")

    if cat["recommend_human_needed"]:
        # v1.3 H3: replace v1.2's blind verify-pattern HUMAN_NEEDED with
        # an enforced META_REFLECT loop. After 2 failed reflection
        # attempts, fall back to HUMAN_NEEDED (safety net for when
        # Roman returns and the engine genuinely needs a human).
        from orchestrator import reflection as reflection_mod

        action, _target = reflection_mod.trigger_meta_reflect(
            project_path, s, recent
        )
        if action == "triggered":
            # Engine continues; next cycle injects the mandatory block.
            return
        if action == "fallback" or action == "skipped":
            s.phase = "failed"
            state.write(project_path, s)
            state.log_event(
                project_path,
                "escalation_skipped",
                iteration=s.iteration,
                consecutive_failures=s.consecutive_failures,
                crash_count=cat["crash_count"],
                verify_count=cat["verify_count"],
                reason=cat["reason"] + f" (meta_reflect_{action})",
            )
            state.log_event(
                project_path,
                "failed",
                iteration=s.iteration,
                consecutive_failures=s.consecutive_failures,
                pattern="verify",
            )
            human_needed_lib.write_verify_pattern(project_path, recent)
            _notify_tg(
                f"[{project_path.name}] needs human attention — "
                f"verify failing {cat['verify_count']}/{cat['total']} "
                f"cycles, meta_reflect_{action}"
            )
    elif (
        cat["recommend_escalation"]
        and esc_cfg.get("enabled")
        and not s.escalated_next_cycle
    ):
        # Crash pattern → escalate (existing Stage L behaviour).
        s.escalated_next_cycle = True
        state.write(project_path, s)
        state.log_event(
            project_path,
            "escalated_to_opus",
            iteration=s.iteration,
            consecutive_failures=s.consecutive_failures,
            crash_count=cat["crash_count"],
            escalate_to=esc_cfg.get("escalate_to"),
            effort=esc_cfg.get("effort"),
        )
        _log(
            f"{project_path.name}: {cat['crash_count']} recent "
            f"crashes → escalating next cycle to "
            f"{esc_cfg.get('escalate_to')}"
        )
    elif cat["recommend_failed"]:
        # 5+ mixed pattern → give up, no escalation.
        s.phase = "failed"
        state.write(project_path, s)
        state.log_event(
            project_path,
            "failed",
            iteration=s.iteration,
            consecutive_failures=s.consecutive_failures,
            pattern="mixed",
        )
        human_needed_lib.write_mixed_pattern(project_path, total=cat["total"])
        _notify_tg(
            f"[{project_path.name}] mixed-pattern fail — "
            f"{cat['total']} failures, marked FAILED"
        )
    else:
        # No clear pattern but consecutive_failures hit the threshold.
        # v1.0 fallback: try escalation once if available, else fail.
        if (
            esc_cfg.get("enabled")
            and s.consecutive_failures >= esc_trigger
            and not s.escalated_next_cycle
        ):
            s.escalated_next_cycle = True
            state.write(project_path, s)
            state.log_event(
                project_path,
                "escalated_to_opus",
                iteration=s.iteration,
                consecutive_failures=s.consecutive_failures,
                crash_count=cat["crash_count"],
                escalate_to=esc_cfg.get("escalate_to"),
                effort=esc_cfg.get("effort"),
                reason="v1.0_fallback_no_clear_pattern",
            )
            _log(
                f"{project_path.name}: consecutive_failures="
                f"{s.consecutive_failures} (no clear category) → "
                f"escalating next cycle to "
                f"{esc_cfg.get('escalate_to')}"
            )
        else:
            s.phase = "failed"
            state.write(project_path, s)
            state.log_event(
                project_path,
                "failed",
                iteration=s.iteration,
                consecutive_failures=s.consecutive_failures,
                escalation_attempted=bool(s.escalated_next_cycle),
            )
            _write_human_needed(project_path, stderr)


def evaluate_stuck(s: state.State) -> str:
    """Return one of: 'ok', 'warn', 'fail' based on time since last_activity_at.

    Caller (cycle.py) updates `state.last_activity_at` whenever
    activity.detect_activity returns is_active=True. Engine flags the
    project warned at 30min and fails at 60min — long-running training
    that touches checkpoints does not trigger because filesystem changes
    keep resetting the timer.
    """
    if not s.last_activity_at:
        return "ok"
    last = _parse_iso_utc(s.last_activity_at)
    if last is None:
        return "ok"
    elapsed = (datetime.now(timezone.utc) - last).total_seconds()
    if elapsed >= STUCK_FAIL_SEC:
        return "fail"
    if elapsed >= STUCK_WARN_SEC:
        return "warn"
    return "ok"


def _is_sentinel_genuinely_stuck(
    s: state.State, project_path: Path
) -> bool:
    """v1.3.8 RECOVERY-SWEEP-SENTINEL-TIMEOUT: True iff the
    `knowledge_update_pending` sentinel has been armed > threshold AND
    knowledge.md mtime hasn't advanced past baseline.

    AI-trade Phase 2 v2.0 production showed the v1.3.6 sentinel-arming
    race could leave a project's pending flag stuck True forever.
    Without an escape, the recovery sweep refused to reset state for
    that project (per v1.3.2 RECOVERY-SAFE), running every 30 min in an
    infinite skip loop. Group A (cycle.py idempotent arming) prevents
    new instances of the bug; this gate breaks already-stuck projects
    out of the loop after 4h.

    The mtime-advance check is belt-and-suspenders: if mtime moved past
    baseline but the detector hasn't fired yet (next stop_helper call
    will clear it), don't force-clear — let the natural path complete.
    """
    if not s.knowledge_update_pending:
        return False
    if not s.last_activity_at:
        return False
    last_activity = _parse_iso_utc(s.last_activity_at)
    if last_activity is None:
        return False
    age_sec = (
        datetime.now(timezone.utc) - last_activity
    ).total_seconds()
    if age_sec < SENTINEL_STUCK_THRESHOLD_SEC:
        return False
    knowledge_md = project_path / ".cc-autopipe" / "knowledge.md"
    if knowledge_md.exists():
        try:
            current_mtime = knowledge_md.stat().st_mtime
        except OSError:
            current_mtime = 0.0
        baseline = s.knowledge_baseline_mtime or 0.0
        if current_mtime > baseline:
            # Mtime advanced — detector will clear soon; not stuck.
            return False
    return True


def _should_recover(
    s: state.State, project_path: Path | None = None
) -> tuple[bool, str]:
    """v1.3.2 RECOVERY-SAFE: gate `maybe_auto_recover` against active
    enforcement state.

    Returns (should_recover, skip_reason). When should_recover is False
    the caller emits an `auto_recovery_skipped` event with the given
    reason so Roman can `grep aggregate.jsonl` and see which enforcement
    loop kept the sweep from clobbering state.

    A FAILED project with `meta_reflect_pending`, `knowledge_update_pending`,
    or `research_plan_required` is sitting in an in-flight enforcement
    loop — the sweep MUST NOT reset state.json or it will clear the
    pending flag and the engine forgets why it triggered. Same logic
    for non-failed phases (paused/detached/done) which have their own
    lifecycles and never need recovery.

    v1.3.8 RECOVERY-SWEEP-SENTINEL-TIMEOUT: a `knowledge_update_pending`
    sentinel that's been armed > 4h with no knowledge.md mtime advance
    is treated as genuinely stuck (vs. legitimate "in progress") and
    returns True with reason `sentinel_stuck_force_clear`. Caller
    clears the sentinel before resetting phase. project_path is
    optional for backward compatibility with callers that haven't been
    updated; without it the v1.3.8 escape hatch is bypassed.
    """
    if s.phase != "failed":
        return False, f"phase={s.phase}_not_failed"
    # v1.3.8: escape hatch BEFORE enforcement-loop checks. If sentinel
    # is genuinely stuck (no progress > threshold), force-clear and
    # recover.
    if project_path is not None and _is_sentinel_genuinely_stuck(
        s, project_path
    ):
        return True, "sentinel_stuck_force_clear"
    if s.meta_reflect_pending:
        return False, "meta_reflect_in_progress"
    if s.knowledge_update_pending:
        return False, "knowledge_update_in_progress"
    if s.research_plan_required:
        return False, "research_plan_pending"
    last = _parse_iso_utc(s.last_activity_at)
    if last is None:
        # Pre-v1.3 failed project (never had activity tracking) —
        # leave alone to preserve v1.2 manual-resume contract. Only
        # revive projects we know fell into failed under v1.3
        # supervision.
        return False, "no_activity_history"
    elapsed = (datetime.now(timezone.utc) - last).total_seconds()
    if elapsed < RECOVERY_AGE_SEC:
        return False, "recent_activity"
    return True, ""


def maybe_auto_recover(project_path: Path | str) -> bool:
    """v1.3 B3: scan a single project's state and revive it from 'failed'
    if at least RECOVERY_AGE_SEC have passed since the last activity.

    Returns True iff the project was actually transitioned. Caller
    (main.py) invokes this from a periodic background sweep across all
    projects. Per-project lock is acquired non-blocking for the
    state.read/write window — if another process holds it (in-flight
    cycle from another orchestrator, or a stale fcntl), we skip rather
    than race.

    v1.3.2 RECOVERY-SAFE: defers the should-recover decision to
    `_should_recover` so projects with active enforcement state
    (meta_reflect / knowledge_update / research_plan) are skipped
    rather than blindly reset.
    """
    project_path = Path(project_path)
    if not (project_path / ".cc-autopipe").exists():
        return False
    proj_lock = locking.acquire_project(project_path)
    if proj_lock is None:
        _log(
            f"{project_path.name}: skip auto-recovery (per-project lock held)"
        )
        return False
    try:
        s = state.read(project_path)
        should, reason = _should_recover(s, project_path)
        if not should:
            # Only emit the skip event for projects that ARE in `failed`
            # phase — emitting one for every healthy project on every
            # 30-min sweep would flood aggregate.jsonl. The phase=*_not_failed
            # case is the boring default.
            if s.phase == "failed":
                state.log_event(
                    project_path, "auto_recovery_skipped", reason=reason
                )
                _log(
                    f"{project_path.name}: auto-recovery skipped — {reason}"
                )
            return False
        # v1.3.8 RECOVERY-SWEEP-SENTINEL-TIMEOUT: when the escape hatch
        # fires, force-clear the stuck sentinel state BEFORE the standard
        # phase reset so the next cycle doesn't immediately re-arm or get
        # blocked by the sentinel injection. Logged separately so the
        # event trail shows why we touched sentinel state outside the
        # normal verdict→detector→clear path.
        if reason == "sentinel_stuck_force_clear":
            baseline_was = s.knowledge_baseline_mtime
            pending_reason_was = s.knowledge_pending_reason
            s.knowledge_update_pending = False
            s.knowledge_baseline_mtime = None
            s.knowledge_pending_reason = None
            state.log_event(
                project_path,
                "sentinel_force_cleared",
                reason="stuck_>4h_no_mtime_advance",
                baseline_was=baseline_was,
                pending_reason_was=pending_reason_was,
                threshold_sec=SENTINEL_STUCK_THRESHOLD_SEC,
            )
        last = _parse_iso_utc(s.last_activity_at)
        # _should_recover already verified last is not None.
        elapsed = (datetime.now(timezone.utc) - last).total_seconds()

        s.phase = "active"
        s.consecutive_failures = 0
        s.consecutive_in_progress = 0
        s.last_in_progress = False
        s.session_id = None
        s.recovery_attempts += 1
        s.last_activity_at = _now_iso()
        state.write(project_path, s)
        state.log_event(
            project_path,
            "auto_recovery_attempted",
            attempts=s.recovery_attempts,
            elapsed_sec=int(elapsed),
            recover_reason=reason or "stale_failed",
        )
        _log(
            f"{project_path.name}: auto-recovery attempt {s.recovery_attempts} "
            f"(elapsed={int(elapsed)}s)"
        )
        _notify_tg(
            f"auto_recovery {project_path.name} attempt {s.recovery_attempts}"
        )
        return True
    finally:
        proj_lock.release()


@dataclass
class _PseudoItem:
    """Minimal stand-in for backlog.BacklogItem when reconstructing from
    a filename. promotion.on_promotion_success only uses `.id` and
    `.priority`. v1.5.3 ORPHAN-PROMOTION-RESCAN."""

    id: str
    priority: int = 1


def _is_in_leaderboard(project_path: Path, task_id: str) -> bool:
    """Cheap grep-style check against LEADERBOARD.md. v1.5.3."""
    lb = project_path / "data" / "debug" / "LEADERBOARD.md"
    if not lb.exists():
        return False
    try:
        text = lb.read_text(encoding="utf-8")
    except OSError:
        return False
    return f"| {task_id} |" in text


_ORPHAN_PROMOTION_RE = re.compile(r"^CAND_(.+)_PROMOTION\.md$")
_PROMOTION_TASK_FIELD_RE = re.compile(
    r"^\*\*Task:\*\*\s*([A-Za-z0-9_]+)", re.MULTILINE
)


def _extract_task_id_from_body(text: str, fallback_from_filename: str) -> str:
    """v1.5.5 ORPHAN-RESCAN-FIX: derive the canonical task_id from the
    PROMOTION body's `**Task:** <id>` line. AI-trade convention writes
    filenames as `CAND_<short>_PROMOTION.md` but real backlog task IDs
    include the `vec_<phase>_<track>_<descr>` prefix; the filename is a
    stripped display alias only. Returns the body value when present,
    otherwise the filename fallback (e.g. for legacy fixtures that omit
    the field entirely)."""
    m = _PROMOTION_TASK_FIELD_RE.search(text)
    if m:
        return m.group(1)
    return fallback_from_filename


def _backfill_cutoff_from_aggregate(project_path: Path) -> float | None:
    """v1.5.4 RESCAN-MIGRATION-GUARD: pre-v1.5.3 state.json has no
    `last_cycle_ended_at` field. Recover a cutoff by scanning
    aggregate.jsonl for the most recent `cycle_end` event matching this
    project's name. Returns UNIX seconds or None if the log is missing
    or no matching event exists.

    Honors CC_AUTOPIPE_USER_HOME via state._user_home() so tests with
    isolated homes find their own seeded log."""
    log = state._user_home() / "log" / "aggregate.jsonl"
    if not log.exists():
        return None
    project_name = project_path.name
    latest_ts: str | None = None
    try:
        with log.open("r", encoding="utf-8") as f:
            for line in f:
                if '"event":"cycle_end"' not in line:
                    continue
                if f'"project":"{project_name}"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = rec.get("ts")
                if ts and (latest_ts is None or ts > latest_ts):
                    latest_ts = ts
    except OSError:
        return None
    if latest_ts is None:
        return None
    dt = _parse_iso_utc(latest_ts)
    if dt is None:
        return None
    return dt.timestamp()


def rescan_orphan_promotions(project_path: Path | str) -> int:
    """Validate `data/debug/CAND_*_PROMOTION.md` files that were not
    leaderboarded — typically because a SIGTERM interrupted the cycle
    that wrote them, so post_cycle_delta never ran for their task_id.

    Returns the count of files actually rescued (validated + appended).

    v1.5.3 ORPHAN-PROMOTION-RESCAN. Closes the gap where SIGTERM-
    interrupted cycles leave PROMOTION files unvalidated because
    post_cycle_delta filters by in-cycle task_id closure events, not
    filesystem mtime. Idempotent — already-leaderboarded files are
    skipped via a simple LEADERBOARD.md membership grep.

    Cutoff resolution (v1.5.4 RESCAN-MIGRATION-GUARD):
      1. state.last_cycle_ended_at — preferred (set by every healthy
         cycle close from v1.5.3 onward).
      2. If missing (pre-v1.5.3 state.json), backfill from the most
         recent `cycle_end` event in aggregate.jsonl for this project.
         An `orphan_rescan_cutoff_backfilled` event is logged so the
         migration is observable.
      3. If aggregate.jsonl is also missing or has no cycle_end for
         this project, cutoff=0 (scan every CAND_*_PROMOTION). The
         `_is_in_leaderboard` membership grep makes that path
         idempotent — duplicate appends are prevented.
    """
    project_path = Path(project_path)
    s = state.read(project_path)
    cutoff_str = getattr(s, "last_cycle_ended_at", None)
    cutoff = 0.0
    if cutoff_str:
        cutoff_dt = _parse_iso_utc(cutoff_str)
        if cutoff_dt is not None:
            cutoff = cutoff_dt.timestamp()
    else:
        backfilled = _backfill_cutoff_from_aggregate(project_path)
        if backfilled is not None:
            cutoff = backfilled
            state.log_event(
                project_path,
                "orphan_rescan_cutoff_backfilled",
                cutoff_ts=datetime.fromtimestamp(
                    backfilled, tz=timezone.utc
                ).strftime("%Y-%m-%dT%H:%M:%SZ"),
                source="aggregate.jsonl",
            )

    debug_dir = project_path / "data" / "debug"
    if not debug_dir.is_dir():
        return 0

    rescued = 0
    for p in sorted(debug_dir.glob("CAND_*_PROMOTION.md")):
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        if mtime <= cutoff:
            continue

        m = _ORPHAN_PROMOTION_RE.match(p.name)
        if not m:
            continue
        fname_task_id = m.group(1)

        # v1.5.5 ORPHAN-RESCAN-FIX: read task_id from the PROMOTION
        # body's `**Task:** <id>` line. Filename derivation strips the
        # `vec_` prefix that real AI-trade backlog IDs carry, producing
        # leaderboard rows under the wrong key.
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        task_id = _extract_task_id_from_body(text, fname_task_id)

        if _is_in_leaderboard(project_path, task_id):
            continue

        # v1.5.5 ORPHAN-RESCAN-FIX: no verdict skip gate here.
        # `on_promotion_success` (v1.5.1 ABLATION-VERDICT-GATE) already
        # gates ablation spawn on PROMOTED while running the leaderboard
        # append for ALL verdicts. The pre-v1.5.5 orphan-rescue gate
        # contradicted that design, leaving NEUTRAL / CONDITIONAL /
        # REJECTED orphans out of the leaderboard while the standard
        # post-cycle-delta path included them.
        ok, missing = promotion_lib.validate_v2_sections(p, task_id=task_id)
        state.log_event(
            project_path,
            "promotion_v2_sections_check",
            task_id=task_id,
            all_present=ok,
            missing=",".join(missing),
            strict=promotion_lib.requires_full_v2_validation(task_id),
            origin="orphan_rescan",
        )
        if not ok:
            promotion_lib.quarantine_invalid(
                project_path, _PseudoItem(task_id), missing
            )
            continue

        metrics = promotion_lib.parse_metrics(p)
        promotion_lib.on_promotion_success(
            project_path, _PseudoItem(task_id), metrics
        )
        state.log_event(
            project_path,
            "promotion_validated",
            task_id=task_id,
            origin="orphan_rescan",
            **{k: v for k, v in metrics.items() if v is not None},
        )
        rescued += 1

    if rescued:
        state.log_event(
            project_path,
            "orphan_promotion_rescan_completed",
            rescued=rescued,
        )
    return rescued


def _count_open_backlog(project_path: Path) -> int:
    """Count `- [ ]` open task lines in the project's backlog. Returns 0
    when no backlog exists. Used by `sweep_done_projects` for telemetry —
    NOT for the resume decision (which delegates to detect_prd_complete
    so the decision matches what the rest of the engine considers
    complete)."""
    candidates = [
        project_path / "backlog.md",
        project_path / ".cc-autopipe" / "backlog.md",
    ]
    backlog = next((p for p in candidates if p.exists()), None)
    if backlog is None:
        return 0
    try:
        text = backlog.read_text(encoding="utf-8")
    except OSError:
        return 0
    import re  # noqa: PLC0415
    return len(re.findall(r"^[ \t]*-[ \t]*\[ \]", text, re.MULTILINE))


def _should_resume_done(s: state.State, project_path: Path) -> tuple[bool, str]:
    """v1.3.6 PHASE-DONE-RECOVERY: gate `sweep_done_projects` against
    active enforcement state.

    Returns (should_resume, skip_reason). Mirrors `_should_recover` but
    targets the `phase=done` → `active` transition: when an operator adds
    new tasks to a done project's backlog, the engine should resume work
    without requiring a manual `state.json` edit. Without this, a
    `phase=done` project on a 3-4 month autonomous run requires manual
    rescue every time backlog cycles drained → reopened.

    Skip reasons:
      - phase != done — boring default, sweep iterates past actives
      - meta_reflect_pending / knowledge_update_pending /
        research_plan_required — enforcement loops outrank reopen, just
        like sweep_failed_projects
      - prd_still_complete — backlog has no open `[ ]`, so there's
        actually nothing to resume to
    """
    if s.phase != "done":
        return False, f"phase={s.phase}_not_done"
    if s.meta_reflect_pending:
        return False, "meta_reflect_in_progress"
    if s.knowledge_update_pending:
        return False, "knowledge_update_in_progress"
    if s.research_plan_required:
        return False, "research_plan_pending"
    # Resume only when the backlog has at least one open `- [ ]` line.
    # `detect_prd_complete` is unsuitable here because it returns False
    # for a *missing* backlog, which would erroneously flip a long-done
    # project that has no backlog file at all into active. The right
    # signal is "actually has open tasks now" — count open lines
    # directly. 0 open lines → either backlog missing or PRD truly
    # complete; either way, nothing to resume to.
    if _count_open_backlog(project_path) == 0:
        return False, "prd_still_complete"
    return True, ""


def maybe_resume_done(project_path: Path | str) -> bool:
    """Single-project resume decision. Returns True iff transitioned.

    `phase=done` is normally terminal — the project's PRD is satisfied,
    no further cycles run. v1.3.6 introduces an exception: when an
    operator manually adds new tasks to backlog.md (Phase 3 / extension
    scenario), the engine detects the reopen at the next sweep cycle and
    flips the project back to `active` automatically. Without this,
    Roman's planned 3-4 month autonomous absence would need manual
    `cc-autopipe update-verify --prd-complete=false` calls every time a
    backlog cycle drained → reopened.

    Atomic via state.write (per the v1.3.6 §"Don't" rule). Per-project
    lock acquired non-blocking — if a cycle is in flight we skip and
    let the next sweep retry.
    """
    project_path = Path(project_path)
    if not (project_path / ".cc-autopipe").exists():
        return False
    proj_lock = locking.acquire_project(project_path)
    if proj_lock is None:
        _log(
            f"{project_path.name}: skip done-resume (per-project lock held)"
        )
        return False
    try:
        # v1.5.3 ORPHAN-PROMOTION-RESCAN: piggyback on the 30-min sweep
        # cadence so SIGTERM-interrupted PROMOTION files get rescued
        # even when the project sits in phase=done. Best-effort; a
        # rescan failure does not block the done→active decision.
        try:
            n_rescued = rescan_orphan_promotions(project_path)
            if n_rescued:
                _log(
                    f"{project_path.name}: rescued {n_rescued} orphan "
                    f"PROMOTION(s) during done-resume sweep"
                )
        except Exception as exc:  # noqa: BLE001
            _log(f"{project_path.name}: orphan rescan error: {exc!r}")
        s = state.read(project_path)
        should, reason = _should_resume_done(s, project_path)
        if not should:
            # Only log when the project IS done — emitting a skip event
            # for every active project on every sweep would flood
            # aggregate.jsonl. The phase=*_not_done case is the boring
            # default for a healthy active project.
            if s.phase == "done":
                state.log_event(
                    project_path, "phase_done_resume_skipped", reason=reason
                )
                _log(
                    f"{project_path.name}: phase_done resume skipped — {reason}"
                )
            return False
        # Transition done → active. Clear PRD-complete flags so the
        # next cycle picks up the new backlog tasks normally; clear
        # current_task because the operator added new work and we
        # want the engine to pick a fresh top item rather than
        # resume on stale state.
        s.phase = "active"
        s.prd_complete = False
        s.prd_complete_detected = False
        s.last_score = None
        s.last_passed = None
        s.current_task = None
        state.write(project_path, s)
        state.log_event(
            project_path,
            "phase_done_to_active",
            reason="backlog_reopened",
            open_tasks=_count_open_backlog(project_path),
        )
        _log(
            f"{project_path.name}: phase_done → active (backlog reopened, "
            f"{_count_open_backlog(project_path)} open tasks)"
        )
        return True
    finally:
        proj_lock.release()


def sweep_done_projects(projects: Iterable[Path]) -> int:
    """Per-cycle sweep: reopen DONE projects whose backlog has gained open
    tasks. Operators add tasks manually; engine should not require
    manual state.json edits to resume work.

    Returns the number of projects transitioned. Aborts on shutdown
    flag mid-sweep so a SIGTERM during a long projects.list scan stops
    mutating state.
    """
    n = 0
    for p in projects:
        if is_shutdown():
            _log("phase-done-resume sweep: shutdown flag set, aborting")
            break
        try:
            if maybe_resume_done(p):
                n += 1
        except Exception as exc:  # noqa: BLE001 — sweep must continue
            _log(f"{p}: phase_done resume error: {exc!r}")
    return n


def auto_recover_failed_projects(projects: Iterable[Path]) -> int:
    """Sweep helper: invoke maybe_auto_recover for each project, return
    count revived. Used by main.py's periodic background sweep.

    Aborts the inner loop if the orchestrator's shutdown flag flips
    mid-sweep, so a SIGTERM during a long projects.list scan doesn't
    keep mutating state.json after the operator asked us to stop.
    """
    n = 0
    for p in projects:
        if is_shutdown():
            _log("auto-recovery sweep: shutdown flag set, aborting")
            break
        try:
            if maybe_auto_recover(p):
                n += 1
        except Exception as exc:  # noqa: BLE001 — sweep must continue
            _log(f"{p}: auto-recovery error: {exc!r}")
    return n


def _write_human_needed(project_path: Path, last_stderr: str) -> None:
    target = project_path / ".cc-autopipe" / "HUMAN_NEEDED.md"
    try:
        target.write_text(
            "# Human attention required\n\n"
            "This project hit `consecutive_failures >= 3` and was marked "
            "FAILED. See `.cc-autopipe/memory/failures.jsonl` for the "
            "trail and the most recent claude stderr below.\n\n"
            "After fixing the underlying issue, run "
            "`cc-autopipe resume <project>` to restart.\n\n"
            "## Last claude stderr (truncated)\n\n"
            "```\n" + (last_stderr or "(empty)")[-2000:] + "\n```\n",
            encoding="utf-8",
        )
    except OSError:
        pass


def _write_in_progress_cap_human_needed(
    project_path: Path, n_cycles: int, cap: int
) -> None:
    """Bug B: write HUMAN_NEEDED.md when a project sits in_progress
    forever. Likely causes: verify.sh expectations don't match where
    Claude actually puts artifacts, or the task itself is too large
    for one PRD item."""
    target = project_path / ".cc-autopipe" / "HUMAN_NEEDED.md"
    try:
        target.write_text(
            "# Human attention required (in_progress cap hit)\n\n"
            f"This project reported `in_progress=true` for "
            f"{n_cycles} consecutive cycles "
            f"(cap = {cap}). Engine has marked it FAILED.\n\n"
            "Likely causes:\n"
            "- verify.sh expects artifacts in different paths than "
            "Claude is producing\n"
            "- task is too large for one PRD item; consider splitting\n"
            "- Claude has been stuck in DETACHED long-op without "
            "the check-cmd succeeding\n\n"
            "Inspect:\n"
            "- `.cc-autopipe/memory/progress.jsonl` for cycle_in_progress events\n"
            "- `.cc-autopipe/CURRENT_TASK.md` for what Claude thinks the task is\n"
            "- `.cc-autopipe/state.json.current_task.artifact_paths` "
            "vs. what files actually exist\n\n"
            "After resolving, run `cc-autopipe resume <project>` to restart.\n",
            encoding="utf-8",
        )
    except OSError:
        pass
