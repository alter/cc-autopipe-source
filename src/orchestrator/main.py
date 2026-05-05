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

Refs: SPEC.md §6.1, §8.3, §8.4, §15.1
"""

from __future__ import annotations

import argparse
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
)
import claude_settings  # noqa: E402
import locking  # noqa: E402
import quota_monitor as quota_monitor_lib  # noqa: E402
import state  # noqa: E402

DEFAULT_COOLDOWN_SEC = 30
DEFAULT_IDLE_SLEEP_SEC = 60


def _read_projects_list(user_home: Path) -> list[Path]:
    list_path = user_home / "projects.list"
    if not list_path.exists():
        return []
    return [
        Path(ln.strip())
        for ln in list_path.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]


def _install_signal_handlers() -> None:
    def handler(signum: int, _frame: object) -> None:
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
    _parse_args(argv if argv is not None else [])

    _install_signal_handlers()
    cooldown = float(os.environ.get("CC_AUTOPIPE_COOLDOWN_SEC", DEFAULT_COOLDOWN_SEC))
    idle_sleep = float(
        os.environ.get("CC_AUTOPIPE_IDLE_SLEEP_SEC", DEFAULT_IDLE_SLEEP_SEC)
    )
    # Test escape hatch: stop after N outer passes (a "pass" is one full
    # walk over projects.list). 0 means run forever.
    max_loops = int(os.environ.get("CC_AUTOPIPE_MAX_LOOPS", "0"))

    user_home = _user_home()

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
