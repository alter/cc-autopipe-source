"""Unit tests for src/lib/ratelimit.py.

Covers Stage E DoD items:
- ratelimit.py implements 5min/15min/1h ladder
- ratelimit.py resets counter after 6h with no 429
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
LIB = REPO_ROOT / "src" / "lib"
RATELIMIT_PY = LIB / "ratelimit.py"

sys.path.insert(0, str(LIB))
import ratelimit  # noqa: E402


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    return user_home


# ---------------------------------------------------------------------------
# Ladder progression
# ---------------------------------------------------------------------------


def test_first_call_returns_5min(isolated_home: Path) -> None:
    assert ratelimit.register_429() == 300


def test_ladder_progresses_5_15_60_60(isolated_home: Path) -> None:
    assert ratelimit.register_429() == 300
    assert ratelimit.register_429() == 900
    assert ratelimit.register_429() == 3600
    assert ratelimit.register_429() == 3600  # caps at 1h


def test_state_persisted_between_calls(isolated_home: Path) -> None:
    ratelimit.register_429()
    ratelimit.register_429()
    state = ratelimit.load_state()
    assert state["count"] == 2
    assert state["last_429_ts"] > 0


def test_load_state_defaults_when_missing(isolated_home: Path) -> None:
    state = ratelimit.load_state()
    assert state["count"] == 0
    assert state["last_429_ts"] == 0.0


def test_load_state_recovers_from_corrupt(isolated_home: Path) -> None:
    isolated_home.mkdir(parents=True, exist_ok=True)
    (isolated_home / "ratelimit.json").write_text("{not valid json")
    state = ratelimit.load_state()
    assert state == {"count": 0, "last_429_ts": 0.0}


def test_load_state_coerces_bad_types(isolated_home: Path) -> None:
    isolated_home.mkdir(parents=True, exist_ok=True)
    (isolated_home / "ratelimit.json").write_text(
        json.dumps({"count": "abc", "last_429_ts": "huh"})
    )
    state = ratelimit.load_state()
    assert state["count"] == 0
    assert state["last_429_ts"] == 0.0


# ---------------------------------------------------------------------------
# 6h reset window
# ---------------------------------------------------------------------------


def test_reset_after_6h_with_no_429(isolated_home: Path) -> None:
    """If the last 429 was >6h ago, the next call starts fresh at 5min."""
    ratelimit.register_429()
    ratelimit.register_429()
    ratelimit.register_429()
    # Push the last_429_ts back by 7h.
    state = ratelimit.load_state()
    state["last_429_ts"] -= 25200
    ratelimit.save_state(state)
    assert ratelimit.register_429() == 300


def test_reset_does_not_trigger_at_5h(isolated_home: Path) -> None:
    """Reset window is 6h — at 5h, the ladder still progresses."""
    ratelimit.register_429()
    state = ratelimit.load_state()
    state["last_429_ts"] -= 18000  # 5h
    ratelimit.save_state(state)
    assert ratelimit.register_429() == 900  # still 2nd step


# ---------------------------------------------------------------------------
# get_resume_at
# ---------------------------------------------------------------------------


def test_get_resume_at_prefers_quota(isolated_home: Path) -> None:
    quota_resets = datetime(2026, 5, 1, 18, 30, 0, tzinfo=timezone.utc)
    resume = ratelimit.get_resume_at(quota_resume_at=quota_resets)
    # SPEC §9.4 demands a 60s safety margin.
    assert resume == quota_resets + timedelta(seconds=60)
    # And the ladder state was NOT advanced.
    assert ratelimit.load_state()["count"] == 0


def test_get_resume_at_falls_back_to_ladder(isolated_home: Path) -> None:
    before = datetime.now(timezone.utc)
    resume = ratelimit.get_resume_at(quota_resume_at=None)
    after = datetime.now(timezone.utc)
    delta = (resume - before).total_seconds()
    # First step is 5min ± a few seconds for test execution time.
    assert 295 <= delta <= 305
    assert resume.tzinfo is not None
    # Ladder state advanced.
    assert ratelimit.load_state()["count"] == 1
    # And `after` is sane.
    assert after >= before


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _run_cli(
    user_home: Path, cmd: str, *, expect_rc: int | None = None
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["CC_AUTOPIPE_USER_HOME"] = str(user_home)
    cp = subprocess.run(
        [sys.executable, str(RATELIMIT_PY), cmd],
        capture_output=True,
        text=True,
        env=env,
    )
    if expect_rc is not None:
        assert cp.returncode == expect_rc, cp.stderr
    return cp


def test_cli_register_429_advances_ladder(tmp_path: Path) -> None:
    home = tmp_path / "uhome"
    cp1 = _run_cli(home, "register-429", expect_rc=0)
    cp2 = _run_cli(home, "register-429", expect_rc=0)
    cp3 = _run_cli(home, "register-429", expect_rc=0)
    assert cp1.stdout.strip() == "300"
    assert cp2.stdout.strip() == "900"
    assert cp3.stdout.strip() == "3600"


def test_cli_state_returns_json(tmp_path: Path) -> None:
    home = tmp_path / "uhome"
    _run_cli(home, "register-429", expect_rc=0)
    cp = _run_cli(home, "state", expect_rc=0)
    state = json.loads(cp.stdout)
    assert state["count"] == 1
    assert state["last_429_ts"] > 0


def test_cli_reset_zeros_counter(tmp_path: Path) -> None:
    home = tmp_path / "uhome"
    _run_cli(home, "register-429", expect_rc=0)
    _run_cli(home, "register-429", expect_rc=0)
    _run_cli(home, "reset", expect_rc=0)
    cp = _run_cli(home, "state", expect_rc=0)
    state = json.loads(cp.stdout)
    assert state["count"] == 0
    assert state["last_429_ts"] == 0.0


def test_cli_unknown_subcommand_rc2(tmp_path: Path) -> None:
    home = tmp_path / "uhome"
    cp = _run_cli(home, "no-such-cmd")
    assert cp.returncode == 2
