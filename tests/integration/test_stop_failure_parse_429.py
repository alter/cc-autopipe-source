"""v1.5.0: stop-failure.sh parses retry-after from the 429 error_details
itself before consulting quota.py / ratelimit.py.

Three formats supported, all flowing into state.paused.resume_at and
logged with resolved_via=parsed_message:
  1. ISO 8601 timestamp anywhere in the message
  2. Retry-After / X-RateLimit-Reset header (seconds)
  3. Relative-time prose ("in 15 minutes", "retry after 600 seconds")

When no parse hits AND quota cache is unavailable AND ratelimit fallback
errors, the last-resort path returns now+15min (v1.5.0, was 1h pre-v1.5.0).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
DISPATCHER = SRC / "helpers" / "cc-autopipe"
HOOKS_DIR = SRC / "hooks"


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


def _stop_failure_env(user_home: Path, *, disable_quota: bool = True) -> dict[str, str]:
    env = os.environ.copy()
    env["CC_AUTOPIPE_HOME"] = str(SRC)
    env["CC_AUTOPIPE_USER_HOME"] = str(user_home)
    if disable_quota:
        env["CC_AUTOPIPE_QUOTA_DISABLED"] = "1"
    return env


def _run_stop_failure(project: Path, env: dict[str, str], payload: dict) -> None:
    cp = subprocess.run(
        ["bash", str(HOOKS_DIR / "stop-failure.sh")],
        input=json.dumps({"cwd": str(project), **payload}),
        text=True,
        capture_output=True,
        env=env,
        timeout=10,
    )
    assert cp.returncode == 0, cp.stderr


def _read_state(project: Path) -> dict:
    return json.loads((project / ".cc-autopipe" / "state.json").read_text())


def _resume_at_utc(s: dict) -> datetime:
    return datetime.strptime(s["paused"]["resume_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )


def _last_aggregate_line(user_home: Path) -> dict:
    log = (user_home / "log" / "aggregate.jsonl").read_text().strip().splitlines()
    return json.loads(log[-1])


# ---------------------------------------------------------------------------
# Form 1: ISO 8601 timestamp in error_details
# ---------------------------------------------------------------------------


def test_parses_iso_8601_with_z_suffix(tmp_path: Path) -> None:
    user_home = tmp_path / "uhome"
    project = tmp_path / "proj"
    _init_project(project, user_home)

    target = datetime.now(timezone.utc) + timedelta(minutes=10)
    iso = target.strftime("%Y-%m-%dT%H:%M:%SZ")
    _run_stop_failure(
        project,
        _stop_failure_env(user_home),
        {
            "error": "rate_limit",
            "error_details": f"Rate limit exceeded. Resets at {iso}",
        },
    )

    s = _read_state(project)
    assert s["phase"] == "paused"
    assert s["paused"]["reason"] == "rate_limit"
    # +60s safety margin applied to the parsed ISO timestamp.
    expected = target + timedelta(seconds=60)
    delta = abs((_resume_at_utc(s) - expected).total_seconds())
    assert delta < 5, f"resume_at off by {delta}s"

    evt = _last_aggregate_line(user_home)
    assert evt["resolved_via"] == "parsed_message"


def test_parses_iso_8601_with_offset(tmp_path: Path) -> None:
    user_home = tmp_path / "uhome"
    project = tmp_path / "proj"
    _init_project(project, user_home)

    # 2026-05-11T18:10:00+00:00 — explicit offset, no Z.
    target = (datetime.now(timezone.utc) + timedelta(minutes=20)).replace(microsecond=0)
    iso = target.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    _run_stop_failure(
        project,
        _stop_failure_env(user_home),
        {
            "error": "rate_limit",
            "error_details": f"Quota exhausted, resets {iso}",
        },
    )

    s = _read_state(project)
    expected = target + timedelta(seconds=60)
    delta = abs((_resume_at_utc(s) - expected).total_seconds())
    assert delta < 5

    evt = _last_aggregate_line(user_home)
    assert evt["resolved_via"] == "parsed_message"


# ---------------------------------------------------------------------------
# Form 2: Retry-After header
# ---------------------------------------------------------------------------


def test_parses_retry_after_seconds(tmp_path: Path) -> None:
    user_home = tmp_path / "uhome"
    project = tmp_path / "proj"
    _init_project(project, user_home)

    before = datetime.now(timezone.utc)
    _run_stop_failure(
        project,
        _stop_failure_env(user_home),
        {"error": "rate_limit", "error_details": "Retry-After: 1800"},
    )
    after = datetime.now(timezone.utc)

    s = _read_state(project)
    resume = _resume_at_utc(s)
    # 1800s + 60s safety margin = 31 minutes from "now".
    expected_min = before + timedelta(seconds=1800 + 60 - 5)
    expected_max = after + timedelta(seconds=1800 + 60 + 5)
    assert expected_min <= resume <= expected_max, (
        f"resume {resume} not in [{expected_min}, {expected_max}]"
    )

    evt = _last_aggregate_line(user_home)
    assert evt["resolved_via"] == "parsed_message"


def test_parses_xratelimit_reset_seconds(tmp_path: Path) -> None:
    """X-RateLimit-Reset surfaced verbatim by claude CLI."""
    user_home = tmp_path / "uhome"
    project = tmp_path / "proj"
    _init_project(project, user_home)

    before = datetime.now(timezone.utc)
    _run_stop_failure(
        project,
        _stop_failure_env(user_home),
        {"error": "rate_limit", "error_details": "X-RateLimit-Reset: 600"},
    )
    after = datetime.now(timezone.utc)

    s = _read_state(project)
    resume = _resume_at_utc(s)
    expected_min = before + timedelta(seconds=600 + 60 - 5)
    expected_max = after + timedelta(seconds=600 + 60 + 5)
    assert expected_min <= resume <= expected_max

    evt = _last_aggregate_line(user_home)
    assert evt["resolved_via"] == "parsed_message"


# ---------------------------------------------------------------------------
# Form 3: Relative-time prose
# ---------------------------------------------------------------------------


def test_parses_relative_minutes(tmp_path: Path) -> None:
    user_home = tmp_path / "uhome"
    project = tmp_path / "proj"
    _init_project(project, user_home)

    before = datetime.now(timezone.utc)
    _run_stop_failure(
        project,
        _stop_failure_env(user_home),
        {"error": "rate_limit", "error_details": "Please retry after 15 minutes."},
    )
    after = datetime.now(timezone.utc)

    s = _read_state(project)
    resume = _resume_at_utc(s)
    # 15min + 60s safety = 16 minutes
    expected_min = before + timedelta(seconds=15 * 60 + 60 - 5)
    expected_max = after + timedelta(seconds=15 * 60 + 60 + 5)
    assert expected_min <= resume <= expected_max

    evt = _last_aggregate_line(user_home)
    assert evt["resolved_via"] == "parsed_message"


def test_parses_relative_in_seconds(tmp_path: Path) -> None:
    user_home = tmp_path / "uhome"
    project = tmp_path / "proj"
    _init_project(project, user_home)

    before = datetime.now(timezone.utc)
    _run_stop_failure(
        project,
        _stop_failure_env(user_home),
        {"error": "rate_limit", "error_details": "Try again in 600 seconds"},
    )
    after = datetime.now(timezone.utc)

    s = _read_state(project)
    resume = _resume_at_utc(s)
    expected_min = before + timedelta(seconds=600 + 60 - 5)
    expected_max = after + timedelta(seconds=600 + 60 + 5)
    assert expected_min <= resume <= expected_max

    evt = _last_aggregate_line(user_home)
    assert evt["resolved_via"] == "parsed_message"


# ---------------------------------------------------------------------------
# Last-resort 15min fallback (replaces v1.4 1h fallback)
# ---------------------------------------------------------------------------


def test_empty_details_falls_back_to_ladder_15min(tmp_path: Path) -> None:
    """Empty error_details + quota disabled → ratelimit.py register-429
    returns flat 15min (resolved_via=ladder(900 s)). The "last-resort
    15min flat" path with resolved_via=fallback(15min) is hard to reach
    in tests because the ratelimit ladder always succeeds; this test
    documents the ladder-as-fallback behaviour which is functionally
    identical (also 15min, v1.5.0)."""
    user_home = tmp_path / "uhome"
    project = tmp_path / "proj"
    _init_project(project, user_home)

    before = datetime.now(timezone.utc)
    _run_stop_failure(
        project,
        _stop_failure_env(user_home, disable_quota=True),
        {"error": "rate_limit", "error_details": ""},
    )
    after = datetime.now(timezone.utc)

    s = _read_state(project)
    resume = _resume_at_utc(s)
    expected_min = before + timedelta(seconds=900 - 5)
    expected_max = after + timedelta(seconds=900 + 5)
    assert expected_min <= resume <= expected_max, (
        f"resume {resume} not in [{expected_min}, {expected_max}] — expected ~15min"
    )

    evt = _last_aggregate_line(user_home)
    assert re.match(r"ladder\(900\s*s?\)", evt["resolved_via"]), evt


# ---------------------------------------------------------------------------
# Precedence: parsed message wins over quota cache
# ---------------------------------------------------------------------------


def test_parsed_message_wins_over_quota_cache(tmp_path: Path) -> None:
    """When both a parseable retry-after AND a quota cache exist, the
    parsed value wins. The parsed timestamp is 30min in the future; the
    quota cache reset is 4h away — the parsed one is authoritative."""
    user_home = tmp_path / "uhome"
    project = tmp_path / "proj"
    _init_project(project, user_home)

    # Seed quota cache: five_hour reset 4h from now.
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

    target = datetime.now(timezone.utc) + timedelta(minutes=30)
    iso = target.strftime("%Y-%m-%dT%H:%M:%SZ")
    env = _stop_failure_env(user_home, disable_quota=False)
    _run_stop_failure(
        project,
        env,
        {
            "error": "rate_limit",
            "error_details": f"Rate limit exceeded. Resets at {iso}",
        },
    )

    s = _read_state(project)
    expected = target + timedelta(seconds=60)
    resume = _resume_at_utc(s)
    delta = abs((resume - expected).total_seconds())
    # ~30min from now, NOT ~4h from now (quota would have produced).
    assert delta < 10, (
        f"expected parsed-message resume (~30min), got resume {resume} "
        f"(quota would be ~4h, delta {delta}s)"
    )

    evt = _last_aggregate_line(user_home)
    assert evt["resolved_via"] == "parsed_message"


@pytest.mark.parametrize("err_value", ["429", "RATE_LIMIT"])
def test_alternative_error_field_values(err_value: str, tmp_path: Path) -> None:
    """The parse block honours error="429" and error="RATE_LIMIT" in
    addition to the canonical "rate_limit"."""
    user_home = tmp_path / "uhome"
    project = tmp_path / "proj"
    _init_project(project, user_home)

    target = datetime.now(timezone.utc) + timedelta(minutes=10)
    iso = target.strftime("%Y-%m-%dT%H:%M:%SZ")
    _run_stop_failure(
        project,
        _stop_failure_env(user_home),
        {
            "error": err_value,
            "error_details": f"limit reached, resets {iso}",
        },
    )

    s = _read_state(project)
    expected = target + timedelta(seconds=60)
    delta = abs((_resume_at_utc(s) - expected).total_seconds())
    assert delta < 5

    evt = _last_aggregate_line(user_home)
    assert evt["resolved_via"] == "parsed_message"
