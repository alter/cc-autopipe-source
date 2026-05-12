"""Integration tests for v1.5.2 CYCLE-END-ON-SIGTERM.

`_flush_in_flight_cycles` is invoked from the SIGTERM/SIGINT handler.
It must:
  - emit a synthetic `cycle_end iteration=N rc=interrupted phase=<current>
    score=null interrupted_by=sigterm` event for any project whose
    progress.jsonl ends with an unmatched cycle_start
  - NOT emit when the last cycle_start was already closed by a cycle_end
  - NOT raise on missing / corrupt progress.jsonl or unreadable state

These tests call the function directly rather than going through
`signal.raise_signal(SIGTERM)` so we can assert per-call effects without
the test process actually exiting.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
LIB = SRC / "lib"
for _p in (str(SRC), str(LIB)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import state  # noqa: E402

main_mod = importlib.import_module("orchestrator.main")


def _bootstrap_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str = "demo"
) -> tuple[Path, Path]:
    """Create user_home + project_dir + projects.list entry. Returns
    (user_home, project_path).
    """
    user_home = tmp_path / "uhome"
    user_home.mkdir(parents=True)
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))

    project = tmp_path / name
    (project / ".cc-autopipe" / "memory").mkdir(parents=True)

    (user_home / "projects.list").write_text(f"{project}\n", encoding="utf-8")
    return user_home, project


def _read_aggregate(user_home: Path) -> list[dict]:
    p = user_home / "log" / "aggregate.jsonl"
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]


def _read_progress(project: Path) -> list[dict]:
    p = project / ".cc-autopipe" / "memory" / "progress.jsonl"
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]


def test_flush_emits_cycle_end_for_in_flight_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """progress.jsonl ends with cycle_start (no matching cycle_end) →
    synthetic cycle_end rc=interrupted emitted."""
    user_home, project = _bootstrap_project(tmp_path, monkeypatch)

    s = state.State.fresh(project.name)
    s.iteration = 168
    s.phase = "active"
    state.write(project, s)
    state.log_event(project, "cycle_start", iteration=168)

    main_mod._flush_in_flight_cycles(user_home)

    events = _read_progress(project)
    cycle_ends = [e for e in events if e["event"] == "cycle_end"]
    assert len(cycle_ends) == 1
    assert cycle_ends[0]["iteration"] == 168
    assert cycle_ends[0]["phase"] == "active"
    assert cycle_ends[0]["rc"] == "interrupted"
    assert cycle_ends[0]["score"] is None
    assert cycle_ends[0]["interrupted_by"] == "sigterm"

    agg = _read_aggregate(user_home)
    agg_ends = [e for e in agg if e["event"] == "cycle_end"]
    assert len(agg_ends) == 1
    assert agg_ends[0]["project"] == project.name
    assert agg_ends[0]["rc"] == "interrupted"


def test_flush_skips_when_last_event_is_cycle_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """progress.jsonl with cycle_start + cycle_end balanced → no flush
    event written (don't double-emit)."""
    user_home, project = _bootstrap_project(tmp_path, monkeypatch)

    s = state.State.fresh(project.name)
    s.iteration = 5
    state.write(project, s)
    state.log_event(project, "cycle_start", iteration=5)
    state.log_event(project, "cycle_end", iteration=5, rc=0, phase="active")

    before = len(_read_progress(project))
    main_mod._flush_in_flight_cycles(user_home)
    after = len(_read_progress(project))

    assert after == before, "flush must not append for idle projects"


def test_flush_safe_on_missing_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No progress.jsonl at all → flush returns cleanly, no exception,
    no event."""
    user_home, project = _bootstrap_project(tmp_path, monkeypatch)
    # Note: bootstrap creates .cc-autopipe/memory/ but no progress.jsonl

    main_mod._flush_in_flight_cycles(user_home)

    assert _read_progress(project) == []


def test_flush_safe_on_corrupt_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Garbage / mixed lines in progress.jsonl → flush parses what it
    can, never raises, and treats unparseable lines as absent."""
    user_home, project = _bootstrap_project(tmp_path, monkeypatch)

    s = state.State.fresh(project.name)
    s.iteration = 42
    state.write(project, s)
    progress = project / ".cc-autopipe" / "memory" / "progress.jsonl"
    progress.write_text(
        "{not even json\n"
        + json.dumps({"ts": "2026-05-12T00:00:00Z", "event": "cycle_start",
                      "iteration": 42}) + "\n"
        + "another garbage line\n",
        encoding="utf-8",
    )

    main_mod._flush_in_flight_cycles(user_home)

    # Re-read tolerant of pre-existing garbage lines we wrote above.
    parsed: list[dict] = []
    for ln in progress.read_text().splitlines():
        try:
            parsed.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    cycle_ends = [e for e in parsed if e.get("event") == "cycle_end"]
    assert len(cycle_ends) == 1
    assert cycle_ends[0]["rc"] == "interrupted"
    assert cycle_ends[0]["iteration"] == 42


def test_flush_handles_empty_projects_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No projects.list → returns cleanly without raising."""
    user_home = tmp_path / "uhome"
    user_home.mkdir(parents=True)
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))

    main_mod._flush_in_flight_cycles(user_home)

    assert _read_aggregate(user_home) == []
