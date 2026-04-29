"""Integration tests for Stage F CLI commands.

Covers AGENTS.md §2 Stage F DoD items:
- cc-autopipe-checkpoint saves checkpoint.md
- cc-autopipe-block marks failed + creates HUMAN_NEEDED.md
- cc-autopipe resume clears PAUSED/FAILED, resets failures
- cc-autopipe doctor checks prerequisites
- cc-autopipe tail follows aggregate.jsonl
- cc-autopipe run <project> --once runs single cycle
- All commands have --help

All tests use --offline / mocks / tmp_path; no real network calls.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
DISPATCHER = SRC / "helpers" / "cc-autopipe"
CHECKPOINT_HELPER = SRC / "helpers" / "cc-autopipe-checkpoint"
BLOCK_HELPER = SRC / "helpers" / "cc-autopipe-block"
RESUME_PY = SRC / "cli" / "resume.py"
TAIL_PY = SRC / "cli" / "tail.py"
RUN_PY = SRC / "cli" / "run.py"
DOCTOR_PY = SRC / "cli" / "doctor.py"


def _engine_env(user_home: Path, **overrides: str) -> dict[str, str]:
    env = os.environ.copy()
    env["CC_AUTOPIPE_HOME"] = str(SRC)
    env["CC_AUTOPIPE_USER_HOME"] = str(user_home)
    env["NO_COLOR"] = "1"
    env.update(overrides)
    return env


def _seed_project(
    base: Path,
    name: str,
    *,
    phase: str = "active",
    iteration: int = 0,
    consecutive_failures: int = 0,
    paused_resume_at: str | None = None,
) -> Path:
    p = base / name
    (p / ".cc-autopipe" / "memory").mkdir(parents=True, exist_ok=True)
    state_doc: dict[str, object] = {
        "schema_version": 1,
        "name": name,
        "phase": phase,
        "iteration": iteration,
        "session_id": None,
        "last_score": None,
        "last_passed": None,
        "prd_complete": False,
        "consecutive_failures": consecutive_failures,
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


# ---------------------------------------------------------------------------
# --help discoverability (AGENTS.md §2 Stage F: "All commands have --help")
# ---------------------------------------------------------------------------


def test_dispatcher_help_lists_stage_f_commands(tmp_path: Path) -> None:
    cp = subprocess.run(
        [str(DISPATCHER), "--help"],
        capture_output=True,
        text=True,
        env=_engine_env(tmp_path / "uhome"),
        check=True,
    )
    for sub in ("resume", "run", "tail", "doctor", "checkpoint", "block"):
        assert sub in cp.stdout, f"--help missing {sub}: {cp.stdout}"


def test_each_stage_f_command_has_help(tmp_path: Path) -> None:
    env = _engine_env(tmp_path / "uhome")
    for sub in ("resume", "run", "tail", "doctor"):
        cp = subprocess.run(
            [str(DISPATCHER), sub, "--help"],
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        assert "usage" in cp.stdout.lower(), f"{sub} --help: {cp.stdout}"
    # Bash helpers print their own usage.
    for helper in (CHECKPOINT_HELPER, BLOCK_HELPER):
        cp = subprocess.run(
            ["bash", str(helper), "--help"],
            capture_output=True,
            text=True,
            check=True,
        )
        assert "Usage" in cp.stdout


# ---------------------------------------------------------------------------
# helpers/cc-autopipe-checkpoint
# ---------------------------------------------------------------------------


def test_checkpoint_writes_file_from_arg(tmp_path: Path) -> None:
    project = _seed_project(tmp_path, "alpha")
    cp = subprocess.run(
        ["bash", str(CHECKPOINT_HELPER), "--project", str(project), "body via arg"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "checkpoint saved" in cp.stdout.lower()
    body = (project / ".cc-autopipe" / "checkpoint.md").read_text()
    assert "body via arg" in body
    assert "<!-- cc-autopipe checkpoint" in body  # header comment


def test_checkpoint_writes_file_from_stdin(tmp_path: Path) -> None:
    project = _seed_project(tmp_path, "alpha")
    cp = subprocess.run(
        ["bash", str(CHECKPOINT_HELPER), "--project", str(project)],
        input="body via stdin\nsecond line",
        capture_output=True,
        text=True,
        check=True,
    )
    assert cp.returncode == 0
    body = (project / ".cc-autopipe" / "checkpoint.md").read_text()
    assert "body via stdin" in body
    assert "second line" in body


def test_checkpoint_refuses_uninitialized_dir(tmp_path: Path) -> None:
    bare = tmp_path / "bare"
    bare.mkdir()
    cp = subprocess.run(
        ["bash", str(CHECKPOINT_HELPER), "--project", str(bare), "body"],
        capture_output=True,
        text=True,
    )
    assert cp.returncode != 0
    assert "not initialised" in cp.stderr.lower()


def test_checkpoint_refuses_empty_body(tmp_path: Path) -> None:
    project = _seed_project(tmp_path, "alpha")
    cp = subprocess.run(
        ["bash", str(CHECKPOINT_HELPER), "--project", str(project)],
        input="   ",
        capture_output=True,
        text=True,
    )
    assert cp.returncode != 0


# ---------------------------------------------------------------------------
# helpers/cc-autopipe-block
# ---------------------------------------------------------------------------


def test_block_marks_failed_and_writes_human_needed(tmp_path: Path) -> None:
    user_home = tmp_path / "uhome"
    user_home.mkdir()
    project = _seed_project(tmp_path, "alpha", iteration=4)
    cp = subprocess.run(
        [
            "bash",
            str(BLOCK_HELPER),
            "--project",
            str(project),
            "verify.sh missing fixture",
        ],
        capture_output=True,
        text=True,
        env=_engine_env(user_home),
        check=True,
    )
    assert "blocked" in cp.stdout.lower()
    s = json.loads((project / ".cc-autopipe" / "state.json").read_text())
    assert s["phase"] == "failed"
    assert s["iteration"] == 4  # other fields preserved
    human = (project / ".cc-autopipe" / "HUMAN_NEEDED.md").read_text()
    assert "verify.sh missing fixture" in human
    assert "alpha" in human
    # log-event landed in aggregate.jsonl
    log = (user_home / "log" / "aggregate.jsonl").read_text()
    assert '"event":"blocked"' in log


def test_block_refuses_empty_reason(tmp_path: Path) -> None:
    project = _seed_project(tmp_path, "alpha")
    cp = subprocess.run(
        ["bash", str(BLOCK_HELPER), "--project", str(project), ""],
        capture_output=True,
        text=True,
        env=_engine_env(tmp_path / "uhome"),
    )
    assert cp.returncode != 0


# ---------------------------------------------------------------------------
# cli/resume.py
# ---------------------------------------------------------------------------


def test_resume_clears_paused(tmp_path: Path) -> None:
    user_home = tmp_path / "uhome"
    user_home.mkdir()
    project = _seed_project(
        tmp_path,
        "alpha",
        phase="paused",
        consecutive_failures=2,
        paused_resume_at="2026-04-29T20:00:00Z",
    )
    cp = subprocess.run(
        [str(DISPATCHER), "resume", str(project)],
        capture_output=True,
        text=True,
        env=_engine_env(user_home),
        check=True,
    )
    assert "active" in cp.stdout
    s = json.loads((project / ".cc-autopipe" / "state.json").read_text())
    assert s["phase"] == "active"
    assert s["consecutive_failures"] == 0
    assert s["paused"] is None


def test_resume_clears_failed_and_removes_human_needed(tmp_path: Path) -> None:
    user_home = tmp_path / "uhome"
    user_home.mkdir()
    project = _seed_project(tmp_path, "alpha", phase="failed", consecutive_failures=3)
    (project / ".cc-autopipe" / "HUMAN_NEEDED.md").write_text("# blocked\n")
    cp = subprocess.run(
        [str(DISPATCHER), "resume", str(project)],
        capture_output=True,
        text=True,
        env=_engine_env(user_home),
        check=True,
    )
    assert "removed" in cp.stdout.lower()
    s = json.loads((project / ".cc-autopipe" / "state.json").read_text())
    assert s["phase"] == "active"
    assert not (project / ".cc-autopipe" / "HUMAN_NEEDED.md").exists()
    log = (user_home / "log" / "aggregate.jsonl").read_text()
    assert '"event":"resume"' in log


def test_resume_rejects_missing_project(tmp_path: Path) -> None:
    cp = subprocess.run(
        [str(DISPATCHER), "resume", str(tmp_path / "nope")],
        capture_output=True,
        text=True,
        env=_engine_env(tmp_path / "uhome"),
    )
    assert cp.returncode == 1


def test_resume_rejects_uninitialized(tmp_path: Path) -> None:
    bare = tmp_path / "bare"
    bare.mkdir()
    cp = subprocess.run(
        [str(DISPATCHER), "resume", str(bare)],
        capture_output=True,
        text=True,
        env=_engine_env(tmp_path / "uhome"),
    )
    assert cp.returncode == 1
    assert "not initialised" in cp.stderr.lower()


# ---------------------------------------------------------------------------
# cli/tail.py
# ---------------------------------------------------------------------------


def _write_aggregate(user_home: Path, records: list[dict[str, object]]) -> Path:
    log = user_home / "log" / "aggregate.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return log


def test_tail_no_follow_prints_existing_lines(tmp_path: Path) -> None:
    user_home = tmp_path / "uhome"
    user_home.mkdir()
    _write_aggregate(
        user_home,
        [
            {
                "ts": "2026-04-29T15:24:00Z",
                "project": "alpha",
                "event": "cycle_start",
                "iteration": 1,
            },
            {
                "ts": "2026-04-29T15:25:00Z",
                "project": "alpha",
                "event": "verify_failed",
                "score": 0.3,
            },
        ],
    )
    cp = subprocess.run(
        [str(DISPATCHER), "tail", "--no-follow"],
        capture_output=True,
        text=True,
        env=_engine_env(user_home),
        check=True,
    )
    assert "alpha" in cp.stdout
    assert "cycle_start" in cp.stdout
    assert "verify_failed" in cp.stdout


def test_tail_filter_by_project(tmp_path: Path) -> None:
    user_home = tmp_path / "uhome"
    user_home.mkdir()
    _write_aggregate(
        user_home,
        [
            {"ts": "T", "project": "alpha", "event": "cycle_start"},
            {"ts": "T", "project": "bravo", "event": "cycle_start"},
        ],
    )
    cp = subprocess.run(
        [str(DISPATCHER), "tail", "--no-follow", "--project", "alpha"],
        capture_output=True,
        text=True,
        env=_engine_env(user_home),
        check=True,
    )
    assert "alpha" in cp.stdout
    assert "bravo" not in cp.stdout


def test_tail_filter_by_event(tmp_path: Path) -> None:
    user_home = tmp_path / "uhome"
    user_home.mkdir()
    _write_aggregate(
        user_home,
        [
            {"ts": "T", "project": "alpha", "event": "cycle_start"},
            {"ts": "T", "project": "alpha", "event": "done"},
            {"ts": "T", "project": "alpha", "event": "paused"},
        ],
    )
    cp = subprocess.run(
        [str(DISPATCHER), "tail", "--no-follow", "--event", "done,paused"],
        capture_output=True,
        text=True,
        env=_engine_env(user_home),
        check=True,
    )
    assert "done" in cp.stdout
    assert "paused" in cp.stdout
    assert "cycle_start" not in cp.stdout


def test_tail_no_log_returns_rc1(tmp_path: Path) -> None:
    user_home = tmp_path / "uhome"
    user_home.mkdir()
    cp = subprocess.run(
        [str(DISPATCHER), "tail", "--no-follow"],
        capture_output=True,
        text=True,
        env=_engine_env(user_home),
    )
    assert cp.returncode == 1


def test_tail_skips_malformed_lines(tmp_path: Path) -> None:
    user_home = tmp_path / "uhome"
    user_home.mkdir()
    log = user_home / "log" / "aggregate.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        '{"ts":"T","project":"alpha","event":"cycle_start"}\n'
        "not-json-garbage\n"
        '{"ts":"T","project":"alpha","event":"done"}\n'
    )
    cp = subprocess.run(
        [str(DISPATCHER), "tail", "--no-follow"],
        capture_output=True,
        text=True,
        env=_engine_env(user_home),
        check=True,
    )
    assert "cycle_start" in cp.stdout
    assert "done" in cp.stdout


# ---------------------------------------------------------------------------
# cli/tail.py — follow mode (subprocess + write more lines)
# ---------------------------------------------------------------------------


def test_tail_follow_picks_up_new_lines(tmp_path: Path) -> None:
    user_home = tmp_path / "uhome"
    user_home.mkdir()
    log = user_home / "log" / "aggregate.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text('{"ts":"T","project":"alpha","event":"seed"}\n')

    proc = subprocess.Popen(
        [str(DISPATCHER), "tail", "-n", "1"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_engine_env(user_home),
    )
    try:
        # Append a new line; tail's poll loop is 0.5s.
        time.sleep(0.6)
        with log.open("a") as f:
            f.write('{"ts":"T","project":"alpha","event":"appended"}\n')
        time.sleep(1.2)
        proc.terminate()
        try:
            stdout, _ = proc.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, _ = proc.communicate(timeout=3)
        assert "appended" in stdout
    finally:
        if proc.poll() is None:
            proc.kill()


# ---------------------------------------------------------------------------
# cli/run.py
# ---------------------------------------------------------------------------


def _init_project_for_run(project: Path, user_home: Path) -> None:
    project.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    env = _engine_env(user_home)
    subprocess.run(
        [str(DISPATCHER), "init", str(project)],
        capture_output=True,
        check=True,
        env=env,
    )


def test_run_once_runs_a_single_cycle(tmp_path: Path) -> None:
    user_home = tmp_path / "uhome"
    project = tmp_path / "alpha"
    _init_project_for_run(project, user_home)
    # Pre-populate quota cache so run never hits api.anthropic.com.
    (user_home / "quota-cache.json").write_text(
        json.dumps(
            {
                "five_hour": {"utilization": 5, "resets_at": "2026-04-29T20:00:00Z"},
                "seven_day": {"utilization": 10, "resets_at": "2026-05-06T20:00:00Z"},
            }
        )
    )

    env = _engine_env(
        user_home,
        CC_AUTOPIPE_CLAUDE_BIN="/usr/bin/true",
        CC_AUTOPIPE_CYCLE_TIMEOUT_SEC="30",
    )
    cp = subprocess.run(
        [str(DISPATCHER), "run", str(project), "--once"],
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )
    assert cp.returncode == 0, cp.stderr
    s = json.loads((project / ".cc-autopipe" / "state.json").read_text())
    assert s["iteration"] == 1
    log = (user_home / "log" / "aggregate.jsonl").read_text()
    assert '"event":"cycle_start"' in log
    assert '"event":"cycle_end"' in log


def test_run_without_once_rejects(tmp_path: Path) -> None:
    user_home = tmp_path / "uhome"
    project = tmp_path / "alpha"
    _init_project_for_run(project, user_home)
    cp = subprocess.run(
        [str(DISPATCHER), "run", str(project)],
        capture_output=True,
        text=True,
        env=_engine_env(user_home),
    )
    assert cp.returncode == 64
    assert "--once" in cp.stderr


def test_run_missing_project_returns_rc1(tmp_path: Path) -> None:
    cp = subprocess.run(
        [str(DISPATCHER), "run", str(tmp_path / "nope"), "--once"],
        capture_output=True,
        text=True,
        env=_engine_env(tmp_path / "uhome"),
    )
    assert cp.returncode == 1


def test_run_uninitialized_returns_rc1(tmp_path: Path) -> None:
    user_home = tmp_path / "uhome"
    user_home.mkdir()
    bare = tmp_path / "bare"
    bare.mkdir()
    env = _engine_env(user_home, CC_AUTOPIPE_QUOTA_DISABLED="1")
    cp = subprocess.run(
        [str(DISPATCHER), "run", str(bare), "--once"],
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    assert cp.returncode == 1


# ---------------------------------------------------------------------------
# cli/doctor.py
# ---------------------------------------------------------------------------


def test_doctor_offline_runs_to_completion(tmp_path: Path) -> None:
    """--offline avoids real network: TG send-test + oauth/usage skipped.
    On Roman's macOS host with live Keychain creds, OAuth token check
    will pass; on a clean CI host without creds it warns. Either way
    rc=0 unless a hard requirement (claude/jq/python) is missing."""
    user_home = tmp_path / "uhome"
    user_home.mkdir()
    cp = subprocess.run(
        [str(DISPATCHER), "doctor", "--offline"],
        capture_output=True,
        text=True,
        env=_engine_env(user_home),
        timeout=10,
    )
    # Either 0 (all green/warn) or 1 (some fail). On the build host
    # we expect 0; assert lenient and surface stderr if not.
    assert cp.returncode in (0, 1), f"rc={cp.returncode}\nstderr={cp.stderr}"
    assert "claude binary" in cp.stdout
    assert "python3" in cp.stdout
    assert "hooks" in cp.stdout
    assert "skipped (--offline)" in cp.stdout


def test_doctor_json_output_is_valid(tmp_path: Path) -> None:
    user_home = tmp_path / "uhome"
    user_home.mkdir()
    cp = subprocess.run(
        [str(DISPATCHER), "doctor", "--offline", "--json"],
        capture_output=True,
        text=True,
        env=_engine_env(user_home),
        timeout=10,
    )
    doc = json.loads(cp.stdout)
    assert "checks" in doc
    assert "summary" in doc
    assert isinstance(doc["checks"], list)
    names = {c["name"] for c in doc["checks"]}
    for required in (
        "claude binary",
        "python3",
        "jq",
        "hooks",
        "OAuth token",
        "TG send-test",
        "oauth/usage endpoint",
    ):
        assert required in names, f"missing check: {required}"


def test_doctor_secrets_env_chmod_warning(tmp_path: Path) -> None:
    """A secrets.env with wrong perms is reported as fail, not silently OK."""
    user_home = tmp_path / "uhome"
    user_home.mkdir()
    secrets = user_home / "secrets.env"
    secrets.write_text("TG_BOT_TOKEN=fake\nTG_CHAT_ID=0\n")
    secrets.chmod(0o644)
    cp = subprocess.run(
        [str(DISPATCHER), "doctor", "--offline", "--json"],
        capture_output=True,
        text=True,
        env=_engine_env(user_home),
        timeout=10,
    )
    doc = json.loads(cp.stdout)
    by_name = {c["name"]: c for c in doc["checks"]}
    assert by_name["secrets.env"]["status"] == "fail"
    assert "0o644" in by_name["secrets.env"]["detail"]


def test_doctor_secrets_env_chmod_600_passes(tmp_path: Path) -> None:
    user_home = tmp_path / "uhome"
    user_home.mkdir()
    secrets = user_home / "secrets.env"
    secrets.write_text("TG_BOT_TOKEN=fake\nTG_CHAT_ID=0\n")
    secrets.chmod(0o600)
    cp = subprocess.run(
        [str(DISPATCHER), "doctor", "--offline", "--json"],
        capture_output=True,
        text=True,
        env=_engine_env(user_home),
        timeout=10,
    )
    doc = json.loads(cp.stdout)
    by_name = {c["name"]: c for c in doc["checks"]}
    assert by_name["secrets.env"]["status"] == "ok"
