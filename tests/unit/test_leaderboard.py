"""Unit tests for src/lib/leaderboard.py.

v1.3.5 Group LEADERBOARD-WRITER.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_LIB = REPO_ROOT / "src" / "lib"

sys.path.insert(0, str(SRC_LIB))

import leaderboard as lb  # noqa: E402


# ---------------------------------------------------------------------------
# _composite
# ---------------------------------------------------------------------------


def test_composite_full_metrics() -> None:
    metrics = {
        "sum_fixed": 268.99,  # → 0.26899
        "regime_parity": 0.18,  # → (1-0.18) = 0.82 contribution
        "max_dd": -8.20,  # → 0.082
        "dm_p_value": 0.003,
        "dsr": 1.12,
    }
    composite = lb._composite(metrics)
    expected = 0.5 * (268.99 / 1000) + 0.3 * (1 - 0.18) + 0.2 * (-8.20 / -100)
    assert abs(composite - round(expected, 4)) < 1e-4


def test_composite_missing_metrics_treated_as_zero_contribution() -> None:
    composite = lb._composite({"sum_fixed": None, "regime_parity": None})
    assert composite == 0.0


def test_composite_partial_metrics_no_exception() -> None:
    composite = lb._composite({"sum_fixed": 100.0})
    # 0.5 * 0.1 = 0.05
    assert composite == 0.05


# ---------------------------------------------------------------------------
# elo_after_match
# ---------------------------------------------------------------------------


def test_elo_symmetric_equal_ratings_score_half() -> None:
    new_a, new_b = lb.elo_after_match(1500, 1500, 0.5)
    assert new_a == 1500
    assert new_b == 1500


def test_elo_underdog_wins_larger_swing_for_underdog() -> None:
    """When the underdog (1300) beats the favourite (1700) the
    underdog gains more points than they would have lost as the
    favourite gained a win."""
    underdog_after, fav_after = lb.elo_after_match(1300, 1700, 1.0)
    underdog_gain = underdog_after - 1300
    fav_loss = 1700 - fav_after
    # Symmetric (zero-sum) — gain == loss in magnitude — but each is
    # > k/2 (16) because the expected was way against the underdog.
    assert underdog_gain == fav_loss
    assert underdog_gain > 16


# ---------------------------------------------------------------------------
# _parse_pct
# ---------------------------------------------------------------------------


def test_parse_pct_positive() -> None:
    assert lb._parse_pct("+268.99%") == 268.99


def test_parse_pct_negative() -> None:
    assert lb._parse_pct("-114.03%") == -114.03


def test_parse_pct_zero() -> None:
    assert lb._parse_pct("0") == 0.0


def test_parse_pct_empty_returns_none() -> None:
    assert lb._parse_pct("") is None


# ---------------------------------------------------------------------------
# append_entry / read_top_n end-to-end
# ---------------------------------------------------------------------------


def _seed_user_home(monkeypatch, tmp_path: Path) -> Path:
    """Isolate aggregate.jsonl path so log_event doesn't pollute the
    real ~/.cc-autopipe."""
    user_home = tmp_path / "uhome"
    (user_home / "log").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    return user_home


def test_append_entry_first_entry_creates_leaderboard(
    tmp_path: Path, monkeypatch
) -> None:
    _seed_user_home(monkeypatch, tmp_path)
    project = tmp_path / "p"
    project.mkdir()
    lb.append_entry(
        project,
        "vec_long_synth_v1",
        {
            "sum_fixed": 200.0,
            "regime_parity": 0.20,
            "max_dd": -10.0,
            "dm_p_value": 0.005,
            "dsr": 1.0,
        },
    )
    text = (project / "data" / "debug" / "LEADERBOARD.md").read_text()
    assert "vec_long_synth_v1" in text
    # First entry: ELO is initial 1500 (no opponents).
    assert "1500" in text
    rows = lb.read_top_n(project)
    assert rows[0]["task_id"] == "vec_long_synth_v1"


def test_append_entry_second_entry_runs_elo_and_sorts_by_composite(
    tmp_path: Path, monkeypatch
) -> None:
    _seed_user_home(monkeypatch, tmp_path)
    project = tmp_path / "p"
    project.mkdir()
    lb.append_entry(
        project,
        "low",
        {"sum_fixed": 50.0, "regime_parity": 0.5, "max_dd": -20.0},
    )
    lb.append_entry(
        project,
        "high",
        {"sum_fixed": 500.0, "regime_parity": 0.1, "max_dd": -5.0},
    )
    rows = lb.read_top_n(project)
    assert [r["task_id"] for r in rows] == ["high", "low"]
    # ELO matchup ran: 'high' should now be > 1500 (it won), 'low' < 1500.
    assert rows[0]["elo"] > 1500
    assert rows[1]["elo"] < 1500


def test_append_entry_replaces_existing_task_id(
    tmp_path: Path, monkeypatch
) -> None:
    _seed_user_home(monkeypatch, tmp_path)
    project = tmp_path / "p"
    project.mkdir()
    lb.append_entry(
        project,
        "vec_long_synth",
        {"sum_fixed": 100.0, "regime_parity": 0.3, "max_dd": -10.0},
    )
    lb.append_entry(
        project,
        "vec_long_synth",  # re-promotion
        {"sum_fixed": 300.0, "regime_parity": 0.15, "max_dd": -8.0},
    )
    rows = lb.read_top_n(project)
    assert len(rows) == 1
    assert rows[0]["task_id"] == "vec_long_synth"
    assert rows[0]["sum_fixed"] == 300.0


def test_append_entry_archives_beyond_top_20(
    tmp_path: Path, monkeypatch
) -> None:
    _seed_user_home(monkeypatch, tmp_path)
    project = tmp_path / "p"
    project.mkdir()
    # Insert 21 entries with monotonically decreasing composites so
    # the 21st (lowest) ends up archived. sum_fixed drives composite
    # via the 0.5*sf/1000 term.
    for i in range(21):
        lb.append_entry(
            project,
            f"task_{i:02d}",
            {"sum_fixed": float(2100 - i * 100)},
        )
    rows = lb.read_top_n(project)
    assert len(rows) == 20
    archive_dir = project / "data" / "debug" / "ARCHIVE"
    archive_files = list(archive_dir.glob("LEADERBOARD_*.md"))
    assert archive_files, "expected at least one archive file"


def test_append_entry_touches_knowledge_sentinel(
    tmp_path: Path, monkeypatch
) -> None:
    _seed_user_home(monkeypatch, tmp_path)
    project = tmp_path / "p"
    cca = project / ".cc-autopipe"
    cca.mkdir(parents=True)
    (cca / "knowledge.md").write_text("# k\n", encoding="utf-8")

    lb.append_entry(
        project, "vec_long_x", {"sum_fixed": 100.0, "max_dd": -5.0}
    )

    import state as state_mod  # noqa: PLC0415

    s = state_mod.read(project)
    assert s.knowledge_update_pending is True
    assert s.knowledge_baseline_mtime is not None
    assert s.knowledge_baseline_mtime > 0


def test_round_trip_read_existing_entries(
    tmp_path: Path, monkeypatch
) -> None:
    _seed_user_home(monkeypatch, tmp_path)
    project = tmp_path / "p"
    project.mkdir()
    lb.append_entry(
        project, "a", {"sum_fixed": 100.0, "regime_parity": 0.2}
    )
    lb.append_entry(
        project, "b", {"sum_fixed": 200.0, "regime_parity": 0.1}
    )
    parsed = lb._read_existing_entries(project)
    assert len(parsed) == 2
    ids = {r["task_id"] for r in parsed}
    assert ids == {"a", "b"}
