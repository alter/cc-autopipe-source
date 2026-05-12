"""Integration tests for v1.5.3 ORPHAN-PROMOTION-RESCAN.

When SIGTERM interrupts a cycle that had just written a PROMOTION file
but before post_cycle_delta ran, the file is orphaned: not validated,
not appended to LEADERBOARD.md, not surfaced anywhere. Real AI-trade
production observed this on 2026-05-12 (iter 174,
`vec_p5_la_champion_full_backtest`).

`recovery.rescan_orphan_promotions` closes the gap: on orchestrator
startup and on each 30-min sweep, scan `data/debug/CAND_*_PROMOTION.md`
whose mtime is newer than `state.last_cycle_ended_at` and validate +
leaderboard any that aren't already represented.
"""

from __future__ import annotations

import json
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

NEUTRAL_BODY = """\
**Verdict: NEUTRAL**

No exploitable edge after backtesting.
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
    project: Path, task_id: str, body: str, mtime_offset_sec: float = 0.0
) -> Path:
    """Drop a PROMOTION file with explicit mtime offset (negative = older)."""
    target = project / "data" / "debug" / f"CAND_{task_id}_PROMOTION.md"
    target.write_text(body, encoding="utf-8")
    if mtime_offset_sec:
        when = time.time() + mtime_offset_sec
        import os  # noqa: PLC0415
        os.utime(target, (when, when))
    return target


def _set_cutoff(project: Path, when_utc: datetime) -> None:
    s = state.read(project)
    s.last_cycle_ended_at = when_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    state.write(project, s)


def _read_aggregate(monkeypatch_user_home: Path) -> list[dict]:
    p = monkeypatch_user_home / "log" / "aggregate.jsonl"
    if not p.exists():
        return []
    return [
        json.loads(line)
        for line in p.read_text().splitlines()
        if line.strip()
    ]


def test_orphan_promotion_rescued_when_newer_than_cutoff(
    tmp_path: Path, monkeypatch
) -> None:
    project = _seed_project(tmp_path, monkeypatch)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
    _set_cutoff(project, cutoff)
    # PROMOTION mtime = now (≈ 30 min after cutoff).
    _write_promotion(project, "orphan_test", PROMOTED_BODY)

    rescued = rescan_orphan_promotions(project)

    assert rescued == 1
    lb = (project / "data" / "debug" / "LEADERBOARD.md").read_text()
    assert "| orphan_test |" in lb

    events = _read_aggregate(tmp_path / "uhome")
    names = [e.get("event") for e in events]
    assert "promotion_validated" in names
    validated = [e for e in events if e.get("event") == "promotion_validated"]
    assert validated[-1]["origin"] == "orphan_rescan"
    assert validated[-1]["task_id"] == "orphan_test"
    assert "orphan_promotion_rescan_completed" in names


def test_promotion_older_than_cutoff_not_rescued(
    tmp_path: Path, monkeypatch
) -> None:
    project = _seed_project(tmp_path, monkeypatch)
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)
    _set_cutoff(project, cutoff)
    # PROMOTION mtime 1 hour ago — well before cutoff.
    _write_promotion(
        project, "old_test", PROMOTED_BODY, mtime_offset_sec=-3600
    )

    rescued = rescan_orphan_promotions(project)

    assert rescued == 0
    lb_path = project / "data" / "debug" / "LEADERBOARD.md"
    assert not lb_path.exists(), "LEADERBOARD must not be created for skipped orphan"


def test_already_leaderboarded_promotion_skipped_idempotently(
    tmp_path: Path, monkeypatch
) -> None:
    project = _seed_project(tmp_path, monkeypatch)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
    _set_cutoff(project, cutoff)
    _write_promotion(project, "dup_test", PROMOTED_BODY)

    first = rescan_orphan_promotions(project)
    assert first == 1

    # Re-run: same file, already leaderboarded → no-op.
    second = rescan_orphan_promotions(project)
    assert second == 0

    events = _read_aggregate(tmp_path / "uhome")
    validated = [e for e in events if e.get("event") == "promotion_validated"]
    assert len(validated) == 1, (
        f"expected exactly one promotion_validated, got {len(validated)}"
    )


def test_neutral_orphan_skipped_with_event_no_leaderboard(
    tmp_path: Path, monkeypatch
) -> None:
    """NEUTRAL/CONDITIONAL/REJECTED PROMOTIONs were never destined for
    the leaderboard. The rescan logs `orphan_promotion_skipped` for
    observability but does NOT append."""
    project = _seed_project(tmp_path, monkeypatch)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
    _set_cutoff(project, cutoff)
    _write_promotion(project, "neutral_test", NEUTRAL_BODY)

    rescued = rescan_orphan_promotions(project)

    assert rescued == 0
    lb_path = project / "data" / "debug" / "LEADERBOARD.md"
    assert not lb_path.exists()
    events = _read_aggregate(tmp_path / "uhome")
    skipped = [
        e for e in events if e.get("event") == "orphan_promotion_skipped"
    ]
    assert len(skipped) == 1
    assert skipped[0]["task_id"] == "neutral_test"


def test_missing_cutoff_skips_rescan(tmp_path: Path, monkeypatch) -> None:
    """First-ever startup has no last_cycle_ended_at — nothing to do."""
    project = _seed_project(tmp_path, monkeypatch)
    _write_promotion(project, "first_run", PROMOTED_BODY)
    rescued = rescan_orphan_promotions(project)
    assert rescued == 0
    lb_path = project / "data" / "debug" / "LEADERBOARD.md"
    assert not lb_path.exists()


def test_no_debug_dir_returns_zero(tmp_path: Path, monkeypatch) -> None:
    """Project without data/debug must not crash the rescan."""
    user_home = tmp_path / "uhome"
    (user_home / "log").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    project = tmp_path / "noviewdir"
    (project / ".cc-autopipe" / "memory").mkdir(parents=True, exist_ok=True)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
    _set_cutoff(project, cutoff)
    assert rescan_orphan_promotions(project) == 0
