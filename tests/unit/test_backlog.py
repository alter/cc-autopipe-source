"""Unit tests for src/lib/backlog.py."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_LIB = REPO_ROOT / "src" / "lib"

sys.path.insert(0, str(SRC_LIB))

import backlog  # noqa: E402


SAMPLE = """\
# Backlog

## Open

- [ ] [implement] [P1] cand_imbloss_v2 — SwingLoss + class_balance_beta=0.999
- [ ] [implement] [P0] cand_regimemoe — iTransformer + 3 regime heads
- [ ] [implement] [P1] cand_mamba — replace iTransformer with Mamba SSM
- [ ] [research] [P2] cand_explorer — explore mixture-of-experts variants
- [~] [implement] [P0] cand_inflight — currently being worked on
- [x] [implement] [P0] cand_done — already shipped
- [x] [implement] [P1] cand_done2 — also shipped

## Notes
Some free text that the parser should ignore.
"""


def _write(tmp_path: Path, body: str = SAMPLE) -> Path:
    p = tmp_path / "backlog.md"
    p.write_text(body, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# parse_open_tasks
# ---------------------------------------------------------------------------


def test_parse_open_tasks_skips_done(tmp_path: Path) -> None:
    items = backlog.parse_open_tasks(_write(tmp_path))
    ids = [it.id for it in items]
    assert "cand_done" not in ids
    assert "cand_done2" not in ids


def test_parse_open_tasks_includes_in_progress_marker(tmp_path: Path) -> None:
    """[~] is "in progress" — still open from the engine's perspective.
    Engine wants it surfaced so SessionStart can highlight it."""
    items = backlog.parse_open_tasks(_write(tmp_path))
    ids = [it.id for it in items]
    assert "cand_inflight" in ids
    inflight = next(it for it in items if it.id == "cand_inflight")
    assert inflight.status == "~"
    assert inflight.is_open is True


def test_parse_open_tasks_extracts_priority(tmp_path: Path) -> None:
    items = backlog.parse_open_tasks(_write(tmp_path))
    by_id = {it.id: it for it in items}
    assert by_id["cand_imbloss_v2"].priority == 1
    assert by_id["cand_regimemoe"].priority == 0
    assert by_id["cand_explorer"].priority == 2


def test_parse_open_tasks_default_priority_when_missing(tmp_path: Path) -> None:
    """Tasks without [Pn] tag default to priority 3 (lowest)."""
    p = _write(tmp_path, "- [ ] [implement] no_priority — task description\n")
    items = backlog.parse_open_tasks(p)
    assert items[0].priority == 3


def test_parse_open_tasks_extracts_id_and_description(tmp_path: Path) -> None:
    items = backlog.parse_open_tasks(_write(tmp_path))
    by_id = {it.id: it for it in items}
    assert "SwingLoss" in by_id["cand_imbloss_v2"].description
    assert "iTransformer" in by_id["cand_regimemoe"].description


def test_parse_open_tasks_handles_dash_or_emdash(tmp_path: Path) -> None:
    """Description delimiter accepts em-dash, en-dash, hyphen, or colon."""
    body = (
        "- [ ] [P0] task_a — em-dash\n"
        "- [ ] [P0] task_b – en-dash\n"
        "- [ ] [P0] task_c - hyphen\n"
        "- [ ] [P0] task_d : colon\n"
    )
    items = backlog.parse_open_tasks(_write(tmp_path, body))
    descriptions = {it.id: it.description for it in items}
    assert descriptions["task_a"] == "em-dash"
    assert descriptions["task_b"] == "en-dash"
    assert descriptions["task_c"] == "hyphen"
    assert descriptions["task_d"] == "colon"


def test_parse_open_tasks_missing_file_empty(tmp_path: Path) -> None:
    assert backlog.parse_open_tasks(tmp_path / "nope.md") == []


def test_parse_open_tasks_empty_file_empty(tmp_path: Path) -> None:
    p = tmp_path / "empty.md"
    p.write_text("")
    assert backlog.parse_open_tasks(p) == []


def test_parse_open_tasks_ignores_non_task_lines(tmp_path: Path) -> None:
    body = (
        "# Heading\n"
        "Some preamble text.\n"
        "- [ ] [P0] task_real — desc\n"
        "* not a task line\n"
        "  - [ ] [P0] indented_task — also captured\n"
    )
    items = backlog.parse_open_tasks(_write(tmp_path, body))
    ids = {it.id for it in items}
    assert "task_real" in ids
    # Indented tasks under sub-bullets are also valid markdown lists.
    assert "indented_task" in ids


# ---------------------------------------------------------------------------
# top_n
# ---------------------------------------------------------------------------


def test_top_n_orders_by_priority(tmp_path: Path) -> None:
    items = backlog.parse_open_tasks(_write(tmp_path))
    top3 = backlog.top_n(items, n=3)
    # P0 tasks come first.
    pris = [it.priority for it in top3]
    assert pris == sorted(pris)
    assert pris[0] == 0


def test_top_n_stable_within_priority(tmp_path: Path) -> None:
    """Two P1 tasks must keep file order in the top-3 output (FIFO
    within same priority)."""
    body = (
        "- [ ] [P1] cand_imbloss_v2 — first P1\n"
        "- [ ] [P0] cand_regimemoe — P0\n"
        "- [ ] [P1] cand_mamba — second P1\n"
    )
    items = backlog.parse_open_tasks(_write(tmp_path, body))
    top3 = backlog.top_n(items, n=3)
    ids = [it.id for it in top3]
    # P0 first, then the two P1s in file order.
    assert ids == ["cand_regimemoe", "cand_imbloss_v2", "cand_mamba"]


def test_top_n_returns_at_most_n(tmp_path: Path) -> None:
    items = backlog.parse_open_tasks(_write(tmp_path))
    assert len(backlog.top_n(items, n=2)) == 2
    assert len(backlog.top_n(items, n=10)) <= len(items)


def test_top_n_empty_input(tmp_path: Path) -> None:
    assert backlog.top_n([], n=3) == []


# ---------------------------------------------------------------------------
# parse_top_open convenience
# ---------------------------------------------------------------------------


def test_parse_top_open_end_to_end(tmp_path: Path) -> None:
    top3 = backlog.parse_top_open(_write(tmp_path), n=3)
    ids = [it.id for it in top3]
    # P0s come first; the [~] in_progress P0 is included.
    assert ids[0] in ("cand_regimemoe", "cand_inflight")
    # All three are non-done.
    for it in top3:
        assert it.is_open


# ---------------------------------------------------------------------------
# v1.3.5: task_type property
# ---------------------------------------------------------------------------


def test_task_type_research(tmp_path: Path) -> None:
    items = backlog.parse_open_tasks(_write(tmp_path))
    by_id = {it.id: it for it in items}
    assert by_id["cand_explorer"].task_type == "research"


def test_task_type_implement(tmp_path: Path) -> None:
    items = backlog.parse_open_tasks(_write(tmp_path))
    by_id = {it.id: it for it in items}
    assert by_id["cand_imbloss_v2"].task_type == "implement"


def test_task_type_defaults_to_implement_when_absent(tmp_path: Path) -> None:
    p = _write(tmp_path, "- [ ] [P1] no_role_tag — desc\n")
    items = backlog.parse_open_tasks(p)
    assert items[0].task_type == "implement"


def test_task_type_skips_priority_tag(tmp_path: Path) -> None:
    """[P1] is a priority tag, not a role tag — task_type must not return 'p1'."""
    p = _write(tmp_path, "- [ ] [P1] [implement] swapped_order — desc\n")
    items = backlog.parse_open_tasks(p)
    assert items[0].task_type == "implement"
