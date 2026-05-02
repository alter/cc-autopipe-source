"""Unit tests for src/lib/current_task.py.

Covers SPEC-v1.2.md Bug A "Mechanism" (CURRENT_TASK.md format) and
the parse/write/round-trip contract.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_LIB = REPO_ROOT / "src" / "lib"

sys.path.insert(0, str(SRC_LIB))

import current_task  # noqa: E402


# ---------------------------------------------------------------------------
# parse_text
# ---------------------------------------------------------------------------


def test_parse_empty_returns_empty() -> None:
    assert current_task.parse_text("") == {}
    assert current_task.parse_text("   \n\n  ") == {}


def test_parse_minimal_task_id_only() -> None:
    out = current_task.parse_text("task: cand_imbloss_v2\n")
    assert out == {"id": "cand_imbloss_v2"}


def test_parse_full_spec_example() -> None:
    src = (
        "task: cand_imbloss_v2\n"
        "stage: training\n"
        "artifact: data/models/exp_cand_imbloss_v2/\n"
        "notes: SwingLoss with class_balance_beta=0.999, training started\n"
    )
    out = current_task.parse_text(src)
    assert out["id"] == "cand_imbloss_v2"
    assert out["stage"] == "training"
    assert out["artifact_paths"] == ["data/models/exp_cand_imbloss_v2/"]
    assert "SwingLoss" in out["claude_notes"]


def test_parse_stages_completed_bracket_form() -> None:
    out = current_task.parse_text("stages_completed: [hypothesis, training]\n")
    assert out["stages_completed"] == ["hypothesis", "training"]


def test_parse_stages_completed_csv_form() -> None:
    out = current_task.parse_text("stages_completed: hypothesis, training, backtests\n")
    assert out["stages_completed"] == ["hypothesis", "training", "backtests"]


def test_parse_stages_completed_empty() -> None:
    """Empty value → empty list, not [''] or [' ']."""
    out = current_task.parse_text("stages_completed: \n")
    assert out["stages_completed"] == []


def test_parse_multiple_artifact_lines_accumulate() -> None:
    src = (
        "task: x\n"
        "artifact: data/models/foo/\n"
        "artifact: data/reports/bar.md\n"
    )
    out = current_task.parse_text(src)
    assert out["artifact_paths"] == ["data/models/foo/", "data/reports/bar.md"]


def test_parse_artifact_paths_alias() -> None:
    """`artifact_paths:` (alias) parses identically to `artifact:`."""
    out = current_task.parse_text("artifact_paths: data/models/foo/\n")
    assert out["artifact_paths"] == ["data/models/foo/"]


def test_parse_multiline_notes_continuation() -> None:
    src = (
        "task: x\n"
        "stage: training\n"
        "notes: First line.\n"
        "Second line is continuation.\n"
        "Third line too.\n"
    )
    out = current_task.parse_text(src)
    assert out["claude_notes"] == (
        "First line.\nSecond line is continuation.\nThird line too."
    )


def test_parse_unknown_keys_ignored() -> None:
    """Unknown keys silently dropped — defends against typos / future fields."""
    src = "task: x\nweird_key: y\nfoo: bar\n"
    out = current_task.parse_text(src)
    assert out == {"id": "x"}


def test_parse_leading_freeform_text_ignored() -> None:
    """Lines before the first recognized key (e.g. a markdown title)
    are dropped so users can prepend a heading if they want."""
    src = (
        "# Current task\n"
        "Some preamble.\n"
        "\n"
        "task: x\n"
        "stage: y\n"
    )
    out = current_task.parse_text(src)
    assert out == {"id": "x", "stage": "y"}


def test_parse_value_with_colons_kept_intact() -> None:
    """A value that itself contains `:` (timestamps, paths) must not
    be re-split. Only the FIRST `:` separates key from value."""
    src = "notes: Started at 18:00:00 UTC, ETA 19:30\n"
    out = current_task.parse_text(src)
    assert out["claude_notes"] == "Started at 18:00:00 UTC, ETA 19:30"


# ---------------------------------------------------------------------------
# parse_file
# ---------------------------------------------------------------------------


def test_parse_file_missing_returns_empty(tmp_path: Path) -> None:
    assert current_task.parse_file(tmp_path / "nope.md") == {}


def test_parse_file_empty_returns_empty(tmp_path: Path) -> None:
    p = tmp_path / "empty.md"
    p.write_text("")
    assert current_task.parse_file(p) == {}


def test_parse_file_reads_disk(tmp_path: Path) -> None:
    p = tmp_path / "ct.md"
    p.write_text("task: from_disk\nstage: ready\n", encoding="utf-8")
    out = current_task.parse_file(p)
    assert out == {"id": "from_disk", "stage": "ready"}


# ---------------------------------------------------------------------------
# render + write_file round-trip
# ---------------------------------------------------------------------------


def test_render_empty_returns_empty_string() -> None:
    assert current_task.render({}) == ""


def test_render_then_parse_round_trip(tmp_path: Path) -> None:
    data = {
        "id": "cand_imbloss_v2",
        "stage": "backtests",
        "stages_completed": ["hypothesis", "training"],
        "artifact_paths": [
            "data/models/exp_cand_imbloss_v2/",
            "data/reports/cand_imbloss_v2/backtests.md",
        ],
        "claude_notes": "Training done. Starting backtests.",
    }
    text = current_task.render(data)
    parsed = current_task.parse_text(text)
    assert parsed == data


def test_write_file_round_trip(tmp_path: Path) -> None:
    p = tmp_path / "ct.md"
    data = {"id": "x", "stage": "y", "claude_notes": "n"}
    current_task.write_file(p, data)
    assert current_task.parse_file(p) == data


def test_write_file_atomic_replaces_existing(tmp_path: Path) -> None:
    p = tmp_path / "ct.md"
    p.write_text("task: old\nstage: old\n", encoding="utf-8")
    current_task.write_file(p, {"id": "new", "stage": "new"})
    parsed = current_task.parse_file(p)
    assert parsed == {"id": "new", "stage": "new"}
    # No leftover .tmp file after replace().
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == []


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_parse_emits_json(tmp_path: Path) -> None:
    p = tmp_path / "ct.md"
    p.write_text("task: x\nstage: y\n", encoding="utf-8")
    cp = subprocess.run(
        [sys.executable, str(SRC_LIB / "current_task.py"), "parse", str(p)],
        check=True,
        capture_output=True,
        text=True,
    )
    out = json.loads(cp.stdout)
    assert out == {"id": "x", "stage": "y"}


def test_cli_write_persists_disk(tmp_path: Path) -> None:
    p = tmp_path / "ct.md"
    payload = json.dumps({"id": "z", "stage": "init"})
    subprocess.run(
        [
            sys.executable,
            str(SRC_LIB / "current_task.py"),
            "write",
            str(p),
            payload,
        ],
        check=True,
    )
    assert current_task.parse_file(p) == {"id": "z", "stage": "init"}


def test_cli_write_rejects_non_object(tmp_path: Path) -> None:
    p = tmp_path / "ct.md"
    cp = subprocess.run(
        [
            sys.executable,
            str(SRC_LIB / "current_task.py"),
            "write",
            str(p),
            json.dumps([1, 2, 3]),
        ],
        capture_output=True,
        text=True,
    )
    assert cp.returncode != 0
    assert "object" in cp.stderr


# ---------------------------------------------------------------------------
# Compatibility with state.CurrentTask
# ---------------------------------------------------------------------------


def test_parsed_dict_feeds_state_currenttask() -> None:
    """The dict produced by parse_text must be directly consumable by
    state.CurrentTask.from_dict — that's the whole integration contract."""
    import state

    parsed = current_task.parse_text(
        "task: x\n"
        "stage: training\n"
        "stages_completed: a, b\n"
        "artifact: data/foo/\n"
        "notes: hi\n"
    )
    ct = state.CurrentTask.from_dict(parsed)
    assert ct.id == "x"
    assert ct.stage == "training"
    assert ct.stages_completed == ["a", "b"]
    assert ct.artifact_paths == ["data/foo/"]
    assert ct.claude_notes == "hi"


@pytest.mark.parametrize(
    "src,expected_id",
    [
        ("task:x\n", "x"),  # no space after colon
        ("task:    x\n", "x"),  # extra whitespace
        ("\ntask: x\n", "x"),  # leading blank line
        ("TASK: x\n", None),  # uppercase TASK is not recognized
    ],
)
def test_parse_edge_cases(src: str, expected_id: str | None) -> None:
    out = current_task.parse_text(src)
    if expected_id is None:
        assert "id" not in out
    else:
        assert out.get("id") == expected_id
