"""v1.5.6 PRD-COMPLETE-EXPIRES: `prd_complete=True` is treated as a
soft signal that auto-clears after PRD_COMPLETE_TTL_HOURS of idle.
Multi-month research runs must not be permanently halted by an
agent's false-positive completion verdict — operator decides project
end-of-life by removing it from the registry, not by trusting the
verify hook.
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


def _project(tmp_path: Path) -> Path:
    p = tmp_path / "demo"
    (p / ".cc-autopipe").mkdir(parents=True)
    return p


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_aggregate(user_home: Path) -> list[dict]:
    log = user_home / "log" / "aggregate.jsonl"
    if not log.exists():
        return []
    return [json.loads(ln) for ln in log.read_text().splitlines() if ln.strip()]


def test_prd_complete_expires_after_ttl(
    tmp_path: Path, monkeypatch
) -> None:
    """prd_complete=True + last_cycle_ended_at=5h ago (>4h TTL) →
    expiry flips prd_complete to False, emits `prd_complete_expired`
    event, and (with 0 open backlog after demote) attempts the meta-
    task injection path. With a backlog file present it should
    inject and return (True, '')."""
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    p = _project(tmp_path)
    (p / "backlog.md").write_text(
        "# Backlog\n\n"
        "- [x] [implement] [P1] vec_old — done\n",
        encoding="utf-8",
    )
    now = datetime.now(timezone.utc)
    s = state.State.fresh(p.name)
    s.phase = "done"
    s.prd_complete = True
    s.last_cycle_ended_at = _iso(now - timedelta(hours=5))
    state.write(p, s)

    should, reason = recovery._should_resume_done(s, p)
    assert should is True, f"expected resume after expiry, got reason={reason}"

    # State on disk must show prd_complete cleared.
    s2 = state.read(p)
    assert s2.prd_complete is False

    events = _read_aggregate(user_home)
    kinds = [e.get("event") for e in events]
    assert "prd_complete_expired" in kinds
    expired = next(e for e in events if e["event"] == "prd_complete_expired")
    assert expired["ttl_hours"] == recovery.PRD_COMPLETE_TTL_HOURS
    assert expired["idle_hours"] >= 4.9


def test_prd_complete_does_not_expire_within_ttl(
    tmp_path: Path, monkeypatch
) -> None:
    """prd_complete=True + last_cycle_ended_at=2h ago (<4h TTL) →
    no expiry, no event, returns (False, 'prd_still_complete')."""
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    p = _project(tmp_path)
    (p / "backlog.md").write_text(
        "# Backlog\n\n- [x] vec_old\n", encoding="utf-8"
    )
    now = datetime.now(timezone.utc)
    s = state.State.fresh(p.name)
    s.phase = "done"
    s.prd_complete = True
    s.last_cycle_ended_at = _iso(now - timedelta(hours=2))
    state.write(p, s)

    should, reason = recovery._should_resume_done(s, p)
    assert should is False
    assert reason == "prd_still_complete"

    s2 = state.read(p)
    assert s2.prd_complete is True

    events = _read_aggregate(user_home)
    assert all(e.get("event") != "prd_complete_expired" for e in events)


def test_prd_complete_without_cycle_end_no_expiry(
    tmp_path: Path, monkeypatch
) -> None:
    """A legacy state.json with prd_complete=True but no
    last_cycle_ended_at must NOT trigger expiry (we have no idea how
    long it has actually been idle). Engine preserves v1.3.6 behaviour."""
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    p = _project(tmp_path)
    (p / "backlog.md").write_text("- [x] vec_x\n", encoding="utf-8")
    s = state.State.fresh(p.name)
    s.phase = "done"
    s.prd_complete = True
    s.last_cycle_ended_at = None
    state.write(p, s)

    should, reason = recovery._should_resume_done(s, p)
    assert should is False
    assert reason == "prd_still_complete"
    assert state.read(p).prd_complete is True
