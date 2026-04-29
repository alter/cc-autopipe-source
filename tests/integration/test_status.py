"""Integration tests for src/cli/status.py.

Covers Stage B DoD items for `cc-autopipe status`:
- displays project phases from state.json
- --json produces valid JSON
- handles edge cases gracefully (no projects, missing state, missing path)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
STATUS_PY = SRC / "cli" / "status.py"


def _run_status(
    user_home: Path, *args: str, expect_rc: int = 0
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["CC_AUTOPIPE_HOME"] = str(SRC)
    env["CC_AUTOPIPE_USER_HOME"] = str(user_home)
    cp = subprocess.run(
        [sys.executable, str(STATUS_PY), *args],
        capture_output=True,
        text=True,
        env=env,
    )
    assert cp.returncode == expect_rc, (
        f"expected rc={expect_rc}, got {cp.returncode}\n"
        f"stdout: {cp.stdout}\nstderr: {cp.stderr}"
    )
    return cp


def _seed_project(
    base: Path,
    name: str,
    *,
    phase: str = "active",
    iteration: int = 0,
    last_score: float | None = None,
    paused_resume_at: str | None = None,
) -> Path:
    p = base / name
    cca = p / ".cc-autopipe" / "memory"
    cca.mkdir(parents=True, exist_ok=True)
    state_doc: dict[str, object] = {
        "schema_version": 1,
        "name": name,
        "phase": phase,
        "iteration": iteration,
        "session_id": None,
        "last_score": last_score,
        "last_passed": None,
        "prd_complete": False,
        "consecutive_failures": 0,
        "last_cycle_started_at": None,
        "last_progress_at": None,
        "threshold": 0.85,
        "paused": (
            {"resume_at": paused_resume_at, "reason": "rate_limit_5h"}
            if paused_resume_at
            else None
        ),
    }
    (p / ".cc-autopipe" / "state.json").write_text(json.dumps(state_doc))
    return p


def _write_projects_list(user_home: Path, projects: list[Path]) -> None:
    user_home.mkdir(parents=True, exist_ok=True)
    (user_home / "projects.list").write_text(
        "\n".join(str(p.resolve()) for p in projects) + "\n"
    )


@pytest.fixture
def env_paths(tmp_path: Path) -> tuple[Path, Path]:
    """Returns (user_home, projects_root)."""
    user_home = tmp_path / "uhome"
    user_home.mkdir()
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    return user_home, projects_root


# --- happy path ----------------------------------------------------------


def test_status_no_projects(env_paths: tuple[Path, Path]) -> None:
    user_home, _ = env_paths
    cp = _run_status(user_home)
    assert "cc-autopipe v" in cp.stdout
    assert "Orchestrator: not running" in cp.stdout
    assert "No projects registered" in cp.stdout


def test_status_one_active_project(env_paths: tuple[Path, Path]) -> None:
    user_home, root = env_paths
    p = _seed_project(root, "alpha", phase="active", iteration=7, last_score=0.78)
    _write_projects_list(user_home, [p])

    cp = _run_status(user_home)
    assert "alpha" in cp.stdout
    assert "ACTIVE" in cp.stdout
    assert "7" in cp.stdout
    assert "0.78" in cp.stdout


def test_status_multi_project_mixed_phases(env_paths: tuple[Path, Path]) -> None:
    user_home, root = env_paths
    a = _seed_project(root, "alpha", phase="active", iteration=12, last_score=0.78)
    b = _seed_project(
        root,
        "bravo",
        phase="paused",
        iteration=45,
        last_score=0.65,
        paused_resume_at=(datetime.now(timezone.utc) + timedelta(minutes=18)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
    )
    c = _seed_project(root, "charlie", phase="done", iteration=78, last_score=0.96)
    _write_projects_list(user_home, [a, b, c])

    cp = _run_status(user_home)
    out = cp.stdout
    assert "alpha" in out and "ACTIVE" in out
    assert "bravo" in out and "PAUSED" in out and "resume in" in out
    assert "charlie" in out and "DONE" in out


# --- --json --------------------------------------------------------------


def test_status_json_is_valid(env_paths: tuple[Path, Path]) -> None:
    user_home, root = env_paths
    p = _seed_project(root, "alpha", phase="active", iteration=3, last_score=0.5)
    _write_projects_list(user_home, [p])

    cp = _run_status(user_home, "--json")
    doc = json.loads(cp.stdout)
    assert doc["engine_version"]
    assert doc["orchestrator"]["running"] is False
    assert doc["quota"]["available"] is False
    assert len(doc["projects"]) == 1
    proj = doc["projects"][0]
    assert proj["name"] == "alpha"
    assert proj["phase"] == "ACTIVE"
    assert proj["iteration"] == 3
    assert proj["last_score"] == 0.5


def test_status_json_no_projects_is_valid(env_paths: tuple[Path, Path]) -> None:
    user_home, _ = env_paths
    cp = _run_status(user_home, "--json")
    doc = json.loads(cp.stdout)
    assert doc["projects"] == []
    assert doc["recent_events"] == []


# --- edge cases ----------------------------------------------------------


def test_status_handles_missing_project_path(env_paths: tuple[Path, Path]) -> None:
    user_home, root = env_paths
    p = _seed_project(root, "ghost", phase="active")
    _write_projects_list(user_home, [p])
    # Now delete it so the path no longer exists.
    import shutil

    shutil.rmtree(p)
    cp = _run_status(user_home)
    assert "ghost" in cp.stdout
    assert "MISSING" in cp.stdout


def test_status_handles_uninit_project(env_paths: tuple[Path, Path]) -> None:
    user_home, root = env_paths
    bare = root / "bare"
    bare.mkdir()
    _write_projects_list(user_home, [bare])
    cp = _run_status(user_home)
    assert "bare" in cp.stdout
    assert "UNINIT" in cp.stdout


def test_status_handles_garbage_state_json(env_paths: tuple[Path, Path]) -> None:
    """Corrupted state.json must not crash status — state.read() resets fresh."""
    user_home, root = env_paths
    p = _seed_project(root, "broken", phase="active", iteration=5)
    (p / ".cc-autopipe" / "state.json").write_text("{ not valid json")
    _write_projects_list(user_home, [p])
    cp = _run_status(user_home)
    assert "broken" in cp.stdout
    assert "ACTIVE" in cp.stdout  # state.read returns fresh State.active


def test_status_recent_events_from_aggregate(env_paths: tuple[Path, Path]) -> None:
    user_home, root = env_paths
    p = _seed_project(root, "alpha", phase="active")
    _write_projects_list(user_home, [p])
    log = user_home / "log" / "aggregate.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        "\n".join(
            json.dumps(
                {
                    "ts": "2026-04-29T15:24:00Z",
                    "project": "alpha",
                    "event": "verify_failed",
                    "score": 0.78,
                }
            )
            for _ in range(3)
        )
        + "\n"
    )
    cp = _run_status(user_home, "--recent", "2")
    assert "Recent events (last 2)" in cp.stdout
    assert "verify_failed" in cp.stdout
    # JSON path also surfaces recent_events.
    cp_json = _run_status(user_home, "--json", "--recent", "2")
    doc = json.loads(cp_json.stdout)
    assert len(doc["recent_events"]) == 2
    assert doc["recent_events"][0]["event"] == "verify_failed"


def test_status_renders_quota_when_cache_present(
    env_paths: tuple[Path, Path],
) -> None:
    """Stage E quota cache populated by quota.py is consumed by status."""
    user_home, root = env_paths
    p = _seed_project(root, "alpha", phase="active")
    _write_projects_list(user_home, [p])
    cache = user_home / "quota-cache.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(
        json.dumps(
            {
                "five_hour": {
                    "utilization": 0.67,
                    "resets_at": "2026-04-29T18:30:00Z",
                },
                "seven_day": {
                    "utilization": 0.42,
                    "resets_at": "2026-05-06T00:00:00Z",
                },
            }
        )
    )

    cp = _run_status(user_home)
    assert "5h quota: 67%" in cp.stdout
    assert "7d quota: 42%" in cp.stdout

    cp_json = _run_status(user_home, "--json")
    doc = json.loads(cp_json.stdout)
    assert doc["quota"]["available"] is True
    assert doc["quota"]["five_hour_pct"] == pytest.approx(0.67)
    assert doc["quota"]["seven_day_pct"] == pytest.approx(0.42)


def test_status_skips_blank_and_comment_lines_in_projects_list(
    env_paths: tuple[Path, Path],
) -> None:
    user_home, root = env_paths
    p = _seed_project(root, "alpha", phase="active")
    (user_home).mkdir(parents=True, exist_ok=True)
    (user_home / "projects.list").write_text(
        f"# this is a comment\n\n{p.resolve()}\n   \n"
    )
    cp = _run_status(user_home)
    assert "alpha" in cp.stdout


# --- via dispatcher ------------------------------------------------------


def test_status_via_bash_dispatcher(env_paths: tuple[Path, Path]) -> None:
    user_home, root = env_paths
    p = _seed_project(root, "alpha", phase="active", iteration=2)
    _write_projects_list(user_home, [p])
    env = os.environ.copy()
    env["CC_AUTOPIPE_HOME"] = str(SRC)
    env["CC_AUTOPIPE_USER_HOME"] = str(user_home)
    cp = subprocess.run(
        [str(SRC / "helpers" / "cc-autopipe"), "status"],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    assert "alpha" in cp.stdout
