"""Integration tests for v1.5.5 ORPHAN-RESCAN-FIX.

Two changes layered on top of v1.5.3 rescan + v1.5.4 migration guard:

1. The verdict skip gate is removed — orphan rescue runs for all
   verdicts, mirroring the standard `_post_cycle_delta_scan` path. The
   v1.5.1 ABLATION-VERDICT-GATE inside `on_promotion_success` already
   protects against runaway ablation children on non-PROMOTED.

2. `task_id` is read from the PROMOTION body's `**Task:** <id>` line
   instead of being derived from the filename. AI-trade convention
   writes filenames as `CAND_<short>_PROMOTION.md` but real backlog
   task IDs include the `vec_` phase/track prefix; filename derivation
   produces leaderboard rows under the wrong key.

Refs: PROMPT-v1.5.5.md GROUP ORPHAN-RESCAN-FIX.
"""

from __future__ import annotations

import json
import sys
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


PROMOTED_BODY_WITH_TASK = """\
**Task:** vec_p5_la_champion_full_backtest

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

NEUTRAL_BODY_WITH_TASK = """\
**Task:** vec_p4_da_inconclusive_probe

## Metrics for leaderboard
- **verdict**: NEUTRAL
- **auc**: 0.51
- **sharpe**: 0.12

No exploitable edge after backtesting.
"""

REJECTED_BODY_WITH_TASK = """\
**Task:** vec_p3_da_long_loses_money

**Verdict: REJECTED**

Strategy loses money long-only across all regimes.
"""


def _seed_project(tmp_path: Path, monkeypatch) -> Path:
    user_home = tmp_path / "uhome"
    (user_home / "log").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    project = tmp_path / "demo"
    (project / ".cc-autopipe" / "memory").mkdir(parents=True, exist_ok=True)
    (project / "data" / "debug").mkdir(parents=True, exist_ok=True)
    return project


def _write_promotion(project: Path, filename_id: str, body: str) -> Path:
    target = (
        project / "data" / "debug" / f"CAND_{filename_id}_PROMOTION.md"
    )
    target.write_text(body, encoding="utf-8")
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


def test_neutral_orphan_rescued_with_body_task_id(
    tmp_path: Path, monkeypatch
) -> None:
    """NEUTRAL orphan with `**Task:** vec_xyz` and stripped filename
    `CAND_xyz_PROMOTION.md` → rescue happens (v1.5.5 removes verdict
    gate) and leaderboard gains row keyed on the full `vec_xyz`."""
    project = _seed_project(tmp_path, monkeypatch)
    _set_cutoff(project, datetime.now(timezone.utc) - timedelta(hours=1))
    # Filename strips the vec_ prefix the way AI-trade does:
    _write_promotion(
        project,
        "p4_da_inconclusive_probe",
        NEUTRAL_BODY_WITH_TASK,
    )

    rescued = rescan_orphan_promotions(project)
    assert rescued == 1

    lb = (project / "data" / "debug" / "LEADERBOARD.md").read_text(
        encoding="utf-8"
    )
    # Canonical full id from **Task:** body line — NOT the stripped
    # filename derivation.
    assert "| vec_p4_da_inconclusive_probe |" in lb
    assert "| p4_da_inconclusive_probe |" not in lb

    events = _read_aggregate(tmp_path / "uhome")
    validated = [
        e for e in events if e.get("event") == "promotion_validated"
    ]
    assert len(validated) == 1
    assert validated[-1]["task_id"] == "vec_p4_da_inconclusive_probe"
    skipped = [
        e for e in events if e.get("event") == "orphan_promotion_skipped"
    ]
    assert skipped == []


def test_rejected_orphan_rescued_no_verdict_gate(
    tmp_path: Path, monkeypatch
) -> None:
    """REJECTED orphan also rescued — no skip-on-non-PROMOTED filter.
    Mirrors standard `_post_cycle_delta_scan` semantics where ALL
    verdicts hit `on_promotion_success` and the leaderboard hook fires
    unconditionally."""
    project = _seed_project(tmp_path, monkeypatch)
    _set_cutoff(project, datetime.now(timezone.utc) - timedelta(hours=1))
    _write_promotion(
        project,
        "p3_da_long_loses_money",
        REJECTED_BODY_WITH_TASK,
    )

    rescued = rescan_orphan_promotions(project)
    assert rescued == 1

    lb = (project / "data" / "debug" / "LEADERBOARD.md").read_text(
        encoding="utf-8"
    )
    assert "| vec_p3_da_long_loses_money |" in lb


def test_promoted_orphan_body_task_id_wins_over_filename(
    tmp_path: Path, monkeypatch
) -> None:
    """The pre-v1.5.5 filename regex stripped the vec_ prefix; if a
    PROMOTED body declares `**Task:** vec_p5_la_champion_full_backtest`
    but the filename is `CAND_p5_la_champion_full_backtest_PROMOTION.md`
    the rescue must land the row under `vec_p5_la_champion_full_backtest`."""
    project = _seed_project(tmp_path, monkeypatch)
    _set_cutoff(project, datetime.now(timezone.utc) - timedelta(hours=1))
    _write_promotion(
        project,
        "p5_la_champion_full_backtest",
        PROMOTED_BODY_WITH_TASK,
    )

    rescued = rescan_orphan_promotions(project)
    assert rescued == 1

    lb = (project / "data" / "debug" / "LEADERBOARD.md").read_text(
        encoding="utf-8"
    )
    assert "| vec_p5_la_champion_full_backtest |" in lb
    assert "| p5_la_champion_full_backtest |" not in lb


def test_legacy_body_without_task_field_falls_back_to_filename(
    tmp_path: Path, monkeypatch
) -> None:
    """Legacy PROMOTION files that pre-date the `**Task:**` convention
    (no field in body) keep working via the filename fallback so old
    fixtures don't suddenly start landing as None / empty task_id."""
    project = _seed_project(tmp_path, monkeypatch)
    _set_cutoff(project, datetime.now(timezone.utc) - timedelta(hours=1))
    legacy_body = "**Verdict: PROMOTED**\n\nLegacy report — no Task field.\n"
    _write_promotion(project, "legacy_task", legacy_body)

    rescued = rescan_orphan_promotions(project)
    assert rescued == 1

    lb = (project / "data" / "debug" / "LEADERBOARD.md").read_text(
        encoding="utf-8"
    )
    assert "| legacy_task |" in lb
