"""Unit tests for src/lib/ratelimit.py.

v1.5.0: ladder collapsed to flat 15min. Pre-v1.5.0 covered the
5min/15min/1h escalating ladder plus 6h reset window; both removed.
register_429() now always returns FALLBACK_WAIT_SEC=900 regardless of
count or elapsed time. count + last_429_ts are still persisted for
postmortem audit but no longer feed the wait calculation.
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
# Flat 15min fallback (v1.5.0)
# ---------------------------------------------------------------------------


def test_first_call_returns_15min(isolated_home: Path) -> None:
    assert ratelimit.register_429() == ratelimit.FALLBACK_WAIT_SEC == 900


def test_register_429_flat_across_many_calls(isolated_home: Path) -> None:
    """Five 429s in succession all return the same flat 15min."""
    for _ in range(5):
        assert ratelimit.register_429() == 900


def test_register_429_flat_after_long_gap(isolated_home: Path) -> None:
    """v1.5.0 has no 6h reset window — flat is flat. Push last_429_ts back
    by a week; the next call still returns 900, not anything else."""
    ratelimit.register_429()
    state = ratelimit.load_state()
    state["last_429_ts"] -= 7 * 86400  # one week ago
    ratelimit.save_state(state)
    assert ratelimit.register_429() == 900


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


def test_no_ladder_or_reset_constants() -> None:
    """v1.5.0: LADDER_SEC and RESET_AFTER_SEC constants removed."""
    assert not hasattr(ratelimit, "LADDER_SEC")
    assert not hasattr(ratelimit, "RESET_AFTER_SEC")
    assert ratelimit.FALLBACK_WAIT_SEC == 900


# ---------------------------------------------------------------------------
# get_resume_at
# ---------------------------------------------------------------------------


def test_get_resume_at_prefers_quota(isolated_home: Path) -> None:
    quota_resets = datetime(2026, 5, 1, 18, 30, 0, tzinfo=timezone.utc)
    resume = ratelimit.get_resume_at(quota_resume_at=quota_resets)
    # SPEC §9.4 demands a 60s safety margin.
    assert resume == quota_resets + timedelta(seconds=60)
    # And the audit state was NOT advanced.
    assert ratelimit.load_state()["count"] == 0


def test_get_resume_at_falls_back_to_flat_15min(isolated_home: Path) -> None:
    before = datetime.now(timezone.utc)
    resume = ratelimit.get_resume_at(quota_resume_at=None)
    delta = (resume - before).total_seconds()
    # v1.5.0 flat 15min ± a few seconds for test execution time.
    assert 895 <= delta <= 905
    assert resume.tzinfo is not None
    # Audit state advanced.
    assert ratelimit.load_state()["count"] == 1


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


def test_cli_register_429_returns_flat_15min(tmp_path: Path) -> None:
    """v1.5.0: CLI register-429 returns 900 every time, regardless of count."""
    home = tmp_path / "uhome"
    cp1 = _run_cli(home, "register-429", expect_rc=0)
    cp2 = _run_cli(home, "register-429", expect_rc=0)
    cp3 = _run_cli(home, "register-429", expect_rc=0)
    assert cp1.stdout.strip() == "900"
    assert cp2.stdout.strip() == "900"
    assert cp3.stdout.strip() == "900"


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
