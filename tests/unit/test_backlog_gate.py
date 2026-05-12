"""v1.5.7 BACKLOG-WRITE-GATE: physical guarantee that `[x]` in
`backlog.md` requires a `verify_completed task_id=X passed=true` event
in the user-home aggregate.jsonl OR a matching
`data/debug/CAND_<task>_PROMOTION.md` file on disk.

AI-trade audit 2026-05-13 found 947/953 closed tasks lacked engine-side
verification — subagents had written `[x]` via Edit/MultiEdit/Write,
bypassing the verify pipeline. The gate closes that gap structurally.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
LIB = SRC / "lib"
for p in (str(SRC), str(LIB)):
    if p not in sys.path:
        sys.path.insert(0, p)

import state  # noqa: E402

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


def _seed_verify_event(project: Path, task_id: str, passed: bool = True) -> None:
    """Emit a verify_completed event via state.log_event so the
    serialised line matches the engine's compact-JSON convention
    (`separators=(",",":")`)."""
    state.log_event(project, "verify_completed", task_id=task_id, passed=passed)


def _read_aggregate(user_home: Path) -> list[dict]:
    log = user_home / "log" / "aggregate.jsonl"
    if not log.exists():
        return []
    return [json.loads(ln) for ln in log.read_text().splitlines() if ln.strip()]


def test_new_close_with_verify_event_is_ok(
    tmp_path: Path, monkeypatch
) -> None:
    """A NEW `[x]` transition backed by a `verify_completed
    passed=true` event in aggregate.jsonl is accepted: ok_verified
    counter ticks, the row is NOT rewritten, no
    `unverified_close_blocked` event is emitted."""
    home = _user_home(tmp_path)
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(home))
    p = _project(tmp_path)

    # Snapshot says the row was `[ ]` last sweep.
    (p / ".cc-autopipe" / "backlog_snapshot.md").write_text(
        "- [ ] [implement] [P0] vec_alpha — do thing\n",
        encoding="utf-8",
    )
    # Current backlog: the row is now `[x]`.
    (p / "backlog.md").write_text(
        "- [x] [implement] [P0] vec_alpha — do thing\n",
        encoding="utf-8",
    )
    _seed_verify_event(p, "vec_alpha", passed=True)

    counts = backlog_gate.audit_and_revert(p, home)

    assert counts["scanned"] == 1
    assert counts["ok_verified"] == 1
    assert counts["reverted"] == 0
    assert counts["ok_orphan_pre_v157"] == 0
    # Backlog row is still [x] — no revert.
    assert "- [x] [implement] [P0] vec_alpha" in (p / "backlog.md").read_text()
    # No revert event.
    events = [
        e for e in _read_aggregate(home)
        if e.get("event") == "unverified_close_blocked"
    ]
    assert events == []


def test_new_close_with_promotion_file_is_ok(
    tmp_path: Path, monkeypatch
) -> None:
    """A NEW `[x]` transition with no verify event but with a matching
    `data/debug/CAND_<task>_PROMOTION.md` is accepted (covers the AI-
    trade case where the engine wrote PROMOTION but verify_completed
    was never emitted)."""
    home = _user_home(tmp_path)
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(home))
    p = _project(tmp_path)

    (p / ".cc-autopipe" / "backlog_snapshot.md").write_text(
        "- [ ] [implement] [P0] vec_beta — analyse\n",
        encoding="utf-8",
    )
    (p / "backlog.md").write_text(
        "- [x] [implement] [P0] vec_beta — analyse\n",
        encoding="utf-8",
    )
    # No verify event seeded; PROMOTION file exists.
    (p / "data" / "debug" / "CAND_vec_beta_PROMOTION.md").write_text(
        "**Task:** vec_beta\n**Verdict:** PROMOTED\n",
        encoding="utf-8",
    )

    counts = backlog_gate.audit_and_revert(p, home)

    assert counts["scanned"] == 1
    assert counts["ok_verified"] == 1
    assert counts["reverted"] == 0
    assert "- [x] [implement] [P0] vec_beta" in (p / "backlog.md").read_text()


def test_new_close_without_proof_is_reverted_and_logged(
    tmp_path: Path, monkeypatch
) -> None:
    """A NEW `[x]` transition with NEITHER verify event NOR PROMOTION
    file is reverted to `[ ]` and an `unverified_close_blocked` event
    is appended to aggregate.jsonl with the offending task_id."""
    home = _user_home(tmp_path)
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(home))
    p = _project(tmp_path)

    (p / ".cc-autopipe" / "backlog_snapshot.md").write_text(
        "- [ ] [implement] [P0] vec_stub — fabricated closure\n",
        encoding="utf-8",
    )
    (p / "backlog.md").write_text(
        "- [x] [implement] [P0] vec_stub — fabricated closure\n",
        encoding="utf-8",
    )

    counts = backlog_gate.audit_and_revert(p, home)

    assert counts["scanned"] == 1
    assert counts["reverted"] == 1
    assert counts["ok_verified"] == 0
    # Row was rewritten back to `[ ]`.
    body = (p / "backlog.md").read_text()
    assert "- [ ] [implement] [P0] vec_stub" in body
    assert "- [x] [implement] [P0] vec_stub" not in body
    # Snapshot was refreshed from the post-revert backlog.
    snap = (p / ".cc-autopipe" / "backlog_snapshot.md").read_text()
    assert "- [ ] [implement] [P0] vec_stub" in snap
    # Event emitted.
    events = [
        e for e in _read_aggregate(home)
        if e.get("event") == "unverified_close_blocked"
    ]
    assert len(events) == 1
    assert events[0]["task_id"] == "vec_stub"
    assert "no verify_completed" in events[0]["reason"]


def test_no_changes_yields_zero_counts(
    tmp_path: Path, monkeypatch
) -> None:
    """Backlog identical to snapshot, no `[x]` transitions at all:
    counters are all zero (apart from scanned for the open rows).
    No event emitted, no rewrite."""
    home = _user_home(tmp_path)
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(home))
    p = _project(tmp_path)

    body = (
        "- [ ] [implement] [P0] vec_one — task one\n"
        "- [ ] [research]  [P1] vec_two — task two\n"
        "- [~] [implement] [P2] vec_three — soft block\n"
    )
    (p / ".cc-autopipe" / "backlog_snapshot.md").write_text(body, encoding="utf-8")
    (p / "backlog.md").write_text(body, encoding="utf-8")

    counts = backlog_gate.audit_and_revert(p, home)

    assert counts["scanned"] == 3
    assert counts["reverted"] == 0
    assert counts["ok_verified"] == 0
    assert counts["ok_orphan_pre_v157"] == 0
    # Backlog untouched (mtime/content stable).
    assert (p / "backlog.md").read_text() == body
    # No revert events.
    events = [
        e for e in _read_aggregate(home)
        if e.get("event") == "unverified_close_blocked"
    ]
    assert events == []


def test_persistent_x_rows_count_as_legacy_amnesty(
    tmp_path: Path, monkeypatch
) -> None:
    """100 rows that were already `[x]` in the snapshot and remain
    `[x]` in the current backlog ALL fall into ok_orphan_pre_v157.
    Engine never touches them — this is the second-and-onwards-run
    contract that keeps a project's history stable across upgrades."""
    home = _user_home(tmp_path)
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(home))
    p = _project(tmp_path)

    rows = [
        f"- [x] [implement] [P0] vec_t{i:03d} — task {i}\n"
        for i in range(100)
    ]
    body = "".join(rows)
    (p / ".cc-autopipe" / "backlog_snapshot.md").write_text(body, encoding="utf-8")
    (p / "backlog.md").write_text(body, encoding="utf-8")

    counts = backlog_gate.audit_and_revert(p, home)

    assert counts["scanned"] == 100
    assert counts["ok_orphan_pre_v157"] == 100
    assert counts["reverted"] == 0
    assert counts["ok_verified"] == 0
    # Backlog untouched.
    assert (p / "backlog.md").read_text() == body
    # No revert events.
    events = [
        e for e in _read_aggregate(home)
        if e.get("event") == "unverified_close_blocked"
    ]
    assert events == []
