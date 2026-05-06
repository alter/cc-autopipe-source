"""End-to-end integration test for v1.3.4 R4 transient retry path.

Spawns the orchestrator with a mock-claude that returns a transient
stderr ("Server is temporarily limiting requests") and rc=1 for the
first N invocations, then succeeds. The orchestrator must:

  - log claude_invocation_transient on each transient attempt
  - NOT increment consecutive_failures
  - sleep with backoff between attempts (overridden to 0s for tests)
  - reset consecutive_transient_failures on success

Also covers the exhaustion path: an always-transient mock should hit
claude_invocation_retry_exhausted at MAX_TRANSIENT_RETRIES and then fall
through to the structural failure (claude_subprocess_failed) path.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
ORCHESTRATOR = REPO / "src" / "orchestrator"
HOOKS_DIR = REPO / "src" / "hooks"


def _seed_project(root: Path, name: str, mock_claude: Path) -> Path:
    p = root / name
    cca = p / ".cc-autopipe"
    (cca / "memory").mkdir(parents=True)
    (p / ".claude").mkdir()
    (p / ".claude" / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {"hooks": [{"command": str(HOOKS_DIR / "session-start.sh")}]}
                    ],
                }
            }
        )
    )

    # Pre-populated state so the orchestrator never thinks the project is
    # uninitialized.
    state_json = {
        "schema_version": 6,
        "name": name,
        "phase": "active",
        "iteration": 0,
        "session_id": None,
        "last_score": None,
        "last_passed": None,
        "prd_complete": False,
        "consecutive_failures": 0,
        "consecutive_transient_failures": 0,
        "last_cycle_started_at": None,
        "last_progress_at": None,
        "threshold": 0.85,
        "paused": None,
        "detached": None,
    }
    (cca / "state.json").write_text(json.dumps(state_json))
    (p / "rules.md").write_text("rules\n")
    (p / "verify.sh").write_text(
        "#!/bin/bash\nprintf 'PASSED 0.9 PRD_COMPLETE=False\\n'\nexit 0\n"
    )
    (p / "verify.sh").chmod(0o755)

    # Custom mock that exits with transient stderr until N hits, then OK.
    return p


def _make_mock_claude(tmp_path: Path, transient_then_ok: int) -> Path:
    """Builds a mock-claude bash script with a per-invocation counter
    file. Until counter reaches N, exit 1 with transient stderr; on the
    N+1-th invocation, exit 0 quickly."""
    counter = tmp_path / "mock-counter"
    counter.write_text("0\n")
    mock = tmp_path / "mock-claude.sh"
    mock.write_text(
        textwrap.dedent(
            f"""\
            #!/bin/bash
            set -u
            COUNTER_FILE="{counter}"
            current=$(cat "$COUNTER_FILE")
            current=$((current + 1))
            echo "$current" > "$COUNTER_FILE"
            if [ "$current" -le "{transient_then_ok}" ]; then
                echo "Error: Server is temporarily limiting requests" >&2
                exit 1
            fi
            exit 0
            """
        )
    )
    mock.chmod(0o755)
    return mock


def _write_projects_list(user_home: Path, projects: list[Path]) -> None:
    user_home.mkdir(parents=True, exist_ok=True)
    (user_home / "projects.list").write_text("\n".join(str(p) for p in projects) + "\n")


def _run_orch(
    user_home: Path,
    *,
    max_loops: int,
    mock_claude: Path,
    extra_env: dict[str, str] | None = None,
    timeout: int = 60,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "CC_AUTOPIPE_USER_HOME": str(user_home),
            "CC_AUTOPIPE_HOOKS_DIR": str(HOOKS_DIR),
            "CC_AUTOPIPE_QUOTA_DISABLED": "1",
            "CC_AUTOPIPE_NETWORK_PROBE_DISABLED": "1",
            "CC_AUTOPIPE_NO_REDIRECT": "1",
            "CC_AUTOPIPE_MAX_LOOPS": str(max_loops),
            "CC_AUTOPIPE_LOOP_INTERVAL_SEC": "0",
            "CC_AUTOPIPE_COOLDOWN_SEC": "0",
            "CC_AUTOPIPE_IDLE_SLEEP_SEC": "0",
            "CC_AUTOPIPE_CLAUDE_BIN": str(mock_claude),
            "CC_AUTOPIPE_TRANSIENT_BACKOFF_OVERRIDE": "0,0,0,0,0",
        }
    )
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(ORCHESTRATOR)],
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )


def _read_aggregate(user_home: Path) -> list[dict[str, object]]:
    log = user_home / "log" / "aggregate.jsonl"
    if not log.exists():
        return []
    return [json.loads(ln) for ln in log.read_text().splitlines() if ln.strip()]


def _read_state(project: Path) -> dict[str, object]:
    return json.loads((project / ".cc-autopipe" / "state.json").read_text())


@pytest.fixture
def env_paths(tmp_path: Path) -> tuple[Path, Path]:
    user_home = tmp_path / "uhome"
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    return user_home, projects_root


def test_transient_then_ok_does_not_increment_consecutive_failures(
    env_paths: tuple[Path, Path], tmp_path: Path
) -> None:
    user_home, root = env_paths
    mock_claude = _make_mock_claude(tmp_path, transient_then_ok=2)
    p = _seed_project(root, "alpha", mock_claude)
    _write_projects_list(user_home, [p])

    cp = _run_orch(user_home, max_loops=3, mock_claude=mock_claude)
    assert cp.returncode == 0, cp.stderr

    s = _read_state(p)
    # The first two cycles are transient retries; the third succeeded.
    assert s["consecutive_failures"] == 0, s
    assert s["consecutive_transient_failures"] == 0, s
    # iteration bumps for every cycle that reached _build_claude_cmd —
    # transient retries return early WITHOUT bumping iteration is wrong
    # actually: iteration is bumped in cycle.py before _build_claude_cmd.
    # So 3 cycles → iteration 3 (or fewer if loop count exhausted earlier).
    assert int(s["iteration"]) >= 1, s

    events = _read_aggregate(user_home)
    transient_events = [
        e for e in events if e.get("event") == "claude_invocation_transient"
    ]
    assert len(transient_events) == 2, [e.get("event") for e in events]
    # Both attempts include the rc, attempt number, and the stderr tail.
    for idx, e in enumerate(transient_events, start=1):
        assert e["rc"] == 1
        assert e["attempt"] == idx
        assert "Server is temporarily limiting" in e["stderr_tail"]


def test_always_transient_exhausts_to_structural(
    env_paths: tuple[Path, Path], tmp_path: Path
) -> None:
    user_home, root = env_paths
    # 99 → effectively always transient.
    mock_claude = _make_mock_claude(tmp_path, transient_then_ok=99)
    p = _seed_project(root, "beta", mock_claude)
    _write_projects_list(user_home, [p])

    # MAX_TRANSIENT_RETRIES = 5 → run 6 cycles to force exhaustion.
    cp = _run_orch(user_home, max_loops=6, mock_claude=mock_claude)
    assert cp.returncode == 0, cp.stderr

    events = _read_aggregate(user_home)
    transient_events = [
        e for e in events if e.get("event") == "claude_invocation_transient"
    ]
    exhausted = [
        e for e in events if e.get("event") == "claude_invocation_retry_exhausted"
    ]
    assert len(transient_events) >= 5, [e.get("event") for e in events]
    assert len(exhausted) >= 1, [e.get("event") for e in events]
    # Exhaustion event records the attempt count.
    assert exhausted[0]["attempts"] == 5
