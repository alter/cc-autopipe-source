"""Integration tests for orchestrator phase-split (Stage J).

Covers SPEC-v1.md §2.3 acceptance:
- Multi-phase PRD: completing phase N (all items checked + verify passes)
  archives the phase block to backlog-archive.md, advances current_phase,
  resets session_id, logs phase_transition + TG.
- Last phase complete → project DONE.
- Single-phase PRDs (no `### Phase N:` headers) keep v0.5 prd_complete
  semantics — no archive file, no phase_transition events.
- A complete phase WITHOUT a passing verify does NOT advance.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
ORCHESTRATOR = SRC / "orchestrator"


def _seed_project(
    base: Path,
    name: str,
    *,
    prd_text: str,
    last_score: float | None = None,
    last_passed: bool | None = None,
    prd_complete: bool = False,
    current_phase: int = 1,
    phases_completed: list[int] | None = None,
    session_id: str | None = None,
) -> Path:
    p = base / name
    cca = p / ".cc-autopipe"
    (cca / "memory").mkdir(parents=True, exist_ok=True)
    (cca / "prd.md").write_text(prd_text)
    state_doc: dict[str, object] = {
        "schema_version": 2,
        "name": name,
        "phase": "active",
        "iteration": 0,
        "session_id": session_id,
        "last_score": last_score,
        "last_passed": last_passed,
        "prd_complete": prd_complete,
        "consecutive_failures": 0,
        "last_cycle_started_at": None,
        "last_progress_at": None,
        "threshold": 0.85,
        "paused": None,
        "detached": None,
        "current_phase": current_phase,
        "phases_completed": phases_completed or [],
    }
    (cca / "state.json").write_text(json.dumps(state_doc))
    return p


def _write_projects_list(user_home: Path, projects: list[Path]) -> None:
    user_home.mkdir(parents=True, exist_ok=True)
    (user_home / "projects.list").write_text(
        "\n".join(str(p.resolve()) for p in projects) + "\n"
    )


def _run_orch(
    user_home: Path,
    *,
    max_loops: int = 1,
    timeout: float = 15.0,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["CC_AUTOPIPE_HOME"] = str(SRC)
    env["CC_AUTOPIPE_USER_HOME"] = str(user_home)
    env["CC_AUTOPIPE_COOLDOWN_SEC"] = "0"
    env["CC_AUTOPIPE_IDLE_SLEEP_SEC"] = "0"
    env["CC_AUTOPIPE_MAX_LOOPS"] = str(max_loops)
    env["CC_AUTOPIPE_CLAUDE_BIN"] = "/usr/bin/true"
    env["CC_AUTOPIPE_QUOTA_DISABLED"] = "1"
    return subprocess.run(
        [sys.executable, str(ORCHESTRATOR)],
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )


def _read_state(project: Path) -> dict[str, object]:
    return json.loads((project / ".cc-autopipe" / "state.json").read_text())


def _read_aggregate(user_home: Path) -> list[dict[str, object]]:
    log = user_home / "log" / "aggregate.jsonl"
    if not log.exists():
        return []
    return [json.loads(ln) for ln in log.read_text().splitlines() if ln.strip()]


@pytest.fixture
def env_paths(tmp_path: Path) -> tuple[Path, Path]:
    user_home = tmp_path / "uhome"
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    return user_home, projects_root


# Reusable PRD fixtures.

PRD_PHASE1_DONE_PHASE2_OPEN = """# PRD: multi-phase

### Phase 1: Foundation
**Acceptance:** all items checked.

- [x] Item 1.1
- [x] Item 1.2

### Phase 2: API
- [ ] Item 2.1
- [ ] Item 2.2

### Phase 3: Frontend
- [ ] Item 3.1
"""

PRD_LAST_PHASE_COMPLETE = """# PRD: nearly done

### Phase 1: Foundation
- [x] Item 1.1

### Phase 2: API
- [x] Item 2.1
- [x] Item 2.2
"""

PRD_NO_PHASES = """# PRD: legacy

A flat backlog without phase headers.

- [ ] Item alpha
- [ ] Item bravo
"""


# ---------------------------------------------------------------------------
# Happy path: phase 1 complete + verify passes → advance to phase 2
# ---------------------------------------------------------------------------


def test_completing_phase_1_archives_and_advances_to_phase_2(
    env_paths: tuple[Path, Path],
) -> None:
    user_home, root = env_paths
    p = _seed_project(
        root,
        "alpha",
        prd_text=PRD_PHASE1_DONE_PHASE2_OPEN,
        last_score=0.95,
        last_passed=True,
        prd_complete=False,  # not used for phased PRDs
        current_phase=1,
        session_id="phase-1-session",
    )
    _write_projects_list(user_home, [p])
    cp = _run_orch(user_home, max_loops=1)
    assert cp.returncode == 0, cp.stderr

    s = _read_state(p)
    assert s["phase"] == "active"  # still active, on phase 2 now
    assert s["current_phase"] == 2
    assert s["phases_completed"] == [1]
    # Session reset on phase transition.
    assert s["session_id"] is None

    # Archive file written with phase 1 body.
    archive = p / ".cc-autopipe" / "backlog-archive.md"
    assert archive.exists()
    body = archive.read_text()
    assert "Archived Phase 1" in body
    assert "Item 1.1" in body
    assert "Item 1.2" in body

    events = _read_aggregate(user_home)
    transitions = [e for e in events if e.get("event") == "phase_transition"]
    assert len(transitions) == 1
    assert transitions[0]["completed_phase"] == 1
    assert transitions[0]["new_phase"] == 2
    assert transitions[0]["is_last_phase"] is False


def test_completing_last_phase_marks_project_done(
    env_paths: tuple[Path, Path],
) -> None:
    user_home, root = env_paths
    p = _seed_project(
        root,
        "alpha",
        prd_text=PRD_LAST_PHASE_COMPLETE,
        last_score=0.92,
        last_passed=True,
        current_phase=2,
        phases_completed=[1],
    )
    _write_projects_list(user_home, [p])
    _run_orch(user_home, max_loops=1)

    s = _read_state(p)
    assert s["phase"] == "done"
    assert s["current_phase"] == 3  # advanced past last
    assert s["phases_completed"] == [1, 2]

    events = _read_aggregate(user_home)
    transitions = [e for e in events if e.get("event") == "phase_transition"]
    done_events = [e for e in events if e.get("event") == "done"]
    assert len(transitions) == 1
    assert transitions[0]["is_last_phase"] is True
    assert any(e.get("via_phase_split") is True for e in done_events)


# ---------------------------------------------------------------------------
# Verify-required guard
# ---------------------------------------------------------------------------


def test_phase_complete_but_verify_failed_does_not_advance(
    env_paths: tuple[Path, Path],
) -> None:
    user_home, root = env_paths
    p = _seed_project(
        root,
        "alpha",
        prd_text=PRD_PHASE1_DONE_PHASE2_OPEN,
        last_score=0.50,  # below threshold
        last_passed=False,
        current_phase=1,
    )
    _write_projects_list(user_home, [p])
    _run_orch(user_home, max_loops=1)

    s = _read_state(p)
    assert s["current_phase"] == 1, "must not advance when verify failed"
    assert s["phases_completed"] == []
    assert not (p / ".cc-autopipe" / "backlog-archive.md").exists()

    events = _read_aggregate(user_home)
    assert not any(e.get("event") == "phase_transition" for e in events)


def test_phase_with_unchecked_items_does_not_advance(
    env_paths: tuple[Path, Path],
) -> None:
    user_home, root = env_paths
    prd_phase1_open = """### Phase 1: Foundation
- [x] Item 1.1
- [ ] Item 1.2

### Phase 2: API
- [ ] Item 2.1
"""
    p = _seed_project(
        root,
        "alpha",
        prd_text=prd_phase1_open,
        last_score=0.95,
        last_passed=True,
        current_phase=1,
    )
    _write_projects_list(user_home, [p])
    _run_orch(user_home, max_loops=1)

    s = _read_state(p)
    assert s["current_phase"] == 1
    assert s["phases_completed"] == []


# ---------------------------------------------------------------------------
# Backward compat: PRD without phases keeps v0.5 prd_complete semantics
# ---------------------------------------------------------------------------


def test_no_phase_prd_uses_v05_prd_complete(env_paths: tuple[Path, Path]) -> None:
    """For a PRD with no `### Phase N:` headers, the v0.5 prd_complete
    flag still drives DONE."""
    user_home, root = env_paths
    p = _seed_project(
        root,
        "alpha",
        prd_text=PRD_NO_PHASES,
        last_score=0.90,
        last_passed=True,
        prd_complete=True,  # v0.5 verify.sh would set this
        current_phase=1,
    )
    _write_projects_list(user_home, [p])
    _run_orch(user_home, max_loops=1)

    s = _read_state(p)
    assert s["phase"] == "done"
    # No archive file for legacy projects.
    assert not (p / ".cc-autopipe" / "backlog-archive.md").exists()

    events = _read_aggregate(user_home)
    assert not any(e.get("event") == "phase_transition" for e in events)


def test_no_phase_prd_without_prd_complete_stays_active(
    env_paths: tuple[Path, Path],
) -> None:
    user_home, root = env_paths
    p = _seed_project(
        root,
        "alpha",
        prd_text=PRD_NO_PHASES,
        last_score=0.90,
        last_passed=True,
        prd_complete=False,
        current_phase=1,
    )
    _write_projects_list(user_home, [p])
    _run_orch(user_home, max_loops=1)

    s = _read_state(p)
    assert s["phase"] == "active"
    assert s["iteration"] == 1


# ---------------------------------------------------------------------------
# Prompt builder shape: phased PRD focuses agent on current phase
# ---------------------------------------------------------------------------


def test_prompt_carries_current_phase_block(env_paths: tuple[Path, Path]) -> None:
    """Indirect: a project on phase 2 should have the prompt builder
    emit Phase 2 content. We assert via the cycle's claude args being
    inspectable when CC_AUTOPIPE_CLAUDE_BIN points at /bin/cat which
    echoes argv to stdout — but argv is too noisy. Instead, exercise
    the Python helper directly."""
    user_home, root = env_paths
    p = _seed_project(
        root,
        "alpha",
        prd_text=PRD_PHASE1_DONE_PHASE2_OPEN,
        current_phase=2,
        phases_completed=[1],
    )
    # v1.3: orchestrator is now a package; import the prompt submodule.
    import importlib
    import sys

    LIB = SRC / "lib"
    for path in (str(SRC), str(LIB)):
        if path not in sys.path:
            sys.path.insert(0, path)
    prompt_mod = importlib.import_module("orchestrator.prompt")
    state_mod = importlib.import_module("state")

    s_obj = state_mod.read(p)
    prompt = prompt_mod._build_prompt(p, s_obj)
    assert "Current PRD phase: **2 — API**" in prompt
    assert "## Current phase (2: API)" in prompt
    assert "Item 2.1" in prompt
    # Prior phase items should NOT appear (phase 1 is done; agent
    # focuses on phase 2 only).
    assert "Item 1.1" not in prompt
