"""Unit tests for v1.5.1 ABLATION-VERDICT-GATE.

`on_promotion_success` spawns 5 ablation children only when
`metrics["verdict"] == "PROMOTED"`. NEUTRAL / CONDITIONAL / unknown
verdicts emit `ablation_skipped_non_promoted` instead, leaving the
backlog untouched. The leaderboard hook runs for every verdict.

Background: AI-trade Phase 4 production (2026-05-11/12) produced
hundreds of legitimate NEUTRAL verdicts ("no exploitable edge").
Pre-v1.5.1 each spawned 5 ablation children unconditionally; the
backlog grew from ~600 done / 11K open to ~38K done / 38K orphan
`_ab_` entries and Claude burned --max-turns reopening stale work.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
LIB = SRC / "lib"
for _p in (str(SRC), str(LIB)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import promotion  # noqa: E402


@dataclass
class _FakeItem:
    id: str
    priority: int = 1


def _project(tmp_path: Path) -> Path:
    p = tmp_path / "demo"
    (p / ".cc-autopipe" / "memory").mkdir(parents=True)
    (p / "data" / "debug").mkdir(parents=True)
    return p


def _read_aggregate(user_home: Path) -> list[dict]:
    p = user_home / "log" / "aggregate.jsonl"
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]


def _events_named(user_home: Path, name: str) -> list[dict]:
    return [e for e in _read_aggregate(user_home) if e.get("event") == name]


def _seed_project(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    p = _project(tmp_path)
    (p / "backlog.md").write_text(
        "- [x] [implement] [P1] vec_long_synth_v1 — done\n"
        "## Done\n",
        encoding="utf-8",
    )
    return p, user_home


def test_promoted_verdict_spawns_five_ablation_children(
    tmp_path: Path, monkeypatch
) -> None:
    """PROMOTED + non-empty backlog → 5 `_ab_` children appended,
    `ablation_children_spawned` event emitted."""
    p, user_home = _seed_project(tmp_path, monkeypatch)
    item = _FakeItem(id="vec_long_synth_v1", priority=1)

    lb_calls: list[tuple] = []
    import leaderboard  # type: ignore
    monkeypatch.setattr(
        leaderboard,
        "append_entry",
        lambda proj, tid, m: lb_calls.append((proj, tid, m)),
    )

    promotion.on_promotion_success(
        p, item, metrics={"verdict": "PROMOTED", "sum_fixed": 100.0}
    )

    body = (p / "backlog.md").read_text(encoding="utf-8")
    assert body.count("_ab_") >= 5
    for suffix in ("ab_drop_top", "ab_loss", "ab_seq", "ab_seed", "ab_eth"):
        assert f"vec_long_synth_v1_{suffix}" in body
    spawned = _events_named(user_home, "ablation_children_spawned")
    assert len(spawned) == 1
    assert spawned[0]["count"] == 5
    assert _events_named(user_home, "ablation_skipped_non_promoted") == []
    # Leaderboard hook fires regardless.
    assert len(lb_calls) == 1


def test_neutral_verdict_skips_ablation(tmp_path: Path, monkeypatch) -> None:
    """NEUTRAL → backlog UNCHANGED, `ablation_skipped_non_promoted`
    event emitted with verdict=NEUTRAL. Leaderboard hook still fires."""
    p, user_home = _seed_project(tmp_path, monkeypatch)
    item = _FakeItem(id="vec_long_synth_v1", priority=1)
    before = (p / "backlog.md").read_text(encoding="utf-8")

    lb_calls: list[tuple] = []
    import leaderboard  # type: ignore
    monkeypatch.setattr(
        leaderboard,
        "append_entry",
        lambda proj, tid, m: lb_calls.append((proj, tid, m)),
    )

    promotion.on_promotion_success(
        p, item, metrics={"verdict": "NEUTRAL", "sum_fixed": 0.0}
    )

    after = (p / "backlog.md").read_text(encoding="utf-8")
    assert after == before, "backlog must NOT be mutated on NEUTRAL"
    skipped = _events_named(user_home, "ablation_skipped_non_promoted")
    assert len(skipped) == 1
    assert skipped[0]["verdict"] == "NEUTRAL"
    assert _events_named(user_home, "ablation_children_spawned") == []
    assert len(lb_calls) == 1


def test_conditional_verdict_skips_ablation(
    tmp_path: Path, monkeypatch
) -> None:
    """CONDITIONAL → same skip path as NEUTRAL (different verdict
    string in event payload). Backlog UNCHANGED, leaderboard fires."""
    p, user_home = _seed_project(tmp_path, monkeypatch)
    item = _FakeItem(id="vec_long_synth_v1", priority=1)
    before = (p / "backlog.md").read_text(encoding="utf-8")

    lb_calls: list[tuple] = []
    import leaderboard  # type: ignore
    monkeypatch.setattr(
        leaderboard,
        "append_entry",
        lambda proj, tid, m: lb_calls.append((proj, tid, m)),
    )

    promotion.on_promotion_success(
        p, item, metrics={"verdict": "CONDITIONAL"}
    )

    after = (p / "backlog.md").read_text(encoding="utf-8")
    assert after == before
    skipped = _events_named(user_home, "ablation_skipped_non_promoted")
    assert len(skipped) == 1
    assert skipped[0]["verdict"] == "CONDITIONAL"
    assert _events_named(user_home, "ablation_children_spawned") == []
    assert len(lb_calls) == 1


def test_missing_verdict_key_treated_as_non_promoted(
    tmp_path: Path, monkeypatch
) -> None:
    """metrics={} (no `verdict` key) → conservatively skip ablation.
    Better to drop spawn on a parse failure than grow the backlog.
    Event records verdict="" so operators can grep parse-failure cases."""
    p, user_home = _seed_project(tmp_path, monkeypatch)
    item = _FakeItem(id="vec_long_synth_v1", priority=1)
    before = (p / "backlog.md").read_text(encoding="utf-8")

    lb_calls: list[tuple] = []
    import leaderboard  # type: ignore
    monkeypatch.setattr(
        leaderboard,
        "append_entry",
        lambda proj, tid, m: lb_calls.append((proj, tid, m)),
    )

    promotion.on_promotion_success(p, item, metrics={})

    after = (p / "backlog.md").read_text(encoding="utf-8")
    assert after == before
    skipped = _events_named(user_home, "ablation_skipped_non_promoted")
    assert len(skipped) == 1
    assert skipped[0]["verdict"] == ""
    assert _events_named(user_home, "ablation_children_spawned") == []
    assert len(lb_calls) == 1


def test_parse_metrics_populates_verdict_from_labelled_block(
    tmp_path: Path,
) -> None:
    """`## Metrics for leaderboard` block `**verdict**: NEUTRAL` →
    parse_metrics output has metrics["verdict"] == "CONDITIONAL"
    (NEUTRAL canonicalises to CONDITIONAL per CANONICAL_MAP)."""
    body = (
        "# Report\n\n"
        "## Metrics for leaderboard\n"
        "- **verdict**: NEUTRAL\n"
        "- **sum_fixed**: 0.0\n"
    )
    p = tmp_path / "CAND_test_PROMOTION.md"
    p.write_text(body, encoding="utf-8")
    metrics = promotion.parse_metrics(p)
    assert metrics["verdict"] == "CONDITIONAL"
    assert metrics["sum_fixed"] == 0.0


def test_parse_metrics_verdict_falls_back_to_parse_verdict_cascade(
    tmp_path: Path,
) -> None:
    """No labelled block → parse_metrics falls back to parse_verdict.
    A `## Verdict` heading with `PROMOTED` keyword → metrics["verdict"]
    == "PROMOTED"."""
    body = (
        "## Verdict\n\nPROMOTED — looks good\n\n"
        "sum_fixed: 50.0%\n"
    )
    p = tmp_path / "CAND_test_PROMOTION.md"
    p.write_text(body, encoding="utf-8")
    metrics = promotion.parse_metrics(p)
    assert metrics["verdict"] == "PROMOTED"
    assert metrics["sum_fixed"] == 50.0
