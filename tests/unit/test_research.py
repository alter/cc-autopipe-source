"""Unit tests for src/orchestrator/research.py — PROMPT_v1.3-FULL.md D."""

from __future__ import annotations

import importlib
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

research = importlib.import_module("orchestrator.research")


def _project(tmp_path: Path) -> Path:
    p = tmp_path / "demo"
    (p / ".cc-autopipe").mkdir(parents=True)
    return p


# ---------------------------------------------------------------------------
# detect_prd_complete (D1)
# ---------------------------------------------------------------------------


def test_prd_complete_no_backlog_returns_false(tmp_path: Path) -> None:
    p = _project(tmp_path)
    assert research.detect_prd_complete(p) is False


def test_prd_complete_with_open_returns_false(tmp_path: Path) -> None:
    p = _project(tmp_path)
    (p / "backlog.md").write_text(
        "- [ ] [P1] open task\n- [x] done\n"
    )
    assert research.detect_prd_complete(p) is False


def test_prd_complete_tilde_blocks_completion(tmp_path: Path) -> None:
    """v1.5.6 TILDE-IS-OPEN flip: a `[~]` line is now actionable, so
    `detect_prd_complete` must return False while any remain.
    Pre-v1.5.6 the same backlog (1 `[x]` + 1 `[~]`) was treated as
    complete, which let agents self-block by marking tasks `[~]`."""
    p = _project(tmp_path)
    (p / "backlog.md").write_text("- [x] done\n- [~] in-progress\n")
    assert research.detect_prd_complete(p) is False


def test_prd_complete_all_done_returns_true(tmp_path: Path) -> None:
    """Pure `[x]` backlog still counts as complete — this is the
    `[~]`-free case the v1.5.5 test used to cover."""
    p = _project(tmp_path)
    (p / "backlog.md").write_text("- [x] done\n- [x] another\n")
    assert research.detect_prd_complete(p) is True


def test_prd_complete_falls_back_to_cca_path(tmp_path: Path) -> None:
    p = _project(tmp_path)
    (p / ".cc-autopipe" / "backlog.md").write_text("- [x] done\n")
    assert research.detect_prd_complete(p) is True


# ---------------------------------------------------------------------------
# Quota gate + iteration cap (D2)
# ---------------------------------------------------------------------------


def test_check_quota_gate_unknown_quota_permissive(monkeypatch) -> None:
    monkeypatch.setattr(research, "_quota_seven_day_pct", lambda: None)
    assert research.check_quota_gate() is True


def test_check_quota_gate_below_threshold(monkeypatch) -> None:
    monkeypatch.setattr(research, "_quota_seven_day_pct", lambda: 0.50)
    assert research.check_quota_gate() is True


def test_check_quota_gate_above_threshold(monkeypatch) -> None:
    monkeypatch.setattr(research, "_quota_seven_day_pct", lambda: 0.85)
    assert research.check_quota_gate() is False


def test_activate_research_mode_normal_path(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    monkeypatch.setattr(research, "_quota_seven_day_pct", lambda: 0.30)
    p = _project(tmp_path)
    s = state.State.fresh(p.name)
    state.write(p, s)
    out = research.activate_research_mode(p, s)
    assert out == "active"
    s2 = state.read(p)
    assert s2.research_mode_active is True
    assert s2.research_plan_required is True
    assert s2.research_plan_target is not None
    assert "RESEARCH_PLAN_" in s2.research_plan_target
    assert len(s2.research_iterations_this_window) == 1


def test_activate_research_mode_quota_suspended(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    monkeypatch.setattr(research, "_quota_seven_day_pct", lambda: 0.85)
    p = _project(tmp_path)
    s = state.State.fresh(p.name)
    state.write(p, s)
    out = research.activate_research_mode(p, s)
    assert out == "suspended_quota"
    assert state.read(p).research_mode_active is False


def test_activate_research_mode_capped_after_3(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    monkeypatch.setattr(research, "_quota_seven_day_pct", lambda: 0.30)
    p = _project(tmp_path)
    s = state.State.fresh(p.name)
    now = datetime.now(timezone.utc)
    s.research_iterations_this_window = [
        (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        (now - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        (now - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    ]
    state.write(p, s)
    out = research.activate_research_mode(p, s)
    assert out == "capped"
    assert state.read(p).research_mode_active is False


def test_activate_research_mode_drops_old_window(
    tmp_path: Path, monkeypatch
) -> None:
    """Iterations older than 7d should NOT count toward the cap."""
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    monkeypatch.setattr(research, "_quota_seven_day_pct", lambda: 0.30)
    p = _project(tmp_path)
    s = state.State.fresh(p.name)
    old = datetime.now(timezone.utc) - timedelta(days=10)
    s.research_iterations_this_window = [
        old.strftime("%Y-%m-%dT%H:%M:%SZ"),
        old.strftime("%Y-%m-%dT%H:%M:%SZ"),
        old.strftime("%Y-%m-%dT%H:%M:%SZ"),
    ]
    state.write(p, s)
    out = research.activate_research_mode(p, s)
    assert out == "active"


# ---------------------------------------------------------------------------
# validate_research_plan (D3)
# ---------------------------------------------------------------------------


def test_validate_no_plan_required_returns_no_plan(tmp_path: Path) -> None:
    p = _project(tmp_path)
    s = state.State.fresh(p.name)
    s.research_plan_required = False
    state.write(p, s)
    assert research.validate_research_plan(p, s, None, []) == "no_plan_required"


def test_validate_plan_filed_clears_flag(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    p = _project(tmp_path)
    plan_target = p / "data" / "debug" / "RESEARCH_PLAN_xxx.md"
    plan_target.parent.mkdir(parents=True, exist_ok=True)
    plan_target.write_text("# plan\n")
    s = state.State.fresh(p.name)
    s.research_plan_required = True
    s.research_plan_target = str(plan_target)
    state.write(p, s)
    out = research.validate_research_plan(p, s, None, [])
    assert out == "filed"
    assert state.read(p).research_plan_required is False


def test_validate_quarantines_new_lines_without_plan(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    p = _project(tmp_path)
    (p / "backlog.md").write_text(
        "- [x] done\n"
        "- [ ] [P1] vec_new — proposed\n"
        "- [ ] [P1] vec_other — also proposed\n"
    )
    s = state.State.fresh(p.name)
    s.research_plan_required = True
    s.research_plan_target = str(p / "data/debug/RESEARCH_PLAN_xxx.md")
    state.write(p, s)
    out = research.validate_research_plan(
        p,
        s,
        cycle_started_iso="2026-05-04T17:24:06Z",
        pre_open_lines=[],  # no open lines BEFORE → both are new
    )
    assert out == "violation"
    backlog_after = (p / "backlog.md").read_text()
    assert "vec_new" not in backlog_after
    assert "vec_other" not in backlog_after
    quarantine = list((p / ".cc-autopipe").glob("UNVALIDATED_BACKLOG_*.md"))
    assert len(quarantine) == 1
    body = quarantine[0].read_text()
    assert "vec_new" in body
    assert "vec_other" in body


def test_validate_no_violation_when_no_new_lines(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    p = _project(tmp_path)
    (p / "backlog.md").write_text("- [ ] [P1] preexisting\n")
    s = state.State.fresh(p.name)
    s.research_plan_required = True
    s.research_plan_target = str(p / "data/debug/RESEARCH_PLAN_xxx.md")
    state.write(p, s)
    out = research.validate_research_plan(
        p,
        s,
        cycle_started_iso="2026-05-04T17:24:06Z",
        pre_open_lines=["- [ ] [P1] preexisting"],
    )
    assert out == "ok"
    # Backlog untouched.
    assert "preexisting" in (p / "backlog.md").read_text()


# ---------------------------------------------------------------------------
# build_research_mode_block (D2 injection)
# ---------------------------------------------------------------------------


def test_block_empty_when_research_inactive(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    p = _project(tmp_path)
    s = state.State.fresh(p.name)
    state.write(p, s)
    assert research.build_research_mode_block(p) == ""


def test_block_emitted_when_active(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    p = _project(tmp_path)
    s = state.State.fresh(p.name)
    s.research_mode_active = True
    s.research_plan_target = "data/debug/RESEARCH_PLAN_TEST.md"
    state.write(p, s)
    block = research.build_research_mode_block(p)
    assert "RESEARCH MODE ACTIVE" in block
    assert "RESEARCH_PLAN_TEST.md" in block
    assert "Cosmetic differences re-fail" in block
