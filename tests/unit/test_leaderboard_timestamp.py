"""Unit tests for v1.5.3 LEADERBOARD-TIMESTAMP-FIX.

`Last updated:` line must refresh on every append regardless of
verdict or composite change, not just on PROMOTED outcomes.
"""

from __future__ import annotations

import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_LIB = REPO_ROOT / "src" / "lib"
sys.path.insert(0, str(SRC_LIB))

import leaderboard as lb  # noqa: E402


_LAST_UPDATED_RE = re.compile(r"^Last updated: (.+)$", re.MULTILINE)


def _seed_user_home(monkeypatch, tmp_path: Path) -> None:
    user_home = tmp_path / "uhome"
    (user_home / "log").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))


def _read_timestamp(project: Path) -> datetime:
    text = (project / "data" / "debug" / "LEADERBOARD.md").read_text()
    m = _LAST_UPDATED_RE.search(text)
    assert m, "Last updated: line missing"
    return datetime.fromisoformat(m.group(1))


def test_last_updated_refreshes_on_every_append(
    tmp_path: Path, monkeypatch
) -> None:
    _seed_user_home(monkeypatch, tmp_path)
    project = tmp_path / "p"
    project.mkdir()
    before = datetime.now(timezone.utc)
    lb.append_entry(
        project,
        "task_neutral",
        {
            "verdict": "NEUTRAL",
            "sum_fixed": 50.0,
            "regime_parity": 0.4,
            "max_dd": -12.0,
        },
    )
    after = datetime.now(timezone.utc)
    ts = _read_timestamp(project)
    assert before <= ts <= after, (
        f"timestamp {ts} not in [{before}, {after}] window"
    )


def test_consecutive_appends_advance_last_updated(
    tmp_path: Path, monkeypatch
) -> None:
    _seed_user_home(monkeypatch, tmp_path)
    project = tmp_path / "p"
    project.mkdir()
    lb.append_entry(
        project,
        "task_first",
        {"sum_fixed": 100.0, "regime_parity": 0.3, "max_dd": -10.0},
    )
    ts_first = _read_timestamp(project)
    time.sleep(0.05)
    lb.append_entry(
        project,
        "task_second",
        {"sum_fixed": 200.0, "regime_parity": 0.2, "max_dd": -8.0},
    )
    ts_second = _read_timestamp(project)
    assert ts_second > ts_first, (
        f"second timestamp {ts_second} did not advance past first {ts_first}"
    )
