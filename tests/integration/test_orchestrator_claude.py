"""Integration tests for the orchestrator + claude + hooks pipeline.

Stage C wired the orchestrator to subprocess.Popen of `claude -p`.
These tests point CC_AUTOPIPE_CLAUDE_BIN at tools/mock-claude.sh
(popen-style invocation) and verify:

- the cycle fires the four hooks in the expected order
- session_id round-trips from the mock through stop.sh into state.json
- a passing verify.sh advances state to DONE when score>=threshold
  AND prd_complete
- a failing verify three times in a row transitions to FAILED + writes
  HUMAN_NEEDED.md
- a rate_limit failure transitions to PAUSED
- wall-clock timeout kills a hung claude
- --resume <session_id> is included on the command line when state has one
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
DISPATCHER = SRC / "helpers" / "cc-autopipe"
HOOKS_DIR = SRC / "hooks"
MOCK_CLAUDE = REPO_ROOT / "tools" / "mock-claude.sh"


def _init_project(project: Path, user_home: Path) -> None:
    project.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    env = os.environ.copy()
    env["CC_AUTOPIPE_HOME"] = str(SRC)
    env["CC_AUTOPIPE_USER_HOME"] = str(user_home)
    subprocess.run(
        [str(DISPATCHER), "init", str(project)],
        check=True,
        capture_output=True,
        env=env,
    )


def _write_verify(project: Path, body: str) -> None:
    v = project / ".cc-autopipe" / "verify.sh"
    v.write_text(f"#!/bin/bash\n{body}\n")
    v.chmod(0o755)


def _read_state(project: Path) -> dict:
    return json.loads((project / ".cc-autopipe" / "state.json").read_text())


def _read_aggregate(user_home: Path) -> list[dict]:
    log = user_home / "log" / "aggregate.jsonl"
    if not log.exists():
        return []
    return [json.loads(ln) for ln in log.read_text().splitlines() if ln.strip()]


def _run_orch(
    user_home: Path,
    *,
    max_loops: int = 1,
    cycle_timeout: float = 30.0,
    cooldown: float = 0.0,
    mock_scenario: str | None = None,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["CC_AUTOPIPE_HOME"] = str(SRC)
    env["CC_AUTOPIPE_USER_HOME"] = str(user_home)
    env["CC_AUTOPIPE_COOLDOWN_SEC"] = str(cooldown)
    env["CC_AUTOPIPE_IDLE_SLEEP_SEC"] = "0"
    env["CC_AUTOPIPE_MAX_LOOPS"] = str(max_loops)
    env["CC_AUTOPIPE_CLAUDE_BIN"] = str(MOCK_CLAUDE)
    env["CC_AUTOPIPE_QUOTA_DISABLED"] = "1"
    env["CC_AUTOPIPE_HOOKS_DIR"] = str(HOOKS_DIR)
    env["CC_AUTOPIPE_CYCLE_TIMEOUT_SEC"] = str(cycle_timeout)
    if mock_scenario:
        env["CC_AUTOPIPE_MOCK_SCENARIO"] = mock_scenario
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(ORCHESTRATOR)],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )


@pytest.fixture
def env_paths(tmp_path: Path) -> tuple[Path, Path]:
    return tmp_path / "uhome", tmp_path / "project"


# ---------------------------------------------------------------------------
# Hook lifecycle
# ---------------------------------------------------------------------------


def test_full_cycle_fires_hooks_and_advances_state(
    env_paths: tuple[Path, Path],
) -> None:
    user_home, project = env_paths
    _init_project(project, user_home)
    _write_verify(
        project,
        'echo \'{"passed":true,"score":0.92,"prd_complete":false,"details":{}}\'',
    )

    cp = _run_orch(user_home, max_loops=1)
    assert cp.returncode == 0, cp.stderr

    s = _read_state(project)
    assert s["iteration"] == 1
    assert s["last_passed"] is True
    assert s["last_score"] == pytest.approx(0.92)
    assert s["session_id"] is not None  # mock supplied one through Stop hook

    events = _read_aggregate(user_home)
    types = [e.get("event") for e in events]
    assert "cycle_start" in types
    assert "hook_session_start" in types
    assert "cycle_end" in types

    # Stop hook routed correctly per §15.2: passing verify writes to
    # progress.jsonl but NOT to aggregate (only verify_malformed does).
    progress = (project / ".cc-autopipe" / "memory" / "progress.jsonl").read_text()
    assert '"event":"verify"' in progress
    aggregate = (user_home / "log" / "aggregate.jsonl").read_text()
    assert '"event":"verify_malformed"' not in aggregate


def test_session_id_round_trip_from_mock_into_state(
    env_paths: tuple[Path, Path], tmp_path: Path
) -> None:
    """Q3 verification path: capture the Stop hook's stdin via the mock's
    DUMP_INPUT facility and verify session_id is present + matches what
    landed in state.json."""
    user_home, project = env_paths
    _init_project(project, user_home)
    _write_verify(
        project,
        'echo \'{"passed":true,"score":0.9,"prd_complete":false,"details":{}}\'',
    )
    dump = tmp_path / "stop_input.json"
    cp = _run_orch(
        user_home,
        max_loops=1,
        extra_env={"CC_AUTOPIPE_MOCK_DUMP_INPUT": str(dump)},
    )
    assert cp.returncode == 0, cp.stderr

    assert dump.exists(), "Stop hook stdin was not captured"
    captured = json.loads(dump.read_text())
    assert "session_id" in captured
    assert captured["session_id"]  # non-empty
    # And the same id reached state.json via stop.sh + state.py.
    assert _read_state(project)["session_id"] == captured["session_id"]


# ---------------------------------------------------------------------------
# Phase transitions
# ---------------------------------------------------------------------------


def test_passing_verify_with_prd_complete_transitions_to_done(
    env_paths: tuple[Path, Path],
) -> None:
    user_home, project = env_paths
    _init_project(project, user_home)
    _write_verify(
        project,
        'echo \'{"passed":true,"score":0.95,"prd_complete":true,"details":{}}\'',
    )

    _run_orch(user_home, max_loops=1)

    s = _read_state(project)
    assert s["phase"] == "done"
    assert s["last_score"] == pytest.approx(0.95)
    assert s["prd_complete"] is True

    types = [e.get("event") for e in _read_aggregate(user_home)]
    assert "done" in types


def test_three_consecutive_failures_transition_to_failed(
    env_paths: tuple[Path, Path],
) -> None:
    user_home, project = env_paths
    _init_project(project, user_home)
    # Malformed verify → consecutive_failures bumps each cycle.
    _write_verify(project, "echo not json")

    _run_orch(user_home, max_loops=3)

    s = _read_state(project)
    assert s["phase"] == "failed"
    assert s["consecutive_failures"] >= 3
    assert (project / ".cc-autopipe" / "HUMAN_NEEDED.md").exists()

    types = [e.get("event") for e in _read_aggregate(user_home)]
    assert "failed" in types


def test_rate_limit_scenario_transitions_to_paused(
    env_paths: tuple[Path, Path],
) -> None:
    user_home, project = env_paths
    _init_project(project, user_home)
    _write_verify(
        project,
        'echo \'{"passed":true,"score":0.9,"prd_complete":false,"details":{}}\'',
    )

    _run_orch(user_home, max_loops=1, mock_scenario="rate-limit")

    s = _read_state(project)
    assert s["phase"] == "paused"
    assert s["paused"]["reason"] == "rate_limit"


# ---------------------------------------------------------------------------
# Subprocess management
# ---------------------------------------------------------------------------


def test_wall_clock_timeout_kills_hung_claude(env_paths: tuple[Path, Path]) -> None:
    """Set a 2s cycle timeout and tell mock-claude to sleep 10s. The
    orchestrator must kill the subprocess and emit cycle_end with rc=-1
    within roughly the timeout — not wait the full 10s."""
    user_home, project = env_paths
    _init_project(project, user_home)
    _write_verify(
        project,
        'echo \'{"passed":true,"score":0.9,"prd_complete":false,"details":{}}\'',
    )

    import time

    started = time.time()
    cp = _run_orch(
        user_home,
        max_loops=1,
        cycle_timeout=2.0,
        extra_env={"CC_AUTOPIPE_MOCK_SLEEP_SEC": "10"},
    )
    elapsed = time.time() - started

    assert cp.returncode == 0, cp.stderr
    # Should exit comfortably faster than the mock's 10s sleep.
    assert elapsed < 6.0, f"timeout enforcement too slow: {elapsed:.1f}s"

    end_events = [
        e for e in _read_aggregate(user_home) if e.get("event") == "cycle_end"
    ]
    assert end_events, "no cycle_end event"
    assert end_events[0]["rc"] == -1, end_events[0]


def test_resume_flag_present_when_state_has_session_id(
    env_paths: tuple[Path, Path],
) -> None:
    """First cycle records a session_id (via mock's Stop hook stdin).
    Second cycle should pass --resume <id> to claude. We capture that
    by checking mock-claude's stderr for the 'resuming session' line."""
    user_home, project = env_paths
    _init_project(project, user_home)
    _write_verify(
        project,
        'echo \'{"passed":true,"score":0.9,"prd_complete":false,"details":{}}\'',
    )

    cp = _run_orch(user_home, max_loops=2)
    assert cp.returncode == 0, cp.stderr

    # The mock's stderr lands in claude_stderr.last via the orchestrator's
    # _stash_stream — read it and assert "resuming session" appeared.
    stderr_last = (project / ".cc-autopipe" / "memory" / "stderr.last").read_text()
    assert "resuming session" in stderr_last

    # State should reflect a session_id (the second cycle's, since we
    # wrote it again on the second Stop).
    assert _read_state(project)["session_id"] is not None


def test_uninit_project_skipped_does_not_spawn_claude(
    tmp_path: Path,
) -> None:
    """Bare project (no .cc-autopipe/) — orchestrator skips before
    invoking claude, so the mock's stderr-trace must NOT appear in
    aggregate.jsonl."""
    user_home = tmp_path / "uhome"
    bare = tmp_path / "bare"
    bare.mkdir()
    user_home.mkdir(parents=True, exist_ok=True)
    (user_home / "projects.list").write_text(str(bare.resolve()) + "\n")

    cp = _run_orch(user_home, max_loops=1)
    assert cp.returncode == 0, cp.stderr
    assert "not initialized" in cp.stderr
    starts = [e for e in _read_aggregate(user_home) if e.get("event") == "cycle_start"]
    assert starts == []  # no cycle_start ever logged for an UNINIT project
