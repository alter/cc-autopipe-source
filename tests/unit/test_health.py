"""Unit tests for src/lib/health.py — PROMPT_v1.3-FULL.md F2."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_LIB = REPO_ROOT / "src" / "lib"
sys.path.insert(0, str(SRC_LIB))

import health  # noqa: E402


def test_emit_appends_record(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    health.emit_cycle_health(
        project_name="alpha",
        iteration=3,
        phase="active",
        five_hour_pct=0.30,
        seven_day_pct=0.50,
        disk_free_gb=45.2,
    )
    p = tmp_path / "uhome" / "log" / "health.jsonl"
    assert p.exists()
    rec = json.loads(p.read_text().strip())
    assert rec["project"] == "alpha"
    assert rec["iteration"] == 3
    assert rec["phase"] == "active"
    assert rec["5h_pct"] == 0.3
    assert rec["7d_pct"] == 0.5
    assert rec["disk_free_gb"] == 45.2


def test_emit_skips_optional_fields(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    health.emit_cycle_health(project_name="x", iteration=1, phase="active")
    rec = json.loads(
        (tmp_path / "uhome" / "log" / "health.jsonl").read_text().strip()
    )
    assert rec["project"] == "x"
    assert "5h_pct" not in rec
    assert "disk_free_gb" not in rec


def test_read_recent_returns_within_window(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    p = tmp_path / "uhome" / "log" / "health.jsonl"
    p.parent.mkdir(parents=True)
    # Hand-write one stale + one fresh record.
    p.write_text(
        '{"ts": "2020-01-01T00:00:00Z", "project": "old"}\n'
        + json.dumps({"ts": health._now_iso(), "project": "new"})
        + "\n"
    )
    recs = health.read_recent_health(since_seconds=60)
    assert len(recs) == 1
    assert recs[0]["project"] == "new"


def test_read_recent_handles_malformed_lines(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    p = tmp_path / "uhome" / "log" / "health.jsonl"
    p.parent.mkdir(parents=True)
    p.write_text(
        "garbage\n"
        + json.dumps({"ts": health._now_iso(), "project": "ok"})
        + "\n"
        + "{ bad\n"
    )
    recs = health.read_recent_health()
    assert len(recs) == 1


def test_summarise_groups_by_project(tmp_path: Path) -> None:
    recs = [
        {"ts": "2026-05-04T00:00:00Z", "project": "a", "phase": "active",
         "5h_pct": 0.1, "7d_pct": 0.2},
        {"ts": "2026-05-04T00:01:00Z", "project": "a", "phase": "active",
         "5h_pct": 0.15},
        {"ts": "2026-05-04T00:02:00Z", "project": "b", "phase": "paused"},
    ]
    summary = health.summarise(recs)
    assert summary["total_records"] == 3
    assert summary["by_project"]["a"]["cycles"] == 2
    assert summary["by_project"]["b"]["cycles"] == 1


def test_cli_smoke(tmp_path: Path, monkeypatch) -> None:
    """`cc-autopipe health` produces output without raising."""
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    health.emit_cycle_health(project_name="x", iteration=1, phase="active")
    import subprocess

    cp = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "src" / "cli" / "health.py"),
        ],
        capture_output=True,
        text=True,
        env={**__import__("os").environ},
    )
    assert cp.returncode == 0
    # Subprocess inherits CC_AUTOPIPE_USER_HOME via test env. Output
    # may be "no health records in window" in rare timing edge cases —
    # both cases acceptable, just check it didn't crash.
