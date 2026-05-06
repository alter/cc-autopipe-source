"""Unit tests for src/lib/research_completion.py.

v1.3.5 Group RESEARCH-COMPLETION.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_LIB = REPO_ROOT / "src" / "lib"

sys.path.insert(0, str(SRC_LIB))

import backlog  # noqa: E402
import research_completion as rc  # noqa: E402


def _item(task_id: str, *, task_type: str = "research", priority: int = 1) -> backlog.BacklogItem:
    tags = [f"[{task_type}]", f"[P{priority}]"]
    return backlog.BacklogItem(
        status=" ",
        priority=priority,
        id=task_id,
        description="",
        tags=tags,
        raw_line="",
    )


def _seed_current_task(project: Path, stages: list[str]) -> None:
    cca = project / ".cc-autopipe"
    cca.mkdir(parents=True, exist_ok=True)
    body = "task: " + (stages[0] if stages else "x") + "\n"
    body += f"stages_completed: {', '.join(stages)}\n"
    (cca / "CURRENT_TASK.md").write_text(body, encoding="utf-8")


# ---------------------------------------------------------------------------
# is_research_task
# ---------------------------------------------------------------------------


def test_is_research_task_true_for_research_tag() -> None:
    assert rc.is_research_task(_item("phase_gate_2_1", task_type="research"))


def test_is_research_task_false_for_implement_tag() -> None:
    assert not rc.is_research_task(_item("vec_long_lgbm", task_type="implement"))


def test_is_research_task_false_for_none() -> None:
    assert not rc.is_research_task(None)


# ---------------------------------------------------------------------------
# expected_artifact_glob — pattern dispatch
# ---------------------------------------------------------------------------


def test_expected_artifact_glob_phase_gate() -> None:
    g = rc.expected_artifact_glob(_item("phase_gate_2_1"))
    assert g == "data/debug/SELECTION_phase_gate_2_1.md"


def test_expected_artifact_glob_negative_mining() -> None:
    g = rc.expected_artifact_glob(_item("vec_long_meta_negative_v1"))
    assert g == "data/debug/NEGATIVE_MINING_*.md"


def test_expected_artifact_glob_research_digest() -> None:
    g = rc.expected_artifact_glob(_item("vec_long_meta_research_q3"))
    assert g == "data/debug/RESEARCH_DIGEST_*.md"


def test_expected_artifact_glob_fallback_hypo() -> None:
    g = rc.expected_artifact_glob(_item("vec_long_random_idea"))
    assert g == "data/debug/HYPO_vec_long_random_idea.md"


# ---------------------------------------------------------------------------
# completion_satisfied
# ---------------------------------------------------------------------------


def test_completion_satisfied_phase_gate_full(tmp_path: Path) -> None:
    item = _item("phase_gate_2_1")
    (tmp_path / "data/debug").mkdir(parents=True)
    (tmp_path / "data/debug/SELECTION_phase_gate_2_1.md").write_text(
        "selection notes\n# results\n…", encoding="utf-8"
    )
    _seed_current_task(tmp_path, ["phase_gate_complete"])
    ok, reason = rc.completion_satisfied(tmp_path, item)
    assert ok, f"expected ok, got reason={reason!r}"


def test_completion_satisfied_artifact_missing(tmp_path: Path) -> None:
    item = _item("phase_gate_2_1")
    _seed_current_task(tmp_path, ["phase_gate_complete"])
    ok, reason = rc.completion_satisfied(tmp_path, item)
    assert not ok
    assert reason.startswith("artifact_missing:")


def test_completion_satisfied_artifact_empty_file(tmp_path: Path) -> None:
    item = _item("phase_gate_2_1")
    (tmp_path / "data/debug").mkdir(parents=True)
    (tmp_path / "data/debug/SELECTION_phase_gate_2_1.md").write_text(
        "", encoding="utf-8"
    )
    _seed_current_task(tmp_path, ["phase_gate_complete"])
    ok, reason = rc.completion_satisfied(tmp_path, item)
    assert not ok
    assert reason.startswith("artifact_missing:")


def test_completion_satisfied_verdict_stage_missing(tmp_path: Path) -> None:
    item = _item("phase_gate_2_1")
    (tmp_path / "data/debug").mkdir(parents=True)
    (tmp_path / "data/debug/SELECTION_phase_gate_2_1.md").write_text(
        "x", encoding="utf-8"
    )
    _seed_current_task(tmp_path, ["something_else"])
    ok, reason = rc.completion_satisfied(tmp_path, item)
    assert not ok
    assert reason == "research_verdict_stage_missing"


def test_completion_satisfied_negative_mining_glob_match(tmp_path: Path) -> None:
    item = _item("vec_long_meta_negative_v1")
    (tmp_path / "data/debug").mkdir(parents=True)
    (tmp_path / "data/debug/NEGATIVE_MINING_2026-06-15.md").write_text(
        "mining notes", encoding="utf-8"
    )
    _seed_current_task(tmp_path, ["negative_mining_complete"])
    ok, reason = rc.completion_satisfied(tmp_path, item)
    assert ok, f"expected ok, got reason={reason!r}"


def test_completion_satisfied_implement_falls_through(tmp_path: Path) -> None:
    item = _item("vec_long_lgbm", task_type="implement")
    ok, reason = rc.completion_satisfied(tmp_path, item)
    assert not ok
    assert reason == "not_a_research_task"


# ---------------------------------------------------------------------------
# find_top_research_task
# ---------------------------------------------------------------------------


def test_find_top_research_task_skips_implement(tmp_path: Path) -> None:
    backlog_md = tmp_path / "backlog.md"
    backlog_md.write_text(
        "- [ ] [implement] [P0] vec_long_lgbm — top\n"
        "- [ ] [research] [P2] phase_gate_2_1 — research item\n",
        encoding="utf-8",
    )
    item = rc.find_top_research_task(tmp_path)
    assert item is not None
    assert item.id == "phase_gate_2_1"


def test_find_top_research_task_returns_none_when_no_research(tmp_path: Path) -> None:
    backlog_md = tmp_path / "backlog.md"
    backlog_md.write_text(
        "- [ ] [implement] [P0] vec_long_lgbm — top\n",
        encoding="utf-8",
    )
    assert rc.find_top_research_task(tmp_path) is None
