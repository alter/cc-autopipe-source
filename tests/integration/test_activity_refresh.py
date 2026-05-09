"""Integration tests for v1.3.7 ACTIVITY-MTIME-BASED.

`last_activity_at` is the timestamp `evaluate_stuck` consults — it
must reflect the most recent filesystem evidence of work, not just
engine-internal state-write moments. The legacy v1.3 path bumped
`last_activity_at` from `activity_lib.detect_activity` (process scan +
fs walk capped at 5000 files); v1.3.7 adds an unconditional refresh
at cycle_end whenever `_check_in_cycle_progress` reports progress, so
even when the activity probe misses (large `data/` trees, no recent
files in scanned dirs), the timestamp tracks reality.

Real driver: AI-trade Phase 2 v2.0 paused 2026-05-07T11:51:07Z and
resumed 2026-05-09T00:00:56Z. After the resume cycle did real work,
`last_activity_at` was still 2026-05-07T01:30:16Z (46h stale), pushing
the project to a spurious `stuck_failed` on the next sweep.

Tests:
  - rc=0 cycle + 1 new PROMOTION.md → last_activity_at advances to now
  - rc=0 cycle + 0 progress → last_activity_at unchanged (only
    state-write fallback applies; this is the negative path that
    proves the refresh is gated on fs evidence)
  - paused project resumed + cycle with progress → last_activity_at
    advances past the pre-pause snapshot (the literal AI-trade bug)
"""

from __future__ import annotations

import importlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
LIB = SRC / "lib"
for p in (str(SRC), str(LIB)):
    if p not in sys.path:
        sys.path.insert(0, p)

import state  # noqa: E402

cycle_mod = importlib.import_module("orchestrator.cycle")


def _project(tmp_path: Path) -> Path:
    p = tmp_path / "proj"
    (p / ".cc-autopipe" / "memory").mkdir(parents=True, exist_ok=True)
    return p


def _bootstrap_active_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    last_activity_at: str | None,
) -> Path:
    user_home = tmp_path / "uhome"
    user_home.mkdir(parents=True)
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    monkeypatch.setenv("CC_AUTOPIPE_QUOTA_DISABLED", "1")
    monkeypatch.setenv("CC_AUTOPIPE_NETWORK_PROBE_DISABLED", "1")

    project = _project(tmp_path)
    s = state.State.fresh(project.name)
    s.phase = "active"
    s.last_activity_at = last_activity_at
    state.write(project, s)
    return project


def _read_aggregate(user_home: Path) -> list[dict]:
    p = user_home / "log" / "aggregate.jsonl"
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]


def _patch_run_claude(
    monkeypatch: pytest.MonkeyPatch, side_effect=None, rc: int = 0
):
    def _fake(project_path: Path, cmd, timeout):  # noqa: ANN001
        if side_effect is not None:
            side_effect(project_path)
        return rc, "", ""

    monkeypatch.setattr(cycle_mod, "_run_claude", _fake)


def _disable_activity_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same rationale as test_stuck_with_progress: stub the legacy
    activity probe so the v1.3.7 fs_progress refresh is the only
    surface bumping `last_activity_at`."""

    def _fake(*_a, **_kw):  # noqa: ANN001
        return {
            "has_running_processes": False,
            "recent_artifact_changes": [],
            "stage_changed": False,
            "last_artifact_mtime": None,
            "process_pids": [],
            "is_active": False,
        }

    monkeypatch.setattr(cycle_mod.activity_lib, "detect_activity", _fake)


def test_rc0_cycle_with_new_promotion_advances_activity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """rc=0 cycle that writes a PROMOTION.md → last_activity_at moves
    to now even though the engine-internal state-write path would have
    set it to last_cycle_started_at and stopped there."""
    pre_iso = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    project = _bootstrap_active_project(tmp_path, monkeypatch, pre_iso)

    def _claude_writes_promotion(p: Path) -> None:
        debug = p / "data" / "debug"
        debug.mkdir(parents=True, exist_ok=True)
        (debug / "CAND_demo_PROMOTION.md").write_text(
            "## Acceptance\n\n✅ criteria met.\n"
        )

    _disable_activity_probe(monkeypatch)
    _patch_run_claude(monkeypatch, side_effect=_claude_writes_promotion, rc=0)

    cycle_mod.process_project(project)

    s = state.read(project)
    assert s.last_activity_at != pre_iso
    parsed = datetime.strptime(
        s.last_activity_at, "%Y-%m-%dT%H:%M:%SZ"
    ).replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - parsed).total_seconds()
    assert age < 30, "last_activity_at should be within 30s of now"


def test_rc0_cycle_with_no_progress_does_not_force_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When fs_progress reports nothing, the v1.3.7 refresh is a no-op.
    The legacy state-write path still touches `last_progress_at` and
    similar fields, but `last_activity_at` should not advance from the
    fs-progress block — leaving stuck-detection accurate when nothing
    actually happened."""
    project = _bootstrap_active_project(tmp_path, monkeypatch, None)

    _disable_activity_probe(monkeypatch)
    _patch_run_claude(monkeypatch, side_effect=None, rc=0)

    cycle_mod.process_project(project)

    s = state.read(project)
    # No progress evidence → fs-progress refresh did not run. The
    # cycle-start path sets `last_progress_at` (state-write timestamp)
    # but NOT `last_activity_at`; the latter stays None.
    assert s.last_activity_at is None


def test_pause_resume_then_active_cycle_refreshes_activity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Literal AI-trade pause/resume scenario: project paused with an
    activity timestamp, resume gate flips, next active cycle does real
    work, and `last_activity_at` must advance past the pre-pause stamp.

    Without v1.3.7 ACTIVITY-MTIME-BASED, the timestamp would still
    point at the pre-pause snapshot, leaving the next sweep cycle one
    misclassification away from a spurious `stuck_failed`."""
    user_home = tmp_path / "uhome"
    user_home.mkdir(parents=True)
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    monkeypatch.setenv("CC_AUTOPIPE_QUOTA_DISABLED", "1")
    monkeypatch.setenv("CC_AUTOPIPE_NETWORK_PROBE_DISABLED", "1")

    project = _project(tmp_path)
    pre_pause_iso = (datetime.now(timezone.utc) - timedelta(hours=46)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    resume_at = (datetime.now(timezone.utc) - timedelta(seconds=1)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    s = state.State.fresh(project.name)
    s.phase = "paused"
    s.paused = state.Paused(resume_at=resume_at, reason="rate_limit_7d")
    s.last_activity_at = pre_pause_iso
    state.write(project, s)

    def _claude_writes_promotion(p: Path) -> None:
        debug = p / "data" / "debug"
        debug.mkdir(parents=True, exist_ok=True)
        (debug / "CAND_seed_var_PROMOTION.md").write_text(
            "## Acceptance\n\n✅ documented as noise floor.\n"
        )

    _disable_activity_probe(monkeypatch)
    _patch_run_claude(monkeypatch, side_effect=_claude_writes_promotion, rc=0)

    cycle_mod.process_project(project)

    s = state.read(project)
    assert s.phase == "active"
    assert s.last_activity_at != pre_pause_iso, (
        "pause/resume + active cycle with progress must NOT leave the "
        "pre-pause activity timestamp in place"
    )
    parsed = datetime.strptime(
        s.last_activity_at, "%Y-%m-%dT%H:%M:%SZ"
    ).replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - parsed).total_seconds()
    assert age < 30, "last_activity_at should be within 30s of now"
