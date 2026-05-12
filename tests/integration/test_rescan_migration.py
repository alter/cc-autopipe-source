"""Integration tests for v1.5.4 RESCAN-MIGRATION-GUARD.

v1.5.3's `rescan_orphan_promotions` cuts off based on
`state.last_cycle_ended_at`. Pre-v1.5.3 state.json has no such field —
on first startup after upgrade, the function silently returned 0 and
never rescued the SIGTERM-orphaned PROMOTION files the rescan was
written for (AI-trade 2026-05-12 hit this exact gap).

v1.5.4 closes the gap by backfilling the cutoff from the most recent
`cycle_end` event in `aggregate.jsonl` when the state field is missing.
If no aggregate.jsonl exists either, cutoff=0 and every CAND_* file is
considered — the `_is_in_leaderboard` membership grep keeps this safe.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
LIB = SRC / "lib"
for p in (str(SRC), str(LIB)):
    if p not in sys.path:
        sys.path.insert(0, p)

import state  # noqa: E402
from orchestrator.recovery import rescan_orphan_promotions  # noqa: E402


PROMOTED_BODY = """\
**Verdict: PROMOTED**

## Long-only verification
yes
## Regime-stratified PnL
yes (parity=0.18)
sum_fixed: +268.99%
regime_parity: 0.18
max_DD: -8.20%
DM_p_value: 0.003
DSR: 1.12
## Statistical significance
yes
## Walk-forward stability
yes
## No-lookahead audit
yes
"""


def _seed_project(tmp_path: Path, monkeypatch) -> Path:
    user_home = tmp_path / "uhome"
    (user_home / "log").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    project = tmp_path / "demo"
    (project / ".cc-autopipe" / "memory").mkdir(parents=True, exist_ok=True)
    (project / "data" / "debug").mkdir(parents=True, exist_ok=True)
    return project


def _write_promotion(
    project: Path, task_id: str, mtime_offset_sec: float = 0.0
) -> Path:
    target = project / "data" / "debug" / f"CAND_{task_id}_PROMOTION.md"
    target.write_text(PROMOTED_BODY, encoding="utf-8")
    if mtime_offset_sec:
        when = time.time() + mtime_offset_sec
        os.utime(target, (when, when))
    return target


def _seed_aggregate_cycle_end(
    user_home: Path, project_name: str, when_utc: datetime
) -> None:
    """Append a single cycle_end event to aggregate.jsonl in the format
    state.log_event emits (no spaces after colons; "project" key)."""
    record = {
        "ts": when_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "project": project_name,
        "event": "cycle_end",
        "iteration": 1,
        "rc": 0,
    }
    log = user_home / "log" / "aggregate.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
        f.write("\n")


def _read_aggregate(user_home: Path) -> list[dict]:
    p = user_home / "log" / "aggregate.jsonl"
    if not p.exists():
        return []
    return [
        json.loads(line)
        for line in p.read_text().splitlines()
        if line.strip()
    ]


def test_backfill_picks_latest_cycle_end_from_aggregate(
    tmp_path: Path, monkeypatch
) -> None:
    """state.json without last_cycle_ended_at + aggregate.jsonl has a
    cycle_end at T; PROMOTION file mtime is T+1h → backfill picks T,
    file is mtime-newer than the cutoff and gets validated."""
    project = _seed_project(tmp_path, monkeypatch)
    user_home = tmp_path / "uhome"

    # Confirm the v1.5.4 precondition: state has no last_cycle_ended_at.
    s = state.read(project)
    assert getattr(s, "last_cycle_ended_at", None) is None

    cycle_end_at = datetime.now(timezone.utc) - timedelta(hours=1)
    _seed_aggregate_cycle_end(user_home, project.name, cycle_end_at)
    _write_promotion(project, "backfill_hit")  # mtime ≈ now > cutoff

    rescued = rescan_orphan_promotions(project)

    assert rescued == 1
    lb = (project / "data" / "debug" / "LEADERBOARD.md").read_text()
    assert "| backfill_hit |" in lb

    events = _read_aggregate(user_home)
    names = [e.get("event") for e in events]
    assert "orphan_rescan_cutoff_backfilled" in names
    backfill_evt = next(
        e for e in events if e.get("event") == "orphan_rescan_cutoff_backfilled"
    )
    assert backfill_evt["source"] == "aggregate.jsonl"
    # cutoff_ts must echo the cycle_end timestamp (UTC, "Z" suffix).
    assert backfill_evt["cutoff_ts"] == cycle_end_at.strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    assert "promotion_validated" in names


def test_no_aggregate_falls_back_to_zero_cutoff_and_dedups(
    tmp_path: Path, monkeypatch
) -> None:
    """state.json without last_cycle_ended_at AND no aggregate.jsonl
    entries → cutoff=0, ALL CAND_*_PROMOTION files scanned. The
    _is_in_leaderboard idempotency guard prevents duplicate appends on
    re-run."""
    project = _seed_project(tmp_path, monkeypatch)
    user_home = tmp_path / "uhome"
    # No aggregate.jsonl seeded — directory exists but the file does not.
    assert not (user_home / "log" / "aggregate.jsonl").exists()

    _write_promotion(project, "fresh_install")

    rescued = rescan_orphan_promotions(project)
    assert rescued == 1
    lb_path = project / "data" / "debug" / "LEADERBOARD.md"
    assert lb_path.exists()
    assert "| fresh_install |" in lb_path.read_text()

    # Re-run: dedup via _is_in_leaderboard keeps it safe.
    rescued_again = rescan_orphan_promotions(project)
    assert rescued_again == 0
    validated = [
        e
        for e in _read_aggregate(user_home)
        if e.get("event") == "promotion_validated"
    ]
    assert len(validated) == 1

    # No backfill event when aggregate was absent — backfill returned None.
    backfilled = [
        e
        for e in _read_aggregate(user_home)
        if e.get("event") == "orphan_rescan_cutoff_backfilled"
    ]
    assert backfilled == []


def test_preset_cutoff_does_not_invoke_backfill(
    tmp_path: Path, monkeypatch
) -> None:
    """When state.last_cycle_ended_at IS set (v1.5.3+ healthy state),
    the v1.5.3 cutoff path is used unchanged. Backfill must NOT fire
    even if aggregate.jsonl has an older cycle_end that would yield a
    different cutoff."""
    project = _seed_project(tmp_path, monkeypatch)
    user_home = tmp_path / "uhome"

    cutoff = datetime.now(timezone.utc) - timedelta(hours=2)
    s = state.read(project)
    s.last_cycle_ended_at = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
    state.write(project, s)

    # Older cycle_end in aggregate — would expand the cutoff window if used.
    _seed_aggregate_cycle_end(
        user_home,
        project.name,
        datetime.now(timezone.utc) - timedelta(hours=6),
    )

    # PROMOTION file 1h ago — newer than state cutoff (2h ago), gets rescued.
    _write_promotion(project, "preset_path", mtime_offset_sec=-3600)

    rescued = rescan_orphan_promotions(project)
    assert rescued == 1

    backfilled = [
        e
        for e in _read_aggregate(user_home)
        if e.get("event") == "orphan_rescan_cutoff_backfilled"
    ]
    assert backfilled == [], (
        "backfill must not fire when state.last_cycle_ended_at is set"
    )
