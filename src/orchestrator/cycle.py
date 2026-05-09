#!/usr/bin/env python3
"""orchestrator.cycle — process_project owns one cycle for one project.

The function pulls heavy lifting from sibling modules (preflight, prompt,
subprocess_runner, recovery, alerts) and orchestrates the cycle event
log + state transitions per SPEC.md §6.1.
"""

from __future__ import annotations

import os
from pathlib import Path

from orchestrator._runtime import (
    _interruptible_sleep,
    _log,
    _now_iso,
    _parse_iso_utc,
    _user_home,
    is_shutdown,
)
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
from orchestrator.reflection import detect_and_apply_decision
from orchestrator.research import (
    maybe_activate_after_cycle,
    validate_research_plan,
)
from orchestrator.subprocess_runner import _run_claude, _stash_stream
import activity as activity_lib  # noqa: E402
import backlog as backlog_lib  # noqa: E402
import disk as disk_lib  # noqa: E402
import health as health_lib  # noqa: E402
import knowledge as knowledge_lib  # noqa: E402
import locking  # noqa: E402
import notify as notify_lib  # noqa: E402
import promotion as promotion_lib  # noqa: E402
import quota as quota_lib  # noqa: E402
import research_completion as research_completion_lib  # noqa: E402
import state  # noqa: E402
import transient as transient_lib  # noqa: E402

DEFAULT_CYCLE_TIMEOUT_SEC = 3600

# v1.3.4 R3: probe api.anthropic.com:443 before each cycle. When the
# probe fails (router reboot, WSL2 networking glitch, DNS hiccup) the
# engine sleeps with exponential backoff and retries. The cycle is
# DEFERRED, not failed — consecutive_failures is NOT incremented.
NETWORK_PROBE_BACKOFF_SEC = (30, 60, 120, 300, 600)  # 30s..10min, ~17min total

# v1.3.4 R4: transient retry schedule. Same shape as the network gate,
# applied AFTER claude exits with a transient stderr signature. Counter
# stored in state.consecutive_transient_failures; after MAX_TRANSIENT_
# RETRIES the failure escalates to the structural path so a genuinely
# broken state masquerading as transient eventually triggers smart
# escalation.
TRANSIENT_BACKOFF_SEC = (30, 60, 120, 300, 600)
MAX_TRANSIENT_RETRIES = 5


# v1.3.6 SENTINEL-PATTERNS fallback: arm the knowledge.md sentinel when
# a fresh PROMOTION.md was just written with a parseable verdict, even
# if CURRENT_TASK.md `stages_completed` lacks a verdict-pattern stage.
# 5-minute window scopes the trigger to a "fresh" artifact — a stale
# PROMOTION from a prior cycle would otherwise re-arm forever.
PROMOTION_MTIME_FRESH_WINDOW_SEC = 300


def _count_backlog_x(project_path: Path) -> int | None:
    """Count `- [x]` backlog lines. Returns None when no backlog exists.

    Used at cycle_start to snapshot a baseline that
    `_check_in_cycle_progress` compares against post-cycle. Mirrors
    `recovery._count_open_backlog` but for closed tasks.
    """
    candidates = [
        project_path / "backlog.md",
        project_path / ".cc-autopipe" / "backlog.md",
    ]
    backlog = next((p for p in candidates if p.exists()), None)
    if backlog is None:
        return None
    try:
        text = backlog.read_text(encoding="utf-8")
    except OSError:
        return None
    return sum(
        1 for ln in text.splitlines() if ln.lstrip().startswith("- [x]")
    )


def _check_in_cycle_progress(
    project_path: Path, cycle_start_at: float, s: state.State
) -> dict:
    """v1.3.7 STUCK-WITH-PROGRESS / ACTIVITY-MTIME-BASED:
    return filesystem evidence of in-cycle work.

    `cycle_start_at`: unix timestamp when the current cycle started.

    Returns:
        {
          'new_promotion_files': int,        # CAND_*_PROMOTION.md
                                             # created/modified ≥ cycle_start
          'backlog_x_delta': int,            # delta in `- [x]` count vs the
                                             # snapshot taken at cycle_start
          'current_task_stages_grew': bool,  # CURRENT_TASK.md mtime ≥
                                             # cycle_start AND the post-cycle
                                             # state has any stages_completed
          'any_progress': bool,              # OR of the three above
        }

    Both stuck-detection (gate fail → skip-fail) and the cycle-end
    activity refresh consult this. Cheap; called twice per cycle is OK.
    """
    out = {
        "new_promotion_files": 0,
        "backlog_x_delta": 0,
        "current_task_stages_grew": False,
        "any_progress": False,
    }

    # Promotion files modified since cycle_start. AI-trade convention is
    # `data/debug/CAND_*_PROMOTION.md` per `promotion.promotion_path`.
    debug_dir = project_path / "data" / "debug"
    if debug_dir.exists():
        try:
            for p in debug_dir.glob("CAND_*_PROMOTION.md"):
                try:
                    if p.stat().st_mtime >= cycle_start_at:
                        out["new_promotion_files"] += 1
                except OSError:
                    continue
        except OSError:
            pass

    # Backlog `[x]` delta vs the snapshot taken at cycle_start. None on
    # the snapshot side means "we never snapshotted" (no backlog at
    # cycle_start) — treat as no delta.
    cached = s.cycle_backlog_x_count_at_start
    current_x = _count_backlog_x(project_path)
    if cached is not None and current_x is not None and current_x > cached:
        out["backlog_x_delta"] = current_x - cached

    # CURRENT_TASK.md was rewritten in-cycle (Stop hook syncs the file
    # → state) AND the post-cycle current_task carries any
    # stages_completed entries. The mtime check pins this to "this
    # cycle"; the stages_completed presence guards against an empty
    # CURRENT_TASK.md (e.g. after a task switch where Claude only wrote
    # the header) being counted as progress.
    ct_path = project_path / ".cc-autopipe" / "CURRENT_TASK.md"
    if ct_path.exists():
        try:
            if (
                ct_path.stat().st_mtime >= cycle_start_at
                and s.current_task is not None
                and s.current_task.stages_completed
            ):
                out["current_task_stages_grew"] = True
        except OSError:
            pass

    out["any_progress"] = bool(
        out["new_promotion_files"] > 0
        or out["backlog_x_delta"] > 0
        or out["current_task_stages_grew"]
    )
    return out


def _safe_baseline_mtime(s: state.State, project_path: Path) -> float:
    """v1.3.8 SENTINEL-RACE-FIX: compute pre-cycle baseline mtime so the
    detector can fire when Claude appends to knowledge.md within the
    current cycle.

    The v1.3.6 bug was setting baseline=current_mtime at arm time. In a
    high-throughput cycle Claude updates knowledge.md AND writes a fresh
    PROMOTION within the same cycle. The detector then fires on the next
    cycle's start, clears pending, and the v1.3.6 sentinel re-arms with
    baseline=just-advanced-mtime — so any subsequent advance test fails
    and pending stays stuck forever.

    Returns min(current_mtime, cycle_start_unix), or current_mtime - 1
    when last_cycle_started_at is unparseable. Always non-negative.
    The min(...) form covers the case where current_mtime predates
    cycle_start (e.g. knowledge.md untouched this cycle): we keep the
    older mtime as baseline so any future advance still clears pending.
    """
    knowledge_md = project_path / ".cc-autopipe" / "knowledge.md"
    try:
        current_mtime = knowledge_md.stat().st_mtime
    except OSError:
        current_mtime = 0.0
    cycle_start_dt = _parse_iso_utc(s.last_cycle_started_at)
    cycle_start_unix = cycle_start_dt.timestamp() if cycle_start_dt else 0.0
    if cycle_start_unix > 0:
        baseline = min(current_mtime, cycle_start_unix)
    else:
        baseline = current_mtime - 1.0
    return max(0.0, baseline)


def _maybe_arm_sentinel_via_promotion(
    project_path: Path, post_task_id: str | None, s: state.State
) -> bool:
    """v1.3.6 SENTINEL-PATTERNS: arm knowledge.md sentinel via fresh
    PROMOTION.md when CURRENT_TASK stages didn't trigger it.

    Returns True iff the sentinel was armed by this call. Caller is
    expected to have already run the v1.3 stage-based arming logic;
    this is a defense-in-depth fallback that fires only when the
    sentinel is NOT already armed.

    Gates:
      - post_task_id starts with vec_ or phase_gate_ (engine convention
        for tasks that produce PROMOTION reports)
      - PROMOTION.md exists at the resolved path
      - PROMOTION.md mtime within PROMOTION_MTIME_FRESH_WINDOW_SEC
      - parse_verdict returns a verdict (PROMOTED|REJECTED|CONDITIONAL)

    On arm: emits `knowledge_sentinel_armed_via_promotion` event with
    the mtime age in seconds so operators can see why the sentinel
    fired without a stage transition.

    v1.3.8 SENTINEL-RACE-FIX: idempotent. When all the gate checks pass
    but the sentinel is already armed, emits
    `knowledge_sentinel_arm_skipped_already_armed` and returns False
    instead of re-arming. Re-arming would have advanced
    `knowledge_baseline_mtime` to the current (just-advanced) mtime,
    making future detector comparisons (`current > baseline`) impossible
    — that's the production deadlock the v1.3.7→v1.3.8 hotfix targets.
    Baseline is now snapshotted via `_safe_baseline_mtime` (pre-cycle
    mtime) so a same-cycle Claude append still clears pending.
    """
    if (
        post_task_id is None
        or not post_task_id.startswith(("vec_", "phase_gate_"))
    ):
        return False
    p_promo = promotion_lib.promotion_path(project_path, post_task_id)
    if not p_promo.exists():
        return False
    import time as _time  # noqa: PLC0415
    try:
        mtime_age = _time.time() - p_promo.stat().st_mtime
    except OSError:
        return False
    if mtime_age >= PROMOTION_MTIME_FRESH_WINDOW_SEC:
        return False
    if promotion_lib.parse_verdict(p_promo) is None:
        return False
    if s.knowledge_update_pending:
        # v1.3.8: idempotent skip. All conditions otherwise matched, so
        # log the would-have-armed signal so operators can see it; do
        # NOT mutate baseline_mtime (the race that left projects stuck).
        state.log_event(
            project_path,
            "knowledge_sentinel_arm_skipped_already_armed",
            task_id=post_task_id,
            promotion_mtime_age_sec=int(mtime_age),
            reason="promotion_mtime_fallback",
        )
        return False
    verdict_ts = _now_iso()
    knowledge_md = project_path / ".cc-autopipe" / "knowledge.md"
    try:
        current_mtime = knowledge_md.stat().st_mtime
    except OSError:
        current_mtime = 0.0
    s.knowledge_update_pending = True
    s.knowledge_baseline_mtime = _safe_baseline_mtime(s, project_path)
    s.knowledge_pending_reason = f"promotion_mtime_fallback on {post_task_id}"
    s.last_verdict_event_at = verdict_ts
    s.last_verdict_task_id = post_task_id
    state.write(project_path, s)
    state.log_event(
        project_path,
        "knowledge_sentinel_armed_via_promotion",
        task_id=post_task_id,
        promotion_mtime_age_sec=int(mtime_age),
        baseline_mtime=s.knowledge_baseline_mtime,
        current_mtime=current_mtime,
    )
    return True


def _backoff_override(env_var: str, default: tuple[int, ...]) -> tuple[int, ...]:
    """Honour CC_AUTOPIPE_*_BACKOFF_OVERRIDE env vars for tests.

    Format: comma-separated seconds, e.g. "1,1,1". Returns `default` on
    missing or malformed input so production deploys are unaffected.
    """
    raw = os.environ.get(env_var)
    if not raw:
        return default
    try:
        parts = tuple(int(x) for x in raw.split(",") if x.strip())
    except ValueError:
        return default
    return parts or default


def _network_gate_ok(project_path: Path, s: state.State) -> bool:
    """Probe api.anthropic.com:443 before each cycle.

    Returns True if reachable. On unreachability, sleeps with exponential
    backoff (interruptible by shutdown flag) until probe recovers, then
    returns True. After exhausting NETWORK_PROBE_BACKOFF_SEC the cycle
    is deferred — caller returns "deferred_network" without touching
    consecutive_failures (router reboots are not project-failures).

    Test escape hatches:
      - CC_AUTOPIPE_NETWORK_PROBE_DISABLED=1 short-circuits to True
        without consulting the network. Mirrors CC_AUTOPIPE_QUOTA_DISABLED
        (set by conftest.py for the whole pytest run).
      - CC_AUTOPIPE_NETWORK_PROBE_BACKOFF_OVERRIDE=1,1,1 collapses the
        backoff schedule for smoke tests that need to exercise the
        retry path within a few seconds.
    """
    if os.environ.get("CC_AUTOPIPE_NETWORK_PROBE_DISABLED") == "1":
        return True

    if transient_lib.is_anthropic_reachable():
        return True

    state.log_event(
        project_path,
        "network_probe_failed",
        target="api.anthropic.com",
        internet_up=transient_lib.is_internet_reachable(),
    )
    _log(f"{project_path.name}: api.anthropic.com unreachable, backing off")

    backoff = _backoff_override(
        "CC_AUTOPIPE_NETWORK_PROBE_BACKOFF_OVERRIDE", NETWORK_PROBE_BACKOFF_SEC
    )
    for delay in backoff:
        _interruptible_sleep(delay)
        if is_shutdown():
            return False
        if transient_lib.is_anthropic_reachable():
            state.log_event(
                project_path,
                "network_probe_recovered",
                waited_sec=delay,
            )
            _log(f"{project_path.name}: network recovered after {delay}s wait")
            return True

    state.log_event(
        project_path,
        "network_probe_giving_up",
        total_wait_sec=sum(backoff),
    )
    return False


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

        # v1.3.4 R3: network probe gate. Runs BEFORE quota pre-flight
        # because quota.read_cached() may itself burn a network round
        # trip — pointless to attempt when api.anthropic.com is down.
        # Detached check_cmd is local (test -f), so the gate sits inside
        # the active-cycle path only (this branch already cleared
        # `phase == "detached"` above).
        if not _network_gate_ok(project_path, s):
            return "deferred_network"

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
        # v1.3.7 STUCK-WITH-PROGRESS: snapshot backlog `[x]` count so
        # post-cycle `_check_in_cycle_progress` can detect the delta.
        # `_count_backlog_x` returns None when no backlog file exists;
        # in that case we leave the field None and the in-cycle progress
        # check falls back on PROMOTION mtime + CURRENT_TASK signals.
        s.cycle_backlog_x_count_at_start = _count_backlog_x(project_path)
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
        # v1.3.3 Group L: clear the one-shot stale-pipeline resume
        # marker so the notice block fires exactly once per resume
        # event. Persist immediately — a crashed cycle should not
        # re-inject the notice on the next attempt.
        if s.last_detach_resume_reason is not None:
            s.last_detach_resume_reason = None
            state.write(project_path, s)
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

        # v1.3.5 RESEARCH-COMPLETION: snapshot the topmost open
        # [research] task BEFORE the cycle so we can detect artifact-
        # based completion afterwards. After the cycle Claude has
        # likely marked the task `[x]` so it's no longer top-open.
        pre_research_item = research_completion_lib.find_top_research_task(
            project_path
        )

        # v1.3.5 PROMOTION-PARSER: snapshot every open vec_long_*
        # [implement] task at cycle start. Post-cycle we'll detect
        # which (if any) transitioned to [x] in this cycle and run
        # PROMOTION.md validation against them.
        pre_open_vec_long: list[backlog_lib.BacklogItem] = []
        try:
            for it in backlog_lib.parse_open_tasks(project_path / "backlog.md"):
                if (
                    it.id.startswith("vec_long_")
                    and it.task_type == "implement"
                ):
                    pre_open_vec_long.append(it)
        except Exception:  # noqa: BLE001 — telemetry must not crash
            pre_open_vec_long = []

        rc, stdout, stderr = _run_claude(project_path, cmd, timeout)

        # Re-read state.json — hooks may have updated it from inside the claude
        # subprocess (stop.sh's update-verify, stop-failure.sh's set-paused).
        s = state.read(project_path)

        # v1.3.5 RESEARCH-COMPLETION: when the cycle started with a top
        # `[research]` task, accept artifact-based completion in lieu of
        # the verify.sh contract. Research tasks produce analysis
        # artifacts (no code, no commit) so verify.sh is meaningless.
        # On completion: synthesise a passed-verify result. On still-
        # pending: log informational event without bumping any failure
        # counter — claude just hasn't finished yet.
        if pre_research_item is not None and rc == 0:
            _research_ok, _research_reason = research_completion_lib.completion_satisfied(
                project_path, pre_research_item
            )
            if _research_ok:
                if not s.last_passed:
                    s.last_passed = True
                    s.last_score = 1.0
                    s.consecutive_failures = 0
                    state.write(project_path, s)
                state.log_event(
                    project_path,
                    "research_task_completed",
                    task_id=pre_research_item.id,
                    artifact=research_completion_lib.expected_artifact_glob(
                        pre_research_item
                    ),
                )
            else:
                state.log_event(
                    project_path,
                    "research_task_pending",
                    task_id=pre_research_item.id,
                    reason=_research_reason,
                )

        # v1.3.5 PROMOTION-PARSER: detect vec_long_* tasks that
        # transitioned to [x] in this cycle. Validate each PROMOTION.md
        # against the v2.0 section list, fire on_promotion_success on
        # PROMOTED+complete, quarantine on PROMOTED+missing-sections,
        # log-only on REJECTED.
        if pre_open_vec_long:
            try:
                bl_items = backlog_lib.parse_all_tasks(
                    project_path / "backlog.md"
                )
                now_done: dict[str, backlog_lib.BacklogItem] = {
                    bl.id: bl for bl in bl_items if bl.status == "x"
                }
                for pre_item in pre_open_vec_long:
                    after = now_done.get(pre_item.id)
                    if after is None:
                        # Task still open or in-progress, no verdict yet.
                        continue
                    p_path = promotion_lib.promotion_path(
                        project_path, pre_item.id
                    )
                    verdict = promotion_lib.parse_verdict(p_path)
                    if verdict == "PROMOTED":
                        ok, missing = promotion_lib.validate_v2_sections(p_path)
                        if ok:
                            metrics = promotion_lib.parse_metrics(p_path)
                            promotion_lib.on_promotion_success(
                                project_path, pre_item, metrics
                            )
                            state.log_event(
                                project_path,
                                "promotion_validated",
                                task_id=pre_item.id,
                                **{
                                    k: v
                                    for k, v in metrics.items()
                                    if v is not None
                                },
                            )
                        else:
                            promotion_lib.quarantine_invalid(
                                project_path, pre_item, missing
                            )
                    elif verdict == "REJECTED":
                        state.log_event(
                            project_path,
                            "promotion_rejected",
                            task_id=pre_item.id,
                        )
                    elif verdict == "CONDITIONAL":
                        # v1.3.6: distinct partial-pass state. Does NOT
                        # fire on_promotion_success — no ablation children,
                        # no leaderboard append. Operator reviews and
                        # decides whether to manually escalate to
                        # PROMOTED or REJECTED. Logged distinctly so it's
                        # visible in aggregate.jsonl.
                        state.log_event(
                            project_path,
                            "promotion_conditional",
                            task_id=pre_item.id,
                        )
                    else:
                        # v1.3.6 rename for clarity: parser explicitly
                        # could not recognize a verdict keyword (vs.
                        # v1.3.5's "missing" which conflated "no
                        # verdict line" and "verdict line in unexpected
                        # format"). Keep emitting the legacy event name
                        # too so any tooling filtering on
                        # `promotion_verdict_missing` keeps working.
                        state.log_event(
                            project_path,
                            "promotion_verdict_unrecognized",
                            task_id=pre_item.id,
                        )
                        state.log_event(
                            project_path,
                            "promotion_verdict_missing",
                            task_id=pre_item.id,
                        )
            except Exception as exc:  # noqa: BLE001 — telemetry must not crash
                _log(f"{project_path.name}: promotion validation error: {exc!r}")

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
                # v1.3 I2: verdict-stage transitions arm the knowledge.md
                # update sentinel. SessionStart hook will keep injecting
                # a mandatory reminder until knowledge.md mtime advances.
                #
                # v1.3.3 Group N: also stamp the verdict timestamp so
                # cc-autopipe-detach's gate (`knowledge_gate.py`) can
                # refuse to detach until knowledge.md mtime advances
                # past this point. This is the engine-side enforcement
                # that catches lessons before they're lost in the next
                # task switch.
                if knowledge_lib.is_verdict_stage(st):
                    verdict_ts = _now_iso()
                    was_already_armed = s.knowledge_update_pending
                    if not was_already_armed:
                        # v1.3.8 SENTINEL-RACE-FIX: idempotent stage-based
                        # arming + pre-cycle baseline. Mirrors the fix in
                        # `_maybe_arm_sentinel_via_promotion`. Re-arming on
                        # every verdict-stage transition would advance the
                        # baseline to the current (post-Claude-append)
                        # mtime; the detector compares `current > baseline`
                        # so an already-advanced mtime leaves pending stuck.
                        s.knowledge_update_pending = True
                        s.knowledge_baseline_mtime = _safe_baseline_mtime(
                            s, project_path
                        )
                        s.knowledge_pending_reason = f"{st} on {post_task_id}"
                    s.last_verdict_event_at = verdict_ts
                    s.last_verdict_task_id = post_task_id
                    state.write(project_path, s)
                    if was_already_armed:
                        state.log_event(
                            project_path,
                            "knowledge_sentinel_arm_skipped_already_armed",
                            task_id=post_task_id,
                            stage=st,
                            reason="stage_based",
                        )
                    state.log_event(
                        project_path,
                        "knowledge_update_required",
                        stage=st,
                        task_id=post_task_id,
                    )
                    state.log_event(
                        project_path,
                        "task_verdict",
                        stage=st,
                        task_id=post_task_id,
                        verdict_ts=verdict_ts,
                    )

        # v1.3.6 SENTINEL-PATTERNS fallback: arm the knowledge.md
        # sentinel when a fresh PROMOTION.md was just written with a
        # parseable verdict, even if CURRENT_TASK.md `stages_completed`
        # never contained a verdict-pattern stage. Defense-in-depth —
        # Claude task discipline may forget to emit a verdict-named
        # stage; the engine reads the artifact directly. Helper is
        # idempotent: skips when sentinel is already armed by the
        # stage-based path above.
        try:
            _maybe_arm_sentinel_via_promotion(project_path, post_task_id, s)
        except Exception as exc:  # noqa: BLE001 — telemetry must not crash
            _log(
                f"{project_path.name}: promotion-mtime fallback error: {exc!r}"
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
            _handle_smart_escalation(project_path, s, stderr, esc_cfg, esc_trigger)

        # Persist claude's stdout/stderr to disk on EVERY cycle (even on
        # empty content) so a fast rc!=0 exit is debuggable. Names are
        # explicit ("claude-last-*") so they're greppable in support
        # tickets.
        _stash_stream(project_path, "claude-last-stdout.log", stdout)
        _stash_stream(project_path, "claude-last-stderr.log", stderr)

        # v1.3.4 R4: transient classification BEFORE structural failure
        # handling. A claude exit caused by "Server is temporarily
        # limiting requests" / network blip / 5xx upstream must NOT be
        # treated as a project-level failure — that path triggers smart
        # escalation after 3 cycles, ending healthy work in 14-day
        # autonomy under transient pressure.
        if rc != 0:
            failure_class = transient_lib.classify_failure(rc, stderr)
            if failure_class == "transient":
                s.consecutive_transient_failures += 1
                s.last_transient_at = _now_iso()
                state.write(project_path, s)

                schedule = _backoff_override(
                    "CC_AUTOPIPE_TRANSIENT_BACKOFF_OVERRIDE", TRANSIENT_BACKOFF_SEC
                )
                idx = min(s.consecutive_transient_failures - 1, len(schedule) - 1)
                wait = schedule[idx]
                state.log_event(
                    project_path,
                    "claude_invocation_transient",
                    rc=rc,
                    stderr_tail=(stderr or "")[-300:],
                    attempt=s.consecutive_transient_failures,
                    backoff_sec=wait,
                )
                _log(
                    f"{project_path.name}: transient claude failure "
                    f"(attempt {s.consecutive_transient_failures}, backoff {wait}s)"
                )

                if s.consecutive_transient_failures >= MAX_TRANSIENT_RETRIES:
                    # Give up — fall through to the structural-failure
                    # path so a genuinely broken state masquerading as
                    # transient still gets smart escalation.
                    state.log_event(
                        project_path,
                        "claude_invocation_retry_exhausted",
                        attempts=s.consecutive_transient_failures,
                    )
                    s.consecutive_transient_failures = 0
                    state.write(project_path, s)
                    # Continue down to log_failure / consecutive_failures++.
                else:
                    _interruptible_sleep(wait)
                    state.log_event(
                        project_path,
                        "cycle_end",
                        iteration=s.iteration,
                        phase=s.phase,
                        rc=rc,
                        score=s.last_score,
                        outcome="transient_retry_scheduled",
                    )
                    return s.phase

        # On a successful cycle reset the transient counter so a single
        # green run forgives the prior pressure. Mirrors the existing
        # consecutive_failures reset semantics in update_verify.
        if rc == 0 and s.consecutive_transient_failures != 0:
            s.consecutive_transient_failures = 0
            state.write(project_path, s)

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
            current_stage = s.current_task.stage if s.current_task is not None else None
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

        # v1.3.7 STUCK-WITH-PROGRESS / ACTIVITY-MTIME-BASED:
        # compute filesystem evidence of in-cycle work. Both the stuck
        # gate (fail-skip) and the unconditional `last_activity_at`
        # refresh consult this. Cheap (`stat()` + small `glob`) so two
        # callers per cycle is fine.
        cycle_start_dt = _parse_iso_utc(s.last_cycle_started_at)
        cycle_start_unix = (
            cycle_start_dt.timestamp() if cycle_start_dt is not None else 0.0
        )
        try:
            fs_progress = _check_in_cycle_progress(
                project_path, cycle_start_unix, s
            )
        except Exception as exc:  # noqa: BLE001 — telemetry must not crash
            _log(f"{project_path.name}: progress probe error: {exc!r}")
            fs_progress = {
                "new_promotion_files": 0,
                "backlog_x_delta": 0,
                "current_task_stages_grew": False,
                "any_progress": False,
            }

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
                # v1.3.7 STUCK-WITH-PROGRESS: the engine-internal
                # `last_activity_at` is stale, but if the filesystem
                # shows Claude closed tasks / wrote PROMOTION files /
                # advanced CURRENT_TASK stages within this cycle window,
                # don't honour the stale-timestamp fail. Refresh
                # `last_activity_at` to now and log the skip; next cycle
                # starts clean. This decouples engine stuck-detection
                # from verify.sh rc=1 (which fires for many reasons
                # unrelated to staleness).
                if fs_progress["any_progress"]:
                    s.last_activity_at = _now_iso()
                    state.write(project_path, s)
                    state.log_event(
                        project_path,
                        "stuck_check_skipped_progress_detected",
                        iteration=s.iteration,
                        new_promotions=fs_progress["new_promotion_files"],
                        backlog_x_delta=fs_progress["backlog_x_delta"],
                        current_task_grew=fs_progress[
                            "current_task_stages_grew"
                        ],
                    )
                else:
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

        # v1.3.7 ACTIVITY-MTIME-BASED: refresh `last_activity_at` from
        # the filesystem evidence on every cycle, not only when stuck-
        # detection fires. Closes the pause+resume staleness bug — the
        # state-write-driven timestamp doesn't advance through a long
        # pause window even when the next cycle does real work; the
        # filesystem does. Idempotent with the stuck-skip path above
        # (which also refreshes), and a no-op when fs_progress shows
        # nothing to refresh from. Skipped on `phase=failed` so the
        # moment-of-fail record retains its diagnostic timestamp.
        if fs_progress["any_progress"] and s.phase != "failed":
            s.last_activity_at = _now_iso()
            state.write(project_path, s)

        # v1.3 F2: emit a health record for this cycle. Best-effort —
        # never blocks the cycle. Quota cache may be unavailable
        # (CC_AUTOPIPE_QUOTA_DISABLED, mock-claude tests) → omit fields.
        try:
            five_pct = None
            seven_pct = None
            try:
                q = quota_lib.read_cached()
                if q is not None:
                    five_pct = float(q.five_hour_pct)
                    seven_pct = float(q.seven_day_pct)
            except Exception:  # noqa: BLE001
                pass
            disk_free = None
            try:
                probe = disk_lib.check_disk_space(project_path, min_free_gb=0.0)
                disk_free = probe.get("free_gb")
            except Exception:  # noqa: BLE001
                pass
            health_lib.emit_cycle_health(
                project_name=project_path.name,
                iteration=s.iteration,
                phase=s.phase,
                five_hour_pct=five_pct,
                seven_day_pct=seven_pct,
                disk_free_gb=disk_free,
            )
        except Exception as exc:  # noqa: BLE001
            _log(f"{project_path.name}: health emit error: {exc!r}")

        # v1.3 H5: detect META_DECISION written this cycle and apply.
        if s.meta_reflect_pending:
            try:
                if detect_and_apply_decision(project_path, s):
                    s = state.read(project_path)
            except Exception as exc:  # noqa: BLE001
                _log(f"{project_path.name}: meta_decision error: {exc!r}")

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
