#!/usr/bin/env python3
"""orchestrator.phase — DETACHED state machine + PRD phase transitions.

Includes:
  - _process_detached:         DETACHED state-machine iteration
  - _maybe_transition_phase:   PRD phase advance + DONE
  - _append_to_archive:        phase block → backlog-archive.md
"""

from __future__ import annotations

import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from orchestrator._runtime import _log, _now_iso, _parse_iso_utc
from orchestrator.alerts import _notify_tg
import prd as prd_lib  # noqa: E402
import state  # noqa: E402

DETACHED_CHECK_TIMEOUT_SEC = 30


def _append_to_archive(archive_path: Path, body: str, phase_number: int) -> None:
    """Append a phase block to backlog-archive.md per SPEC-v1.md §2.3.4.

    Atomic via tmp+rename so a crash mid-write doesn't truncate the
    existing archive. The archive file accumulates each completed phase
    as one section so the operator can audit what shipped per phase.
    """
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    header = f"\n## Archived Phase {phase_number} — {_now_iso()}\n\n"
    new_content = ""
    if archive_path.exists():
        try:
            new_content = archive_path.read_text(encoding="utf-8")
        except OSError:
            new_content = ""
    new_content += header + body.rstrip() + "\n"
    tmp = archive_path.with_suffix(f".tmp.{os.getpid()}")
    try:
        tmp.write_text(new_content, encoding="utf-8")
        os.replace(tmp, archive_path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _maybe_transition_phase(project_path: Path, s: state.State) -> bool:
    """If the project's PRD has phases AND the current phase is complete,
    advance to the next phase (or mark DONE on last phase).

    Returns True iff the project's terminal state was changed (transition
    completed or DONE). Caller should NOT run additional post-cycle logic
    when True is returned because state.phase / current_phase have moved.

    Behaviour:
    - PRD without `### Phase N:` headers → returns False (caller falls
      back to v0.5 prd_complete logic).
    - Current phase incomplete (unchecked items remain) → False.
    - Current phase complete AND verify passed (last_score >= threshold) →
      archive the phase block to backlog-archive.md, log phase_transition,
      TG, advance current_phase. If that was the last phase: phase=done.
    """
    prd_path = project_path / ".cc-autopipe" / "prd.md"
    phases = prd_lib.read_phases(prd_path)
    if not phases:
        return False  # single-phase PRD; defer to caller

    current = next((p for p in phases if p.number == s.current_phase), None)
    if current is None:
        if all(p.number in s.phases_completed for p in phases):
            s.phase = "done"
            state.write(project_path, s)
            state.log_event(
                project_path,
                "done",
                reason="all phases completed",
                phases_completed=list(s.phases_completed),
            )
            return True
        return False

    if not current.is_complete:
        return False

    if s.last_score is None or s.last_score < s.threshold or s.last_passed is False:
        return False

    archive_path = project_path / ".cc-autopipe" / "backlog-archive.md"
    completed_phase = s.current_phase
    _append_to_archive(archive_path, current.body, completed_phase)

    last_phase_number = max(p.number for p in phases)
    is_last_phase = completed_phase >= last_phase_number

    state.complete_phase(project_path)  # writes; resets session_id
    s_after = state.read(project_path)
    state.log_event(
        project_path,
        "phase_transition",
        completed_phase=completed_phase,
        new_phase=(None if is_last_phase else s_after.current_phase),
        is_last_phase=is_last_phase,
    )
    _notify_tg(
        f"[{project_path.name}] Phase {completed_phase} complete. "
        f"{'All phases done — project DONE.' if is_last_phase else 'Advancing to next phase.'}"
    )

    if is_last_phase:
        s_after.phase = "done"
        state.write(project_path, s_after)
        state.log_event(
            project_path,
            "done",
            score=s_after.last_score,
            iteration=s_after.iteration,
            via_phase_split=True,
        )

    s.phase = s_after.phase
    s.current_phase = s_after.current_phase
    s.phases_completed = s_after.phases_completed
    s.session_id = s_after.session_id
    return True


def _process_detached(project_path: Path, s: state.State) -> str:
    """One iteration of the DETACHED state machine per SPEC-v1.md §2.1.3.

    Returns one of:
      "detached" — still waiting (poll interval not reached, or check_cmd
                   ran and still failed); orchestrator releases the slot
                   for next outer pass
      "failed"   — max_wait_sec exceeded; project transitioned to FAILED
      "active"   — check_cmd succeeded; caller should fall through to a
                   normal ACTIVE cycle
    """
    # Lazy import to avoid circular dep with recovery.
    from orchestrator.recovery import _write_human_needed

    if s.detached is None:
        s.phase = "active"
        state.write(project_path, s)
        state.log_event(project_path, "detach_corrupt_recovery")
        _log(
            f"{project_path.name}: phase=detached but detached=None; recovered to active"
        )
        return "active"

    now = datetime.now(timezone.utc)
    started = _parse_iso_utc(s.detached.started_at) or now
    elapsed = (now - started).total_seconds()

    if elapsed > s.detached.max_wait_sec:
        s.phase = "failed"
        state.write(project_path, s)
        state.log_event(
            project_path,
            "detached_timeout",
            elapsed_sec=int(elapsed),
            reason=s.detached.reason,
            checks=s.detached.checks_count,
        )
        _notify_tg(
            f"[{project_path.name}] DETACHED timeout after "
            f"{int(elapsed)}s — check check_cmd"
        )
        _write_human_needed(
            project_path,
            f"DETACHED operation '{s.detached.reason}' timed out after "
            f"{int(elapsed)}s (max_wait_sec={s.detached.max_wait_sec}). "
            f"check_cmd: {s.detached.check_cmd}",
        )
        return "failed"

    last_check = _parse_iso_utc(s.detached.last_check_at) or started
    if (now - last_check).total_seconds() < s.detached.check_every_sec:
        return "detached"

    try:
        cp = subprocess.run(
            ["bash", "-c", s.detached.check_cmd],
            cwd=project_path,
            timeout=DETACHED_CHECK_TIMEOUT_SEC,
            capture_output=True,
            text=True,
        )
        rc = cp.returncode
        check_stderr_tail = (cp.stderr or "")[-200:]
    except subprocess.TimeoutExpired:
        rc = 124
        check_stderr_tail = f"check_cmd exceeded {DETACHED_CHECK_TIMEOUT_SEC}s timeout"
        _log(
            f"{project_path.name}: detached check_cmd timed out after "
            f"{DETACHED_CHECK_TIMEOUT_SEC}s"
        )

    s.detached.last_check_at = _now_iso()
    s.detached.checks_count += 1
    state.write(project_path, s)

    if rc == 0:
        state.log_event(
            project_path,
            "detach_resumed",
            reason=s.detached.reason,
            checks=s.detached.checks_count,
            elapsed_sec=int(elapsed),
        )
        _log(
            f"{project_path.name}: detach_resumed "
            f"checks={s.detached.checks_count} elapsed={int(elapsed)}s"
        )
        s.phase = "active"
        s.detached = None
        state.write(project_path, s)
        return "active"

    state.log_event(
        project_path,
        "detach_check_failed",
        rc=rc,
        checks=s.detached.checks_count,
        elapsed_sec=int(elapsed),
        stderr_tail=check_stderr_tail,
    )

    # v1.3.3 Group L: liveness check. If --pipeline-log + --stale-after-sec
    # were configured at detach time, treat a stalled log mtime as a
    # silent pipeline death — emit detach_pipeline_stale and force a
    # recovery cycle so Claude can investigate (instead of waiting the
    # full max_wait_sec on a dead pipeline).
    stale = _maybe_resume_on_stale_pipeline(project_path, s, elapsed)
    if stale:
        return "active"

    return "detached"


def _maybe_resume_on_stale_pipeline(
    project_path: Path, s: state.State, elapsed: float
) -> bool:
    """Inspect pipeline_log mtime; auto-resume if it has been stale longer
    than stale_after_sec. Returns True iff the project was transitioned
    from `detached` back to `active` due to stale liveness signal.

    No-op when either pipeline_log_path or stale_after_sec is unset.
    """
    if s.detached is None:
        return False
    log_path_str = s.detached.pipeline_log_path
    threshold = s.detached.stale_after_sec
    if not log_path_str or threshold is None:
        return False

    log_path = Path(log_path_str)
    if not log_path.exists():
        state.log_event(
            project_path,
            "detach_pipeline_log_missing",
            pipeline_log=str(log_path),
            checks_count=s.detached.checks_count,
            elapsed_sec=int(elapsed),
        )
        _resume_from_stale(project_path, s, "pipeline_log_missing")
        return True

    try:
        mtime = log_path.stat().st_mtime
    except OSError:
        return False

    age_sec = int(time.time() - mtime)
    if age_sec < 0:
        # Clock skew — log once per detached run, not stale.
        if s.detached.checks_count == 1:
            state.log_event(
                project_path,
                "detach_pipeline_log_clock_skew",
                pipeline_log=str(log_path),
                age_sec=age_sec,
            )
        return False

    if age_sec <= threshold:
        return False

    state.log_event(
        project_path,
        "detach_pipeline_stale",
        pipeline_log=str(log_path),
        log_age_sec=age_sec,
        stale_threshold_sec=threshold,
        checks_count=s.detached.checks_count,
        elapsed_sec=int(elapsed),
    )
    _resume_from_stale(project_path, s, "pipeline_stale")
    return True


def _resume_from_stale(project_path: Path, s: state.State, reason: str) -> None:
    """Common transition path for stale auto-resume.

    Mirrors the rc==0 branch in _process_detached but tags the next
    cycle's prompt with `last_detach_resume_reason` so Claude knows it
    was woken up to investigate (vs. resumed because work succeeded).
    """
    s.phase = "active"
    s.detached = None
    s.last_detach_resume_reason = reason
    state.write(project_path, s)
    _log(f"{project_path.name}: resumed from stale ({reason})")
