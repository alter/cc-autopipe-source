"""Unit tests for state.py `clear-paused` CLI subcommand (v1.5.2).

Mirror of test_cli_set_paused. Three cases:
1. paused + prd_complete=False  → phase=active, paused=None, event logged
2. paused + prd_complete=True   → phase=done,   paused=None
3. not paused (no-op)           → unchanged, exit 0, message `already not paused`
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_LIB = REPO_ROOT / "src" / "lib"

sys.path.insert(0, str(SRC_LIB))

import state  # noqa: E402


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    p = tmp_path / "demo-project"
    (p / ".cc-autopipe" / "memory").mkdir(parents=True)
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / ".cc-autopipe-user"))
    return p


def _run_clear_paused(project: Path, user_home: Path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["CC_AUTOPIPE_USER_HOME"] = str(user_home)
    return subprocess.run(
        [sys.executable, str(SRC_LIB / "state.py"), "clear-paused", str(project)],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )


def _read_progress(project: Path) -> list[dict]:
    p = project / ".cc-autopipe" / "memory" / "progress.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def test_clear_paused_active_branch(project: Path, tmp_path: Path) -> None:
    """paused + prd_complete=False → phase=active, paused=None, event logged."""
    s = state.State.fresh(project.name)
    s.phase = "paused"
    s.paused = state.Paused(resume_at="2026-05-01T00:00:00Z", reason="rate_limit_5h")
    s.prd_complete = False
    state.write(project, s)

    result = _run_clear_paused(project, tmp_path / ".cc-autopipe-user")
    assert "unpaused, phase=active" in result.stdout

    s2 = state.read(project)
    assert s2.phase == "active"
    assert s2.paused is None

    events = _read_progress(project)
    assert any(e["event"] == "paused_cleared" and e.get("new_phase") == "active"
               for e in events)


def test_clear_paused_done_branch(project: Path, tmp_path: Path) -> None:
    """paused + prd_complete=True → phase=done, paused=None."""
    s = state.State.fresh(project.name)
    s.phase = "paused"
    s.paused = state.Paused(resume_at="2026-05-01T00:00:00Z", reason="rate_limit_5h")
    s.prd_complete = True
    state.write(project, s)

    result = _run_clear_paused(project, tmp_path / ".cc-autopipe-user")
    assert "unpaused, phase=done" in result.stdout

    s2 = state.read(project)
    assert s2.phase == "done"
    assert s2.paused is None


def test_clear_paused_noop_when_already_not_paused(
    project: Path, tmp_path: Path
) -> None:
    """Idempotent: clearing a non-paused project prints `already not paused`
    and writes no `paused_cleared` event."""
    s = state.State.fresh(project.name)
    s.phase = "active"
    s.paused = None
    state.write(project, s)

    result = _run_clear_paused(project, tmp_path / ".cc-autopipe-user")
    assert "already not paused" in result.stdout
    assert "phase=active" in result.stdout

    s2 = state.read(project)
    assert s2.phase == "active"
    assert s2.paused is None

    events = _read_progress(project)
    assert not any(e["event"] == "paused_cleared" for e in events)
