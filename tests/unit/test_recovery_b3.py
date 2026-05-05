"""Unit tests for src/orchestrator/recovery.py — v1.3 B2 + B3.

Covers:
  - evaluate_stuck (B2): activity-age based ok/warn/fail
  - maybe_auto_recover (B3): single-project revive
  - auto_recover_failed_projects (B3): sweep helper
"""

from __future__ import annotations

import importlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
LIB = SRC / "lib"
for p in (str(SRC), str(LIB)):
    if p not in sys.path:
        sys.path.insert(0, p)

import state  # noqa: E402

recovery = importlib.import_module("orchestrator.recovery")


def _ts_offset_min(minutes: int) -> str:
    """ISO 8601 string `minutes` ago (negative = future)."""
    dt = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _project(tmp_path: Path) -> Path:
    p = tmp_path / "demo"
    (p / ".cc-autopipe").mkdir(parents=True)
    return p


# ---------------------------------------------------------------------------
# evaluate_stuck (B2)
# ---------------------------------------------------------------------------


def test_evaluate_stuck_no_activity_at_returns_ok() -> None:
    s = state.State.fresh("p")
    s.last_activity_at = None
    assert recovery.evaluate_stuck(s) == "ok"


def test_evaluate_stuck_recent_activity_ok() -> None:
    s = state.State.fresh("p")
    s.last_activity_at = _ts_offset_min(5)
    assert recovery.evaluate_stuck(s) == "ok"


def test_evaluate_stuck_warn_band() -> None:
    s = state.State.fresh("p")
    s.last_activity_at = _ts_offset_min(35)  # >30, <60
    assert recovery.evaluate_stuck(s) == "warn"


def test_evaluate_stuck_fail_band() -> None:
    s = state.State.fresh("p")
    s.last_activity_at = _ts_offset_min(75)  # >60
    assert recovery.evaluate_stuck(s) == "fail"


def test_evaluate_stuck_malformed_ts_returns_ok() -> None:
    s = state.State.fresh("p")
    s.last_activity_at = "not-a-date"
    assert recovery.evaluate_stuck(s) == "ok"


# ---------------------------------------------------------------------------
# maybe_auto_recover (B3)
# ---------------------------------------------------------------------------


def test_auto_recover_skips_active_project(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    p = _project(tmp_path)
    s = state.State.fresh(p.name)
    s.phase = "active"
    state.write(p, s)
    assert recovery.maybe_auto_recover(p) is False
    assert state.read(p).phase == "active"


def test_auto_recover_skips_recently_failed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    p = _project(tmp_path)
    s = state.State.fresh(p.name)
    s.phase = "failed"
    s.last_activity_at = _ts_offset_min(15)  # too recent
    state.write(p, s)
    assert recovery.maybe_auto_recover(p) is False
    assert state.read(p).phase == "failed"


def test_auto_recover_revives_old_failed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    p = _project(tmp_path)
    s = state.State.fresh(p.name)
    s.phase = "failed"
    s.consecutive_failures = 5
    s.consecutive_in_progress = 4
    s.session_id = "old-session"
    s.last_in_progress = True
    s.last_activity_at = _ts_offset_min(75)  # >1h ago
    state.write(p, s)

    assert recovery.maybe_auto_recover(p) is True
    s2 = state.read(p)
    assert s2.phase == "active"
    assert s2.consecutive_failures == 0
    assert s2.consecutive_in_progress == 0
    assert s2.last_in_progress is False
    assert s2.session_id is None
    assert s2.recovery_attempts == 1
    assert s2.last_activity_at is not None


def test_auto_recover_no_cap_increments_attempts(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    p = _project(tmp_path)
    s = state.State.fresh(p.name)
    s.phase = "failed"
    s.recovery_attempts = 99
    s.last_activity_at = _ts_offset_min(75)
    state.write(p, s)
    assert recovery.maybe_auto_recover(p) is True
    assert state.read(p).recovery_attempts == 100


def test_auto_recover_skips_when_no_activity_history(
    tmp_path: Path, monkeypatch
) -> None:
    """A failed project with no last_activity_at must NOT auto-revive —
    the field is missing only for pre-v1.3 projects, and reviving them
    would break the v1.2 manual-resume contract operators rely on for
    cold-failed projects."""
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    p = _project(tmp_path)
    s = state.State.fresh(p.name)
    s.phase = "failed"
    s.last_activity_at = None
    state.write(p, s)
    assert recovery.maybe_auto_recover(p) is False
    assert state.read(p).phase == "failed"


# ---------------------------------------------------------------------------
# auto_recover_failed_projects sweep
# ---------------------------------------------------------------------------


def test_sweep_counts_revived(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    p1 = tmp_path / "p1"
    p2 = tmp_path / "p2"
    p3 = tmp_path / "p3"
    for p in (p1, p2, p3):
        (p / ".cc-autopipe").mkdir(parents=True)
    # p1: failed long ago — revivable
    s1 = state.State.fresh(p1.name)
    s1.phase = "failed"
    s1.last_activity_at = _ts_offset_min(75)
    state.write(p1, s1)
    # p2: active — skipped
    s2 = state.State.fresh(p2.name)
    s2.phase = "active"
    state.write(p2, s2)
    # p3: failed but recent — skipped
    s3 = state.State.fresh(p3.name)
    s3.phase = "failed"
    s3.last_activity_at = _ts_offset_min(15)
    state.write(p3, s3)

    revived = recovery.auto_recover_failed_projects([p1, p2, p3])
    assert revived == 1
    assert state.read(p1).phase == "active"
    assert state.read(p2).phase == "active"
    assert state.read(p3).phase == "failed"


def test_sweep_continues_on_per_project_error(
    tmp_path: Path, monkeypatch
) -> None:
    """If maybe_auto_recover raises for one project, sweep must not abort."""
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    p_good = tmp_path / "good"
    p_bad = tmp_path / "bad"
    (p_good / ".cc-autopipe").mkdir(parents=True)
    (p_bad / ".cc-autopipe").mkdir(parents=True)
    s = state.State.fresh(p_good.name)
    s.phase = "failed"
    s.last_activity_at = _ts_offset_min(75)
    state.write(p_good, s)

    calls = []
    original = recovery.maybe_auto_recover

    def maybe(p: Path) -> bool:
        calls.append(p)
        if p.name == "bad":
            raise RuntimeError("boom")
        return original(p)

    monkeypatch.setattr(recovery, "maybe_auto_recover", maybe)
    revived = recovery.auto_recover_failed_projects([p_bad, p_good])
    assert revived == 1
    assert len(calls) == 2


# ---------------------------------------------------------------------------
# v1.3.1 B3-FIX: shutdown safety + per-project lock awareness
# ---------------------------------------------------------------------------


def test_sweep_aborts_on_shutdown(tmp_path: Path, monkeypatch) -> None:
    """A SIGTERM mid-sweep flips the shutdown flag — the inner loop
    must stop iterating projects so state.json mutations don't keep
    happening after the operator asked us to stop."""
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    p1 = tmp_path / "p1"
    p2 = tmp_path / "p2"
    for p in (p1, p2):
        (p / ".cc-autopipe").mkdir(parents=True)
        s = state.State.fresh(p.name)
        s.phase = "failed"
        s.last_activity_at = _ts_offset_min(75)
        state.write(p, s)

    calls: list[Path] = []
    original = recovery.maybe_auto_recover

    def stop_after_first(p: Path) -> bool:
        calls.append(p)
        # Flip shutdown after the first project is processed.
        from orchestrator import _runtime
        _runtime.set_shutdown(True)
        return original(p)

    monkeypatch.setattr(recovery, "maybe_auto_recover", stop_after_first)
    try:
        recovery.auto_recover_failed_projects([p1, p2])
        # Only p1 was visited; p2 was skipped because the shutdown flag
        # tripped before it was reached.
        assert calls == [p1]
        assert state.read(p1).phase == "active"
        assert state.read(p2).phase == "failed"
    finally:
        from orchestrator import _runtime
        _runtime.set_shutdown(False)


def test_auto_recover_skips_when_lock_held(tmp_path: Path, monkeypatch) -> None:
    """Race protection: if another process holds the per-project lock
    (in-flight cycle from another orchestrator, or stale fcntl handoff),
    auto-recovery must skip rather than clobber that process's state."""
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    p = _project(tmp_path)
    s = state.State.fresh(p.name)
    s.phase = "failed"
    s.last_activity_at = _ts_offset_min(75)
    state.write(p, s)

    import locking
    other = locking.acquire_project(p)
    assert other is not None  # we hold it now
    try:
        # Recovery sees the lock held, skips, returns False.
        assert recovery.maybe_auto_recover(p) is False
        assert state.read(p).phase == "failed"
    finally:
        other.release()


# ---------------------------------------------------------------------------
# v1.3.1 B-FIX: long-running training scenario (AI-trade regression)
# ---------------------------------------------------------------------------


def test_15_active_cycles_never_fail() -> None:
    """AI-trade regression: 7 in_progress cycles of legitimate ML
    training tripped v1.2's `consecutive_in_progress >= max` cap and
    sent the project to phase=failed. v1.3 B2 replaced the cap with
    activity-based stuck detection — as long as detect_activity keeps
    returning is_active=True (touched checkpoints, running processes,
    stage transitions), last_activity_at stays current and
    evaluate_stuck returns 'ok' regardless of cycle count.
    """
    s = state.State.fresh("ai-trade")
    s.last_in_progress = True
    for _ in range(15):
        s.consecutive_in_progress += 1
        # Simulates cycle.py:372 — any activity signal updates timestamp.
        s.last_activity_at = _ts_offset_min(0)
        assert recovery.evaluate_stuck(s) == "ok"
    assert s.consecutive_in_progress == 15  # telemetry preserved
    # Now stop activity entirely — 65 min later, evaluate_stuck flips.
    s.last_activity_at = _ts_offset_min(65)
    assert recovery.evaluate_stuck(s) == "fail"


def test_auto_recover_releases_lock_on_success(
    tmp_path: Path, monkeypatch
) -> None:
    """After a successful recovery, the per-project lock must be released
    so the next cycle's process_project can acquire it normally."""
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    p = _project(tmp_path)
    s = state.State.fresh(p.name)
    s.phase = "failed"
    s.last_activity_at = _ts_offset_min(75)
    state.write(p, s)

    assert recovery.maybe_auto_recover(p) is True
    # Now the next cycle should be able to acquire the lock.
    import locking
    again = locking.acquire_project(p)
    assert again is not None
    again.release()
