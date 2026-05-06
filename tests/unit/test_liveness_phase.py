"""Unit tests for orchestrator.phase._maybe_resume_on_stale_pipeline.

Group L liveness check. Tests the pure stale-detection logic against a
seeded Detached + filesystem fixture, without spinning up the full
orchestrator subprocess.

Covers:
- no-op when pipeline_log_path is None
- no-op when stale_after_sec is None
- detach_pipeline_log_missing event when log path is gone
- detach_pipeline_stale when mtime gap exceeds threshold
- no stale when mtime gap is below threshold
- clock-skew (negative age) not treated as stale; logs once
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "src" / "lib"))

# Now import phase + state.
from orchestrator import phase  # noqa: E402
import state  # noqa: E402


def _seed(tmp_path: Path) -> Path:
    p = tmp_path / "demo"
    (p / ".cc-autopipe" / "memory").mkdir(parents=True, exist_ok=True)
    return p


def _aggregate_events(user_home: Path) -> list[dict]:
    log = user_home / "log" / "aggregate.jsonl"
    if not log.exists():
        return []
    return [json.loads(ln) for ln in log.read_text().splitlines() if ln.strip()]


def _fresh_detached(
    pipeline_log_path: str | None = None,
    stale_after_sec: int | None = None,
) -> state.Detached:
    return state.Detached(
        reason="test",
        started_at="2026-05-06T10:00:00Z",
        check_cmd="false",
        check_every_sec=600,
        max_wait_sec=14400,
        last_check_at=None,
        checks_count=1,
        pipeline_log_path=pipeline_log_path,
        stale_after_sec=stale_after_sec,
    )


def test_no_op_when_pipeline_log_path_unset(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    project = _seed(tmp_path)
    s = state.State.fresh("demo")
    s.phase = "detached"
    s.detached = _fresh_detached(pipeline_log_path=None, stale_after_sec=1800)
    state.write(project, s)

    out = phase._maybe_resume_on_stale_pipeline(project, s, elapsed=120.0)
    assert out is False
    s2 = state.read(project)
    assert s2.phase == "detached"


def test_no_op_when_stale_after_sec_unset(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    project = _seed(tmp_path)
    s = state.State.fresh("demo")
    s.phase = "detached"
    s.detached = _fresh_detached(pipeline_log_path="/abs/log", stale_after_sec=None)
    state.write(project, s)

    assert phase._maybe_resume_on_stale_pipeline(project, s, elapsed=120.0) is False


def test_log_missing_emits_event_and_resumes(tmp_path: Path, monkeypatch) -> None:
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    project = _seed(tmp_path)
    s = state.State.fresh("demo")
    s.phase = "detached"
    s.detached = _fresh_detached(
        pipeline_log_path=str(tmp_path / "missing.log"),
        stale_after_sec=300,
    )
    state.write(project, s)

    assert phase._maybe_resume_on_stale_pipeline(project, s, elapsed=600.0) is True

    s2 = state.read(project)
    assert s2.phase == "active"
    assert s2.detached is None
    assert s2.last_detach_resume_reason == "pipeline_log_missing"

    events = _aggregate_events(user_home)
    missing = [e for e in events if e["event"] == "detach_pipeline_log_missing"]
    assert len(missing) == 1


def test_stale_log_resumes_with_pipeline_stale_reason(
    tmp_path: Path, monkeypatch
) -> None:
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    project = _seed(tmp_path)

    log = tmp_path / "pipe.log"
    log.write_text("started\n")
    # Backdate mtime by 1 hour.
    one_hour_ago = time.time() - 3600
    os.utime(log, (one_hour_ago, one_hour_ago))

    s = state.State.fresh("demo")
    s.phase = "detached"
    s.detached = _fresh_detached(
        pipeline_log_path=str(log),
        stale_after_sec=300,  # 5 min threshold; gap is 1h
    )
    state.write(project, s)

    assert phase._maybe_resume_on_stale_pipeline(project, s, elapsed=4000.0) is True

    s2 = state.read(project)
    assert s2.phase == "active"
    assert s2.detached is None
    assert s2.last_detach_resume_reason == "pipeline_stale"

    events = _aggregate_events(user_home)
    stale = [e for e in events if e["event"] == "detach_pipeline_stale"]
    assert len(stale) == 1
    assert stale[0]["log_age_sec"] >= 3000
    assert stale[0]["stale_threshold_sec"] == 300


def test_fresh_log_does_not_resume(tmp_path: Path, monkeypatch) -> None:
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    project = _seed(tmp_path)

    log = tmp_path / "pipe.log"
    log.write_text("hello\n")  # mtime = now

    s = state.State.fresh("demo")
    s.phase = "detached"
    s.detached = _fresh_detached(
        pipeline_log_path=str(log),
        stale_after_sec=600,  # 10 min threshold; mtime is fresh
    )
    state.write(project, s)

    assert phase._maybe_resume_on_stale_pipeline(project, s, elapsed=120.0) is False

    s2 = state.read(project)
    assert s2.phase == "detached"
    events = _aggregate_events(user_home)
    assert not [e for e in events if e["event"] == "detach_pipeline_stale"]


def test_clock_skew_logged_once_not_treated_as_stale(
    tmp_path: Path, monkeypatch
) -> None:
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    project = _seed(tmp_path)

    log = tmp_path / "pipe.log"
    log.write_text("future")
    future = time.time() + 600
    os.utime(log, (future, future))

    s = state.State.fresh("demo")
    s.phase = "detached"
    s.detached = _fresh_detached(
        pipeline_log_path=str(log),
        stale_after_sec=300,
    )
    state.write(project, s)

    assert phase._maybe_resume_on_stale_pipeline(project, s, elapsed=120.0) is False

    s2 = state.read(project)
    assert s2.phase == "detached"

    events = _aggregate_events(user_home)
    skew = [e for e in events if e["event"] == "detach_pipeline_log_clock_skew"]
    # Logged exactly once (because checks_count=1 in fixture).
    assert len(skew) == 1
