"""v1.5.8 GATE-ALWAYS-RUNS integration tests.

The backlog write gate must catch fabricated [x] closures regardless
of `state.phase`. v1.5.7 invoked it only from the phase-done resume
path; AI-trade 2026-05-13 produced 363 closures in 3 hours while
`phase=active`, none of which the v1.5.7 gate ever audited.

These two tests prove the v1.5.8 per-tick sweep audits both
phase=active and phase=done projects.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
LIB = SRC / "lib"
for p in (str(SRC), str(LIB)):
    if p not in sys.path:
        sys.path.insert(0, p)

import state  # noqa: E402

main_mod = importlib.import_module("orchestrator.main")


def _project(tmp_path: Path, name: str = "demo") -> Path:
    p = tmp_path / name
    (p / ".cc-autopipe").mkdir(parents=True)
    (p / "data" / "debug").mkdir(parents=True)
    return p


def _user_home(tmp_path: Path) -> Path:
    home = tmp_path / "uhome"
    (home / "log").mkdir(parents=True)
    return home


def _read_aggregate(user_home: Path) -> list[dict]:
    log = user_home / "log" / "aggregate.jsonl"
    if not log.exists():
        return []
    return [json.loads(ln) for ln in log.read_text().splitlines() if ln.strip()]


def _seed_state(project: Path, phase: str) -> None:
    s = state.read(project)
    s.phase = phase
    state.write(project, s)


def _seed_unverified_closure(project: Path) -> None:
    """Snapshot says [ ], current says [x] — a NEW transition with
    neither a verify_completed event nor a PROMOTION file."""
    (project / ".cc-autopipe" / "backlog_snapshot.md").write_text(
        "- [ ] [implement] [P0] vec_phase_active — fabricated closure\n",
        encoding="utf-8",
    )
    (project / "backlog.md").write_text(
        "- [x] [implement] [P0] vec_phase_active — fabricated closure\n",
        encoding="utf-8",
    )


def test_gate_runs_for_phase_active_project(
    tmp_path: Path, monkeypatch
) -> None:
    """Project with `phase=active`, agent flipped a row to [x] without
    proof → per-tick gate sweep reverts to [ ] and emits
    `unverified_close_blocked`. Closes the v1.5.7 phase-coverage gap
    where the gate only ran during done→active transitions."""
    home = _user_home(tmp_path)
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(home))
    p = _project(tmp_path, "phase_active")
    _seed_state(p, "active")
    _seed_unverified_closure(p)

    main_mod._gate_sweep_all_projects(home, [p])

    body = (p / "backlog.md").read_text()
    assert "- [ ] [implement] [P0] vec_phase_active" in body
    assert "- [x] [implement] [P0] vec_phase_active" not in body

    events = [
        e for e in _read_aggregate(home)
        if e.get("event") == "unverified_close_blocked"
    ]
    assert len(events) == 1
    assert events[0]["task_id"] == "vec_phase_active"


def test_gate_runs_for_phase_done_project(
    tmp_path: Path, monkeypatch
) -> None:
    """Regression check: the same scenario with `phase=done` still
    reverts. The v1.5.7 done-path used to be the ONLY path that
    audited; v1.5.8 keeps it green via the per-tick sweep instead of
    the in-`_should_resume_done` call."""
    home = _user_home(tmp_path)
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(home))
    p = _project(tmp_path, "phase_done")
    _seed_state(p, "done")
    (p / ".cc-autopipe" / "backlog_snapshot.md").write_text(
        "- [ ] [implement] [P0] vec_phase_done — fabricated closure\n",
        encoding="utf-8",
    )
    (p / "backlog.md").write_text(
        "- [x] [implement] [P0] vec_phase_done — fabricated closure\n",
        encoding="utf-8",
    )

    main_mod._gate_sweep_all_projects(home, [p])

    body = (p / "backlog.md").read_text()
    assert "- [ ] [implement] [P0] vec_phase_done" in body
    events = [
        e for e in _read_aggregate(home)
        if e.get("event") == "unverified_close_blocked"
    ]
    assert len(events) == 1
    assert events[0]["task_id"] == "vec_phase_done"
