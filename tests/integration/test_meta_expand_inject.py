"""v1.5.6 IDLE-INJECT-EXPAND-BACKLOG: when phase=done drains to 0
open after prd_complete expiry, the engine injects a
`meta_expand_backlog_<ts>` task into backlog.md and resumes work.
Throttled per project via `state.last_meta_expand_at` so a defiant
agent that refuses to expand cannot trigger a spam loop.
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


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _project(tmp_path: Path, name: str = "demo") -> Path:
    p = tmp_path / name
    (p / ".cc-autopipe").mkdir(parents=True)
    return p


def _read_aggregate(user_home: Path) -> list[dict]:
    log = user_home / "log" / "aggregate.jsonl"
    if not log.exists():
        return []
    return [json.loads(ln) for ln in log.read_text().splitlines() if ln.strip()]


def test_inject_when_drained_after_expiry(
    tmp_path: Path, monkeypatch
) -> None:
    """phase=done + prd_complete=True (5h ago) + 0 `[ ]` + no prior
    inject → meta-task is appended to backlog, state gains
    last_meta_expand_at, meta_expand_backlog_injected event fires,
    project resumes to active on next maybe_resume_done sweep."""
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    p = _project(tmp_path)
    backlog = p / "backlog.md"
    backlog.write_text(
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

    assert recovery.maybe_resume_done(p) is True

    s2 = state.read(p)
    assert s2.phase == "active"
    assert s2.prd_complete is False
    assert s2.last_meta_expand_at is not None

    body = backlog.read_text(encoding="utf-8")
    assert "meta_expand_backlog_" in body
    # The injected task must be a real `[ ]` line the next backlog
    # scan picks up. The leading marker is exact.
    assert any(
        line.startswith("- [ ] [research] [P0] meta_expand_backlog_")
        for line in body.splitlines()
    )

    events = _read_aggregate(user_home)
    kinds = [e.get("event") for e in events]
    assert "prd_complete_expired" in kinds
    assert "meta_expand_backlog_injected" in kinds
    inj = next(e for e in events if e["event"] == "meta_expand_backlog_injected")
    assert inj["meta_task_id"].startswith("meta_expand_backlog_")
    assert inj["throttle_hours"] == recovery.META_EXPAND_THROTTLE_HOURS


def test_inject_throttled_within_window(
    tmp_path: Path, monkeypatch
) -> None:
    """A prior inject 1h ago should block a second inject — the agent
    gets one meta-task per throttle window, not one per sweep."""
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    p = _project(tmp_path)
    (p / "backlog.md").write_text(
        "# Backlog\n\n- [x] vec_old\n", encoding="utf-8"
    )
    now = datetime.now(timezone.utc)
    s = state.State.fresh(p.name)
    s.phase = "done"
    s.prd_complete = False  # already expired in a prior sweep
    s.last_cycle_ended_at = _iso(now - timedelta(hours=6))
    s.last_meta_expand_at = _iso(now - timedelta(hours=1))
    state.write(p, s)

    injected = recovery._maybe_inject_expand_backlog(p, s)
    assert injected is False

    # backlog must remain untouched
    body = (p / "backlog.md").read_text(encoding="utf-8")
    assert "meta_expand_backlog_" not in body


def test_inject_skipped_when_backlog_has_open_tasks(
    tmp_path: Path, monkeypatch
) -> None:
    """The injector should never run when the backlog still has
    actionable lines — engine resumes against the real backlog.
    `_should_resume_done` must NOT call the injector in this case."""
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    p = _project(tmp_path)
    (p / "backlog.md").write_text(
        "# Backlog\n\n"
        "- [x] vec_old\n"
        "- [ ] [implement] [P1] vec_new — operator-added\n",
        encoding="utf-8",
    )
    now = datetime.now(timezone.utc)
    s = state.State.fresh(p.name)
    s.phase = "done"
    s.prd_complete = True
    s.last_cycle_ended_at = _iso(now - timedelta(hours=5))
    state.write(p, s)

    assert recovery.maybe_resume_done(p) is True
    s2 = state.read(p)
    # Operator's `[ ]` carries the resume — no meta-task should be
    # appended on top of real work.
    assert s2.last_meta_expand_at is None
    body = (p / "backlog.md").read_text(encoding="utf-8")
    assert "meta_expand_backlog_" not in body

    events = _read_aggregate(user_home)
    assert all(
        e.get("event") != "meta_expand_backlog_injected" for e in events
    )


def test_inject_after_throttle_expires(
    tmp_path: Path, monkeypatch
) -> None:
    """A second inject 5h after the first MUST go through — the
    throttle is 4h, not permanent."""
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    p = _project(tmp_path)
    (p / "backlog.md").write_text(
        "# Backlog\n\n- [x] vec_old\n", encoding="utf-8"
    )
    now = datetime.now(timezone.utc)
    s = state.State.fresh(p.name)
    s.phase = "done"
    s.prd_complete = False
    s.last_cycle_ended_at = _iso(now - timedelta(hours=6))
    s.last_meta_expand_at = _iso(now - timedelta(hours=5))
    state.write(p, s)

    assert recovery._maybe_inject_expand_backlog(p, s) is True
    body = (p / "backlog.md").read_text(encoding="utf-8")
    assert "meta_expand_backlog_" in body
