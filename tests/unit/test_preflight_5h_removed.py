"""v1.5.0: 5h pre-check branch removed from orchestrator.preflight.

These tests assert the INVERSE of the pre-v1.5.0 behaviour: 5h
saturation alone (without 7d also tripping) MUST NOT pause the
project. The engine now relies on Claude CLI's 429 responses,
handled reactively in src/hooks/stop-failure.sh + ratelimit.py.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
for d in (SRC, SRC / "lib", SRC / "orchestrator"):
    if str(d) not in sys.path:
        sys.path.insert(0, str(d))

import quota as quota_lib  # noqa: E402
import state  # noqa: E402
from orchestrator.preflight import (  # noqa: E402
    PREFLIGHT_7D_PAUSE,
    PREFLIGHT_7D_WARN,
    _preflight_quota,
)


def _make_quota(*, five_hour: float, seven_day: float) -> quota_lib.Quota:
    now = datetime.now(timezone.utc)
    return quota_lib.Quota(
        five_hour_pct=five_hour,
        five_hour_resets_at=now + timedelta(hours=4),
        seven_day_pct=seven_day,
        seven_day_resets_at=now + timedelta(days=6),
    )


def _make_state() -> state.State:
    return state.State(
        schema_version=state.SCHEMA_VERSION,
        phase="active",
        iteration=0,
    )


def test_5h_at_100pct_with_low_7d_does_not_pause(tmp_path: Path) -> None:
    """v1.4.1 would have returned "paused_5h" here. v1.5.0 returns "ok"."""
    s = _make_state()
    q = _make_quota(five_hour=1.0, seven_day=0.40)
    with patch.object(quota_lib, "read_cached", return_value=q):
        result = _preflight_quota(tmp_path, s)
    assert result == "ok"
    assert s.phase == "active"
    assert s.paused is None


def test_5h_at_99pct_with_low_7d_does_not_pause(tmp_path: Path) -> None:
    s = _make_state()
    q = _make_quota(five_hour=0.99, seven_day=0.10)
    with patch.object(quota_lib, "read_cached", return_value=q):
        result = _preflight_quota(tmp_path, s)
    assert result == "ok"
    assert s.phase == "active"


def test_5h_saturation_does_not_block_7d_pause(tmp_path: Path) -> None:
    """When 7d is also at-or-above the pause threshold, 7d still wins.
    The 5h component is simply ignored — 7d threshold dominates."""
    s = _make_state()
    q = _make_quota(five_hour=1.0, seven_day=0.99)
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    (project_dir / ".cc-autopipe").mkdir()
    with (
        patch.object(quota_lib, "read_cached", return_value=q),
        patch("orchestrator.preflight._should_send_7d_alert", return_value=False),
        patch("orchestrator.preflight._notify_tg"),
    ):
        result = _preflight_quota(project_dir, s)
    assert result == "paused_7d"
    assert s.phase == "paused"
    assert s.paused is not None
    assert s.paused.reason == "7d_pre_check"


def test_no_5h_constants_in_module() -> None:
    """v1.5.0: PREFLIGHT_5H_PAUSE / PREFLIGHT_5H_WARN constants removed."""
    import orchestrator.preflight as pf

    assert not hasattr(pf, "PREFLIGHT_5H_PAUSE")
    assert not hasattr(pf, "PREFLIGHT_5H_WARN")


@pytest.mark.parametrize("five_hour", [0.0, 0.50, 0.85, 0.95, 1.0])
def test_5h_at_any_level_with_safe_7d_proceeds(
    five_hour: float, tmp_path: Path
) -> None:
    """5h utilization no longer triggers any state change."""
    s = _make_state()
    q = _make_quota(five_hour=five_hour, seven_day=0.30)
    with patch.object(quota_lib, "read_cached", return_value=q):
        result = _preflight_quota(tmp_path, s)
    assert result == "ok"
    assert s.phase == "active"


def test_preflight_thresholds_are_v15_values() -> None:
    """v1.5.0: 7d pause bumped 0.95 → 0.98, warn 0.90 → 0.95."""
    assert PREFLIGHT_7D_PAUSE == 0.98
    assert PREFLIGHT_7D_WARN == 0.95
