"""quota_monitor.py — daemon thread that warns on 7d quota burn.

Refs: SPEC-v1.md §2.4

The orchestrator's pre-flight check pauses projects when 7d hits ~95%
(see orchestrator.PREFLIGHT_7D_PAUSE), but by then it's too late — the
weekly window is already burned. quota_monitor runs in the background
and fires TG warnings at 70 / 80 / 90 % so the operator can slow down
deliberately before pre-flight forces a hard pause.

Architecture:
- Single daemon thread inside the orchestrator process.
- Loop: read quota.read_cached() (60s TTL → cheap), compare against
  thresholds, fire TG once per (threshold × day) combo.
- Dedup via per-day-per-threshold sentinel files at
  $CC_AUTOPIPE_USER_HOME/7d-warn-{pct}-{YYYY-MM-DD}.flag.
- Stops cleanly on shutdown event (mirrors HeartbeatThread).
- Failures (TG down, quota.py None) are logged but never crash the
  monitor — orchestrator should never fall over because a warning
  thread had a bad day.

Test escape hatches (via env or constructor args, not module globals,
so per-test isolation is clean):
- check_interval_sec    poll cadence (default 1800 = 30min)
- thresholds            ordered list of (pct, label) tuples
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from collections.abc import Callable, Iterable
from datetime import date
from pathlib import Path

# Lazily import quota at call time so test fixtures can monkeypatch the
# module's read_cached without importing this module first.
import quota as quota_lib  # type: ignore[import-not-found]

DEFAULT_CHECK_INTERVAL_SEC = 1800  # 30 minutes
DEFAULT_THRESHOLDS: tuple[tuple[float, str], ...] = (
    (0.95, "95"),
    (0.90, "90"),
    (0.80, "80"),
    (0.70, "70"),
)


def _log(msg: str) -> None:
    print(f"[quota_monitor] {msg}", file=sys.stderr, flush=True)


def _default_notify_tg(message: str) -> None:
    """Fire-and-forget TG. Identical contract to orchestrator._notify_tg."""
    tg_sh = Path(__file__).resolve().parent / "tg.sh"
    if not tg_sh.exists():
        return
    try:
        subprocess.run(
            ["bash", str(tg_sh), message],
            timeout=10,
            check=False,
            capture_output=True,
        )
    except (subprocess.TimeoutExpired, OSError):
        pass


def _user_home() -> Path:
    return Path(
        os.environ.get("CC_AUTOPIPE_USER_HOME", str(Path.home() / ".cc-autopipe"))
    )


def _flag_path(user_home: Path, pct_label: str, today: str) -> Path:
    """Per-day-per-threshold sentinel file. Touch once per fired warning."""
    return user_home / f"7d-warn-{pct_label}-{today}.flag"


def check_once(
    *,
    user_home: Path,
    thresholds: Iterable[tuple[float, str]] = DEFAULT_THRESHOLDS,
    notify_tg: Callable[[str], None] = _default_notify_tg,
    today_iso: str | None = None,
    quota_reader: Callable[[], object] | None = None,
) -> str | None:
    """Run one quota check + dedup + warn.

    Returns the pct_label that fired (e.g. "95"), or None if nothing
    fired. Useful for tests to assert exactly which threshold tripped.

    quota_reader defaults to `quota_lib.read_cached` resolved at call
    time (NOT at function-definition time) so tests can monkeypatch
    `quota_monitor.quota_lib.read_cached` and have it take effect even
    inside the QuotaMonitor daemon's bound call.
    """
    if quota_reader is None:
        quota_reader = quota_lib.read_cached
    q = quota_reader()
    if q is None:
        return None

    seven_day_pct = getattr(q, "seven_day_pct", None)
    if seven_day_pct is None:
        return None

    today = today_iso or date.today().isoformat()

    # Iterate from highest threshold downward; only the first match fires.
    # That way 95% doesn't ALSO trigger 70/80/90 in the same check.
    for pct, label in thresholds:
        if seven_day_pct >= pct:
            flag = _flag_path(user_home, label, today)
            if flag.exists():
                return None  # already warned today at this threshold
            try:
                flag.parent.mkdir(parents=True, exist_ok=True)
                flag.touch()
            except OSError as exc:
                _log(f"failed to write dedup flag {flag}: {exc!r}")
                # Send TG anyway — operator alert is more important than dedup.
            level = (
                "EMERGENCY"
                if pct >= 0.95
                else "DANGER"
                if pct >= 0.90
                else "warning"
                if pct >= 0.80
                else "heads up"
            )
            try:
                notify_tg(
                    f"[cc-autopipe] 7d quota at {int(seven_day_pct * 100)}% — {level}"
                )
            except Exception as exc:  # noqa: BLE001
                _log(f"notify_tg raised: {exc!r}")
            return label
    return None


class QuotaMonitor:
    """Daemon thread: every interval seconds, run check_once().

    Mirrors lib/locking.HeartbeatThread's start/stop interface so the
    orchestrator main() can manage it identically. Daemon=True so an
    orchestrator crash doesn't leave it dangling.
    """

    def __init__(
        self,
        *,
        check_interval_sec: float = DEFAULT_CHECK_INTERVAL_SEC,
        thresholds: Iterable[tuple[float, str]] = DEFAULT_THRESHOLDS,
        notify_tg: Callable[[str], None] = _default_notify_tg,
        user_home: Path | None = None,
    ) -> None:
        self.check_interval_sec = check_interval_sec
        self.thresholds = tuple(thresholds)
        self.notify_tg = notify_tg
        self.user_home = user_home or _user_home()
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="quota-monitor", daemon=True
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def _run(self) -> None:
        # Initial check fires immediately so a stale 90%+ environment
        # doesn't wait 30 min for the first warning.
        while not self._stop.is_set():
            try:
                check_once(
                    user_home=self.user_home,
                    thresholds=self.thresholds,
                    notify_tg=self.notify_tg,
                )
            except Exception as exc:  # noqa: BLE001
                _log(f"check_once raised: {exc!r}")
            if self._stop.wait(self.check_interval_sec):
                break
