"""Integration tests for orchestrator pre-flight + stop-failure + quota.

Covers Stage E DoD items:
- orchestrator pre-flight check pauses project at >95% 5h
- orchestrator pre-flight check pauses ALL projects at >90% 7d
- stop-failure.sh uses quota.py first, falls back to ratelimit.py

Pre-populates quota-cache.json directly so the orchestrator's
read_cached() returns the value we want without ever fetching.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
ORCHESTRATOR = SRC / "orchestrator"
DISPATCHER = SRC / "helpers" / "cc-autopipe"
HOOKS_DIR = SRC / "hooks"


def _orch_env(user_home: Path, **overrides: str) -> dict[str, str]:
    env = os.environ.copy()
    env["CC_AUTOPIPE_HOME"] = str(SRC)
    env["CC_AUTOPIPE_USER_HOME"] = str(user_home)
    env["CC_AUTOPIPE_COOLDOWN_SEC"] = "0"
    env["CC_AUTOPIPE_IDLE_SLEEP_SEC"] = "0"
    env["CC_AUTOPIPE_CLAUDE_BIN"] = "/usr/bin/true"
    # NOTE: not disabling quota — these tests SHOULD exercise the path.
    env.update(overrides)
    return env


def _init_project(project: Path, user_home: Path) -> None:
    project.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    env = os.environ.copy()
    env["CC_AUTOPIPE_HOME"] = str(SRC)
    env["CC_AUTOPIPE_USER_HOME"] = str(user_home)
    subprocess.run(
        [str(DISPATCHER), "init", str(project)],
        capture_output=True,
        check=True,
        env=env,
    )


def _seed_quota_cache(
    user_home: Path,
    *,
    five_hour: float,
    seven_day: float,
    five_resets_in_h: float = 4.0,
    seven_resets_in_d: float = 6.0,
) -> None:
    user_home.mkdir(parents=True, exist_ok=True)
    five_resets = (
        datetime.now(timezone.utc) + timedelta(hours=five_resets_in_h)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    seven_resets = (
        datetime.now(timezone.utc) + timedelta(days=seven_resets_in_d)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {
        "five_hour": {"utilization": five_hour, "resets_at": five_resets},
        "seven_day": {"utilization": seven_day, "resets_at": seven_resets},
    }
    (user_home / "quota-cache.json").write_text(json.dumps(payload))


def _run_orch_one_loop(env: dict[str, str], timeout: float = 15.0):
    env = dict(env)
    env["CC_AUTOPIPE_MAX_LOOPS"] = env.get("CC_AUTOPIPE_MAX_LOOPS", "1")
    return subprocess.run(
        [sys.executable, str(ORCHESTRATOR)],
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# Pre-flight 5h
# ---------------------------------------------------------------------------


def test_preflight_pauses_project_at_95pct_5h(tmp_path: Path) -> None:
    user_home = tmp_path / "uhome"
    project = tmp_path / "proj"
    _init_project(project, user_home)
    _seed_quota_cache(user_home, five_hour=0.97, seven_day=0.20)

    env = _orch_env(user_home)
    cp = _run_orch_one_loop(env)
    assert cp.returncode == 0, cp.stderr

    s = json.loads((project / ".cc-autopipe" / "state.json").read_text())
    assert s["phase"] == "paused"
    assert s["paused"]["reason"] == "5h_pre_check"
    # iteration NOT bumped (the cycle was a no-op due to pre-flight pause)
    assert s["iteration"] == 0


def test_preflight_warns_at_85pct_5h_but_proceeds(tmp_path: Path) -> None:
    user_home = tmp_path / "uhome"
    project = tmp_path / "proj"
    _init_project(project, user_home)
    _seed_quota_cache(user_home, five_hour=0.85, seven_day=0.20)

    env = _orch_env(user_home)
    cp = _run_orch_one_loop(env)
    assert cp.returncode == 0, cp.stderr
    assert "5h quota at 85%" in cp.stderr

    s = json.loads((project / ".cc-autopipe" / "state.json").read_text())
    assert s["phase"] == "active"
    assert s["iteration"] == 1  # cycle ran


def test_preflight_proceeds_at_70pct_5h_no_warn(tmp_path: Path) -> None:
    user_home = tmp_path / "uhome"
    project = tmp_path / "proj"
    _init_project(project, user_home)
    _seed_quota_cache(user_home, five_hour=0.70, seven_day=0.20)

    env = _orch_env(user_home)
    cp = _run_orch_one_loop(env)
    assert cp.returncode == 0, cp.stderr
    assert "5h quota at" not in cp.stderr

    s = json.loads((project / ".cc-autopipe" / "state.json").read_text())
    assert s["phase"] == "active"
    assert s["iteration"] == 1


# ---------------------------------------------------------------------------
# Pre-flight 7d
# ---------------------------------------------------------------------------


def test_preflight_pauses_at_90pct_7d_with_tg(tmp_path: Path) -> None:
    user_home = tmp_path / "uhome"
    project = tmp_path / "proj"
    _init_project(project, user_home)
    _seed_quota_cache(user_home, five_hour=0.30, seven_day=0.92)

    env = _orch_env(user_home)
    cp = _run_orch_one_loop(env)
    assert cp.returncode == 0, cp.stderr

    s = json.loads((project / ".cc-autopipe" / "state.json").read_text())
    assert s["phase"] == "paused"
    assert s["paused"]["reason"] == "7d_pre_check"

    # The 7d-tg sentinel was created (proves _should_send_7d_alert ran).
    assert (user_home / "7d-tg.last").exists()


def test_preflight_7d_pauses_all_projects(tmp_path: Path) -> None:
    user_home = tmp_path / "uhome"
    proj1 = tmp_path / "p1"
    proj2 = tmp_path / "p2"
    proj3 = tmp_path / "p3"
    for p in (proj1, proj2, proj3):
        _init_project(p, user_home)
    _seed_quota_cache(user_home, five_hour=0.30, seven_day=0.95)

    env = _orch_env(user_home)
    cp = _run_orch_one_loop(env)
    assert cp.returncode == 0, cp.stderr

    for p in (proj1, proj2, proj3):
        s = json.loads((p / ".cc-autopipe" / "state.json").read_text())
        assert s["phase"] == "paused", f"{p.name} not paused"
        assert s["paused"]["reason"] == "7d_pre_check"


def test_preflight_7d_tg_dedup_within_window(tmp_path: Path) -> None:
    """Multiple projects in one outer loop must not each fire TG."""
    user_home = tmp_path / "uhome"
    proj1 = tmp_path / "p1"
    proj2 = tmp_path / "p2"
    for p in (proj1, proj2):
        _init_project(p, user_home)
    _seed_quota_cache(user_home, five_hour=0.30, seven_day=0.95)

    env = _orch_env(user_home)
    _run_orch_one_loop(env)

    # The sentinel exists; only the very first project's pre-flight
    # could have written it. Subsequent projects' pre-flights would
    # have seen the sentinel and skipped TG.
    sentinel = user_home / "7d-tg.last"
    mtime_after_first_run = sentinel.stat().st_mtime

    # Run another loop — within the 5min window, sentinel should NOT
    # be touched again.
    _run_orch_one_loop(env)
    assert sentinel.stat().st_mtime == mtime_after_first_run


def test_preflight_7d_takes_priority_over_5h(tmp_path: Path) -> None:
    """When both thresholds tripped, 7d branch wins."""
    user_home = tmp_path / "uhome"
    project = tmp_path / "proj"
    _init_project(project, user_home)
    _seed_quota_cache(user_home, five_hour=0.99, seven_day=0.95)

    env = _orch_env(user_home)
    _run_orch_one_loop(env)

    s = json.loads((project / ".cc-autopipe" / "state.json").read_text())
    assert s["paused"]["reason"] == "7d_pre_check"


# ---------------------------------------------------------------------------
# Pre-flight: quota disabled / unavailable
# ---------------------------------------------------------------------------


def test_preflight_no_cache_proceeds_silently(tmp_path: Path) -> None:
    """No quota cache and quota disabled → pre-flight returns "ok" and
    cycle runs normally. (Tests the "endpoint failed, fall through" path.)"""
    user_home = tmp_path / "uhome"
    project = tmp_path / "proj"
    _init_project(project, user_home)
    # No _seed_quota_cache call — cache file doesn't exist.

    env = _orch_env(user_home, CC_AUTOPIPE_QUOTA_DISABLED="1")
    cp = _run_orch_one_loop(env)
    assert cp.returncode == 0, cp.stderr

    s = json.loads((project / ".cc-autopipe" / "state.json").read_text())
    assert s["phase"] == "active"
    assert s["iteration"] == 1


# ---------------------------------------------------------------------------
# stop-failure.sh quota integration
# ---------------------------------------------------------------------------


def test_stop_failure_uses_quota_resets_at_when_cache_present(
    tmp_path: Path,
) -> None:
    """When quota-cache.json has a five_hour.resets_at, stop-failure
    derives resume_at from it (with the 60s safety margin) instead of
    advancing the ladder."""
    user_home = tmp_path / "uhome"
    project = tmp_path / "proj"
    _init_project(project, user_home)
    # Seed cache with a precise resets_at 4h from now.
    five_resets = (datetime.now(timezone.utc) + timedelta(hours=4)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    seven_resets = (datetime.now(timezone.utc) + timedelta(days=6)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    (user_home / "quota-cache.json").write_text(
        json.dumps(
            {
                "five_hour": {"utilization": 0.50, "resets_at": five_resets},
                "seven_day": {"utilization": 0.30, "resets_at": seven_resets},
            }
        )
    )

    env = os.environ.copy()
    env["CC_AUTOPIPE_HOME"] = str(SRC)
    env["CC_AUTOPIPE_USER_HOME"] = str(user_home)
    cp = subprocess.run(
        ["bash", str(HOOKS_DIR / "stop-failure.sh")],
        input=json.dumps({"cwd": str(project), "error": "rate_limit"}),
        text=True,
        capture_output=True,
        env=env,
        timeout=10,
    )
    assert cp.returncode == 0, cp.stderr

    s = json.loads((project / ".cc-autopipe" / "state.json").read_text())
    assert s["phase"] == "paused"
    resume = datetime.strptime(s["paused"]["resume_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )
    expected = datetime.now(timezone.utc) + timedelta(hours=4) + timedelta(seconds=60)
    delta = abs((resume - expected).total_seconds())
    assert delta < 30, f"resume_at off by {delta}s; got {resume}, expected ~{expected}"

    # resolved_via=quota appears in aggregate
    log = (user_home / "log" / "aggregate.jsonl").read_text()
    assert '"resolved_via":"quota"' in log


def test_stop_failure_falls_back_to_ladder_without_cache(tmp_path: Path) -> None:
    """No quota cache + QUOTA_DISABLED=1 forces the ladder path. First
    invocation lands at the 5min step."""
    user_home = tmp_path / "uhome"
    project = tmp_path / "proj"
    _init_project(project, user_home)

    env = os.environ.copy()
    env["CC_AUTOPIPE_HOME"] = str(SRC)
    env["CC_AUTOPIPE_USER_HOME"] = str(user_home)
    env["CC_AUTOPIPE_QUOTA_DISABLED"] = "1"
    cp = subprocess.run(
        ["bash", str(HOOKS_DIR / "stop-failure.sh")],
        input=json.dumps({"cwd": str(project), "error": "rate_limit"}),
        text=True,
        capture_output=True,
        env=env,
        timeout=10,
    )
    assert cp.returncode == 0, cp.stderr

    s = json.loads((project / ".cc-autopipe" / "state.json").read_text())
    resume = datetime.strptime(s["paused"]["resume_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )
    delta = (resume - datetime.now(timezone.utc)).total_seconds()
    assert 290 <= delta <= 320, f"expected ~5min ladder step, got {delta}s"

    log = (user_home / "log" / "aggregate.jsonl").read_text()
    assert '"resolved_via":"ladder' in log


def test_stop_failure_ladder_progression(tmp_path: Path) -> None:
    """Three rate_limits in a row advance the ladder 5 → 15 → 60 min."""
    user_home = tmp_path / "uhome"
    project = tmp_path / "proj"
    _init_project(project, user_home)

    env = os.environ.copy()
    env["CC_AUTOPIPE_HOME"] = str(SRC)
    env["CC_AUTOPIPE_USER_HOME"] = str(user_home)
    env["CC_AUTOPIPE_QUOTA_DISABLED"] = "1"

    deltas: list[float] = []
    for _ in range(3):
        subprocess.run(
            ["bash", str(HOOKS_DIR / "stop-failure.sh")],
            input=json.dumps({"cwd": str(project), "error": "rate_limit"}),
            text=True,
            capture_output=True,
            env=env,
            check=True,
            timeout=10,
        )
        s = json.loads((project / ".cc-autopipe" / "state.json").read_text())
        resume = datetime.strptime(
            s["paused"]["resume_at"], "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=timezone.utc)
        deltas.append((resume - datetime.now(timezone.utc)).total_seconds())

    # Tolerances: ±15s for clock drift between sample and assert.
    assert 285 <= deltas[0] <= 315, f"step 0: {deltas[0]}"
    assert 885 <= deltas[1] <= 915, f"step 1: {deltas[1]}"
    assert 3585 <= deltas[2] <= 3615, f"step 2: {deltas[2]}"


def test_preflight_uses_5h_resets_at_for_resume(tmp_path: Path) -> None:
    """Pre-flight pause uses quota.five_hour_resets_at for state.paused.resume_at."""
    user_home = tmp_path / "uhome"
    project = tmp_path / "proj"
    _init_project(project, user_home)
    _seed_quota_cache(
        user_home,
        five_hour=0.97,
        seven_day=0.20,
        five_resets_in_h=3.5,
    )

    env = _orch_env(user_home)
    _run_orch_one_loop(env)

    s = json.loads((project / ".cc-autopipe" / "state.json").read_text())
    resume = datetime.strptime(s["paused"]["resume_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )
    expected = datetime.now(timezone.utc) + timedelta(hours=3.5)
    delta = abs((resume - expected).total_seconds())
    # Pre-flight uses quota's resets_at directly without 60s margin
    # (the 60s is in stop-failure on 429; pre-flight pauses BEFORE
    # the request so no margin needed).
    assert delta < 60, f"resume_at off by {delta}s; got {resume}, expected ~{expected}"
