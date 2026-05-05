#!/usr/bin/env python3
"""orchestrator.cycle — process_project owns one cycle for one project.

The function pulls heavy lifting from sibling modules (preflight, prompt,
subprocess_runner, recovery, alerts) and orchestrates the cycle event
log + state transitions per SPEC.md §6.1.
"""

from __future__ import annotations

import os
from pathlib import Path

from orchestrator._runtime import _log, _now_iso, _user_home
from orchestrator.alerts import _notify_tg
from orchestrator.preflight import (
    _preflight_disk,
    _preflight_quota,
    _resume_paused_if_due,
)
from orchestrator.prompt import (
    _build_claude_cmd,
    _read_config_auto_escalation,
    _read_config_improver,
)
from orchestrator.phase import _maybe_transition_phase, _process_detached
from orchestrator.recovery import (
    _handle_smart_escalation,
    _write_in_progress_cap_human_needed,
    evaluate_stuck,
)
from orchestrator.research import (
    maybe_activate_after_cycle,
    validate_research_plan,
)
from orchestrator.subprocess_runner import _run_claude, _stash_stream
import activity as activity_lib  # noqa: E402
import locking  # noqa: E402
import notify as notify_lib  # noqa: E402
import state  # noqa: E402

DEFAULT_CYCLE_TIMEOUT_SEC = 3600


def process_project(project_path: Path) -> str:
    """Run one cycle for a single project."""
    if not project_path.exists():
        _log(f"skip {project_path}: path missing")
        return "missing"

    cca = project_path / ".cc-autopipe"
    if not cca.exists():
        _log(f"skip {project_path}: not initialized (run `cc-autopipe init`)")
        return "uninit"

    s = state.read(project_path)

    if s.phase in ("done", "failed"):
        return s.phase

    if s.phase == "paused":
        if _resume_paused_if_due(s):
            state.write(project_path, s)
            state.log_event(project_path, "resumed_from_pause")
            _log(f"{project_path.name}: resumed from pause")
        else:
            return "paused"

    # Acquire per-project lock per SPEC §8.3 BEFORE bumping iteration
    # or spawning claude. fcntl auto-releases on crash; a hung peer
    # holding the lock is logged + skipped (no force-recovery in v0.5).
    project_lock = locking.acquire_project(project_path)
    if project_lock is None:
        _log(f"skip {project_path.name}: per-project lock held by another process")
        return "locked"

    heartbeat = locking.HeartbeatThread(project_lock)
    heartbeat.start()
    try:
        # DETACHED handling per SPEC-v1.md §2.1.3. Runs BEFORE quota
        # pre-flight: a detached cycle is a cheap check_cmd, not a claude
        # spawn — no quota burn, so the 7d cap shouldn't gate it.
        if s.phase == "detached":
            d_result = _process_detached(project_path, s)
            if d_result == "detached":
                return "detached"
            if d_result == "failed":
                return "failed"
            # d_result == "active": fall through to normal cycle.

        # Pre-flight quota check per SPEC §9.2. Pauses the project (or
        # all projects, on 7d threshold) BEFORE bumping iteration so
        # we don't burn one on a paused decision. Cache TTL is 60s, so
        # repeat cycles within a minute share the same quota read.
        preflight = _preflight_quota(project_path, s)
        if preflight in ("paused_5h", "paused_7d"):
            return "paused"

        # v1.3 C2: pre-cycle disk check + auto-cleanup. Only runs after
        # quota cleared (so a paused-on-quota project doesn't burn disk
        # cleanup work). Pauses the project on disk_full when cleanup
        # cannot recover enough space.
        disk_status = _preflight_disk(project_path, s)
        if disk_status == "paused":
            return "paused"

        s.iteration += 1
        s.last_cycle_started_at = _now_iso()
        s.last_progress_at = s.last_cycle_started_at
        state.write(project_path, s)

        # Snapshot improver_due for the prompt builder, then clear it on
        # state so we don't double-send the trigger if the cycle fails
        # and we re-enter. The trigger is one-shot: orchestrator detected
        # N successes, asked the main agent to invoke improver, done.
        improver_was_due = s.improver_due
        if s.improver_due:
            s.improver_due = False
            state.write(project_path, s)
            state.log_event(
                project_path,
                "improver_invoked_in_prompt",
                successful_cycles_since_improver=(s.successful_cycles_since_improver),
            )
            # Re-tag s.improver_due so _build_claude_cmd → _build_prompt
            # still sees it (we only cleared it in persisted state, not
            # in this snapshot).
            s.improver_due = True

        cmd = _build_claude_cmd(project_path, s)
        # After the prompt is baked, drop the in-memory flag so any
        # subsequent re-read doesn't see stale True.
        if improver_was_due:
            s.improver_due = False
        timeout = float(
            os.environ.get("CC_AUTOPIPE_CYCLE_TIMEOUT_SEC", DEFAULT_CYCLE_TIMEOUT_SEC)
        )
        state.log_event(
            project_path,
            "cycle_start",
            iteration=s.iteration,
            claude_bin=cmd[0],
            resumed=bool(s.session_id),
        )
        _log(
            f"{project_path.name}: cycle_start iteration={s.iteration} "
            f"claude_bin={cmd[0]}"
        )

        # v1.2 Bug D + Bug F: capture current_task identity + stages
        # snapshot BEFORE the cycle so we can detect task_switched and
        # stage_completed events after the cycle's Stop hook runs.
        pre_task_id = s.current_task.id if s.current_task else None
        pre_stages = list(s.current_task.stages_completed) if s.current_task else []

        # v1.3 D3: snapshot open backlog lines BEFORE the cycle so we
        # can spot lines Claude ADDED during the cycle (used for
        # research-plan enforcement).
        from orchestrator.research import _list_open_backlog_lines, _backlog_path
        _bl_path = _backlog_path(project_path)
        pre_open_lines: list[str] = (
            [ln for _, ln in _list_open_backlog_lines(_bl_path)]
            if _bl_path is not None
            else []
        )

        rc, stdout, stderr = _run_claude(project_path, cmd, timeout)

        # Re-read state.json — hooks may have updated it from inside the claude
        # subprocess (stop.sh's update-verify, stop-failure.sh's set-paused).
        s = state.read(project_path)

        # v1.2 Bug D: task_switched event when current_task.id changes
        # cycle-over-cycle. None → non-None is task_started; non-None →
        # different non-None is task_switched. Same id → no event.
        post_task_id = s.current_task.id if s.current_task else None
        if pre_task_id != post_task_id:
            if pre_task_id is None and post_task_id is not None:
                state.log_event(
                    project_path,
                    "task_started",
                    iteration=s.iteration,
                    task_id=post_task_id,
                )
            elif pre_task_id is not None and post_task_id is not None:
                state.log_event(
                    project_path,
                    "task_switched",
                    iteration=s.iteration,
                    from_task=pre_task_id,
                    to_task=post_task_id,
                )

        # v1.2 Bug F: stage_completed event when stages_completed grows
        # within the same task. Switching tasks resets the streak (the
        # comparison is only meaningful when the task id is unchanged).
        if (
            post_task_id is not None
            and pre_task_id == post_task_id
            and s.current_task is not None
        ):
            new_stages = [
                st for st in s.current_task.stages_completed if st not in pre_stages
            ]
            for st in new_stages:
                state.log_event(
                    project_path,
                    "stage_completed",
                    iteration=s.iteration,
                    task_id=post_task_id,
                    stage=st,
                    stages_total=len(s.current_task.stages_completed),
                )

        # Post-cycle phase transition per SPEC-v1.md §2.3.4 — only fires
        # when prd.md declares `### Phase N:` headers AND the current
        # phase is complete AND verify passed. Single-phase PRDs (and
        # legacy v0.5 projects without phase headers) fall through to
        # the v0.5 prd_complete logic below.
        phase_transitioned = False
        if s.phase == "active":
            phase_transitioned = _maybe_transition_phase(project_path, s)

        # Stage N: improver-trigger bookkeeping on successful cycles. This
        # runs BEFORE the escalation revert / done / failed branches so a
        # cycle that ALSO completes a phase still gets counted toward the
        # next improver pass. The actual subagent invocation is delegated
        # to the main agent on the NEXT cycle via the prompt hint
        # _build_prompt emits when improver_due is True.
        if s.last_passed and s.phase == "active":
            imp_cfg = _read_config_improver(project_path)
            if imp_cfg.get("enabled"):
                trigger_n = int(imp_cfg.get("trigger_every_n_successes") or 5)
                s.successful_cycles_since_improver += 1
                if s.successful_cycles_since_improver >= trigger_n:
                    skills_dir = project_path / ".claude" / "skills"
                    try:
                        skills_dir.mkdir(parents=True, exist_ok=True)
                    except OSError as exc:
                        _log(
                            f"{project_path.name}: could not create skills dir: {exc!r}"
                        )
                    s.successful_cycles_since_improver = 0
                    s.improver_due = True
                    state.write(project_path, s)
                    state.log_event(
                        project_path,
                        "improver_trigger_due",
                        iteration=s.iteration,
                        skills_dir=str(skills_dir),
                    )
                    _log(
                        f"{project_path.name}: improver triggered "
                        f"(every {trigger_n} successes)"
                    )
                else:
                    state.write(project_path, s)

        # Stage L: revert escalation flag on a successful cycle BEFORE the
        # done / failed branches so the next cycle goes back to the default
        # model. Sonnet stays the first responder; opus is only the relief.
        esc_cfg = _read_config_auto_escalation(project_path)
        esc_trigger = int(esc_cfg.get("trigger_consecutive_failures") or 3)
        if (
            esc_cfg.get("enabled")
            and bool(esc_cfg.get("revert_after_success", True))
            and s.last_passed
            and s.escalated_next_cycle
        ):
            s.escalated_next_cycle = False
            state.write(project_path, s)
            state.log_event(
                project_path,
                "escalation_reverted",
                iteration=s.iteration,
                last_score=s.last_score,
            )

        # Post-cycle phase transitions per SPEC §6.1 (single-phase PRDs).
        if (
            not phase_transitioned
            and s.last_score is not None
            and s.last_score >= s.threshold
            and s.prd_complete
            and s.phase == "active"
        ):
            s.phase = "done"
            state.write(project_path, s)
            state.log_event(
                project_path, "done", score=s.last_score, iteration=s.iteration
            )
        elif s.consecutive_failures >= 3 and s.phase == "active":
            # v1.2 Bug H: smart escalation. Routes 3+ consecutive failures
            # by recent-failure category (verify-pattern / crash / mixed /
            # fallback). Implementation in recovery._handle_smart_escalation.
            _handle_smart_escalation(
                project_path, s, stderr, esc_cfg, esc_trigger
            )

        # Persist claude's stdout/stderr to disk on EVERY cycle (even on
        # empty content) so a fast rc!=0 exit is debuggable. Names are
        # explicit ("claude-last-*") so they're greppable in support
        # tickets.
        _stash_stream(project_path, "claude-last-stdout.log", stdout)
        _stash_stream(project_path, "claude-last-stderr.log", stderr)

        if rc != 0:
            state.log_failure(
                project_path,
                "claude_subprocess_failed",
                exit_code=rc,
                stderr_tail=(stderr or "")[-500:],
            )
            # v1.2 Bug G: TG alert on subprocess failure with sentinel
            # dedup. v0.5/v1.0 only alerted on quota; silent rc!=0 loops
            # were running for hours unnoticed in real-world test.
            # 600s dedup window (per project, per rc) so noisy crashes
            # don't drown the channel.
            sent = notify_lib.notify_subprocess_failed_dedup(
                project_path.name,
                rc,
                stderr or "",
                sentinel_dir=_user_home(),
            )
            if sent:
                state.log_event(
                    project_path,
                    "subprocess_alerted",
                    iteration=s.iteration,
                    rc=rc,
                )

        # v1.3 B2: replace v1.2's blind consecutive_in_progress cap with
        # activity-based stuck detection. consecutive_in_progress stays
        # for telemetry but no longer auto-fails — instead, if there's
        # been NO activity (no fs changes, no running processes, no
        # stage transition) for 60 minutes, the project is stuck for
        # real and we mark it failed. Long training (multi-hour) keeps
        # touching checkpoints, which resets the stuck timer cleanly.
        try:
            current_stage = (
                s.current_task.stage if s.current_task is not None else None
            )
            act = activity_lib.detect_activity(
                project_path,
                project_path.name,
                last_observed_stage=s.last_observed_stage,
                current_stage=current_stage,
            )
            if act["is_active"]:
                s.last_activity_at = _now_iso()
                state.write(project_path, s)
            # Always update last_observed_stage so the next cycle compares
            # against this cycle's snapshot.
            if current_stage != s.last_observed_stage:
                s.last_observed_stage = current_stage
                state.write(project_path, s)
        except Exception as exc:  # noqa: BLE001 — telemetry must not crash
            _log(f"{project_path.name}: activity probe error: {exc!r}")

        if s.phase == "active":
            stuck = evaluate_stuck(s)
            if stuck == "warn":
                state.log_event(
                    project_path,
                    "stuck_warning",
                    iteration=s.iteration,
                    last_activity_at=s.last_activity_at,
                )
            elif stuck == "fail":
                s.phase = "failed"
                state.write(project_path, s)
                state.log_event(
                    project_path,
                    "stuck_failed",
                    iteration=s.iteration,
                    last_activity_at=s.last_activity_at,
                    consecutive_in_progress=s.consecutive_in_progress,
                )
                _write_in_progress_cap_human_needed(
                    project_path,
                    s.consecutive_in_progress,
                    s.consecutive_in_progress,
                )
                _notify_tg(
                    f"[{project_path.name}] no activity for >60 min — "
                    f"phase=failed (stuck), see HUMAN_NEEDED.md"
                )

        # v1.3 D3: enforce research plan if required this cycle.
        if s.research_plan_required:
            try:
                validate_research_plan(
                    project_path,
                    s,
                    cycle_started_iso=s.last_cycle_started_at,
                    pre_open_lines=pre_open_lines,
                )
                # Re-read because validate_research_plan may have written.
                s = state.read(project_path)
            except Exception as exc:  # noqa: BLE001
                _log(f"{project_path.name}: research-plan validate error: {exc!r}")

        # v1.3 D1+D2: detect PRD-complete + activate research mode.
        # Runs at end of every cycle so a backlog that just became
        # empty (last [x] / [~] in this cycle) flips the flag immediately.
        if s.phase == "active":
            try:
                maybe_activate_after_cycle(project_path, s)
                s = state.read(project_path)
            except Exception as exc:  # noqa: BLE001
                _log(f"{project_path.name}: research mode error: {exc!r}")

        state.log_event(
            project_path,
            "cycle_end",
            iteration=s.iteration,
            phase=s.phase,
            rc=rc,
            score=s.last_score,
        )
        _log(
            f"{project_path.name}: cycle_end iteration={s.iteration} "
            f"phase={s.phase} rc={rc}"
        )
        return s.phase
    finally:
        heartbeat.stop()
        project_lock.release()
