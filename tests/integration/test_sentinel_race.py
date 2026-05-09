"""Integration tests for v1.3.8 SENTINEL-RACE-FIX.

The v1.3.6 sentinel-arming race could leave a project's
`knowledge_update_pending` flag stuck True forever. Order observed in
production (AI-trade Phase 2 v2.0, ~10h autonomous run, 2026-05-09):

    knowledge_updated_detected           ← v1.3 detector clears pending
    task_switched
    knowledge_sentinel_armed_via_promotion  ← v1.3.6 re-arms with baseline
                                              = current_mtime (just-advanced)
    stuck_failed                          ← engine permanently stuck

Bug: v1.3.6 sentinel-arming sets `knowledge_baseline_mtime = current
knowledge.md mtime`. Future detector compares `current_mtime > baseline`
→ False if no further advance → pending stays True → recovery sweep
refuses to recover (`auto_recovery_skipped reason=knowledge_update_in_progress`).

v1.3.8 fixes:
1. Idempotent arming — skip re-arm when sentinel is already pending
   (emit `knowledge_sentinel_arm_skipped_already_armed` instead).
2. Baseline = pre-cycle mtime (via `_safe_baseline_mtime`), so a
   same-cycle Claude knowledge.md append still clears pending.
3. Detector resets `knowledge_baseline_mtime` to None on clear (already
   the v1.3 behaviour) so next arming starts fresh.

Coverage:
- pending=False + new PROMOTION fresh → arm + baseline ≤ cycle_start
- pending=True + new PROMOTION fresh → no re-arm; skip event emitted
- armed + Claude appends to knowledge.md → detector clears pending
- v1.3.6-bug-state simulation (pending=True, baseline=current_mtime):
  next arm-attempt with fresh PROMOTION → still skipped (waits for
  detector to clear, doesn't re-arm)
- pending cleared + new PROMOTION → arm with fresh baseline_mtime
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "src" / "lib"))

from orchestrator import cycle  # noqa: E402
import promotion as promotion_lib  # noqa: E402
import state  # noqa: E402
import stop_helper  # noqa: E402


def _project(tmp_path: Path) -> Path:
    p = tmp_path / "demo"
    (p / ".cc-autopipe" / "memory").mkdir(parents=True)
    (p / "data" / "debug").mkdir(parents=True)
    return p


def _write_promotion(project: Path, task_id: str, body: str) -> Path:
    p = promotion_lib.promotion_path(project, task_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def _write_knowledge(project: Path, body: str) -> Path:
    k = project / ".cc-autopipe" / "knowledge.md"
    k.parent.mkdir(parents=True, exist_ok=True)
    k.write_text(body, encoding="utf-8")
    return k


def _read_aggregate(user_home: Path) -> list[dict]:
    p = user_home / "log" / "aggregate.jsonl"
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]


def _events_named(user_home: Path, name: str) -> list[dict]:
    return [e for e in _read_aggregate(user_home) if e.get("event") == name]


def _set_cycle_start(s: state.State, seconds_ago: float = 5.0) -> None:
    """Stamp last_cycle_started_at to N seconds ago in UTC ISO form."""
    cycle_start_unix = time.time() - seconds_ago
    cycle_start_dt = datetime.fromtimestamp(cycle_start_unix, tz=timezone.utc)
    s.last_cycle_started_at = cycle_start_dt.isoformat().replace(
        "+00:00", "Z"
    )


def test_arm_sets_baseline_to_cycle_start_not_current_mtime(
    tmp_path: Path, monkeypatch
) -> None:
    """v1.3.8 fix: when arming, baseline must be ≤ cycle_start so a
    same-cycle Claude knowledge.md append clears pending. The v1.3.6 bug
    set baseline = current_mtime, causing detector to permanently fail."""
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    p = _project(tmp_path)
    # knowledge.md predates cycle_start (existed before this cycle).
    k = _write_knowledge(p, "# knowledge\n")
    knowledge_predates = time.time() - 30.0
    os.utime(k, (knowledge_predates, knowledge_predates))

    _write_promotion(
        p, "vec_long_lgbm", "## Verdict\n\n### STABLE — model ready\n"
    )

    s = state.State.fresh(p.name)
    _set_cycle_start(s, seconds_ago=5.0)
    state.write(p, s)

    armed = cycle._maybe_arm_sentinel_via_promotion(p, "vec_long_lgbm", s)
    assert armed is True

    s2 = state.read(p)
    assert s2.knowledge_update_pending is True
    # Baseline must be ≤ knowledge.md's pre-cycle mtime (which is older
    # than cycle_start). v1.3.6 bug stamped current_mtime here.
    assert s2.knowledge_baseline_mtime is not None
    assert s2.knowledge_baseline_mtime <= knowledge_predates + 0.001

    events = _events_named(user_home, "knowledge_sentinel_armed_via_promotion")
    assert len(events) == 1
    # v1.3.8 enriches the event with baseline + current mtime so race
    # diagnoses are visible in aggregate.jsonl.
    assert "baseline_mtime" in events[0]
    assert "current_mtime" in events[0]


def test_already_armed_skips_re_arm_and_logs_skip_event(
    tmp_path: Path, monkeypatch
) -> None:
    """v1.3.8 idempotency: when pending=True, all gate checks pass but
    arming is skipped. Skip event includes mtime_age + reason. Baseline
    stays put — re-stamping it is the production bug we're closing."""
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    p = _project(tmp_path)
    _write_knowledge(p, "# knowledge\n")
    _write_promotion(
        p, "vec_long_lgbm", "## Verdict\n\n### STABLE — model ready\n"
    )

    # Pre-armed state — baseline already snapshotted at an earlier time.
    s = state.State.fresh(p.name)
    s.knowledge_update_pending = True
    s.knowledge_baseline_mtime = 12345.0
    s.knowledge_pending_reason = "stage_e_verdict on vec_long_lgbm"
    _set_cycle_start(s, seconds_ago=5.0)
    state.write(p, s)

    armed = cycle._maybe_arm_sentinel_via_promotion(p, "vec_long_lgbm", s)
    assert armed is False

    s2 = state.read(p)
    assert s2.knowledge_update_pending is True
    # Baseline UNCHANGED — the whole point of idempotency.
    assert s2.knowledge_baseline_mtime == 12345.0
    assert s2.knowledge_pending_reason == "stage_e_verdict on vec_long_lgbm"

    arm_events = _events_named(
        user_home, "knowledge_sentinel_armed_via_promotion"
    )
    assert arm_events == []
    skip_events = _events_named(
        user_home, "knowledge_sentinel_arm_skipped_already_armed"
    )
    assert len(skip_events) == 1
    assert skip_events[0]["task_id"] == "vec_long_lgbm"
    assert skip_events[0]["reason"] == "promotion_mtime_fallback"
    assert skip_events[0]["promotion_mtime_age_sec"] >= 0


def test_armed_then_knowledge_appended_clears_pending_and_resets_baseline(
    tmp_path: Path, monkeypatch
) -> None:
    """End-to-end: arm with cycle_start baseline → Claude appends to
    knowledge.md → detector clears pending and resets baseline_mtime to
    None (so next arming starts fresh, not stuck on stale baseline)."""
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    p = _project(tmp_path)
    k = _write_knowledge(p, "# knowledge\n")
    pre_cycle = time.time() - 30.0
    os.utime(k, (pre_cycle, pre_cycle))

    _write_promotion(
        p, "vec_long_lgbm", "## Verdict\n\n### STABLE — ready\n"
    )

    s = state.State.fresh(p.name)
    _set_cycle_start(s, seconds_ago=5.0)
    state.write(p, s)

    assert cycle._maybe_arm_sentinel_via_promotion(
        p, "vec_long_lgbm", s
    ) is True
    assert state.read(p).knowledge_update_pending is True

    # Sync CURRENT_TASK.md so stop_helper can run its pipeline; here we
    # invoke the helper directly because we've already exercised the
    # arming arm. Append to knowledge.md and call clear-helper directly.
    k.write_text(
        "# knowledge\n\n## Architectures\n- new lesson — 2026-05-09\n",
        encoding="utf-8",
    )
    # Force mtime advance past baseline (in case the test runs faster than
    # filesystem mtime resolution).
    new_mtime = time.time() + 1.0
    os.utime(k, (new_mtime, new_mtime))

    cleared = stop_helper.maybe_clear_knowledge_update_flag(p)
    assert cleared is True

    s2 = state.read(p)
    assert s2.knowledge_update_pending is False
    # v1.3 + v1.3.8: baseline reset to None so next arming starts fresh.
    assert s2.knowledge_baseline_mtime is None
    assert s2.knowledge_pending_reason is None

    detect_events = _events_named(user_home, "knowledge_updated_detected")
    assert len(detect_events) == 1
    # v1.3.8: event includes baseline_was + current_mtime for race diagnosis.
    assert "baseline_was" in detect_events[0]
    assert "current_mtime" in detect_events[0]
    assert detect_events[0]["current_mtime"] > detect_events[0]["baseline_was"]


def test_v1_3_6_bug_state_does_not_re_arm_or_clear(
    tmp_path: Path, monkeypatch
) -> None:
    """Reproduce the production deadlock state and prove v1.3.8 doesn't
    re-arm: pending=True, baseline=current_mtime (the v1.3.6 trap). A
    fresh PROMOTION arrives in the next cycle. v1.3.8 must NOT re-arm
    (would advance baseline again — same trap); detector won't clear
    (mtime hasn't advanced past baseline). Project waits for either a
    real knowledge.md append or the recovery sweep escape hatch."""
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    p = _project(tmp_path)
    k = _write_knowledge(p, "# knowledge\n")
    current_mtime = k.stat().st_mtime

    _write_promotion(
        p, "vec_long_lgbm", "## Verdict\n\n### STABLE — ready\n"
    )

    # Reproduce the v1.3.6 bug state directly.
    s = state.State.fresh(p.name)
    s.knowledge_update_pending = True
    s.knowledge_baseline_mtime = current_mtime
    s.knowledge_pending_reason = "v1.3.6_arm on vec_long_lgbm"
    _set_cycle_start(s, seconds_ago=5.0)
    state.write(p, s)

    # New cycle attempts to arm via promotion — must skip.
    armed = cycle._maybe_arm_sentinel_via_promotion(p, "vec_long_lgbm", s)
    assert armed is False

    s2 = state.read(p)
    assert s2.knowledge_update_pending is True
    # Baseline did NOT advance to a NEW current_mtime — the whole point.
    assert s2.knowledge_baseline_mtime == current_mtime

    # Detector also can't clear (mtime hasn't moved past baseline).
    cleared = stop_helper.maybe_clear_knowledge_update_flag(p)
    assert cleared is False
    assert state.read(p).knowledge_update_pending is True

    # Skip event is emitted so an operator can grep aggregate.jsonl for
    # the deadlock signature.
    skip_events = _events_named(
        user_home, "knowledge_sentinel_arm_skipped_already_armed"
    )
    assert len(skip_events) >= 1


def test_cleared_then_new_promotion_arms_with_fresh_baseline(
    tmp_path: Path, monkeypatch
) -> None:
    """After detector clears pending (baseline reset to None), a fresh
    PROMOTION in a later cycle arms again — with a new pre-cycle
    baseline, NOT the stale prior one."""
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    p = _project(tmp_path)
    k = _write_knowledge(p, "# knowledge\n")
    pre_cycle1 = time.time() - 60.0
    os.utime(k, (pre_cycle1, pre_cycle1))

    _write_promotion(
        p, "vec_long_lgbm", "## Verdict\n\n### STABLE — ready\n"
    )

    # Cycle 1: arm.
    s = state.State.fresh(p.name)
    _set_cycle_start(s, seconds_ago=5.0)
    state.write(p, s)
    assert cycle._maybe_arm_sentinel_via_promotion(
        p, "vec_long_lgbm", s
    ) is True

    # Claude appends, detector clears.
    new_mtime = time.time() + 1.0
    k.write_text("# knowledge\n\n## Architectures\n- 1\n", encoding="utf-8")
    os.utime(k, (new_mtime, new_mtime))
    assert stop_helper.maybe_clear_knowledge_update_flag(p) is True
    s_cleared = state.read(p)
    assert s_cleared.knowledge_update_pending is False
    assert s_cleared.knowledge_baseline_mtime is None

    # Cycle 2: another PROMOTION lands. Need to refresh the file mtime
    # so the freshness window catches it again, and bump cycle_start.
    p_promo = promotion_lib.promotion_path(p, "vec_long_lgbm")
    p_promo.write_text(
        "## Verdict\n\n### STABLE — second pass\n", encoding="utf-8"
    )
    s_cleared.last_cycle_started_at = (
        datetime.fromtimestamp(time.time(), tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )
    state.write(p, s_cleared)

    armed2 = cycle._maybe_arm_sentinel_via_promotion(
        p, "vec_long_lgbm", s_cleared
    )
    assert armed2 is True
    s2 = state.read(p)
    assert s2.knowledge_update_pending is True
    # Fresh baseline (not stale None, not last cycle's value).
    assert s2.knowledge_baseline_mtime is not None
    assert s2.knowledge_baseline_mtime > 0
