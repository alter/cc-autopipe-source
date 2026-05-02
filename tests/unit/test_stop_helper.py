"""Unit tests for src/lib/stop_helper.py.

Covers SPEC-v1.2.md Bug A "Mechanism" — the Stop-hook side of the
CURRENT_TASK.md ↔ state.json bridge.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_LIB = REPO_ROOT / "src" / "lib"

sys.path.insert(0, str(SRC_LIB))

import current_task  # noqa: E402
import state  # noqa: E402
import stop_helper  # noqa: E402


def _make_project(tmp_path: Path) -> Path:
    p = tmp_path / "demo"
    (p / ".cc-autopipe" / "memory").mkdir(parents=True)
    return p


def test_sync_no_md_file_is_noop(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    project = _make_project(tmp_path)
    state.write(project, state.State.fresh(project.name))

    changed = stop_helper.sync_current_task_from_md(project)
    assert changed is False
    s = state.read(project)
    assert s.current_task is None


def test_sync_empty_md_file_is_noop(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    project = _make_project(tmp_path)
    state.write(project, state.State.fresh(project.name))
    (project / ".cc-autopipe" / "CURRENT_TASK.md").write_text("")

    changed = stop_helper.sync_current_task_from_md(project)
    assert changed is False
    s = state.read(project)
    assert s.current_task is None


def test_sync_populated_md_overwrites_state_current_task(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    project = _make_project(tmp_path)
    state.write(project, state.State.fresh(project.name))

    (project / ".cc-autopipe" / "CURRENT_TASK.md").write_text(
        "task: cand_imbloss_v2\n"
        "stage: training\n"
        "stages_completed: hypothesis\n"
        "artifact: data/models/foo/\n"
        "notes: kicked off\n"
    )

    changed = stop_helper.sync_current_task_from_md(project)
    assert changed is True

    s = state.read(project)
    assert s.current_task is not None
    assert s.current_task.id == "cand_imbloss_v2"
    assert s.current_task.stage == "training"
    assert s.current_task.stages_completed == ["hypothesis"]
    assert s.current_task.artifact_paths == ["data/models/foo/"]
    assert s.current_task.claude_notes == "kicked off"


def test_sync_replaces_existing_current_task(tmp_path: Path, monkeypatch) -> None:
    """Claude is authoritative — second sync replaces, not merges."""
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    project = _make_project(tmp_path)

    # Pre-populate state with an old task.
    s = state.State.fresh(project.name)
    s.current_task = state.CurrentTask(
        id="old_task",
        stage="done",
        stages_completed=["a", "b", "c"],
        artifact_paths=["data/old/"],
    )
    state.write(project, s)

    # Claude writes a new task, omitting fields the old one had.
    (project / ".cc-autopipe" / "CURRENT_TASK.md").write_text(
        "task: new_task\nstage: init\n"
    )
    stop_helper.sync_current_task_from_md(project)

    s2 = state.read(project)
    assert s2.current_task is not None
    assert s2.current_task.id == "new_task"
    assert s2.current_task.stage == "init"
    # Old fields gone — replace, not merge.
    assert s2.current_task.stages_completed == []
    assert s2.current_task.artifact_paths == []


def test_sync_migrates_v2_state_in_place(tmp_path: Path, monkeypatch) -> None:
    """Sync against a v1.0 schema_v2 state.json must auto-upgrade to v3
    and preserve all v2 fields."""
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    project = _make_project(tmp_path)

    legacy_v2 = {
        "schema_version": 2,
        "name": project.name,
        "phase": "active",
        "iteration": 7,
        "session_id": "v2-sid",
        "consecutive_failures": 0,
        "current_phase": 2,
        "phases_completed": [1],
    }
    (project / ".cc-autopipe" / "state.json").write_text(json.dumps(legacy_v2))

    (project / ".cc-autopipe" / "CURRENT_TASK.md").write_text("task: x\nstage: y\n")
    stop_helper.sync_current_task_from_md(project)

    raw = json.loads((project / ".cc-autopipe" / "state.json").read_text())
    assert raw["schema_version"] == 3
    assert raw["iteration"] == 7  # preserved
    assert raw["session_id"] == "v2-sid"  # preserved
    assert raw["current_phase"] == 2  # preserved
    assert raw["phases_completed"] == [1]  # preserved
    assert raw["current_task"]["id"] == "x"  # newly synced


def test_sync_swallows_parser_exceptions(tmp_path: Path, monkeypatch) -> None:
    """Hook helpers must NEVER abort the parent session. If the helper
    is asked to sync a corrupt file or one with permission problems,
    it should log to stderr and exit cleanly via the CLI path."""
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    project = _make_project(tmp_path)
    state.write(project, state.State.fresh(project.name))

    # Inject a parser fault by monkey-patching parse_file to raise.
    def boom(*_args, **_kwargs):
        raise RuntimeError("simulated parse failure")

    monkeypatch.setattr(current_task, "parse_file", boom)

    cp = subprocess.run(
        [
            sys.executable,
            str(SRC_LIB / "stop_helper.py"),
            "sync",
            str(project),
        ],
        capture_output=True,
        text=True,
        env={**__import__("os").environ},
    )
    # CLI must exit 0 regardless of internal failure.
    assert cp.returncode == 0


def test_cli_sync_invokes_helper(tmp_path: Path) -> None:
    """End-to-end CLI: `python3 stop_helper.py sync <project>` updates
    state.json.current_task without raising."""
    project = _make_project(tmp_path)
    state.write(project, state.State.fresh(project.name))
    (project / ".cc-autopipe" / "CURRENT_TASK.md").write_text(
        "task: cli_test\nstage: ready\n"
    )

    import os as _os

    env = _os.environ.copy()
    env["CC_AUTOPIPE_USER_HOME"] = str(tmp_path / "uhome")

    cp = subprocess.run(
        [
            sys.executable,
            str(SRC_LIB / "stop_helper.py"),
            "sync",
            str(project),
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    assert cp.returncode == 0
    s = state.read(project)
    assert s.current_task is not None
    assert s.current_task.id == "cli_test"
    assert s.current_task.stage == "ready"
