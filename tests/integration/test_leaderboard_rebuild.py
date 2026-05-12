"""Integration tests for v1.5.5 LEADERBOARD-REPLAY.

`leaderboard.rebuild_from_files(project)` is a one-shot operator
recovery: rescan every `CAND_*_PROMOTION.md` under `data/debug/` and
rewrite LEADERBOARD.md from scratch with current parser semantics.

Intended use: post v1.5.5 CANONICAL-MAP-FIX deploy, run once per
project to regenerate the leaderboard that was previously corrupted
by NEUTRAL → CONDITIONAL collapse.

Refs: PROMPT-v1.5.5.md GROUP LEADERBOARD-REPLAY.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
LIB = SRC / "lib"
for p in (str(SRC), str(LIB)):
    if p not in sys.path:
        sys.path.insert(0, p)

import leaderboard  # noqa: E402


PROMOTED_BODY = """\
**Task:** vec_promoted_alpha

**Verdict: PROMOTED**

## Long-only verification
yes
## Regime-stratified PnL
yes
## Statistical significance
yes (DM p=0.003)
## Walk-forward stability
yes
## No-lookahead audit
yes

## Metrics for leaderboard
- **verdict**: PROMOTED
- **sum_fixed**: 245.5
- **regime_parity**: 0.18
- **max_dd**: -8.2
- **dm_p_value**: 0.003
- **dsr**: 1.12
"""

NEUTRAL_BODY = """\
**Task:** vec_neutral_beta

**Verdict: NEUTRAL**

No exploitable edge.

## Metrics for leaderboard
- **verdict**: NEUTRAL
- **auc**: 0.51
- **sharpe**: 0.12
- **dm_p_value**: 0.42
"""

REJECTED_BODY = """\
**Task:** vec_rejected_gamma

**Verdict: REJECTED**

Always loses money.

## Metrics for leaderboard
- **verdict**: REJECTED
- **sum_fixed**: -12.0
- **regime_parity**: 0.90
- **max_dd**: -42.0
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


def _seed_corrupted_leaderboard(project: Path) -> None:
    """Simulate pre-v1.5.5 corruption: a stale row keyed on the wrong
    task_id and a wrong verdict that the rebuild must overwrite."""
    lb_path = project / "data" / "debug" / "LEADERBOARD.md"
    lb_path.write_text(
        "# Promotion Leaderboard\n\n"
        "Last updated: 2026-04-01T00:00:00Z\n\n"
        "| Rank | task_id | composite | sum_fixed | regime_parity | "
        "max_DD | DM_p | DSR | ELO | promotion_date |\n"
        "|------|---------|-----------|-----------|---------------|"
        "--------|------|-----|-----|----------------|\n"
        "| 1 | stale_corrupted_row | 0.5 | +50.00 | 0.5 | -10.00% | "
        "0.05 | 0.5 | 1500 | 2026-04-01 |\n",
        encoding="utf-8",
    )


def test_rebuild_from_files_mixed_verdicts(
    tmp_path: Path, monkeypatch
) -> None:
    """Seed three PROMOTIONs (PROMOTED / NEUTRAL / REJECTED) and a
    corrupted LEADERBOARD.md → rebuild_from_files reconstructs a
    leaderboard with exactly three rows, all keyed on the canonical
    `vec_*` task ids derived from the body's `**Task:**` line, and
    the stale corrupted row is gone."""
    project = _seed_project(tmp_path, monkeypatch)

    _write_promotion(project, "promoted_alpha", PROMOTED_BODY)
    _write_promotion(project, "neutral_beta", NEUTRAL_BODY)
    _write_promotion(project, "rejected_gamma", REJECTED_BODY)
    _seed_corrupted_leaderboard(project)

    counts = leaderboard.rebuild_from_files(project)
    assert counts["scanned"] == 3
    assert counts["appended"] == 3
    assert counts["failed"] == 0

    lb_text = (
        project / "data" / "debug" / "LEADERBOARD.md"
    ).read_text(encoding="utf-8")

    # Stale row was wiped before rebuild.
    assert "stale_corrupted_row" not in lb_text
    # Each row keyed on the canonical full id from `**Task:**`, not the
    # filename's stripped form.
    assert "| vec_promoted_alpha |" in lb_text
    assert "| vec_neutral_beta |" in lb_text
    assert "| vec_rejected_gamma |" in lb_text
    # Stripped-filename forms must NOT appear (would indicate fallback
    # kicked in despite the body field being present).
    assert "| promoted_alpha |" not in lb_text
    assert "| neutral_beta |" not in lb_text
    assert "| rejected_gamma |" not in lb_text


def test_rebuild_no_debug_dir_returns_zero_counts(
    tmp_path: Path, monkeypatch
) -> None:
    """A project without a `data/debug/` directory yields zero counts
    instead of an exception. Defensive — operator may invoke the CLI
    against a freshly-init'd project."""
    monkeypatch.setenv(
        "CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome")
    )
    project = tmp_path / "no_debug"
    project.mkdir()
    counts = leaderboard.rebuild_from_files(project)
    assert counts == {"scanned": 0, "appended": 0, "failed": 0}


def test_state_cli_rebuild_leaderboard_subcommand(
    tmp_path: Path, monkeypatch
) -> None:
    """`python3 state.py rebuild-leaderboard <project>` runs the
    rebuild and prints `{scanned, appended, failed}` as JSON. Asserts
    the operator-facing surface (subprocess, no Python import) so a
    scripted post-deploy run is exercised."""
    project = _seed_project(tmp_path, monkeypatch)
    _write_promotion(project, "promoted_alpha", PROMOTED_BODY)
    _write_promotion(project, "neutral_beta", NEUTRAL_BODY)

    env = {
        **__import__("os").environ,
        "CC_AUTOPIPE_USER_HOME": str(tmp_path / "uhome"),
        "PYTHONPATH": f"{SRC}:{LIB}",
    }
    result = subprocess.run(
        [sys.executable, str(LIB / "state.py"),
         "rebuild-leaderboard", str(project)],
        capture_output=True,
        text=True,
        env=env,
        check=True,
        timeout=30,
    )
    counts = json.loads(result.stdout.strip())
    assert counts == {"scanned": 2, "appended": 2, "failed": 0}

    lb_text = (
        project / "data" / "debug" / "LEADERBOARD.md"
    ).read_text(encoding="utf-8")
    assert "| vec_promoted_alpha |" in lb_text
    assert "| vec_neutral_beta |" in lb_text
