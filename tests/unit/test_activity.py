"""Unit tests for src/lib/activity.py.

Covers PROMPT_v1.3-FULL.md GROUP B1.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_LIB = REPO_ROOT / "src" / "lib"
sys.path.insert(0, str(SRC_LIB))

import activity  # noqa: E402


def _project_with_data(tmp_path: Path) -> Path:
    p = tmp_path / "project_a"
    for d in ("data/models", "data/backtest", "data/debug"):
        (p / d).mkdir(parents=True, exist_ok=True)
    return p


def test_no_signals_inactive(tmp_path: Path) -> None:
    p = _project_with_data(tmp_path)
    out = activity.detect_activity(p, "project_a")
    assert out["is_active"] is False
    assert out["has_running_processes"] is False
    assert out["recent_artifact_changes"] == []
    assert out["stage_changed"] is False


def test_recent_artifact_change_marks_active(tmp_path: Path) -> None:
    p = _project_with_data(tmp_path)
    artifact = p / "data/models/exp_a/checkpoint_epoch_1.pt"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(b"x")
    out = activity.detect_activity(p, "project_a", since_seconds=60)
    assert out["is_active"] is True
    assert any("checkpoint_epoch_1" in s for s in out["recent_artifact_changes"])


def test_old_artifact_does_not_mark_active(tmp_path: Path) -> None:
    p = _project_with_data(tmp_path)
    artifact = p / "data/debug/old.md"
    artifact.write_text("x")
    # Force mtime to 2 hours ago.
    old = time.time() - 7200
    os.utime(artifact, (old, old))
    out = activity.detect_activity(p, "project_a", since_seconds=1800)
    assert out["is_active"] is False
    assert out["recent_artifact_changes"] == []


def test_stage_change_marks_active(tmp_path: Path) -> None:
    p = _project_with_data(tmp_path)
    out = activity.detect_activity(
        p,
        "project_a",
        last_observed_stage="stage_a",
        current_stage="stage_b",
    )
    assert out["stage_changed"] is True
    assert out["is_active"] is True


def test_stage_unchanged_alone_inactive(tmp_path: Path) -> None:
    p = _project_with_data(tmp_path)
    out = activity.detect_activity(
        p,
        "project_a",
        last_observed_stage="stage_a",
        current_stage="stage_a",
    )
    assert out["stage_changed"] is False


def test_initial_stage_observation_no_change(tmp_path: Path) -> None:
    """First observation (last_observed_stage=None) should NOT count
    as a change — otherwise every fresh project starts 'active' on
    bootstrap and we lose the signal."""
    p = _project_with_data(tmp_path)
    out = activity.detect_activity(
        p,
        "project_a",
        last_observed_stage=None,
        current_stage="stage_a",
    )
    assert out["stage_changed"] is False


def test_extra_dirs_walked(tmp_path: Path) -> None:
    p = _project_with_data(tmp_path)
    extra = p / "custom_data"
    extra.mkdir()
    f = extra / "fresh.txt"
    f.write_text("x")
    out = activity.detect_activity(
        p, "project_a", since_seconds=60, extra_dirs=["custom_data"]
    )
    assert out["is_active"] is True
    assert any("fresh.txt" in s for s in out["recent_artifact_changes"])


def test_running_process_marks_active(tmp_path: Path, monkeypatch) -> None:
    p = _project_with_data(tmp_path)

    # Stub _scan_processes to return a fake PID for project_a.
    def fake_scan(name, _path):
        if name == "project_a":
            return [12345]
        return []

    monkeypatch.setattr(activity, "_scan_processes", fake_scan)
    out = activity.detect_activity(p, "project_a")
    assert out["has_running_processes"] is True
    assert out["process_pids"] == [12345]
    assert out["is_active"] is True
