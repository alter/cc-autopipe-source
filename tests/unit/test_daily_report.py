"""Unit tests for orchestrator.daily_report — PROMPT_v1.3-FULL.md F1."""

from __future__ import annotations

import importlib
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
LIB = SRC / "lib"
for p in (str(SRC), str(LIB)):
    if p not in sys.path:
        sys.path.insert(0, p)

import state  # noqa: E402

daily_report = importlib.import_module("orchestrator.daily_report")


def _project(tmp_path: Path) -> Path:
    p = tmp_path / "demo"
    (p / ".cc-autopipe").mkdir(parents=True)
    return p


def _seed_aggregate(user_home: Path, project: str, events: list[dict]) -> None:
    p = user_home / "log" / "aggregate.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


def test_render_minimal(tmp_path: Path, monkeypatch) -> None:
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    p = _project(tmp_path)
    s = state.State.fresh(p.name)
    state.write(p, s)

    body = daily_report.render_daily_report(p)
    assert body.startswith("# Daily summary")
    assert "Cycles" in body
    assert "Phase: active" in body


def test_counts_cycles_and_recoveries(tmp_path: Path, monkeypatch) -> None:
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    p = _project(tmp_path)
    s = state.State.fresh(p.name)
    state.write(p, s)

    today = datetime.now(timezone.utc).date().isoformat()
    _seed_aggregate(
        user_home,
        p.name,
        [
            {"ts": f"{today}T01:00:00Z", "project": p.name, "event": "cycle_start"},
            {
                "ts": f"{today}T01:01:00Z",
                "project": p.name,
                "event": "cycle_end",
                "rc": 0,
            },
            {"ts": f"{today}T02:00:00Z", "project": p.name, "event": "cycle_start"},
            {
                "ts": f"{today}T02:01:00Z",
                "project": p.name,
                "event": "cycle_end",
                "rc": 1,
            },
            {
                "ts": f"{today}T03:00:00Z",
                "project": p.name,
                "event": "auto_recovery_attempted",
            },
        ],
    )

    body = daily_report.render_daily_report(p)
    assert "Total: 2" in body
    assert "Successful (rc=0): 1" in body
    assert "Failed: 1" in body
    assert "Auto-recoveries: 1" in body


def test_findings_for_today_listed(tmp_path: Path, monkeypatch) -> None:
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    p = _project(tmp_path)
    today = datetime.now(timezone.utc).date().isoformat()
    f = p / ".cc-autopipe" / "findings_index.md"
    f.write_text(
        f"## {today}T00:00:00Z | vec_a | stage_e_verdict\n"
        "- **Notes:** REJECT — bla\n"
        "## 2026-01-01T00:00:00Z | vec_old | s\n"
        "- **Notes:** old\n"
    )
    body = daily_report.render_daily_report(p)
    assert "vec_a" in body
    assert "vec_old" not in body  # different day


def test_write_creates_file(tmp_path: Path, monkeypatch) -> None:
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    p = _project(tmp_path)
    out = daily_report.write_daily_report(p)
    assert out is not None
    assert out.exists()
    today = datetime.now(timezone.utc).date().isoformat()
    assert out.name == f"daily_{today}.md"


def test_write_returns_none_for_uninit_project(tmp_path: Path) -> None:
    """An uninit project (no .cc-autopipe/) must NOT have one created
    just to host a daily report — that would mask uninit state."""
    p = tmp_path / "bare"
    p.mkdir()
    assert daily_report.write_daily_report(p) is None
    assert not (p / ".cc-autopipe").exists()


def test_maybe_write_skips_within_24h(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    p = _project(tmp_path)
    import time as _t

    now = _t.time()
    last, written = daily_report.maybe_write_for_all([p], now - 60, now)
    assert written == []
    assert last == now - 60  # unchanged


def test_maybe_write_runs_when_24h_elapsed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    p = _project(tmp_path)
    import time as _t

    now = _t.time()
    last, written = daily_report.maybe_write_for_all([p], 0, now)
    assert len(written) == 1
    assert last == now
