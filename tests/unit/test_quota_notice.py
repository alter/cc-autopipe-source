"""Unit tests for build_quota_notice_block — PROMPT_v1.3-FULL.md GROUP E."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_LIB = REPO_ROOT / "src" / "lib"
sys.path.insert(0, str(SRC_LIB))

import session_start_helper  # noqa: E402


def _patch_quota(monkeypatch, pct: float | None) -> None:
    """Patch the lazy quota probe to return (pct, resets_at)."""
    if pct is None:
        monkeypatch.setattr(
            session_start_helper, "_read_quota_pct", lambda: (None, None)
        )
    else:
        resets = datetime(2026, 5, 12, tzinfo=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        monkeypatch.setattr(
            session_start_helper, "_read_quota_pct", lambda: (pct, resets)
        )


def test_block_empty_when_pct_unknown(monkeypatch) -> None:
    _patch_quota(monkeypatch, None)
    assert session_start_helper.build_quota_notice_block() == ""


def test_block_empty_when_below_60(monkeypatch) -> None:
    _patch_quota(monkeypatch, 0.45)
    assert session_start_helper.build_quota_notice_block() == ""


def test_block_notice_60_to_80(monkeypatch) -> None:
    _patch_quota(monkeypatch, 0.65)
    block = session_start_helper.build_quota_notice_block()
    assert "QUOTA NOTICE" in block
    assert "65%" in block
    assert "Continue normally" in block


def test_block_high_80_to_95(monkeypatch) -> None:
    _patch_quota(monkeypatch, 0.85)
    block = session_start_helper.build_quota_notice_block()
    assert "QUOTA HIGH" in block
    assert "85%" in block
    assert "avoid starting new" in block


def test_block_critical_95_plus(monkeypatch) -> None:
    _patch_quota(monkeypatch, 0.97)
    block = session_start_helper.build_quota_notice_block()
    assert "QUOTA CRITICAL" in block
    assert "97%" in block
    assert "VERDICT MODE ONLY" in block


def test_block_at_exact_60_emits_notice(monkeypatch) -> None:
    """Boundary check: 60% should emit NOTICE (>=60), not be empty."""
    _patch_quota(monkeypatch, 0.60)
    block = session_start_helper.build_quota_notice_block()
    assert "QUOTA NOTICE" in block


def test_full_block_includes_quota(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    project = tmp_path / "demo"
    (project / ".cc-autopipe").mkdir(parents=True)
    _patch_quota(monkeypatch, 0.85)
    block = session_start_helper.build_full_block(project)
    assert "QUOTA HIGH" in block
