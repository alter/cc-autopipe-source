"""v1.5.8 STALE-PROMOTION-REJECTED unit tests.

`backlog_gate.audit_and_revert` must accept a PROMOTION file as proof
of a [x] closure only when the file is fresh relative to the snapshot
mtime (with a 60s grace for the legitimate close-race where the agent
writes PROMOTION and flips [x] within the same cycle).

These three cases pin down the three outcomes:
  1. PROMOTION mtime > snapshot mtime → fresh, accepted.
  2. PROMOTION mtime < snapshot mtime - grace → stale, reverted.
  3. PROMOTION mtime = snapshot mtime - half-grace → still fresh.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
LIB = SRC / "lib"
for p in (str(SRC), str(LIB)):
    if p not in sys.path:
        sys.path.insert(0, p)

backlog_gate = importlib.import_module("orchestrator.backlog_gate")


def _project(tmp_path: Path) -> Path:
    p = tmp_path / "demo"
    (p / ".cc-autopipe").mkdir(parents=True)
    (p / "data" / "debug").mkdir(parents=True)
    return p


def _user_home(tmp_path: Path) -> Path:
    home = tmp_path / "uhome"
    (home / "log").mkdir(parents=True)
    return home


def _read_aggregate(user_home: Path) -> list[dict]:
    log = user_home / "log" / "aggregate.jsonl"
    if not log.exists():
        return []
    return [json.loads(ln) for ln in log.read_text().splitlines() if ln.strip()]


def _set_mtime(path: Path, mtime: float) -> None:
    os.utime(path, (mtime, mtime))


def test_promotion_newer_than_snapshot_is_fresh(
    tmp_path: Path, monkeypatch
) -> None:
    """Snapshot says [ ], current says [x], PROMOTION mtime > snapshot
    mtime → ok_verified, no revert."""
    home = _user_home(tmp_path)
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(home))
    p = _project(tmp_path)

    snap = p / ".cc-autopipe" / "backlog_snapshot.md"
    snap.write_text(
        "- [ ] [implement] [P0] vec_fresh — do thing\n", encoding="utf-8"
    )
    base = time.time()
    _set_mtime(snap, base - 600)  # snapshot 10 min ago

    (p / "backlog.md").write_text(
        "- [x] [implement] [P0] vec_fresh — do thing\n", encoding="utf-8"
    )
    pfile = p / "data" / "debug" / "CAND_vec_fresh_PROMOTION.md"
    pfile.write_text("**Task:** vec_fresh\n**Verdict:** PROMOTED\n", encoding="utf-8")
    _set_mtime(pfile, base - 60)  # written 1 min ago — well after snapshot

    counts = backlog_gate.audit_and_revert(p, home)

    assert counts["scanned"] == 1
    assert counts["ok_verified"] == 1
    assert counts["reverted"] == 0
    body = (p / "backlog.md").read_text()
    assert "- [x] [implement] [P0] vec_fresh" in body
    events = [
        e for e in _read_aggregate(home)
        if e.get("event") == "unverified_close_blocked"
    ]
    assert events == []


def test_promotion_older_than_snapshot_is_stale_and_reverted(
    tmp_path: Path, monkeypatch
) -> None:
    """Snapshot says [ ], current says [x], PROMOTION mtime < snapshot
    mtime - grace → reverted, event carries stale=True."""
    home = _user_home(tmp_path)
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(home))
    p = _project(tmp_path)

    snap = p / ".cc-autopipe" / "backlog_snapshot.md"
    snap.write_text(
        "- [ ] [implement] [P0] vec_stale — re-closed without work\n",
        encoding="utf-8",
    )
    base = time.time()
    _set_mtime(snap, base - 60)  # snapshot ~now

    (p / "backlog.md").write_text(
        "- [x] [implement] [P0] vec_stale — re-closed without work\n",
        encoding="utf-8",
    )
    pfile = p / "data" / "debug" / "CAND_vec_stale_PROMOTION.md"
    pfile.write_text(
        "**Task:** vec_stale\n**Verdict:** PROMOTED\n", encoding="utf-8"
    )
    # Stub from a prior fabrication run: 7 days old, well past the 60s grace.
    _set_mtime(pfile, base - 7 * 86400)

    counts = backlog_gate.audit_and_revert(p, home)

    assert counts["scanned"] == 1
    assert counts["reverted"] == 1
    assert counts["ok_verified"] == 0
    body = (p / "backlog.md").read_text()
    assert "- [ ] [implement] [P0] vec_stale" in body
    assert "- [x] [implement] [P0] vec_stale" not in body
    events = [
        e for e in _read_aggregate(home)
        if e.get("event") == "unverified_close_blocked"
    ]
    assert len(events) == 1
    assert events[0]["task_id"] == "vec_stale"
    assert events[0]["stale"] is True
    assert "stale" in events[0]["reason"].lower()


def test_promotion_within_grace_window_is_fresh(
    tmp_path: Path, monkeypatch
) -> None:
    """Race-condition tolerance: PROMOTION mtime = snapshot mtime - 30s
    (within the 60s grace) is still accepted as fresh evidence."""
    home = _user_home(tmp_path)
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(home))
    p = _project(tmp_path)

    snap = p / ".cc-autopipe" / "backlog_snapshot.md"
    snap.write_text(
        "- [ ] [implement] [P0] vec_race — close-race case\n",
        encoding="utf-8",
    )
    base = time.time()
    _set_mtime(snap, base)

    (p / "backlog.md").write_text(
        "- [x] [implement] [P0] vec_race — close-race case\n",
        encoding="utf-8",
    )
    pfile = p / "data" / "debug" / "CAND_vec_race_PROMOTION.md"
    pfile.write_text(
        "**Task:** vec_race\n**Verdict:** PROMOTED\n", encoding="utf-8"
    )
    # Slightly older than the snapshot but inside the grace window.
    _set_mtime(pfile, base - 30)

    counts = backlog_gate.audit_and_revert(p, home)

    assert counts["scanned"] == 1
    assert counts["ok_verified"] == 1
    assert counts["reverted"] == 0
    body = (p / "backlog.md").read_text()
    assert "- [x] [implement] [P0] vec_race" in body
    events = [
        e for e in _read_aggregate(home)
        if e.get("event") == "unverified_close_blocked"
    ]
    assert events == []
