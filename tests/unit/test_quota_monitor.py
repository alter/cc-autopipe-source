"""Unit tests for src/lib/quota_monitor.py — Stage K (proactive 7d warnings).

Covers SPEC-v1.md §2.4 acceptance:
- Warning fires at each documented threshold (70/80/90/95)
- Iterating from highest threshold down: only one fires per check
- Per-day-per-threshold dedup via flag files
- Tomorrow re-fires (different date → different flag)
- Quota unavailable (read_cached returns None) → no warning
- TG failure does not crash the monitor
- Daemon lifecycle: start/stop within reasonable wall clock
"""

from __future__ import annotations

import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src" / "lib"))

import quota_monitor  # noqa: E402


@dataclass
class _FakeQuota:
    """Stand-in for quota.Quota — only seven_day_pct is read by check_once."""

    seven_day_pct: float


def _make_reader(pct: float | None) -> "callable[[], _FakeQuota | None]":
    if pct is None:
        return lambda: None
    return lambda: _FakeQuota(seven_day_pct=pct)


def _capture_tg() -> tuple[list[str], "callable[[str], None]"]:
    msgs: list[str] = []

    def notify(msg: str) -> None:
        msgs.append(msg)

    return msgs, notify


# ---------------------------------------------------------------------------
# Threshold dispatch
# ---------------------------------------------------------------------------


def test_70_pct_fires_heads_up_warning(tmp_path: Path) -> None:
    msgs, notify = _capture_tg()
    fired = quota_monitor.check_once(
        user_home=tmp_path,
        notify_tg=notify,
        today_iso="2026-05-02",
        quota_reader=_make_reader(0.72),
    )
    assert fired == "70"
    assert len(msgs) == 1
    assert "72%" in msgs[0]
    assert "heads up" in msgs[0]


def test_80_pct_fires_warning(tmp_path: Path) -> None:
    msgs, notify = _capture_tg()
    fired = quota_monitor.check_once(
        user_home=tmp_path,
        notify_tg=notify,
        today_iso="2026-05-02",
        quota_reader=_make_reader(0.83),
    )
    assert fired == "80"
    assert "warning" in msgs[0]
    assert "DANGER" not in msgs[0]


def test_90_pct_fires_danger(tmp_path: Path) -> None:
    msgs, notify = _capture_tg()
    fired = quota_monitor.check_once(
        user_home=tmp_path,
        notify_tg=notify,
        today_iso="2026-05-02",
        quota_reader=_make_reader(0.91),
    )
    assert fired == "90"
    assert "DANGER" in msgs[0]


def test_95_pct_fires_emergency(tmp_path: Path) -> None:
    msgs, notify = _capture_tg()
    fired = quota_monitor.check_once(
        user_home=tmp_path,
        notify_tg=notify,
        today_iso="2026-05-02",
        quota_reader=_make_reader(0.97),
    )
    assert fired == "95"
    assert "EMERGENCY" in msgs[0]


def test_below_70_pct_does_not_fire(tmp_path: Path) -> None:
    msgs, notify = _capture_tg()
    fired = quota_monitor.check_once(
        user_home=tmp_path,
        notify_tg=notify,
        today_iso="2026-05-02",
        quota_reader=_make_reader(0.42),
    )
    assert fired is None
    assert msgs == []


def test_only_highest_threshold_fires_per_check(tmp_path: Path) -> None:
    """At 95% all thresholds (95/90/80/70) match, but only the top
    one should fire — we don't want 4 messages from a single check."""
    msgs, notify = _capture_tg()
    fired = quota_monitor.check_once(
        user_home=tmp_path,
        notify_tg=notify,
        today_iso="2026-05-02",
        quota_reader=_make_reader(0.99),
    )
    assert fired == "95"
    assert len(msgs) == 1


# ---------------------------------------------------------------------------
# Dedup
# ---------------------------------------------------------------------------


def test_same_threshold_same_day_dedups(tmp_path: Path) -> None:
    msgs, notify = _capture_tg()
    quota_monitor.check_once(
        user_home=tmp_path,
        notify_tg=notify,
        today_iso="2026-05-02",
        quota_reader=_make_reader(0.83),
    )
    quota_monitor.check_once(
        user_home=tmp_path,
        notify_tg=notify,
        today_iso="2026-05-02",
        quota_reader=_make_reader(0.84),
    )
    assert len(msgs) == 1, "second 80% check on same day must be deduped"


def test_same_threshold_next_day_re_fires(tmp_path: Path) -> None:
    msgs, notify = _capture_tg()
    quota_monitor.check_once(
        user_home=tmp_path,
        notify_tg=notify,
        today_iso="2026-05-02",
        quota_reader=_make_reader(0.83),
    )
    quota_monitor.check_once(
        user_home=tmp_path,
        notify_tg=notify,
        today_iso="2026-05-03",
        quota_reader=_make_reader(0.83),
    )
    assert len(msgs) == 2


def test_higher_threshold_after_lower_fires_independently(tmp_path: Path) -> None:
    """Day 1: 80% fires. Quota climbs to 90% same day → 90% should still
    fire because it's a different threshold (separate flag file)."""
    msgs, notify = _capture_tg()
    quota_monitor.check_once(
        user_home=tmp_path,
        notify_tg=notify,
        today_iso="2026-05-02",
        quota_reader=_make_reader(0.83),
    )
    assert len(msgs) == 1
    quota_monitor.check_once(
        user_home=tmp_path,
        notify_tg=notify,
        today_iso="2026-05-02",
        quota_reader=_make_reader(0.92),
    )
    assert len(msgs) == 2
    assert "DANGER" in msgs[1]


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_quota_unavailable_returns_none(tmp_path: Path) -> None:
    msgs, notify = _capture_tg()
    fired = quota_monitor.check_once(
        user_home=tmp_path,
        notify_tg=notify,
        today_iso="2026-05-02",
        quota_reader=_make_reader(None),
    )
    assert fired is None
    assert msgs == []


def test_notify_tg_exception_is_swallowed(tmp_path: Path) -> None:
    """A TG outage must not crash the monitor — the flag file is still
    written so we don't spam on every retry."""

    def bad_notify(_msg: str) -> None:
        raise RuntimeError("TG down")

    fired = quota_monitor.check_once(
        user_home=tmp_path,
        notify_tg=bad_notify,
        today_iso="2026-05-02",
        quota_reader=_make_reader(0.83),
    )
    assert fired == "80"
    flag = tmp_path / "7d-warn-80-2026-05-02.flag"
    assert flag.exists()


# ---------------------------------------------------------------------------
# QuotaMonitor daemon lifecycle
# ---------------------------------------------------------------------------


def test_quota_monitor_starts_and_stops(tmp_path: Path) -> None:
    msgs, notify = _capture_tg()
    # Use a custom quota_reader closure via the underlying check_once kwargs
    # is not possible here — the QuotaMonitor uses module default. Instead,
    # monkeypatch the quota_lib.read_cached on this module.
    state = {"calls": 0}

    def fake_read_cached() -> _FakeQuota:
        state["calls"] += 1
        return _FakeQuota(seven_day_pct=0.83)

    original = quota_monitor.quota_lib.read_cached
    quota_monitor.quota_lib.read_cached = fake_read_cached  # type: ignore[assignment]
    try:
        mon = quota_monitor.QuotaMonitor(
            check_interval_sec=0.05,  # fast, for the test
            notify_tg=notify,
            user_home=tmp_path,
        )
        mon.start()
        # Give the thread enough time for at least 2 ticks.
        deadline = time.time() + 3.0
        while time.time() < deadline and state["calls"] < 2:
            time.sleep(0.05)
        mon.stop(timeout=2.0)
    finally:
        quota_monitor.quota_lib.read_cached = original

    assert state["calls"] >= 2, f"expected >=2 quota reads, got {state['calls']}"
    # Dedup means only one TG even though we polled twice.
    assert len(msgs) == 1


def test_quota_monitor_thread_stops_within_timeout(tmp_path: Path) -> None:
    """stop() must join the thread cleanly — a leaky daemon would
    survive orchestrator shutdown and confuse next-session
    locking diagnostics."""
    mon = quota_monitor.QuotaMonitor(
        check_interval_sec=10.0,
        user_home=tmp_path,
        notify_tg=lambda _m: None,
    )
    mon.start()
    t0 = time.time()
    mon.stop(timeout=1.5)
    elapsed = time.time() - t0
    assert elapsed < 1.5
    # Thread should be done.
    assert not mon._thread.is_alive()


def test_quota_monitor_daemon_attribute(tmp_path: Path) -> None:
    """Daemon flag = True so a crashed orchestrator doesn't keep us
    pinned alive."""
    mon = quota_monitor.QuotaMonitor(
        check_interval_sec=10.0,
        user_home=tmp_path,
    )
    assert mon._thread.daemon is True
    # Don't start — just verify the construction.


def test_quota_monitor_run_does_not_crash_on_check_exception(
    tmp_path: Path,
) -> None:
    """If check_once itself raises (e.g. quota.read_cached crashes),
    the loop should swallow + continue, not propagate."""
    bad_event = threading.Event()

    def bad_reader() -> _FakeQuota:
        bad_event.set()
        raise RuntimeError("kaboom")

    original = quota_monitor.quota_lib.read_cached
    quota_monitor.quota_lib.read_cached = bad_reader  # type: ignore[assignment]
    try:
        mon = quota_monitor.QuotaMonitor(
            check_interval_sec=0.05,
            user_home=tmp_path,
        )
        mon.start()
        assert bad_event.wait(timeout=2.0), "bad reader was never called"
        # Give it another tick to ensure swallowing works without crashing.
        time.sleep(0.2)
        mon.stop(timeout=1.0)
    finally:
        quota_monitor.quota_lib.read_cached = original
    # If we got here, the thread didn't crash the test process.
