"""Unit tests for src/lib/knowledge.py.

Covers PROMPT_v1.3-FULL.md GROUP A2 — knowledge.md read/inject helpers.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_LIB = REPO_ROOT / "src" / "lib"
sys.path.insert(0, str(SRC_LIB))

import knowledge  # noqa: E402


def _project(tmp_path: Path) -> Path:
    p = tmp_path / "demo"
    (p / ".cc-autopipe").mkdir(parents=True)
    return p


def test_read_missing(tmp_path: Path) -> None:
    p = _project(tmp_path)
    assert knowledge.read_knowledge(p) == ""


def test_read_full(tmp_path: Path) -> None:
    p = _project(tmp_path)
    body = "# Knowledge\n- lesson 1\n- lesson 2\n"
    (p / ".cc-autopipe" / "knowledge.md").write_text(body)
    assert knowledge.read_knowledge(p) == body


def test_read_truncates_to_tail(tmp_path: Path) -> None:
    p = _project(tmp_path)
    big = "\n".join(f"line {i}" for i in range(10000))
    (p / ".cc-autopipe" / "knowledge.md").write_text(big)
    out = knowledge.read_knowledge(p, max_bytes=512)
    assert len(out.encode("utf-8")) <= 512
    assert "line 9999" in out


def test_format_for_injection(tmp_path: Path) -> None:
    text = "# Knowledge\n- Architectures: foo\n"
    out = knowledge.format_for_injection(text)
    assert out.startswith("=== Project knowledge ===")
    assert out.endswith("===")
    assert "Architectures" in out


def test_format_for_injection_empty(tmp_path: Path) -> None:
    assert knowledge.format_for_injection("") == ""
    assert knowledge.format_for_injection("   \n") == ""


def test_relevant_excerpt_filters_by_task_id(tmp_path: Path) -> None:
    p = _project(tmp_path)
    body = (
        "# Knowledge\n\n"
        "## Architectures\n"
        "- vec_meta uses cross-attention — 2026-05-04\n\n"
        "## Baselines\n"
        "- vec_tbm symmetric thresholds work — 2026-05-04\n"
    )
    (p / ".cc-autopipe" / "knowledge.md").write_text(body)
    excerpt = knowledge.read_relevant_excerpt(p, "vec_meta")
    assert "vec_meta" in excerpt
    # vec_tbm section should be filtered out — different task
    assert "vec_tbm" not in excerpt


def test_relevant_excerpt_falls_back_when_no_match(tmp_path: Path) -> None:
    p = _project(tmp_path)
    body = "# Knowledge\n- general lesson — 2026-05-04\n"
    (p / ".cc-autopipe" / "knowledge.md").write_text(body)
    excerpt = knowledge.read_relevant_excerpt(p, "vec_unknown")
    # No match; module returns the full file as the safe fallback.
    assert "general lesson" in excerpt


def test_relevant_excerpt_handles_missing_file(tmp_path: Path) -> None:
    p = _project(tmp_path)
    assert knowledge.read_relevant_excerpt(p, "vec_x") == ""
