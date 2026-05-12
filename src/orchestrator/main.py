#!/usr/bin/env python3
"""orchestrator.main — main loop, signal handlers, projects.list walk.

Per SPEC.md §6.1: read ~/.cc-autopipe/projects.list, walk FIFO, spawn
`claude -p` per active project (via cycle.process_project), monitor with
wall-clock timeout, log every cycle event. Hooks (SPEC §10) update
state.json from inside the claude subprocess. Singleton + per-project
locking via lib/locking (Stage D).

Test escape hatches (env vars):
  CC_AUTOPIPE_USER_HOME            override ~/.cc-autopipe
  CC_AUTOPIPE_COOLDOWN_SEC         seconds between projects (default 30)
  CC_AUTOPIPE_IDLE_SLEEP_SEC       seconds when no project active (default 60)
  CC_AUTOPIPE_MAX_LOOPS            exit after N outer passes (test-only)
  CC_AUTOPIPE_CLAUDE_BIN           path to claude binary (default: "claude").
                                   Tests point this at tools/mock-claude.sh.
  CC_AUTOPIPE_CYCLE_TIMEOUT_SEC    wall-clock cap on a claude subprocess
                                   (default 3600s)
  CC_AUTOPIPE_NO_REDIRECT          when set (any non-empty), disables the
                                   v1.3.2 stderr/stdout redirect even
                                   without --foreground. Used by tests
                                   that need to capture subprocess output
                                   directly via subprocess.run.

Refs: SPEC.md §6.1, §8.3, §8.4, §15.1
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
from pathlib import Path

import time

from orchestrator._runtime import (
    _interruptible_sleep,
    _log,
    _user_home,
    is_shutdown,
    set_shutdown,
)
from orchestrator.alerts import _notify_tg
from orchestrator.cycle import process_project
from orchestrator.prompt import _read_config_in_progress
from orchestrator.daily_report import maybe_write_for_all
from orchestrator.recovery import (
    RECOVERY_INTERVAL_SEC,
    auto_recover_failed_projects,
    rescan_orphan_promotions,
    sweep_done_projects,
)
import claude_settings  # noqa: E402
import locking  # noqa: E402
import quota_monitor as quota_monitor_lib  # noqa: E402
import state  # noqa: E402

DEFAULT_COOLDOWN_SEC = 30
DEFAULT_IDLE_SLEEP_SEC = 60
# v1.3.2 STDERR-LOGGING: rotation thresholds for daemonized log capture.
LOG_ROTATE_BYTES = 50 * 1024 * 1024  # 50 MB
LOG_ROTATE_KEEP = 3                  # keep .1, .2, .3 (drop older)


def _rotate_log(path: Path, keep: int = LOG_ROTATE_KEEP) -> None:
    """Shift path → path.1, path.1 → path.2, ... oldest beyond keep dropped.

    Called from `_redirect_streams_for_daemon` when a log exceeds
    LOG_ROTATE_BYTES. Best-effort: any individual rename failure is
    swallowed so a transient OS error doesn't take down the engine.
    """
    drop = Path(f"{path}.{keep}")
    if drop.exists():
        try:
            drop.unlink()
        except OSError:
            pass
    for i in range(keep - 1, 0, -1):
        src = Path(f"{path}.{i}")
        dst = Path(f"{path}.{i + 1}")
        if src.exists():
            try:
                os.replace(src, dst)
            except OSError:
                pass
    if path.exists():
        try:
            os.replace(path, Path(f"{path}.1"))
        except OSError:
            pass


def _redirect_streams_for_daemon(user_home: Path) -> None:
    """Redirect Python + OS-level stderr/stdout to rotating log files.

    Called when the orchestrator is invoked without --foreground (and
    without CC_AUTOPIPE_NO_REDIRECT). Without this, daemonized
    invocations (`cc-autopipe start` from a shell, nohup'd in the
    background, etc.) lose all stderr — including tracebacks from a
    silent crash. Real-world: 4-5 May AI-trade run died twice with no
    diagnostic.

    Implementation:
      - stderr → user_home/log/orchestrator-stderr.log
      - stdout → user_home/log/orchestrator-stdout.log
      - Append-mode, line-buffered.
      - Pre-rotate when the existing file exceeds LOG_ROTATE_BYTES so
        long-lived autonomy doesn't accumulate hundreds of MB.
      - os.dup2 replaces the OS-level fds so subprocess children
        (claude, etc.) inherit the redirected streams too.
    """
    log_dir = user_home / "log"
    log_dir.mkdir(parents=True, exist_ok=True)

    stderr_path = log_dir / "orchestrator-stderr.log"
    stdout_path = log_dir / "orchestrator-stdout.log"

    for path in (stderr_path, stdout_path):
        if path.exists() and path.stat().st_size > LOG_ROTATE_BYTES:
            _rotate_log(path)

    stderr_f = open(stderr_path, "a", buffering=1, encoding="utf-8")
    stdout_f = open(stdout_path, "a", buffering=1, encoding="utf-8")

    # Flush whatever was already buffered on the original streams before
    # we swap them out.
    try:
        sys.stderr.flush()
        sys.stdout.flush()
    except Exception:  # noqa: BLE001 — best effort during startup
        pass

    sys.stderr = stderr_f
    sys.stdout = stdout_f

    # OS-level fds: subprocess.Popen children inherit fds 1/2 from the
    # parent, so dup2 ensures any spawned process (claude, helper hooks)
    # writes into the same log files instead of the original console.
    os.dup2(stderr_f.fileno(), 2)
    os.dup2(stdout_f.fileno(), 1)


def _read_projects_list(user_home: Path) -> list[Path]:
    list_path = user_home / "projects.list"
    if not list_path.exists():
        return []
    return [
        Path(ln.strip())
        for ln in list_path.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]


def _has_in_flight_cycle(project_path: Path) -> bool:
    """v1.5.2 CYCLE-END-ON-SIGTERM probe.

    True iff the project's `progress.jsonl` ends with a `cycle_start` that
    has no following `cycle_end` event. Reads the whole file (small —
    one line per cycle event) and tracks the latest index of each.

    Best-effort: any I/O or parse error → False.
    """
    progress = project_path / ".cc-autopipe" / "memory" / "progress.jsonl"
    if not progress.exists():
        return False
    last_start = -1
    last_end = -1
    try:
        with progress.open("r", encoding="utf-8") as f:
            for i, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except (ValueError, json.JSONDecodeError):
                    continue
                name = ev.get("event")
                if name == "cycle_start":
                    last_start = i
                elif name == "cycle_end":
                    last_end = i
    except OSError:
        return False
    return last_start > last_end


def _flush_in_flight_cycles(user_home: Path) -> None:
    """v1.5.2 CYCLE-END-ON-SIGTERM: close dangling cycle_start events.

    Iterates projects.list and, for each project whose progress.jsonl
    ends with an unmatched cycle_start, emits a synthetic
    `cycle_end iteration=<current> rc=interrupted phase=<current_phase>
    score=null interrupted_by=sigterm` event so per-cycle telemetry
    cannot have a dangling cycle_start when systemd SIGKILLs the
    orchestrator after TimeoutStopSec expires.

    Best-effort: never raises, never blocks. <500ms target — only one
    state read + one log_event append per affected project.
    """
    try:
        projects = _read_projects_list(user_home)
    except Exception as exc:  # noqa: BLE001
        _log(f"flush_in_flight_cycles: projects.list unreadable: {exc!r}")
        return
    for project_path in projects:
        try:
            if not _has_in_flight_cycle(project_path):
                continue
            s = state.read(project_path)
            state.log_event(
                project_path,
                "cycle_end",
                iteration=s.iteration,
                phase=s.phase,
                rc="interrupted",
                score=None,
                interrupted_by="sigterm",
            )
        except Exception as exc:  # noqa: BLE001
            # Per-project failure must not block other projects' flush.
            _log(f"flush_in_flight_cycles: skipping {project_path}: {exc!r}")


def _install_signal_handlers() -> None:
    def handler(signum: int, _frame: object) -> None:
        # v1.5.2 CYCLE-END-ON-SIGTERM: flush BEFORE setting the shutdown
        # flag so the synthetic cycle_end lands even if systemd's
        # TimeoutStopSec elapses while we're still inside this handler.
        # Best-effort and guarded — never raise from inside a signal
        # handler.
        try:
            _flush_in_flight_cycles(_user_home())
        except Exception as exc:  # noqa: BLE001
            _log(f"flush_in_flight_cycles unexpected error: {exc!r}")
        set_shutdown(True)
        _log(f"received signal {signum}, shutting down at next safe point")

    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="cc-autopipe start",
        description="Run the cc-autopipe orchestrator loop.",
    )
    parser.add_argument(
        "--foreground",
        action="store_true",
        help=(
            "Run in foreground without daemonization. Required for systemd "
            "Type=simple service execution."
        ),
    )
    parser.add_argument(
        "--background",
        action="store_true",
        help=(
            "Reserved: explicit background mode. Currently the default "
            "(orchestrator does not self-daemonize)."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    # Tolerate unknown flags gracefully — the dispatcher passes any
    # `cc-autopipe start <flag>` straight through.
    args = _parse_args(argv if argv is not None else [])

    user_home = _user_home()

    # v1.3.2 STDERR-LOGGING: redirect stderr/stdout to rotating log
    # files unless --foreground (systemd service, terminal session)
    # or CC_AUTOPIPE_NO_REDIRECT (test harness) is set. Must happen
    # before _install_signal_handlers so any traceback from a signal
    # received during startup lands in the log file.
    if not args.foreground and not os.environ.get("CC_AUTOPIPE_NO_REDIRECT"):
        _redirect_streams_for_daemon(user_home)

    _install_signal_handlers()
    cooldown = float(os.environ.get("CC_AUTOPIPE_COOLDOWN_SEC", DEFAULT_COOLDOWN_SEC))
    idle_sleep = float(
        os.environ.get("CC_AUTOPIPE_IDLE_SLEEP_SEC", DEFAULT_IDLE_SLEEP_SEC)
    )
    # Test escape hatch: stop after N outer passes (a "pass" is one full
    # walk over projects.list). 0 means run forever.
    max_loops = int(os.environ.get("CC_AUTOPIPE_MAX_LOOPS", "0"))

    # Acquire the singleton orchestrator lock per SPEC §8.3. fcntl
    # auto-releases on process death, so a previous orchestrator killed
    # via kill -9 doesn't block us — this acquire just succeeds.
    singleton = locking.acquire_singleton(user_home)
    if singleton is None:
        prior = locking.read_lock_payload(user_home / "orchestrator.pid") or {}
        prior_pid = prior.get("pid", "?")
        prior_started = prior.get("started_at", "?")
        _log(
            f"another orchestrator is already running "
            f"(pid={prior_pid}, started_at={prior_started}). Exiting."
        )
        return 1

    # Disable the operator's global ~/.claude/settings.json hooks for
    # the duration of the engine run. Roman's PreToolUse / UserPromptSubmit
    # hooks block routine bash + inject compliance reminders that conflict
    # with engine-driven Claude sessions. Backup is restored by
    # `cc-autopipe stop`. Idempotent across crashes (existing backup not
    # overwritten). Refs: instruction-hotfix.md (post-v1.2 patch).
    hooks_result = claude_settings.disable_global_hooks_with_backup()
    if hooks_result["action"] == "backed_up":
        _log(
            f"disabled global Claude hooks "
            f"(backed up to {hooks_result['backup_path']})"
        )
    elif hooks_result["action"] == "parse_error":
        _log("WARN: could not parse ~/.claude/settings.json, leaving as-is")

    # Stage K: spin up the proactive 7d-quota warning daemon. This is
    # additive to the pre-flight check (which still hard-pauses at
    # >=95%) — the monitor fires TG warnings at 70 / 80 / 90 / 95 so
    # the operator sees the burn early. Daemon thread; orchestrator
    # crash takes it down with the process per quota_monitor design.
    monitor_interval = float(
        os.environ.get(
            "CC_AUTOPIPE_QUOTA_MONITOR_INTERVAL_SEC",
            quota_monitor_lib.DEFAULT_CHECK_INTERVAL_SEC,
        )
    )
    quota_monitor = quota_monitor_lib.QuotaMonitor(
        check_interval_sec=monitor_interval,
        notify_tg=_notify_tg,
        user_home=user_home,
    )
    quota_monitor.start()

    try:
        _log(
            f"started; user_home={user_home}; cooldown={cooldown}s; "
            f"singleton_pid={singleton.pid}; "
            f"quota_monitor_interval={monitor_interval}s"
        )

        # v1.5.3 ORPHAN-PROMOTION-RESCAN: at startup, scan every project
        # for CAND_*_PROMOTION.md files written by a cycle that was
        # SIGTERM-interrupted before post_cycle_delta could validate
        # and leaderboard them. Best-effort per project — never blocks
        # the main loop.
        try:
            for _orph_proj in _read_projects_list(user_home):
                try:
                    n_rescued = rescan_orphan_promotions(_orph_proj)
                    if n_rescued:
                        _log(
                            f"{_orph_proj.name}: rescued {n_rescued} orphan "
                            f"PROMOTION(s) on startup"
                        )
                except Exception as exc:  # noqa: BLE001
                    _log(
                        f"{_orph_proj.name}: orphan rescan failed: {exc!r}"
                    )
        except Exception as exc:  # noqa: BLE001
            _log(f"orphan rescan startup sweep error: {exc!r}")

        loops = 0
        last_recovery_sweep_at = 0.0
        last_daily_report_at = 0.0
        while not is_shutdown():
            projects = _read_projects_list(user_home)
            # v1.3 B3: periodic auto-recovery sweep — revive any
            # project that has been `phase=failed` for >1h with no
            # activity. Bounded by RECOVERY_INTERVAL_SEC across the
            # full projects list (cheap: state.read per project).
            if time.time() - last_recovery_sweep_at >= RECOVERY_INTERVAL_SEC:
                try:
                    revived = auto_recover_failed_projects(projects)
                    if revived:
                        _log(f"auto-recovery sweep revived {revived} project(s)")
                except Exception as exc:  # noqa: BLE001
                    _log(f"auto-recovery sweep error: {exc!r}")
                # v1.3.6 PHASE-DONE-RECOVERY: parallel sweep that flips
                # `phase=done` projects back to `active` when their
                # backlog has been reopened (operator added new tasks).
                # Without this, a 3-4 month autonomous run requires
                # manual state.json edits every time a backlog cycle
                # drains → reopens.
                try:
                    resumed = sweep_done_projects(projects)
                    if resumed:
                        _log(
                            f"phase-done-resume sweep flipped {resumed} "
                            f"project(s) back to active"
                        )
                except Exception as exc:  # noqa: BLE001
                    _log(f"phase-done-resume sweep error: {exc!r}")
                last_recovery_sweep_at = time.time()

            # v1.3 F1: write per-project daily summary every 24h.
            # Best-effort, capped to one write per project per day.
            try:
                last_daily_report_at, written = maybe_write_for_all(
                    list(projects), last_daily_report_at, time.time()
                )
                if written:
                    _log(f"daily report wrote {len(written)} summary file(s)")
            except Exception as exc:  # noqa: BLE001
                _log(f"daily report error: {exc!r}")

            active_count = 0
            for project in projects:
                if is_shutdown():
                    break
                try:
                    status_str = process_project(project)
                except Exception as exc:  # noqa: BLE001
                    _log(f"{project}: cycle error: {exc!r}")
                    status_str = "error"
                if status_str == "active":
                    active_count += 1
                # v1.2 Bug B: extend cooldown when verify reported the
                # project is "still cooking". Burns less of the operator's
                # observed cycle budget on a project that won't have
                # anything new to verify for a while.
                project_cooldown = cooldown
                try:
                    project_state = state.read(project)
                    if project_state.last_in_progress:
                        ip_cfg = _read_config_in_progress(project)
                        mult = int(ip_cfg.get("cooldown_multiplier") or 3)
                        if mult > 1:
                            project_cooldown = cooldown * mult
                except Exception:  # noqa: BLE001 — fail open to base cooldown
                    pass
                _interruptible_sleep(project_cooldown)

            loops += 1
            if max_loops and loops >= max_loops:
                _log(f"reached CC_AUTOPIPE_MAX_LOOPS={max_loops}; exiting")
                break

            if active_count == 0 and not is_shutdown():
                _interruptible_sleep(idle_sleep)

        _log("orchestrator shutdown gracefully")
        return 0
    finally:
        quota_monitor.stop(timeout=5.0)
        singleton.release()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
