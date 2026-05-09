"""Integration tests for v1.3.8 RECOVERY-SWEEP-SENTINEL-TIMEOUT.

The v1.3.6 sentinel-arming race could leave a `phase=failed` project
with `knowledge_update_pending=True` forever. The v1.3.2 RECOVERY-SAFE
gate then refused to recover it (`auto_recovery_skipped
reason=knowledge_update_in_progress`), spinning the recovery sweep in
an infinite skip loop every 30 minutes with no progress.

v1.3.8 adds an escape hatch: when the sentinel has been pending > 4
hours AND knowledge.md mtime hasn't advanced past baseline, the gate
returns `(True, 'sentinel_stuck_force_clear')` and `maybe_auto_recover`
clears the sentinel state before the standard phase reset.

Coverage:
- pending=True + last_activity 2h ago + mtime unchanged → still skipped
  (`auto_recovery_skipped reason=knowledge_update_in_progress`)
- pending=True + last_activity 5h ago + mtime unchanged →
  `sentinel_force_cleared` + recover (phase=active, pending=False,
  baseline_mtime=None)
- pending=True + last_activity 5h ago + mtime ADVANCED past baseline →
  not stuck (detector will clear) → reverts to standard recovery path
  via knowledge_update_in_progress skip
- pending=False + last_activity 5h ago → standard
  `_activity_older_than_1h` recovery path (unchanged from v1.3.2)
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
LIB = SRC / "lib"
for _p in (str(SRC), str(LIB)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from orchestrator import recovery  # noqa: E402
import state  # noqa: E402


def _project(tmp_path: Path, name: str = "demo") -> Path:
    p = tmp_path / name
    (p / ".cc-autopipe").mkdir(parents=True)
    return p


def _read_aggregate(user_home: Path) -> list[dict]:
    p = user_home / "log" / "aggregate.jsonl"
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]


def _events_named(user_home: Path, name: str) -> list[dict]:
    return [e for e in _read_aggregate(user_home) if e.get("event") == name]


def _iso_n_hours_ago(hours: float) -> str:
    """Match the engine's _now_iso() format (no microseconds): the
    `_parse_iso_utc` in orchestrator._runtime only accepts
    YYYY-MM-DDTHH:MM:SSZ. Microsecond precision in tests would silently
    parse to None and degrade _is_sentinel_genuinely_stuck checks."""
    dt = datetime.now(timezone.utc) - timedelta(hours=hours)
    return dt.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _failed_with_sentinel(
    project: Path,
    activity_hours_ago: float,
    baseline_mtime: float | None,
) -> state.State:
    s = state.State.fresh(project.name)
    s.phase = "failed"
    s.knowledge_update_pending = True
    s.knowledge_baseline_mtime = baseline_mtime
    s.knowledge_pending_reason = "stage_e_verdict on vec_long_lgbm"
    s.last_activity_at = _iso_n_hours_ago(activity_hours_ago)
    state.write(project, s)
    return s


def test_pending_2h_ago_still_skipped(tmp_path: Path, monkeypatch) -> None:
    """Below the 4h threshold, the v1.3.2 RECOVERY-SAFE gate still
    blocks. Standard `auto_recovery_skipped reason=knowledge_update_in_progress`
    fires; sentinel state untouched."""
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    p = _project(tmp_path)
    # knowledge.md present at a baseline that hasn't been advanced.
    k = p / ".cc-autopipe" / "knowledge.md"
    k.write_text("# k\n", encoding="utf-8")
    baseline = k.stat().st_mtime
    _failed_with_sentinel(
        p, activity_hours_ago=2.0, baseline_mtime=baseline
    )

    assert recovery.maybe_auto_recover(p) is False
    s2 = state.read(p)
    assert s2.phase == "failed"
    assert s2.knowledge_update_pending is True
    assert s2.knowledge_baseline_mtime == baseline

    skip_events = _events_named(user_home, "auto_recovery_skipped")
    assert len(skip_events) == 1
    assert skip_events[0]["reason"] == "knowledge_update_in_progress"
    assert _events_named(user_home, "sentinel_force_cleared") == []


def test_pending_5h_ago_force_cleared_and_recovered(
    tmp_path: Path, monkeypatch
) -> None:
    """Past the 4h threshold + mtime unchanged → sentinel force-cleared,
    project recovered to phase=active. Recovery event records the
    sentinel_stuck reason so operators can trace what unblocked it."""
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    p = _project(tmp_path)
    k = p / ".cc-autopipe" / "knowledge.md"
    k.write_text("# k\n", encoding="utf-8")
    baseline = k.stat().st_mtime
    _failed_with_sentinel(
        p, activity_hours_ago=5.0, baseline_mtime=baseline
    )

    assert recovery.maybe_auto_recover(p) is True
    s2 = state.read(p)
    assert s2.phase == "active"
    assert s2.knowledge_update_pending is False
    assert s2.knowledge_baseline_mtime is None
    assert s2.knowledge_pending_reason is None
    assert s2.recovery_attempts == 1

    force_events = _events_named(user_home, "sentinel_force_cleared")
    assert len(force_events) == 1
    assert force_events[0]["reason"] == "stuck_>4h_no_mtime_advance"
    assert force_events[0]["baseline_was"] == baseline
    assert (
        force_events[0]["pending_reason_was"]
        == "stage_e_verdict on vec_long_lgbm"
    )
    assert (
        force_events[0]["threshold_sec"]
        == recovery.SENTINEL_STUCK_THRESHOLD_SEC
    )

    recover_events = _events_named(user_home, "auto_recovery_attempted")
    assert len(recover_events) == 1
    assert recover_events[0]["recover_reason"] == "sentinel_stuck_force_clear"


def test_pending_5h_ago_but_mtime_advanced_falls_through_to_skip(
    tmp_path: Path, monkeypatch
) -> None:
    """Past the threshold but knowledge.md mtime is past baseline →
    detector will clear soon → not "stuck". Falls through to the v1.3.2
    `knowledge_update_in_progress` skip; sentinel is preserved so the
    real detector handles it on the next stop_helper run."""
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    p = _project(tmp_path)
    k = p / ".cc-autopipe" / "knowledge.md"
    k.write_text("# k\n", encoding="utf-8")
    # Baseline well below current_mtime — mtime "advanced past baseline".
    baseline = k.stat().st_mtime - 100.0
    _failed_with_sentinel(
        p, activity_hours_ago=5.0, baseline_mtime=baseline
    )

    assert recovery.maybe_auto_recover(p) is False
    s2 = state.read(p)
    assert s2.phase == "failed"
    assert s2.knowledge_update_pending is True
    assert s2.knowledge_baseline_mtime == baseline

    assert _events_named(user_home, "sentinel_force_cleared") == []
    skip_events = _events_named(user_home, "auto_recovery_skipped")
    assert len(skip_events) == 1
    assert skip_events[0]["reason"] == "knowledge_update_in_progress"


def test_pending_false_5h_ago_uses_standard_recovery_path(
    tmp_path: Path, monkeypatch
) -> None:
    """No sentinel pending → escape hatch doesn't fire → v1.3.2 standard
    recovery path on `_activity_older_than_1h`. Verifies v1.3.8 doesn't
    accidentally regress projects without sentinel state."""
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    p = _project(tmp_path)
    s = state.State.fresh(p.name)
    s.phase = "failed"
    s.knowledge_update_pending = False
    s.knowledge_baseline_mtime = None
    s.last_activity_at = _iso_n_hours_ago(5.0)
    state.write(p, s)

    assert recovery.maybe_auto_recover(p) is True
    s2 = state.read(p)
    assert s2.phase == "active"
    assert s2.recovery_attempts == 1

    # No force-clear event — the escape hatch wasn't needed.
    assert _events_named(user_home, "sentinel_force_cleared") == []
    recover_events = _events_named(user_home, "auto_recovery_attempted")
    assert len(recover_events) == 1
    # reason carries the empty-string fallback (= regular stale-failed).
    assert recover_events[0]["recover_reason"] == "stale_failed"


def test_is_sentinel_genuinely_stuck_pure_helper(tmp_path: Path) -> None:
    """Direct unit test for `_is_sentinel_genuinely_stuck` — exercise
    each True/False branch without going through the full recovery path."""
    p = _project(tmp_path)
    k = p / ".cc-autopipe" / "knowledge.md"
    k.write_text("# k\n", encoding="utf-8")
    baseline = k.stat().st_mtime

    # No pending → not stuck.
    s = state.State.fresh(p.name)
    s.knowledge_update_pending = False
    assert recovery._is_sentinel_genuinely_stuck(s, p) is False

    # Pending but no last_activity_at → not stuck (defensive).
    s.knowledge_update_pending = True
    s.last_activity_at = None
    assert recovery._is_sentinel_genuinely_stuck(s, p) is False

    # Pending + recent activity (1h ago) → not stuck.
    s.last_activity_at = _iso_n_hours_ago(1.0)
    s.knowledge_baseline_mtime = baseline
    assert recovery._is_sentinel_genuinely_stuck(s, p) is False

    # Pending + 5h activity + mtime unchanged → STUCK.
    s.last_activity_at = _iso_n_hours_ago(5.0)
    assert recovery._is_sentinel_genuinely_stuck(s, p) is True

    # Pending + 5h activity + mtime advanced → not stuck (detector pending).
    s.knowledge_baseline_mtime = baseline - 100.0
    assert recovery._is_sentinel_genuinely_stuck(s, p) is False
