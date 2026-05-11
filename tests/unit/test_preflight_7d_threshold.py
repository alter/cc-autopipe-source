"""v1.5.0: 7d preflight thresholds bumped 0.95→0.98 (pause), 0.90→0.95 (warn).

Tests the new ladder against the bumped thresholds. Pre-v1.5.0 versions
of these assertions live in tests/integration/test_orchestrator_quota.py
but exercise the orchestrator end-to-end; this file is a focused unit
test against the threshold values themselves.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
for d in (SRC, SRC / "lib", SRC / "orchestrator"):
    if str(d) not in sys.path:
        sys.path.insert(0, str(d))

import quota as quota_lib  # noqa: E402
import state  # noqa: E402
from orchestrator.preflight import _preflight_quota  # noqa: E402


def _make_quota(seven_day: float) -> quota_lib.Quota:
    now = datetime.now(timezone.utc)
    return quota_lib.Quota(
        five_hour_pct=0.10,
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


def test_7d_at_0_94_is_ok(tmp_path: Path) -> None:
    """Below warn threshold — no warning, no pause."""
    s = _make_state()
    with patch.object(quota_lib, "read_cached", return_value=_make_quota(0.94)):
        result = _preflight_quota(tmp_path, s)
    assert result == "ok"
    assert s.phase == "active"


def test_7d_at_0_95_is_warn(tmp_path: Path) -> None:
    """At warn threshold — warn but proceed."""
    s = _make_state()
    with patch.object(quota_lib, "read_cached", return_value=_make_quota(0.95)):
        result = _preflight_quota(tmp_path, s)
    assert result == "warn_7d"
    assert s.phase == "active"


def test_7d_at_0_97_is_warn(tmp_path: Path) -> None:
    """Between warn and pause — still proceeds (was paused pre-v1.5.0)."""
    s = _make_state()
    with patch.object(quota_lib, "read_cached", return_value=_make_quota(0.97)):
        result = _preflight_quota(tmp_path, s)
    assert result == "warn_7d"
    assert s.phase == "active"


def test_7d_at_0_98_is_paused(tmp_path: Path) -> None:
    """At pause threshold — paused (was warn pre-v1.5.0)."""
    s = _make_state()
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    (project_dir / ".cc-autopipe").mkdir()
    with (
        patch.object(quota_lib, "read_cached", return_value=_make_quota(0.98)),
        patch("orchestrator.preflight._should_send_7d_alert", return_value=False),
        patch("orchestrator.preflight._notify_tg"),
    ):
        result = _preflight_quota(project_dir, s)
    assert result == "paused_7d"
    assert s.phase == "paused"


def test_7d_at_0_99_is_paused(tmp_path: Path) -> None:
    s = _make_state()
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    (project_dir / ".cc-autopipe").mkdir()
    with (
        patch.object(quota_lib, "read_cached", return_value=_make_quota(0.99)),
        patch("orchestrator.preflight._should_send_7d_alert", return_value=False),
        patch("orchestrator.preflight._notify_tg"),
    ):
        result = _preflight_quota(project_dir, s)
    assert result == "paused_7d"
    assert s.phase == "paused"
