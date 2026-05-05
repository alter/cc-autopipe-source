"""Unit tests for v1.3.2 RECOVERY-SAFE — recovery skips projects that
are in an in-flight enforcement loop (meta_reflect / knowledge_update /
research_plan).

Pins the gate in `_should_recover` so a FAILED project stuck on a
pending flag is never blindly revived (which would wipe the flag and
leave the engine confused about why it triggered).
"""

from __future__ import annotations

import importlib
import json
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
    dt = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_aggregate(uhome: Path) -> list[dict]:
    f = uhome / "log" / "aggregate.jsonl"
    if not f.exists():
        return []
    return [json.loads(ln) for ln in f.read_text().splitlines() if ln.strip()]


def _project(tmp_path: Path) -> Path:
    p = tmp_path / "demo"
    (p / ".cc-autopipe").mkdir(parents=True)
    return p


# ---------------------------------------------------------------------------
# _should_recover (pure logic)
# ---------------------------------------------------------------------------


def test_should_recover_not_failed_phase_returns_false() -> None:
    s = state.State.fresh("p")
    s.phase = "active"
    should, reason = recovery._should_recover(s)
    assert should is False
    assert reason == "phase=active_not_failed"


def test_should_recover_paused_phase_returns_false() -> None:
    s = state.State.fresh("p")
    s.phase = "paused"
    should, reason = recovery._should_recover(s)
    assert should is False
    assert "paused" in reason


def test_should_recover_detached_phase_returns_false() -> None:
    s = state.State.fresh("p")
    s.phase = "detached"
    should, reason = recovery._should_recover(s)
    assert should is False
    assert "detached" in reason


def test_should_recover_done_phase_returns_false() -> None:
    s = state.State.fresh("p")
    s.phase = "done"
    should, reason = recovery._should_recover(s)
    assert should is False
    assert "done" in reason


def test_should_recover_meta_reflect_pending_blocks() -> None:
    s = state.State.fresh("p")
    s.phase = "failed"
    s.meta_reflect_pending = True
    s.last_activity_at = _ts_offset_min(75)  # would otherwise be eligible
    should, reason = recovery._should_recover(s)
    assert should is False
    assert reason == "meta_reflect_in_progress"


def test_should_recover_knowledge_update_pending_blocks() -> None:
    s = state.State.fresh("p")
    s.phase = "failed"
    s.knowledge_update_pending = True
    s.last_activity_at = _ts_offset_min(75)
    should, reason = recovery._should_recover(s)
    assert should is False
    assert reason == "knowledge_update_in_progress"


def test_should_recover_research_plan_required_blocks() -> None:
    s = state.State.fresh("p")
    s.phase = "failed"
    s.research_plan_required = True
    s.last_activity_at = _ts_offset_min(75)
    should, reason = recovery._should_recover(s)
    assert should is False
    assert reason == "research_plan_pending"


def test_should_recover_recent_activity_blocks() -> None:
    s = state.State.fresh("p")
    s.phase = "failed"
    s.last_activity_at = _ts_offset_min(15)
    should, reason = recovery._should_recover(s)
    assert should is False
    assert reason == "recent_activity"


def test_should_recover_no_activity_history_blocks() -> None:
    s = state.State.fresh("p")
    s.phase = "failed"
    s.last_activity_at = None
    should, reason = recovery._should_recover(s)
    assert should is False
    assert reason == "no_activity_history"


def test_should_recover_clean_failed_returns_true() -> None:
    s = state.State.fresh("p")
    s.phase = "failed"
    s.last_activity_at = _ts_offset_min(75)
    should, reason = recovery._should_recover(s)
    assert should is True
    assert reason == ""


def test_should_recover_enforcement_outranks_activity_age() -> None:
    """5h-old failure with meta_reflect_pending still blocks — enforcement
    state is more authoritative than the activity gate. The point of
    the gate is that the failure is MEANINGFUL (engine is waiting for
    Claude to write META_DECISION); time alone shouldn't override it."""
    s = state.State.fresh("p")
    s.phase = "failed"
    s.meta_reflect_pending = True
    s.last_activity_at = _ts_offset_min(300)  # 5h
    should, reason = recovery._should_recover(s)
    assert should is False
    assert reason == "meta_reflect_in_progress"


# ---------------------------------------------------------------------------
# maybe_auto_recover integration: skipped event emission
# ---------------------------------------------------------------------------


def test_skip_event_emitted_for_meta_reflect(tmp_path: Path, monkeypatch) -> None:
    uhome = tmp_path / "uhome"
    uhome.mkdir(parents=True)
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(uhome))
    p = _project(tmp_path)
    s = state.State.fresh(p.name)
    s.phase = "failed"
    s.meta_reflect_pending = True
    s.last_activity_at = _ts_offset_min(75)
    state.write(p, s)

    assert recovery.maybe_auto_recover(p) is False
    # State unchanged.
    s2 = state.read(p)
    assert s2.phase == "failed"
    assert s2.meta_reflect_pending is True
    # Event logged with reason.
    events = _read_aggregate(uhome)
    skip_events = [e for e in events if e["event"] == "auto_recovery_skipped"]
    assert len(skip_events) == 1
    assert skip_events[0]["reason"] == "meta_reflect_in_progress"


def test_skip_event_emitted_for_knowledge_update(
    tmp_path: Path, monkeypatch
) -> None:
    uhome = tmp_path / "uhome"
    uhome.mkdir(parents=True)
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(uhome))
    p = _project(tmp_path)
    s = state.State.fresh(p.name)
    s.phase = "failed"
    s.knowledge_update_pending = True
    s.last_activity_at = _ts_offset_min(75)
    state.write(p, s)

    assert recovery.maybe_auto_recover(p) is False
    events = _read_aggregate(uhome)
    skip_events = [e for e in events if e["event"] == "auto_recovery_skipped"]
    assert len(skip_events) == 1
    assert skip_events[0]["reason"] == "knowledge_update_in_progress"


def test_skip_event_emitted_for_research_plan(tmp_path: Path, monkeypatch) -> None:
    uhome = tmp_path / "uhome"
    uhome.mkdir(parents=True)
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(uhome))
    p = _project(tmp_path)
    s = state.State.fresh(p.name)
    s.phase = "failed"
    s.research_plan_required = True
    s.last_activity_at = _ts_offset_min(75)
    state.write(p, s)

    assert recovery.maybe_auto_recover(p) is False
    events = _read_aggregate(uhome)
    skip_events = [e for e in events if e["event"] == "auto_recovery_skipped"]
    assert len(skip_events) == 1
    assert skip_events[0]["reason"] == "research_plan_pending"


def test_skip_event_NOT_emitted_for_active_phase(
    tmp_path: Path, monkeypatch
) -> None:
    """phase=active is the common case — emitting auto_recovery_skipped
    for every healthy project on every 30-min sweep would flood
    aggregate.jsonl. Only failed-but-blocked projects log skips."""
    uhome = tmp_path / "uhome"
    uhome.mkdir(parents=True)
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(uhome))
    p = _project(tmp_path)
    s = state.State.fresh(p.name)
    s.phase = "active"
    state.write(p, s)

    assert recovery.maybe_auto_recover(p) is False
    events = _read_aggregate(uhome)
    skip_events = [e for e in events if e["event"] == "auto_recovery_skipped"]
    assert skip_events == []


def test_clean_failed_still_revives(tmp_path: Path, monkeypatch) -> None:
    """Regression guard: the new gate must not break the happy path."""
    uhome = tmp_path / "uhome"
    uhome.mkdir(parents=True)
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(uhome))
    p = _project(tmp_path)
    s = state.State.fresh(p.name)
    s.phase = "failed"
    s.last_activity_at = _ts_offset_min(75)
    state.write(p, s)

    assert recovery.maybe_auto_recover(p) is True
    assert state.read(p).phase == "active"
    events = _read_aggregate(uhome)
    attempted = [e for e in events if e["event"] == "auto_recovery_attempted"]
    assert len(attempted) == 1
