"""Integration tests for v1.3.6 SENTINEL-PATTERNS PROMOTION mtime fallback.

`_maybe_arm_sentinel_via_promotion` arms the knowledge.md sentinel when
a fresh PROMOTION.md is written with a parseable verdict, even if the
task's CURRENT_TASK.md `stages_completed` never contained a
verdict-pattern stage. Defense-in-depth: Claude task discipline may
forget to emit a verdict-named stage; the engine reads the artifact.

Covers:
- Fresh PROMOTION + non-verdict stage → sentinel armed via fallback
- Stale PROMOTION (>5 min) → sentinel NOT armed
- No PROMOTION file → sentinel NOT armed
- Sentinel already armed → fallback is a no-op (idempotent)
- Non-vec_/phase_gate_ task id → fallback skipped (out-of-scope tasks
  shouldn't tickle the sentinel through this path)
- Unparseable verdict → sentinel NOT armed
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "src" / "lib"))

from orchestrator import cycle  # noqa: E402
import promotion as promotion_lib  # noqa: E402
import state  # noqa: E402


def _project(tmp_path: Path) -> Path:
    p = tmp_path / "demo"
    (p / ".cc-autopipe" / "memory").mkdir(parents=True)
    (p / "data" / "debug").mkdir(parents=True)
    return p


def _write_promotion(project: Path, task_id: str, body: str) -> Path:
    p = promotion_lib.promotion_path(project, task_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def _read_aggregate(user_home: Path) -> list[dict]:
    p = user_home / "log" / "aggregate.jsonl"
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]


def test_fresh_promotion_arms_sentinel_via_fallback(
    tmp_path: Path, monkeypatch
) -> None:
    """Task closed with stages_completed=['implementation'] (no verdict
    word). Fresh PROMOTION.md with parseable verdict is the only signal.
    Fallback should arm the sentinel and emit
    `knowledge_sentinel_armed_via_promotion`."""
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    p = _project(tmp_path)
    _write_promotion(
        p,
        "vec_long_lgbm",
        "## Verdict\n\n### STABLE — model ready\n",
    )
    s = state.State.fresh(p.name)
    state.write(p, s)

    armed = cycle._maybe_arm_sentinel_via_promotion(p, "vec_long_lgbm", s)
    assert armed is True

    s2 = state.read(p)
    assert s2.knowledge_update_pending is True
    assert s2.last_verdict_task_id == "vec_long_lgbm"
    assert "promotion_mtime_fallback" in (s2.knowledge_pending_reason or "")

    events = [
        e
        for e in _read_aggregate(user_home)
        if e.get("event") == "knowledge_sentinel_armed_via_promotion"
    ]
    assert len(events) == 1
    assert events[0]["task_id"] == "vec_long_lgbm"
    assert events[0]["promotion_mtime_age_sec"] >= 0


def test_stale_promotion_older_than_5_min_does_not_arm(
    tmp_path: Path, monkeypatch
) -> None:
    """A PROMOTION.md modified more than 5 minutes ago must NOT re-arm
    the sentinel — otherwise every subsequent cycle would re-fire on
    the same stale artifact."""
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    p = _project(tmp_path)
    promo = _write_promotion(
        p,
        "vec_long_lgbm",
        "## Verdict\n\n### STABLE — model ready\n",
    )
    # Backdate mtime 10 minutes
    stale_mtime = time.time() - 600
    os.utime(promo, (stale_mtime, stale_mtime))

    s = state.State.fresh(p.name)
    state.write(p, s)

    armed = cycle._maybe_arm_sentinel_via_promotion(p, "vec_long_lgbm", s)
    assert armed is False
    assert state.read(p).knowledge_update_pending is False


def test_no_promotion_file_does_not_arm(tmp_path: Path, monkeypatch) -> None:
    """No PROMOTION.md at the resolved path → fallback is a no-op."""
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    p = _project(tmp_path)
    s = state.State.fresh(p.name)
    state.write(p, s)
    armed = cycle._maybe_arm_sentinel_via_promotion(p, "vec_long_lgbm", s)
    assert armed is False
    assert state.read(p).knowledge_update_pending is False


def test_sentinel_already_armed_skips_fallback(
    tmp_path: Path, monkeypatch
) -> None:
    """If the stage-based arming path already set
    knowledge_update_pending=True, the fallback should be a no-op even
    when a fresh PROMOTION exists — idempotent."""
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    p = _project(tmp_path)
    _write_promotion(
        p, "vec_long_lgbm", "## Verdict\n\n### STABLE — ready\n"
    )
    s = state.State.fresh(p.name)
    s.knowledge_update_pending = True
    s.knowledge_pending_reason = "stage_e_verdict on vec_long_lgbm"
    state.write(p, s)

    armed = cycle._maybe_arm_sentinel_via_promotion(p, "vec_long_lgbm", s)
    assert armed is False
    s2 = state.read(p)
    assert s2.knowledge_update_pending is True
    # Reason unchanged (stage-based path still owns it).
    assert s2.knowledge_pending_reason == "stage_e_verdict on vec_long_lgbm"

    events = [
        e
        for e in _read_aggregate(user_home)
        if e.get("event") == "knowledge_sentinel_armed_via_promotion"
    ]
    assert events == []


def test_non_vec_task_id_skips_fallback(
    tmp_path: Path, monkeypatch
) -> None:
    """A regular task without vec_ / phase_gate_ prefix doesn't go
    through the PROMOTION pipeline — the fallback should not fire even
    if a PROMOTION-shaped file happens to exist at that path."""
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    p = _project(tmp_path)
    _write_promotion(
        p, "regular_task", "## Verdict\n\n### PASS — done\n"
    )
    s = state.State.fresh(p.name)
    state.write(p, s)
    armed = cycle._maybe_arm_sentinel_via_promotion(p, "regular_task", s)
    assert armed is False


def test_unparseable_promotion_does_not_arm(
    tmp_path: Path, monkeypatch
) -> None:
    """A PROMOTION.md present but without any verdict heading or keyword
    → parse_verdict returns None → fallback no-op."""
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    p = _project(tmp_path)
    _write_promotion(p, "vec_long_lgbm", "no verdict here, just notes\n")
    s = state.State.fresh(p.name)
    state.write(p, s)
    armed = cycle._maybe_arm_sentinel_via_promotion(p, "vec_long_lgbm", s)
    assert armed is False
    assert state.read(p).knowledge_update_pending is False


def test_phase_gate_task_id_arms_via_fallback(
    tmp_path: Path, monkeypatch
) -> None:
    """Phase-gate tasks also produce PROMOTION reports and benefit from
    the fallback. Pin the prefix so a future refactor doesn't drop it."""
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    p = _project(tmp_path)
    _write_promotion(
        p, "phase_gate_2", "## Verdict\n\n### PASS — gate cleared\n"
    )
    s = state.State.fresh(p.name)
    state.write(p, s)
    armed = cycle._maybe_arm_sentinel_via_promotion(p, "phase_gate_2", s)
    assert armed is True
    assert state.read(p).knowledge_update_pending is True
