"""Integration tests for cycle._post_cycle_delta_scan (v1.3.10).

Exercises the post-cycle delta scan helper in isolation — no full cycle,
no state.read/write. Each test owns its own tmp project + user_home.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
LIB = SRC / "lib"
for _p in (str(SRC), str(LIB)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from orchestrator import cycle  # noqa: E402
import promotion  # noqa: E402
import backlog as backlog_lib  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture bodies
# ---------------------------------------------------------------------------

_PROMOTED_FULL = """\
**Verdict: PROMOTED**

## Long-only verification
yes
## Regime-stratified PnL
yes
## Statistical significance
yes
## Walk-forward stability
yes
## No-lookahead audit
yes
"""

_PROMOTED_NO_SECTIONS = """\
**Verdict: PROMOTED**

(no sections)
"""

_REJECTED = """\
**Verdict: REJECTED**
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _project(tmp_path: Path) -> Path:
    """Create a minimal project tree under tmp_path/demo."""
    p = tmp_path / "demo"
    (p / ".cc-autopipe" / "memory").mkdir(parents=True)
    (p / "data" / "debug").mkdir(parents=True)
    return p


def _read_aggregate(user_home: Path) -> list[dict]:
    """Read aggregate.jsonl; return [] when absent."""
    p = user_home / "log" / "aggregate.jsonl"
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]


def _events_named(user_home: Path, name: str) -> list[dict]:
    """Filter aggregate events by event name."""
    return [e for e in _read_aggregate(user_home) if e.get("event") == name]


def _write_promotion(project: Path, task_id: str, body: str) -> None:
    """Write a PROMOTION.md fixture via promotion.promotion_path."""
    p = promotion.promotion_path(project, task_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def _backlog_line(task_id: str, status: str = "x", desc: str = "done") -> str:
    """Return a single backlog.md task line."""
    return f"- [{status}] [implement] [P1] {task_id} — {desc}\n"


# ---------------------------------------------------------------------------
# Test 1: pre-existing task excluded; only new mid-cycle task fires events
# ---------------------------------------------------------------------------

def test_pre_existing_excluded_only_new_via_delta_path(
    tmp_path: Path, monkeypatch
) -> None:
    """vec_long_pre was open at cycle start → excluded by pre_ids.
    vec_long_new_mid was NOT in pre_open snapshot → delta path fires.
    Only vec_long_new_mid should produce promotion_validated_attempt.
    """
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    project = _project(tmp_path)

    # Write backlog with both tasks closed [x]
    (project / "backlog.md").write_text(
        _backlog_line("vec_long_pre", status="x", desc="pre")
        + _backlog_line("vec_long_new_mid", status="x", desc="mid")
        + "## Done\n",
        encoding="utf-8",
    )

    # Both have full v2 PROMOTION.md
    _write_promotion(project, "vec_long_pre", _PROMOTED_FULL)
    _write_promotion(project, "vec_long_new_mid", _PROMOTED_FULL)

    # Simulate pre-cycle snapshot: vec_long_pre was open at cycle start.
    # _post_cycle_delta_scan only reads .id from these items.
    pre_item = backlog_lib.BacklogItem(
        status=" ",
        priority=1,
        id="vec_long_pre",
        description="pre",
        tags=["[implement]", "[P1]"],
        raw_line="- [ ] [implement] [P1] vec_long_pre — pre",
    )

    cycle._post_cycle_delta_scan(project, [pre_item])

    # Only vec_long_new_mid should appear in promotion_validated_attempt
    attempts = _events_named(user_home, "promotion_validated_attempt")
    assert len(attempts) == 1, f"expected 1 attempt, got {attempts}"
    assert attempts[0]["task_id"] == "vec_long_new_mid"
    assert attempts[0]["origin"] == "post_cycle_delta"

    # vec_long_pre must not appear in any event at all
    all_events = _read_aggregate(user_home)
    for ev in all_events:
        assert ev.get("task_id") != "vec_long_pre", (
            f"vec_long_pre leaked into events: {ev}"
        )


# ---------------------------------------------------------------------------
# Test 2: empty pre_open, mid-cycle added task PROMOTED with full sections
# ---------------------------------------------------------------------------

def test_empty_precycle_with_mid_cycle_added_promoted(
    tmp_path: Path, monkeypatch
) -> None:
    """pre_open empty → all closed vec_long_* tasks are delta candidates.
    vec_long_added_x is NOT a strategy prefix → strict=False, relaxed ok.
    Expect: validated_attempt, v2_sections_check(all_present=True, strict=False),
    promotion_validated, ablation_children_spawned(count=5).
    """
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    project = _project(tmp_path)

    (project / "backlog.md").write_text(
        _backlog_line("vec_long_added_x", status="x") + "## Done\n",
        encoding="utf-8",
    )
    _write_promotion(project, "vec_long_added_x", _PROMOTED_FULL)

    cycle._post_cycle_delta_scan(project, [])

    # promotion_validated_attempt with origin=post_cycle_delta
    attempts = _events_named(user_home, "promotion_validated_attempt")
    assert len(attempts) == 1
    assert attempts[0]["origin"] == "post_cycle_delta"

    # v2 sections check: non-strategy prefix → strict=False, all_present=True
    checks = _events_named(user_home, "promotion_v2_sections_check")
    assert len(checks) == 1
    assert checks[0]["origin"] == "post_cycle_delta"
    assert checks[0]["all_present"] is True
    assert checks[0]["strict"] is False

    # promotion_validated
    validated = _events_named(user_home, "promotion_validated")
    assert len(validated) == 1
    assert validated[0]["origin"] == "post_cycle_delta"

    # ablation children spawned (count=5)
    spawned = _events_named(user_home, "ablation_children_spawned")
    assert len(spawned) == 1
    assert spawned[0]["count"] == 5


# ---------------------------------------------------------------------------
# Test 3: strategy prefix + missing sections → quarantined
# ---------------------------------------------------------------------------

def test_strategy_prefix_missing_sections_quarantines(
    tmp_path: Path, monkeypatch
) -> None:
    """vec_long_synth_meta_v1 is a strategy prefix (strict=True).
    PROMOTION has verdict PROMOTED but NO v2 sections → quarantined.
    Expect: v2_sections_check(strict=True, all_present=False),
    NO promotion_validated, promotion_invalid event,
    UNVALIDATED_PROMOTION_vec_long_synth_meta_v1.md exists.
    """
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    project = _project(tmp_path)

    (project / "backlog.md").write_text(
        _backlog_line("vec_long_synth_meta_v1", status="x"),
        encoding="utf-8",
    )
    _write_promotion(project, "vec_long_synth_meta_v1", _PROMOTED_NO_SECTIONS)

    cycle._post_cycle_delta_scan(project, [])

    checks = _events_named(user_home, "promotion_v2_sections_check")
    assert len(checks) == 1
    assert checks[0]["origin"] == "post_cycle_delta"
    assert checks[0]["strict"] is True
    assert checks[0]["all_present"] is False

    # No promotion_validated
    assert _events_named(user_home, "promotion_validated") == []

    # quarantine_invalid emits promotion_invalid
    invalid = _events_named(user_home, "promotion_invalid")
    assert len(invalid) == 1
    assert invalid[0]["task_id"] == "vec_long_synth_meta_v1"

    # Quarantine marker file written
    quar = project / "data" / "debug" / "UNVALIDATED_PROMOTION_vec_long_synth_meta_v1.md"
    assert quar.exists(), "quarantine marker file not created"


# ---------------------------------------------------------------------------
# Test 4: non-strategy prefix + missing sections → relaxed ok → validated
# ---------------------------------------------------------------------------

def test_non_strategy_prefix_missing_sections_relaxes_to_ok(
    tmp_path: Path, monkeypatch
) -> None:
    """vec_long_q_compressed_x is NOT a strategy prefix → relaxed.
    Even with missing v2 sections validate_v2_sections returns ok=True.
    Expect: v2_sections_check(strict=False, all_present=True),
    promotion_validated, ablation_children_spawned(count=5).
    """
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    project = _project(tmp_path)

    (project / "backlog.md").write_text(
        _backlog_line("vec_long_q_compressed_x", status="x") + "## Done\n",
        encoding="utf-8",
    )
    _write_promotion(project, "vec_long_q_compressed_x", _PROMOTED_NO_SECTIONS)

    cycle._post_cycle_delta_scan(project, [])

    checks = _events_named(user_home, "promotion_v2_sections_check")
    assert len(checks) == 1
    assert checks[0]["origin"] == "post_cycle_delta"
    assert checks[0]["strict"] is False
    assert checks[0]["all_present"] is True

    validated = _events_named(user_home, "promotion_validated")
    assert len(validated) == 1
    assert validated[0]["origin"] == "post_cycle_delta"

    spawned = _events_named(user_home, "ablation_children_spawned")
    assert len(spawned) == 1
    assert spawned[0]["count"] == 5


# ---------------------------------------------------------------------------
# Test 5: REJECTED verdict via delta path
# ---------------------------------------------------------------------------

def test_rejected_verdict_via_delta_path(
    tmp_path: Path, monkeypatch
) -> None:
    """REJECTED verdict → promotion_rejected with origin=post_cycle_delta.
    No promotion_validated_attempt, no ablation children.
    """
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    project = _project(tmp_path)

    (project / "backlog.md").write_text(
        _backlog_line("vec_long_meta_failed", status="x"),
        encoding="utf-8",
    )
    _write_promotion(project, "vec_long_meta_failed", _REJECTED)

    cycle._post_cycle_delta_scan(project, [])

    rejected = _events_named(user_home, "promotion_rejected")
    assert len(rejected) == 1
    assert rejected[0]["task_id"] == "vec_long_meta_failed"
    assert rejected[0]["origin"] == "post_cycle_delta"

    assert _events_named(user_home, "promotion_validated_attempt") == []
    assert _events_named(user_home, "ablation_children_spawned") == []


# ---------------------------------------------------------------------------
# Test 6: missing PROMOTION.md → unrecognized + missing events
# ---------------------------------------------------------------------------

def test_missing_promotion_md_emits_unrecognized_and_missing(
    tmp_path: Path, monkeypatch
) -> None:
    """No PROMOTION.md written → parse_verdict returns None.
    Expect: promotion_verdict_unrecognized AND promotion_verdict_missing,
    both with origin=post_cycle_delta for the same task_id.
    """
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    project = _project(tmp_path)

    (project / "backlog.md").write_text(
        _backlog_line("vec_long_no_promotion", status="x"),
        encoding="utf-8",
    )
    # Deliberately do NOT write any PROMOTION.md

    cycle._post_cycle_delta_scan(project, [])

    unrecognized = _events_named(user_home, "promotion_verdict_unrecognized")
    assert len(unrecognized) == 1
    assert unrecognized[0]["task_id"] == "vec_long_no_promotion"
    assert unrecognized[0]["origin"] == "post_cycle_delta"

    missing_ev = _events_named(user_home, "promotion_verdict_missing")
    assert len(missing_ev) == 1
    assert missing_ev[0]["task_id"] == "vec_long_no_promotion"
    assert missing_ev[0]["origin"] == "post_cycle_delta"


# ---------------------------------------------------------------------------
# Test 7: idempotency — pre_open id excluded, zero delta events for it
# ---------------------------------------------------------------------------

def test_idempotency_pre_and_post_no_double_emit(
    tmp_path: Path, monkeypatch
) -> None:
    """vec_long_dup_id is in pre_open → excluded by pre_ids set.
    Even though backlog shows [x] and a valid PROMOTION.md exists,
    the delta scan must emit ZERO events with origin=post_cycle_delta
    for vec_long_dup_id.
    """
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    project = _project(tmp_path)

    (project / "backlog.md").write_text(
        _backlog_line("vec_long_dup_id", status="x") + "## Done\n",
        encoding="utf-8",
    )
    _write_promotion(project, "vec_long_dup_id", _PROMOTED_FULL)

    # Simulate pre-cycle snapshot that already contained vec_long_dup_id
    pre_item = backlog_lib.BacklogItem(
        status=" ",
        priority=1,
        id="vec_long_dup_id",
        description="dup",
        tags=["[implement]", "[P1]"],
        raw_line="- [ ] [implement] [P1] vec_long_dup_id — dup",
    )

    cycle._post_cycle_delta_scan(project, [pre_item])

    # The delta scan must not emit any origin=post_cycle_delta event for this id
    all_events = _read_aggregate(user_home)
    delta_events_for_dup = [
        e for e in all_events
        if e.get("task_id") == "vec_long_dup_id"
        and e.get("origin") == "post_cycle_delta"
    ]
    assert delta_events_for_dup == [], (
        f"delta scan emitted events for excluded id: {delta_events_for_dup}"
    )
