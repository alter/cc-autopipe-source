"""Unit tests for src/lib/state.py.

Covers Stage A DoD items:
- pytest tests/unit/test_state.py passes
- atomic write verified by concurrent-write test
- recovers from corrupted JSON
"""

from __future__ import annotations

import json
import multiprocessing
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_LIB = REPO_ROOT / "src" / "lib"

sys.path.insert(0, str(SRC_LIB))

import state  # noqa: E402  (imported after sys.path insertion)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A scratch project with .cc-autopipe/, isolated user home for logs."""
    p = tmp_path / "demo-project"
    (p / ".cc-autopipe" / "memory").mkdir(parents=True)
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / ".cc-autopipe-user"))
    return p


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


def test_fresh_state_when_file_missing(project: Path) -> None:
    s = state.read(project)
    assert s.phase == "active"
    assert s.iteration == 0
    assert s.session_id is None
    assert s.consecutive_failures == 0
    assert s.paused is None
    # Name is derived from project basename when fresh.
    assert s.name == project.name


def test_write_then_read_round_trip(project: Path) -> None:
    s = state.State.fresh(project.name)
    s.iteration = 7
    s.session_id = "abc-123"
    s.last_score = 0.92
    s.last_passed = True
    s.prd_complete = False
    state.write(project, s)

    raw = json.loads((project / ".cc-autopipe" / "state.json").read_text())
    assert raw["schema_version"] == state.SCHEMA_VERSION
    assert raw["iteration"] == 7
    assert raw["session_id"] == "abc-123"
    assert raw["last_score"] == pytest.approx(0.92)

    s2 = state.read(project)
    assert s2.iteration == 7
    assert s2.session_id == "abc-123"
    assert s2.last_score == pytest.approx(0.92)


def test_paused_round_trip(project: Path) -> None:
    s = state.State.fresh(project.name)
    s.phase = "paused"
    s.paused = state.Paused(resume_at="2026-04-29T18:30:00Z", reason="rate_limit_5h")
    state.write(project, s)

    s2 = state.read(project)
    assert s2.phase == "paused"
    assert s2.paused is not None
    assert s2.paused.resume_at == "2026-04-29T18:30:00Z"
    assert s2.paused.reason == "rate_limit_5h"


def test_extras_preserved(project: Path) -> None:
    """Unknown JSON keys survive a read-modify-write round trip."""
    raw = {
        "schema_version": 1,
        "name": project.name,
        "phase": "active",
        "iteration": 3,
        "future_field": "v1-only",
    }
    (project / ".cc-autopipe" / "state.json").write_text(json.dumps(raw))
    s = state.read(project)
    s.iteration = 4
    state.write(project, s)
    out = json.loads((project / ".cc-autopipe" / "state.json").read_text())
    assert out["future_field"] == "v1-only"
    assert out["iteration"] == 4


# ---------------------------------------------------------------------------
# Corruption recovery
# ---------------------------------------------------------------------------


def test_recovers_from_garbage_json(project: Path) -> None:
    sf = project / ".cc-autopipe" / "state.json"
    sf.write_text("{this is not valid json")
    s = state.read(project)
    assert s.iteration == 0
    assert s.phase == "active"
    # And subsequent write produces valid JSON.
    state.write(project, s)
    json.loads(sf.read_text())  # no exception


def test_recovers_from_truncated_json(project: Path) -> None:
    sf = project / ".cc-autopipe" / "state.json"
    sf.write_text('{"schema_version":1,"phase":"act')  # truncated mid-write
    s = state.read(project)
    assert s.phase == "active"


def test_recovers_from_empty_file(project: Path) -> None:
    sf = project / ".cc-autopipe" / "state.json"
    sf.write_text("")
    s = state.read(project)
    assert s.iteration == 0


# ---------------------------------------------------------------------------
# Atomic concurrent write
# ---------------------------------------------------------------------------


def _writer_worker(args: tuple[str, int, int]) -> None:
    project_str, worker_id, n_writes = args
    sys.path.insert(0, str(SRC_LIB))
    import state as st  # local re-import in subprocess

    for i in range(n_writes):
        s = st.State.fresh(Path(project_str).name)
        s.iteration = worker_id * 100000 + i
        s.session_id = f"w{worker_id}-i{i}"
        st.write(project_str, s)


def test_concurrent_writes_never_corrupt(project: Path) -> None:
    """Many parallel writers must leave state.json valid JSON at every read."""
    n_workers = 8
    n_writes_each = 50

    with multiprocessing.get_context("spawn").Pool(n_workers) as pool:
        pool.map(
            _writer_worker,
            [(str(project), wid, n_writes_each) for wid in range(n_workers)],
        )

    # File must be valid JSON.
    raw = (project / ".cc-autopipe" / "state.json").read_text()
    parsed = json.loads(raw)
    assert "iteration" in parsed
    assert "session_id" in parsed

    # State.read must succeed cleanly.
    s = state.read(project)
    assert isinstance(s.iteration, int)
    assert s.session_id is not None

    # No leftover .tmp.* files (the writer cleans them).
    leftovers = list((project / ".cc-autopipe").glob("state.json.tmp.*"))
    assert leftovers == [], f"leftover tmp files: {leftovers}"


def test_concurrent_reads_during_writes_dont_raise(project: Path) -> None:
    """Reader during writer storm must always return a valid State."""
    # Seed initial valid file.
    state.write(project, state.State.fresh(project.name))

    ctx = multiprocessing.get_context("spawn")
    writer = ctx.Process(target=_writer_worker, args=((str(project), 0, 200),))
    writer.start()
    try:
        for _ in range(100):
            s = state.read(project)
            assert isinstance(s.iteration, int)
            assert s.phase in {"active", "paused", "done", "failed"}
    finally:
        writer.join(timeout=30)
        assert writer.exitcode == 0


# ---------------------------------------------------------------------------
# Mutators
# ---------------------------------------------------------------------------


def test_inc_failures_monotonic(project: Path) -> None:
    state.write(project, state.State.fresh(project.name))
    assert state.inc_failures(project) == 1
    assert state.inc_failures(project) == 2
    assert state.read(project).consecutive_failures == 2


def test_update_verify_passing_resets_failures(project: Path) -> None:
    s = state.State.fresh(project.name)
    s.consecutive_failures = 2
    state.write(project, s)

    state.update_verify(project, passed=True, score=0.91, prd_complete=False)
    s2 = state.read(project)
    assert s2.consecutive_failures == 0
    assert s2.last_passed is True
    assert s2.last_score == pytest.approx(0.91)


def test_update_verify_failing_increments_failures(project: Path) -> None:
    state.write(project, state.State.fresh(project.name))
    state.update_verify(project, passed=False, score=0.4, prd_complete=False)
    state.update_verify(project, passed=False, score=0.5, prd_complete=False)
    s = state.read(project)
    assert s.consecutive_failures == 2
    assert s.last_score == pytest.approx(0.5)


def test_set_paused_records_resume_at(project: Path) -> None:
    state.write(project, state.State.fresh(project.name))
    state.set_paused(project, "2026-05-01T12:00:00Z", "rate_limit_5h")
    s = state.read(project)
    assert s.phase == "paused"
    assert s.paused is not None
    assert s.paused.resume_at == "2026-05-01T12:00:00Z"


def test_set_session_id(project: Path) -> None:
    state.write(project, state.State.fresh(project.name))
    state.set_session_id(project, "session-xyz")
    assert state.read(project).session_id == "session-xyz"


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def test_log_event_writes_progress_and_aggregate(project: Path, tmp_path: Path) -> None:
    state.log_event(project, "cycle_start", iteration=12)

    progress = (project / ".cc-autopipe" / "memory" / "progress.jsonl").read_text()
    assert '"event":"cycle_start"' in progress
    assert '"iteration":12' in progress

    aggregate = (
        Path(os.environ["CC_AUTOPIPE_USER_HOME"]) / "log" / "aggregate.jsonl"
    ).read_text()
    assert '"project":"%s"' % project.name in aggregate
    assert '"event":"cycle_start"' in aggregate


def test_log_failure(project: Path) -> None:
    state.log_failure(project, "verify_failed", score=0.6)
    failures = (project / ".cc-autopipe" / "memory" / "failures.jsonl").read_text()
    assert '"error":"verify_failed"' in failures
    assert '"score":0.6' in failures


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_read_prints_json(project: Path, tmp_path: Path) -> None:
    state.write(project, state.State.fresh(project.name))
    env = os.environ.copy()
    env["CC_AUTOPIPE_USER_HOME"] = str(tmp_path / ".cc-autopipe-user")
    result = subprocess.run(
        [sys.executable, str(SRC_LIB / "state.py"), "read", str(project)],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    parsed = json.loads(result.stdout)
    assert parsed["name"] == project.name
    assert parsed["phase"] == "active"


def test_cli_inc_failures(project: Path, tmp_path: Path) -> None:
    state.write(project, state.State.fresh(project.name))
    env = os.environ.copy()
    env["CC_AUTOPIPE_USER_HOME"] = str(tmp_path / ".cc-autopipe-user")
    subprocess.run(
        [sys.executable, str(SRC_LIB / "state.py"), "inc-failures", str(project)],
        check=True,
        env=env,
    )
    assert state.read(project).consecutive_failures == 1


def test_cli_update_verify(project: Path, tmp_path: Path) -> None:
    state.write(project, state.State.fresh(project.name))
    env = os.environ.copy()
    env["CC_AUTOPIPE_USER_HOME"] = str(tmp_path / ".cc-autopipe-user")
    subprocess.run(
        [
            sys.executable,
            str(SRC_LIB / "state.py"),
            "update-verify",
            str(project),
            "--passed",
            "true",
            "--score",
            "0.88",
            "--prd-complete",
            "false",
        ],
        check=True,
        env=env,
    )
    s = state.read(project)
    assert s.last_passed is True
    assert s.last_score == pytest.approx(0.88)
    assert s.prd_complete is False


def test_cli_set_paused(project: Path, tmp_path: Path) -> None:
    state.write(project, state.State.fresh(project.name))
    env = os.environ.copy()
    env["CC_AUTOPIPE_USER_HOME"] = str(tmp_path / ".cc-autopipe-user")
    subprocess.run(
        [
            sys.executable,
            str(SRC_LIB / "state.py"),
            "set-paused",
            str(project),
            "2026-05-01T00:00:00Z",
            "rate_limit_5h",
        ],
        check=True,
        env=env,
    )
    s = state.read(project)
    assert s.phase == "paused"
    assert s.paused is not None
    assert s.paused.reason == "rate_limit_5h"


# ---------------------------------------------------------------------------
# v1.0 schema additions (SPEC-v1.md §3.1)
# ---------------------------------------------------------------------------


def test_schema_version_is_current_for_fresh_state(project: Path) -> None:
    state.write(project, state.State.fresh(project.name))
    raw = json.loads((project / ".cc-autopipe" / "state.json").read_text())
    assert raw["schema_version"] == state.SCHEMA_VERSION
    # v1.0 fields still present.
    assert raw["detached"] is None
    assert raw["current_phase"] == 1
    assert raw["phases_completed"] == []
    assert raw["escalated_next_cycle"] is False
    assert raw["successful_cycles_since_improver"] == 0
    assert raw["improver_due"] is False
    # v1.2 fields present with defaults.
    assert raw["current_task"] is None
    assert raw["last_in_progress"] is False
    assert raw["consecutive_in_progress"] == 0


def test_v1_state_file_migrates_to_current_on_read(project: Path) -> None:
    """A pre-v1.0 state.json (schema_version=1) should read cleanly with
    defaults for the missing fields, then persist as schema_version=3."""
    legacy = {
        "schema_version": 1,
        "name": project.name,
        "phase": "active",
        "iteration": 4,
        "session_id": "legacy-sid",
        "last_score": 0.71,
        "last_passed": False,
        "prd_complete": False,
        "consecutive_failures": 1,
        "last_cycle_started_at": "2026-04-29T10:00:00Z",
        "last_progress_at": "2026-04-29T10:00:00Z",
        "threshold": 0.85,
        "paused": None,
    }
    (project / ".cc-autopipe" / "state.json").write_text(json.dumps(legacy))

    s = state.read(project)
    # v1 fields preserved.
    assert s.iteration == 4
    assert s.session_id == "legacy-sid"
    # v1.0 fields filled with defaults.
    assert s.detached is None
    assert s.current_phase == 1
    assert s.phases_completed == []
    assert s.escalated_next_cycle is False
    # schema_version forced to current on read so write() persists v3.
    assert s.schema_version == state.SCHEMA_VERSION

    state.write(project, s)
    raw = json.loads((project / ".cc-autopipe" / "state.json").read_text())
    assert raw["schema_version"] == state.SCHEMA_VERSION
    assert "detached" in raw
    assert "current_phase" in raw
    assert "phases_completed" in raw
    assert "escalated_next_cycle" in raw
    # v1.2 fields filled with defaults on migration.
    assert raw["current_task"] is None
    assert raw["last_in_progress"] is False
    assert raw["consecutive_in_progress"] == 0


def test_set_detached_round_trip(project: Path) -> None:
    state.write(project, state.State.fresh(project.name))
    state.set_detached(
        project,
        reason="training model",
        check_cmd="ls models/checkpoint_*.pt | wc -l | grep -q '^[1-9]'",
        check_every_sec=600,
        max_wait_sec=14400,
    )
    s = state.read(project)
    assert s.phase == "detached"
    assert s.detached is not None
    assert s.detached.reason == "training model"
    assert s.detached.check_every_sec == 600
    assert s.detached.max_wait_sec == 14400
    assert s.detached.checks_count == 0
    assert s.detached.last_check_at is None
    assert s.detached.started_at  # non-empty ISO timestamp


def test_detached_round_trip_via_to_from_dict(project: Path) -> None:
    s = state.State.fresh(project.name)
    s.phase = "detached"
    s.detached = state.Detached(
        reason="r",
        started_at="2026-05-15T10:00:00Z",
        check_cmd="true",
        check_every_sec=60,
        max_wait_sec=3600,
        last_check_at="2026-05-15T10:05:00Z",
        checks_count=3,
    )
    state.write(project, s)
    s2 = state.read(project)
    assert s2.detached is not None
    assert s2.detached.checks_count == 3
    assert s2.detached.last_check_at == "2026-05-15T10:05:00Z"


def test_complete_phase_advances_and_resets_session(project: Path) -> None:
    s = state.State.fresh(project.name)
    s.session_id = "phase-1-sid"
    s.current_phase = 1
    state.write(project, s)

    new = state.complete_phase(project)
    assert new == 2
    s2 = state.read(project)
    assert s2.current_phase == 2
    assert s2.phases_completed == [1]
    assert s2.session_id is None  # session reset for fresh phase context


def test_complete_phase_idempotent_within_phase(project: Path) -> None:
    """Calling complete_phase twice without entering a new phase
    increments only once into phases_completed (no duplicates)."""
    s = state.State.fresh(project.name)
    s.current_phase = 2
    s.phases_completed = [1, 2]  # already there from a prior idempotent call
    state.write(project, s)
    state.complete_phase(project)
    s2 = state.read(project)
    assert s2.phases_completed.count(2) == 1
    assert s2.current_phase == 3


def test_cli_set_detached(project: Path, tmp_path: Path) -> None:
    state.write(project, state.State.fresh(project.name))
    env = os.environ.copy()
    env["CC_AUTOPIPE_USER_HOME"] = str(tmp_path / ".cc-autopipe-user")
    subprocess.run(
        [
            sys.executable,
            str(SRC_LIB / "state.py"),
            "set-detached",
            str(project),
            "--reason",
            "training run",
            "--check-cmd",
            "test -f /tmp/done",
            "--check-every",
            "120",
            "--max-wait",
            "7200",
        ],
        check=True,
        env=env,
    )
    s = state.read(project)
    assert s.phase == "detached"
    assert s.detached is not None
    assert s.detached.reason == "training run"
    assert s.detached.check_every_sec == 120
    assert s.detached.max_wait_sec == 7200


def test_escalated_next_cycle_round_trip(project: Path) -> None:
    s = state.State.fresh(project.name)
    s.escalated_next_cycle = True
    state.write(project, s)
    s2 = state.read(project)
    assert s2.escalated_next_cycle is True


def test_cli_complete_phase(project: Path, tmp_path: Path) -> None:
    state.write(project, state.State.fresh(project.name))
    env = os.environ.copy()
    env["CC_AUTOPIPE_USER_HOME"] = str(tmp_path / ".cc-autopipe-user")
    cp = subprocess.run(
        [
            sys.executable,
            str(SRC_LIB / "state.py"),
            "complete-phase",
            str(project),
        ],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )
    assert cp.stdout.strip() == "2"
    s = state.read(project)
    assert s.current_phase == 2
    assert s.phases_completed == [1]


# ---------------------------------------------------------------------------
# v1.2 schema additions (SPEC-v1.2.md Bug A + Bug B)
# ---------------------------------------------------------------------------


def test_v2_state_file_migrates_to_v3_on_read(project: Path) -> None:
    """A v1.0 state.json (schema_version=2, no current_task /
    in_progress fields) should read cleanly with v1.2 defaults, then
    persist as schema_version=3 on the next write. This is the
    primary backward-compat path for v1.0 → v1.2 upgrades."""
    legacy_v2 = {
        "schema_version": 2,
        "name": project.name,
        "phase": "active",
        "iteration": 5,
        "session_id": "v2-session",
        "last_score": 0.5,
        "last_passed": False,
        "prd_complete": False,
        "consecutive_failures": 1,
        "last_cycle_started_at": "2026-05-01T10:00:00Z",
        "last_progress_at": "2026-05-01T10:00:00Z",
        "threshold": 0.85,
        "paused": None,
        "detached": None,
        "current_phase": 2,
        "phases_completed": [1],
        "escalated_next_cycle": False,
        "successful_cycles_since_improver": 3,
        "improver_due": False,
    }
    (project / ".cc-autopipe" / "state.json").write_text(json.dumps(legacy_v2))

    s = state.read(project)
    # v2 fields preserved verbatim.
    assert s.iteration == 5
    assert s.session_id == "v2-session"
    assert s.current_phase == 2
    assert s.phases_completed == [1]
    assert s.successful_cycles_since_improver == 3
    # v1.2 fields filled with defaults.
    assert s.current_task is None
    assert s.last_in_progress is False
    assert s.consecutive_in_progress == 0
    # schema_version forced to current on read.
    assert s.schema_version == state.SCHEMA_VERSION

    state.write(project, s)
    raw = json.loads((project / ".cc-autopipe" / "state.json").read_text())
    assert raw["schema_version"] == state.SCHEMA_VERSION
    assert "current_task" in raw
    assert raw["current_task"] is None
    assert "last_in_progress" in raw
    assert "consecutive_in_progress" in raw


def test_current_task_round_trip(project: Path) -> None:
    """A populated CurrentTask survives to_dict/from_dict and disk."""
    s = state.State.fresh(project.name)
    s.current_task = state.CurrentTask(
        id="cand_imbloss_v2",
        started_at="2026-05-02T18:00:00Z",
        stage="training",
        stages_completed=["hypothesis"],
        artifact_paths=["data/models/exp_cand_imbloss_v2/"],
        claude_notes="SwingLoss with class_balance_beta=0.999",
    )
    state.write(project, s)

    s2 = state.read(project)
    assert s2.current_task is not None
    assert s2.current_task.id == "cand_imbloss_v2"
    assert s2.current_task.started_at == "2026-05-02T18:00:00Z"
    assert s2.current_task.stage == "training"
    assert s2.current_task.stages_completed == ["hypothesis"]
    assert s2.current_task.artifact_paths == ["data/models/exp_cand_imbloss_v2/"]
    assert s2.current_task.claude_notes.startswith("SwingLoss")


def test_current_task_from_dict_tolerates_partial(project: Path) -> None:
    """A CURRENT_TASK.md or external client may write a partial
    current_task dict (no claude_notes, scalar artifact instead of list).
    from_dict must coerce to safe defaults rather than raise."""
    ct = state.CurrentTask.from_dict({"id": "x", "stage": "init"})
    assert ct.id == "x"
    assert ct.stage == "init"
    assert ct.stages_completed == []
    assert ct.artifact_paths == []
    assert ct.claude_notes == ""

    # Scalar coerced to single-element list.
    ct2 = state.CurrentTask.from_dict(
        {"id": "y", "artifact_paths": "data/foo/", "stages_completed": "a"}
    )
    assert ct2.artifact_paths == ["data/foo/"]
    assert ct2.stages_completed == ["a"]


def test_in_progress_counters_round_trip(project: Path) -> None:
    s = state.State.fresh(project.name)
    s.last_in_progress = True
    s.consecutive_in_progress = 4
    state.write(project, s)
    s2 = state.read(project)
    assert s2.last_in_progress is True
    assert s2.consecutive_in_progress == 4


def test_update_verify_in_progress_does_not_count_failure(project: Path) -> None:
    """Bug B: in_progress=True must NOT bump consecutive_failures."""
    state.write(project, state.State.fresh(project.name))
    state.update_verify(
        project,
        passed=False,
        score=0.4,
        prd_complete=False,
        in_progress=True,
    )
    s = state.read(project)
    assert s.consecutive_failures == 0  # NOT incremented
    assert s.consecutive_in_progress == 1
    assert s.last_in_progress is True
    assert s.last_passed is False
    assert s.last_score == 0.4


def test_update_verify_in_progress_accumulates(project: Path) -> None:
    state.write(project, state.State.fresh(project.name))
    for _ in range(5):
        state.update_verify(
            project,
            passed=False,
            score=0.0,
            prd_complete=False,
            in_progress=True,
        )
    s = state.read(project)
    assert s.consecutive_failures == 0
    assert s.consecutive_in_progress == 5


def test_update_verify_passing_resets_in_progress_counter(project: Path) -> None:
    """A passing cycle resets consecutive_in_progress as well as
    consecutive_failures, so a project that was 'cooking' for 5 cycles
    and finally produced output starts fresh."""
    s = state.State.fresh(project.name)
    s.consecutive_in_progress = 5
    state.write(project, s)
    state.update_verify(project, passed=True, score=1.0, prd_complete=False)
    s2 = state.read(project)
    assert s2.consecutive_in_progress == 0
    assert s2.last_in_progress is False


def test_update_verify_real_failure_resets_in_progress_counter(
    project: Path,
) -> None:
    """A real failure (in_progress=False, passed=False) breaks any
    in-progress streak. The next cycle has to re-build the streak
    if it really is still cooking."""
    s = state.State.fresh(project.name)
    s.consecutive_in_progress = 3
    state.write(project, s)
    state.update_verify(
        project,
        passed=False,
        score=0.0,
        prd_complete=False,
        in_progress=False,
    )
    s2 = state.read(project)
    assert s2.consecutive_failures == 1
    assert s2.consecutive_in_progress == 0
    assert s2.last_in_progress is False


def test_update_verify_in_progress_default_false_preserves_v10(project: Path) -> None:
    """Callers that omit in_progress hit the v1.0 path verbatim — no
    accidental side effects on consecutive_in_progress."""
    state.write(project, state.State.fresh(project.name))
    state.update_verify(project, passed=False, score=0.4, prd_complete=False)
    s = state.read(project)
    assert s.consecutive_failures == 1
    assert s.consecutive_in_progress == 0
    assert s.last_in_progress is False


def test_cli_update_verify_in_progress_flag(project: Path, tmp_path: Path) -> None:
    """state.py update-verify --in-progress true exposes the new path."""
    state.write(project, state.State.fresh(project.name))
    env = os.environ.copy()
    env["CC_AUTOPIPE_USER_HOME"] = str(tmp_path / ".cc-autopipe-user")
    subprocess.run(
        [
            sys.executable,
            str(SRC_LIB / "state.py"),
            "update-verify",
            str(project),
            "--passed",
            "false",
            "--score",
            "0.3",
            "--prd-complete",
            "false",
            "--in-progress",
            "true",
        ],
        check=True,
        env=env,
    )
    s = state.read(project)
    assert s.last_in_progress is True
    assert s.consecutive_in_progress == 1
    assert s.consecutive_failures == 0


def test_v3_preserves_extras_dict(project: Path) -> None:
    """Unknown keys in state.json (e.g. from a future v1.3) flow into
    extras and round-trip without loss. Ensures forward-compat."""
    raw = {
        "schema_version": 3,
        "name": project.name,
        "phase": "active",
        "iteration": 1,
        "session_id": None,
        "last_score": None,
        "last_passed": None,
        "prd_complete": False,
        "consecutive_failures": 0,
        "threshold": 0.85,
        "paused": None,
        "detached": None,
        "current_phase": 1,
        "phases_completed": [],
        "escalated_next_cycle": False,
        "successful_cycles_since_improver": 0,
        "improver_due": False,
        "current_task": None,
        "last_in_progress": False,
        "consecutive_in_progress": 0,
        "future_v13_field": {"hello": "world"},
    }
    (project / ".cc-autopipe" / "state.json").write_text(json.dumps(raw))
    s = state.read(project)
    assert s.extras["future_v13_field"] == {"hello": "world"}
    state.write(project, s)
    raw2 = json.loads((project / ".cc-autopipe" / "state.json").read_text())
    assert raw2["future_v13_field"] == {"hello": "world"}
