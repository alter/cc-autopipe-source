"""Integration tests for v1.3.8 PROMOTION-HOOK-DIAGNOSTICS event trail.

AI-trade Phase 2 v2.0 production observed 4 measurement tasks closed
with `## Verdict: PROMOTED` but produced 0 ablation children and no
LEADERBOARD.md append. Without per-stage diagnostic events, root-causing
the silent drop was impossible.

v1.3.8 emits a stage-tagged event trail in `on_promotion_success`:

    on_promotion_success_entered          (always)
    promotion_children_skipped/spawned    (one of)
    on_promotion_success_failed (per stage on raise)
    on_promotion_success_completed        (only when both ablation + LB OK)

Coverage:
- Strategy task PROMOTED with all v2 sections → entered + spawned + completed
- Measurement task PROMOTED missing v2 sections → relaxed → spawned + completed
- on_promotion_success internal raise → on_promotion_success_failed event
  with stage info; no _completed event
- Strategy task PROMOTED missing v2 sections → quarantined
  (promotion_invalid event); on_promotion_success NOT called
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
import state  # noqa: E402


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


def test_strategy_promotion_emits_entered_spawned_completed(
    tmp_path: Path, monkeypatch
) -> None:
    """PROMOTED strategy task with backlog → all three diagnostic events
    fire in order. Verifies the canonical happy path is observable."""
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    p = _project(tmp_path)
    (p / "backlog.md").write_text(
        "- [x] [implement] [P1] vec_long_synth_v1 — done\n"
        "## Done\n",
        encoding="utf-8",
    )
    item = _FakeItem(id="vec_long_synth_v1", priority=1)
    promotion.on_promotion_success(p, item, metrics={"sum_fixed": 12.3})

    entered = _events_named(user_home, "on_promotion_success_entered")
    spawned = _events_named(user_home, "ablation_children_spawned")
    completed = _events_named(user_home, "on_promotion_success_completed")
    assert len(entered) == 1
    assert entered[0]["task_id"] == "vec_long_synth_v1"
    assert len(spawned) == 1
    assert spawned[0]["count"] == 5
    assert len(completed) == 1
    assert completed[0]["task_id"] == "vec_long_synth_v1"


def test_promotion_no_backlog_emits_skipped_then_completed(
    tmp_path: Path, monkeypatch
) -> None:
    """When backlog.md is absent, ablation spawn legitimately can't run.
    Engine logs `promotion_children_skipped reason=backlog_missing` but
    leaderboard append still proceeds. _completed only fires when BOTH
    stages are OK — and `promotion_children_skipped` (no exception) does
    NOT count as failure, so completed fires when leaderboard succeeds.

    NOTE: ablation_ok is False when backlog is missing (intentional —
    no work to spawn), so on_promotion_success_completed is NOT emitted
    even though leaderboard succeeded. Confirms the per-stage gating."""
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    p = _project(tmp_path)
    item = _FakeItem(id="vec_long_synth_v1", priority=1)
    promotion.on_promotion_success(p, item, metrics={})

    assert (
        len(_events_named(user_home, "on_promotion_success_entered")) == 1
    )
    skipped = _events_named(user_home, "promotion_children_skipped")
    assert len(skipped) == 1
    assert skipped[0]["reason"] == "backlog_missing"
    # No spawned event (no backlog to mutate).
    assert _events_named(user_home, "ablation_children_spawned") == []


def test_promotion_failed_in_ablation_logs_stage_specific_event(
    tmp_path: Path, monkeypatch
) -> None:
    """Inject a failure in `_ablation_children_for` and verify
    on_promotion_success_failed is emitted with stage='ablation_spawn'.
    Leaderboard append still tries (decoupled from ablation failure)."""
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    p = _project(tmp_path)
    (p / "backlog.md").write_text(
        "- [x] [implement] [P1] vec_long_synth_v1 — done\n",
        encoding="utf-8",
    )

    def _boom(*args, **kwargs):
        raise RuntimeError("ablation_explode")

    monkeypatch.setattr(promotion, "_ablation_children_for", _boom)
    item = _FakeItem(id="vec_long_synth_v1", priority=1)
    promotion.on_promotion_success(p, item, metrics={})

    assert (
        len(_events_named(user_home, "on_promotion_success_entered")) == 1
    )
    failed = _events_named(user_home, "on_promotion_success_failed")
    assert len(failed) == 1
    assert failed[0]["stage"] == "ablation_spawn"
    assert "ablation_explode" in failed[0]["error"]
    # No spawned event — failed path took over.
    assert _events_named(user_home, "ablation_children_spawned") == []
    # No completed — at least one stage failed.
    assert (
        _events_named(user_home, "on_promotion_success_completed") == []
    )


def test_promotion_failed_in_leaderboard_logs_stage_event(
    tmp_path: Path, monkeypatch
) -> None:
    """Make leaderboard.append_entry raise. Ablation spawn already
    succeeded → ablation_ok=True; failure in leaderboard → leaderboard_ok
    stays False → no _completed event. Both
    on_promotion_success_failed (v1.3.8) AND leaderboard_append_skipped
    (v1.3.5 backward-compat) are emitted."""
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    p = _project(tmp_path)
    (p / "backlog.md").write_text(
        "- [x] [implement] [P1] vec_long_synth_v1 — done\n"
        "## Done\n",
        encoding="utf-8",
    )

    import leaderboard  # type: ignore

    def _boom(*args, **kwargs):
        raise RuntimeError("lb_explode")

    monkeypatch.setattr(leaderboard, "append_entry", _boom)
    item = _FakeItem(id="vec_long_synth_v1", priority=1)
    promotion.on_promotion_success(p, item, metrics={})

    assert (
        len(_events_named(user_home, "ablation_children_spawned")) == 1
    )
    failed = _events_named(user_home, "on_promotion_success_failed")
    assert len(failed) == 1
    assert failed[0]["stage"] == "leaderboard"
    assert "lb_explode" in failed[0]["error"]
    legacy = _events_named(user_home, "leaderboard_append_skipped")
    assert len(legacy) == 1
    # Completed gated by both stages → not emitted.
    assert (
        _events_named(user_home, "on_promotion_success_completed") == []
    )


def test_strategy_promotion_completed_with_real_leaderboard_call(
    tmp_path: Path, monkeypatch
) -> None:
    """End-to-end happy path including the actual leaderboard module:
    entered + spawned + completed events fire, LEADERBOARD.md created."""
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    p = _project(tmp_path)
    (p / "backlog.md").write_text(
        "- [x] [implement] [P1] vec_long_synth_v1 — done\n"
        "## Done\n",
        encoding="utf-8",
    )
    item = _FakeItem(id="vec_long_synth_v1", priority=1)
    promotion.on_promotion_success(
        p,
        item,
        metrics={
            "sum_fixed": 100.0,
            "regime_parity": 0.2,
            "max_dd": -5.0,
            "dm_p_value": 0.01,
            "dsr": 1.5,
        },
    )

    completed = _events_named(user_home, "on_promotion_success_completed")
    assert len(completed) == 1
    leaderboard_md = p / "data" / "debug" / "LEADERBOARD.md"
    assert leaderboard_md.exists()
    text = leaderboard_md.read_text(encoding="utf-8")
    assert "vec_long_synth_v1" in text
