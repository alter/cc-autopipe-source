"""Integration tests for cc-autopipe-detach knowledge gate (v1.3.3 N).

Drives the bash helper end-to-end against a seeded project to verify:
- Without a verdict recorded, detach proceeds (rc=0) regardless of
  knowledge.md state — backwards-compatible.
- With a verdict recorded and knowledge.md missing, detach exits 3.
- With a verdict recorded and knowledge.md older than verdict, exit 3.
- Once knowledge.md mtime advances past the verdict, detach succeeds
  and clears last_verdict_event_at (gate fires once per verdict).
- Liveness flags --pipeline-log + --stale-after-sec land in state.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
HELPER = SRC / "helpers" / "cc-autopipe-detach"

LIB = SRC / "lib"
sys.path.insert(0, str(LIB))
import state  # noqa: E402


def _seed(tmp_path: Path, *, with_verdict: bool = False) -> Path:
    p = tmp_path / "demo"
    (p / ".cc-autopipe" / "memory").mkdir(parents=True, exist_ok=True)
    s = state.State.fresh("demo")
    if with_verdict:
        s.last_verdict_event_at = (
            datetime.now(timezone.utc) - timedelta(seconds=60)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        s.last_verdict_task_id = "vec_test"
    state.write(p, s)
    return p


def _run_helper(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    cmd = [
        "bash",
        str(HELPER),
        "--reason",
        "test",
        "--check-cmd",
        "true",
        "--project",
        str(project),
        *args,
    ]
    env = os.environ.copy()
    env["CC_AUTOPIPE_HOME"] = str(SRC)
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


def _read_state(project: Path) -> dict:
    return json.loads((project / ".cc-autopipe" / "state.json").read_text())


def test_detach_succeeds_when_no_verdict_recorded(tmp_path: Path) -> None:
    """Backwards-compat: projects without a verdict event must detach
    cleanly even with knowledge.md absent."""
    project = _seed(tmp_path, with_verdict=False)
    cp = _run_helper(project)
    assert cp.returncode == 0, cp.stderr
    raw = _read_state(project)
    assert raw["phase"] == "detached"


def test_detach_blocked_exit_3_when_knowledge_missing(tmp_path: Path) -> None:
    project = _seed(tmp_path, with_verdict=True)
    # Ensure knowledge.md does not exist (set_detached / fresh state).
    knowledge = project / ".cc-autopipe" / "knowledge.md"
    if knowledge.exists():
        knowledge.unlink()

    cp = _run_helper(project)
    assert cp.returncode == 3, (
        f"expected exit 3, got {cp.returncode}\n"
        f"stdout: {cp.stdout}\nstderr: {cp.stderr}"
    )
    assert "BLOCKED" in cp.stderr
    # State NOT mutated — phase stays active, detached is null.
    raw = _read_state(project)
    assert raw["phase"] != "detached"
    assert raw.get("detached") is None


def test_detach_blocked_when_knowledge_older_than_verdict(
    tmp_path: Path,
) -> None:
    project = _seed(tmp_path, with_verdict=True)
    knowledge = project / ".cc-autopipe" / "knowledge.md"
    knowledge.write_text("# Project Knowledge\n", encoding="utf-8")
    # Backdate knowledge.md mtime to 1h ago (verdict is 60s ago).
    one_hour_ago = time.time() - 3600
    os.utime(knowledge, (one_hour_ago, one_hour_ago))

    cp = _run_helper(project)
    assert cp.returncode == 3, cp.stderr
    assert "older than last verdict" in cp.stderr


def test_detach_succeeds_after_knowledge_appended(tmp_path: Path) -> None:
    """After knowledge.md mtime advances past the verdict, the gate
    passes and last_verdict_event_at is cleared."""
    project = _seed(tmp_path, with_verdict=True)
    knowledge = project / ".cc-autopipe" / "knowledge.md"
    # Touch knowledge.md NOW — mtime > verdict (60s ago).
    knowledge.write_text(
        "# Project Knowledge\n\n## Entry\nLesson appended.\n",
        encoding="utf-8",
    )

    cp = _run_helper(project)
    assert cp.returncode == 0, (
        f"expected success after appending knowledge, "
        f"got rc={cp.returncode}\nstderr: {cp.stderr}"
    )
    raw = _read_state(project)
    assert raw["phase"] == "detached"
    # Verdict reset so next detach is unblocked.
    assert raw["last_verdict_event_at"] is None
    assert raw["last_verdict_task_id"] is None


def test_detach_with_liveness_flags_persists_to_state(tmp_path: Path) -> None:
    project = _seed(tmp_path, with_verdict=False)
    cp = _run_helper(
        project,
        "--pipeline-log",
        "/abs/pipeline.log",
        "--stale-after-sec",
        "900",
    )
    assert cp.returncode == 0, cp.stderr
    raw = _read_state(project)
    d = raw["detached"]
    assert d["pipeline_log_path"] == "/abs/pipeline.log"
    assert d["stale_after_sec"] == 900


def test_detach_pipeline_log_default_stale_when_only_log_provided(
    tmp_path: Path,
) -> None:
    project = _seed(tmp_path, with_verdict=False)
    cp = _run_helper(project, "--pipeline-log", "/abs/pipe.log")
    assert cp.returncode == 0, cp.stderr
    d = _read_state(project)["detached"]
    assert d["pipeline_log_path"] == "/abs/pipe.log"
    assert d["stale_after_sec"] == 1800  # default


def test_detach_rejects_stale_after_sec_without_pipeline_log(
    tmp_path: Path,
) -> None:
    project = _seed(tmp_path, with_verdict=False)
    cp = _run_helper(project, "--stale-after-sec", "600")
    assert cp.returncode == 64, cp.stderr
    assert "requires --pipeline-log" in cp.stderr


@pytest.mark.parametrize(
    "extra",
    [
        ["--pipeline-log", "/x.log"],
        ["--pipeline-log", "/x.log", "--stale-after-sec", "60"],
    ],
)
def test_liveness_flags_clear_verdict_state(tmp_path: Path, extra) -> None:
    """Successful detach (regardless of liveness flags) resets
    last_verdict_event_at after the gate passes."""
    project = _seed(tmp_path, with_verdict=True)
    knowledge = project / ".cc-autopipe" / "knowledge.md"
    knowledge.write_text("ok\n", encoding="utf-8")  # mtime > verdict

    cp = _run_helper(project, *extra)
    assert cp.returncode == 0, cp.stderr
    raw = _read_state(project)
    assert raw["last_verdict_event_at"] is None
