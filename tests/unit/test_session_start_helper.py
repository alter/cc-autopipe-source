"""Unit tests for src/lib/session_start_helper.py.

Covers SPEC-v1.2.md Bug A "SessionStart hook reads state.json.current_task
and injects a context block" — the formatting / null-safety / hook-contract
side. The bash hook integration is covered in
tests/unit/test_hooks/test_session_start.sh.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_LIB = REPO_ROOT / "src" / "lib"

sys.path.insert(0, str(SRC_LIB))

import session_start_helper  # noqa: E402
import state  # noqa: E402


def _make_project(tmp_path: Path) -> Path:
    p = tmp_path / "demo"
    (p / ".cc-autopipe" / "memory").mkdir(parents=True)
    return p


# ---------------------------------------------------------------------------
# Empty / null current_task path
# ---------------------------------------------------------------------------


def test_block_when_no_state_file(tmp_path: Path) -> None:
    """No state.json present → fresh state defaults → 'no current task' block."""
    project = _make_project(tmp_path)
    block = session_start_helper.build_current_task_block(project)
    assert "=== Current task ===" in block
    assert "No current task tracked" in block
    assert "CURRENT_TASK.md" in block


def test_block_when_current_task_is_none(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    project = _make_project(tmp_path)
    state.write(project, state.State.fresh(project.name))
    block = session_start_helper.build_current_task_block(project)
    assert "No current task tracked" in block


def test_block_when_id_is_empty_string(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    project = _make_project(tmp_path)
    s = state.State.fresh(project.name)
    # Empty id should be treated like None — defensive.
    s.current_task = state.CurrentTask(id="", stage="x")
    state.write(project, s)
    block = session_start_helper.build_current_task_block(project)
    assert "No current task tracked" in block


# ---------------------------------------------------------------------------
# Populated current_task path
# ---------------------------------------------------------------------------


def test_block_with_populated_current_task(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    project = _make_project(tmp_path)
    s = state.State.fresh(project.name)
    s.current_task = state.CurrentTask(
        id="cand_imbloss_v2",
        started_at="2026-05-02T18:00:00Z",
        stage="training",
        stages_completed=["hypothesis"],
        artifact_paths=["data/models/exp_cand_imbloss_v2/"],
        claude_notes="SwingLoss kicked off",
    )
    state.write(project, s)

    block = session_start_helper.build_current_task_block(project)
    assert "Task: cand_imbloss_v2" in block
    assert "Stage: training" in block
    assert "Stages completed: hypothesis" in block
    assert "data/models/exp_cand_imbloss_v2/" in block
    assert "SwingLoss kicked off" in block
    assert "Update CURRENT_TASK.md" in block


def test_block_handles_missing_started_at(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    project = _make_project(tmp_path)
    s = state.State.fresh(project.name)
    s.current_task = state.CurrentTask(id="x", stage="y")  # started_at=None
    state.write(project, s)
    block = session_start_helper.build_current_task_block(project)
    assert "Started: unknown" in block


def test_block_handles_empty_stages_and_artifacts(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    project = _make_project(tmp_path)
    s = state.State.fresh(project.name)
    s.current_task = state.CurrentTask(id="x", stage="init")
    state.write(project, s)
    block = session_start_helper.build_current_task_block(project)
    assert "Stages completed: (none)" in block
    assert "(none declared)" in block
    assert "Notes: (none)" in block


def test_block_renders_multiple_artifacts(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    project = _make_project(tmp_path)
    s = state.State.fresh(project.name)
    s.current_task = state.CurrentTask(
        id="x",
        stage="y",
        artifact_paths=["data/foo/", "data/bar/", "report.md"],
    )
    state.write(project, s)
    block = session_start_helper.build_current_task_block(project)
    for path in ("data/foo/", "data/bar/", "report.md"):
        assert path in block


# ---------------------------------------------------------------------------
# Relative time formatting
# ---------------------------------------------------------------------------


def test_relative_just_now() -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert session_start_helper._format_relative(now) == "just now"


def test_relative_minutes() -> None:
    ts = (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    assert "minute" in session_start_helper._format_relative(ts)
    assert "5" in session_start_helper._format_relative(ts)


def test_relative_hours() -> None:
    ts = (datetime.now(timezone.utc) - timedelta(hours=3)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    rel = session_start_helper._format_relative(ts)
    assert "hour" in rel
    assert "3" in rel


def test_relative_days() -> None:
    ts = (datetime.now(timezone.utc) - timedelta(days=4)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rel = session_start_helper._format_relative(ts)
    assert "day" in rel
    assert "4" in rel


def test_relative_returns_input_on_unparseable() -> None:
    # Garbage input must not raise — return-as-is is the documented contract.
    out = session_start_helper._format_relative("not a date")
    assert out == "not a date"


def test_relative_handles_empty() -> None:
    assert session_start_helper._format_relative("") == ""
    assert session_start_helper._format_relative(None) == ""


# ---------------------------------------------------------------------------
# Hook contract — never raise
# ---------------------------------------------------------------------------


def test_block_does_not_raise_on_corrupt_state(tmp_path: Path) -> None:
    """SPEC-v1.2 hook contract: helper must not abort the parent
    session even if state.json is corrupt."""
    project = _make_project(tmp_path)
    (project / ".cc-autopipe" / "state.json").write_text("{ not json")
    # Must not raise.
    block = session_start_helper.build_current_task_block(project)
    # state.read recovers a fresh State, so we get the no-task path.
    assert "=== Current task ===" in block


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_current_task_emits_block(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    s = state.State.fresh(project.name)
    s.current_task = state.CurrentTask(id="cli", stage="ready")
    state.write(project, s)
    cp = subprocess.run(
        [
            sys.executable,
            str(SRC_LIB / "session_start_helper.py"),
            "current-task",
            str(project),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert cp.returncode == 0
    assert "Task: cli" in cp.stdout


def test_cli_exits_zero_even_when_state_unreadable(tmp_path: Path) -> None:
    """Hook helper contract: CLI must exit 0 on internal failure."""
    project = _make_project(tmp_path)
    (project / ".cc-autopipe" / "state.json").write_text("{ corrupt")
    cp = subprocess.run(
        [
            sys.executable,
            str(SRC_LIB / "session_start_helper.py"),
            "current-task",
            str(project),
        ],
        capture_output=True,
        text=True,
    )
    assert cp.returncode == 0


# ---------------------------------------------------------------------------
# Integration with state migration (v2 → v3 transparently)
# ---------------------------------------------------------------------------


def test_block_works_against_v2_state_file(tmp_path: Path, monkeypatch) -> None:
    """A v1.0 schema_v2 state.json must produce a sensible block (no
    current_task field present → no-task path)."""
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    project = _make_project(tmp_path)
    legacy = {
        "schema_version": 2,
        "name": project.name,
        "phase": "active",
        "iteration": 5,
    }
    (project / ".cc-autopipe" / "state.json").write_text(json.dumps(legacy))
    block = session_start_helper.build_current_task_block(project)
    assert "No current task tracked" in block


# ---------------------------------------------------------------------------
# build_backlog_top3_block (Bug D)
# ---------------------------------------------------------------------------


def test_backlog_block_empty_when_no_backlog(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    assert session_start_helper.build_backlog_top3_block(project) == ""


def test_backlog_block_empty_when_all_done(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    (project / "backlog.md").write_text("- [x] [P0] task_done — already finished\n")
    assert session_start_helper.build_backlog_top3_block(project) == ""


def test_backlog_block_lists_top3_in_priority_order(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    (project / "backlog.md").write_text(
        "- [ ] [implement] [P1] cand_imbloss_v2 — SwingLoss\n"
        "- [ ] [implement] [P0] cand_regimemoe — iTransformer + 3 regime heads\n"
        "- [ ] [implement] [P1] cand_mamba — Mamba SSM\n"
        "- [ ] [implement] [P2] cand_explorer — exploratory\n"
    )
    block = session_start_helper.build_backlog_top3_block(project)
    assert "=== Backlog directive ===" in block
    # P0 first.
    assert block.index("cand_regimemoe") < block.index("cand_imbloss_v2")
    # P2 not in top-3 (n=3 default).
    assert "cand_explorer" not in block


def test_backlog_block_highlights_current_task(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    project = _make_project(tmp_path)
    (project / "backlog.md").write_text(
        "- [ ] [P0] cand_active — being worked on\n- [ ] [P1] cand_other — other\n"
    )
    s = state.State.fresh(project.name)
    s.current_task = state.CurrentTask(id="cand_active", stage="training")
    state.write(project, s)
    block = session_start_helper.build_backlog_top3_block(project)
    assert "CURRENT TASK (per state.json): cand_active" in block


def test_backlog_block_falls_back_to_cca_path(tmp_path: Path) -> None:
    """Some Stage I projects placed backlog.md under .cc-autopipe/.
    Helper should find it there as a fallback."""
    project = _make_project(tmp_path)
    (project / ".cc-autopipe" / "backlog.md").write_text("- [ ] [P0] task_x — desc\n")
    block = session_start_helper.build_backlog_top3_block(project)
    assert "task_x" in block


def test_backlog_block_no_current_task_message(tmp_path: Path) -> None:
    """When state.current_task is None and backlog has tasks, the
    block tells the operator the agent should pick from top-3."""
    project = _make_project(tmp_path)
    (project / "backlog.md").write_text("- [ ] [P0] task_x — desc\n")
    block = session_start_helper.build_backlog_top3_block(project)
    assert "(none — pick one of the above)" in block


def test_backlog_block_in_progress_marker_visible(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    (project / "backlog.md").write_text("- [~] [P0] task_active — in flight\n")
    block = session_start_helper.build_backlog_top3_block(project)
    assert "[~]" in block


# ---------------------------------------------------------------------------
# build_long_op_block (Bug C)
# ---------------------------------------------------------------------------


def test_long_op_block_static_content() -> None:
    block = session_start_helper.build_long_op_block()
    assert "=== Long operation guidance ===" in block
    assert "cc-autopipe-detach" in block
    assert "nohup" in block
    assert "check-every 600" in block
    assert "max-wait 14400" in block


def test_long_op_block_no_args_required() -> None:
    """Long-op block is universal — no project arg, no state read."""
    block = session_start_helper.build_long_op_block()
    assert len(block) > 200  # not empty / not stub


# ---------------------------------------------------------------------------
# build_full_block (composer)
# ---------------------------------------------------------------------------


def test_full_block_includes_all_three_when_populated(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    project = _make_project(tmp_path)
    (project / "backlog.md").write_text("- [ ] [P0] x — desc\n")
    s = state.State.fresh(project.name)
    s.current_task = state.CurrentTask(id="x", stage="setup")
    state.write(project, s)
    block = session_start_helper.build_full_block(project)
    assert "=== Current task ===" in block
    assert "=== Backlog directive ===" in block
    assert "=== Long operation guidance ===" in block


def test_full_block_omits_empty_subblocks(tmp_path: Path) -> None:
    """With no backlog and null current_task, the full block should
    still emit the no-current-task helper and long-op guidance, but
    NOT a backlog block."""
    project = _make_project(tmp_path)
    block = session_start_helper.build_full_block(project)
    assert "=== Current task ===" in block
    assert "=== Backlog directive ===" not in block
    assert "=== Long operation guidance ===" in block


def test_full_block_cli_smoke(tmp_path: Path) -> None:
    """`session_start_helper.py all <project>` runs without errors and
    emits the long-op block at minimum."""
    project = _make_project(tmp_path)
    cp = subprocess.run(
        [
            sys.executable,
            str(SRC_LIB / "session_start_helper.py"),
            "all",
            str(project),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "=== Long operation guidance ===" in cp.stdout
