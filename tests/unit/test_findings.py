"""Unit tests for src/lib/findings.py.

Covers PROMPT_v1.3-FULL.md GROUP A1 — findings_index.md auto-memory used
by Stop hook + SessionStart injection.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_LIB = REPO_ROOT / "src" / "lib"
sys.path.insert(0, str(SRC_LIB))

import findings  # noqa: E402


def _project(tmp_path: Path) -> Path:
    p = tmp_path / "demo"
    (p / ".cc-autopipe").mkdir(parents=True)
    return p


def test_append_creates_file(tmp_path: Path) -> None:
    p = _project(tmp_path)
    ok = findings.append_finding(
        p,
        "vec_meta",
        "stage_e_verdict",
        "REJECT — val AUC=0.5311",
        ["data/debug/CAND_meta_PROMOTION.md"],
    )
    assert ok is True
    f = p / ".cc-autopipe" / "findings_index.md"
    assert f.exists()
    text = f.read_text(encoding="utf-8")
    assert "vec_meta" in text
    assert "stage_e_verdict" in text
    assert "REJECT" in text
    assert "data/debug/CAND_meta_PROMOTION.md" in text
    assert text.startswith("## ")


def test_append_idempotent_dedup(tmp_path: Path) -> None:
    p = _project(tmp_path)
    findings.append_finding(p, "vec_a", "stage_b", "first", ["a.md"])
    ok2 = findings.append_finding(p, "vec_a", "stage_b", "second", ["b.md"])
    assert ok2 is False
    text = (p / ".cc-autopipe" / "findings_index.md").read_text()
    assert text.count("## ") == 1


def test_append_different_stages_not_deduped(tmp_path: Path) -> None:
    p = _project(tmp_path)
    findings.append_finding(p, "vec_a", "stage_a", "x")
    findings.append_finding(p, "vec_a", "stage_b", "y")
    text = (p / ".cc-autopipe" / "findings_index.md").read_text()
    assert text.count("## ") == 2


def test_append_skips_empty_task_or_stage(tmp_path: Path) -> None:
    p = _project(tmp_path)
    assert findings.append_finding(p, "", "stage_x", "n") is False
    assert findings.append_finding(p, "vec_x", "", "n") is False
    assert not (p / ".cc-autopipe" / "findings_index.md").exists()


def test_append_collapses_multiline_notes(tmp_path: Path) -> None:
    p = _project(tmp_path)
    findings.append_finding(p, "v", "s", "line one\nline two\n   indented")
    text = (p / ".cc-autopipe" / "findings_index.md").read_text()
    assert "line one line two indented" in text


def test_read_findings_returns_newest_first(tmp_path: Path) -> None:
    p = _project(tmp_path)
    findings.append_finding(p, "vec_a", "s1", "first")
    findings.append_finding(p, "vec_a", "s2", "second")
    findings.append_finding(p, "vec_b", "s1", "third")
    items = findings.read_findings(p, top_n=10)
    assert len(items) == 3
    assert items[0]["task_id"] == "vec_b"
    assert items[0]["stage"] == "s1"
    assert items[0]["notes"].startswith("third")
    assert items[2]["task_id"] == "vec_a"
    assert items[2]["stage"] == "s1"


def test_read_findings_top_n_caps(tmp_path: Path) -> None:
    p = _project(tmp_path)
    for i in range(5):
        findings.append_finding(p, f"vec_{i}", "s", f"note {i}")
    items = findings.read_findings(p, top_n=2)
    assert len(items) == 2
    assert items[0]["task_id"] == "vec_4"


def test_read_findings_missing_file(tmp_path: Path) -> None:
    p = _project(tmp_path)
    assert findings.read_findings(p) == []


def test_read_findings_malformed_section_skipped(tmp_path: Path) -> None:
    p = _project(tmp_path)
    f = p / ".cc-autopipe" / "findings_index.md"
    f.write_text(
        "garbage line\n"
        "## 2026-05-04T00:00:00Z | vec_a | s1\n"
        "- **Notes:** ok\n"
        "## bad header without pipes\n"
        "- **Notes:** lost\n"
        "## 2026-05-05T00:00:00Z | vec_b | s2\n"
        "- **Notes:** good\n",
        encoding="utf-8",
    )
    items = findings.read_findings(p)
    assert items[0]["task_id"] == "vec_b"


def test_read_findings_for_task_filters(tmp_path: Path) -> None:
    p = _project(tmp_path)
    findings.append_finding(p, "vec_meta", "s1", "x")
    findings.append_finding(p, "vec_tbm", "s1", "y")
    findings.append_finding(p, "vec_meta", "s2", "z")
    matches = findings.read_findings_for_task(p, "vec_meta", n=5)
    assert len(matches) == 2
    assert all(m["task_id"] == "vec_meta" for m in matches)


def test_read_findings_for_task_caps_n(tmp_path: Path) -> None:
    p = _project(tmp_path)
    for i in range(5):
        findings.append_finding(p, "vec_a", f"s{i}", f"n{i}")
    matches = findings.read_findings_for_task(p, "vec_a", n=2)
    assert len(matches) == 2


def test_format_findings_for_injection(tmp_path: Path) -> None:
    p = _project(tmp_path)
    findings.append_finding(p, "vec_a", "stage_e", "REJECT")
    out = findings.format_findings_for_injection(findings.read_findings(p))
    assert "Recent findings" in out
    assert "vec_a | stage_e" in out
    assert out.startswith("=== ")
    assert out.endswith("===")


def test_format_findings_empty_returns_empty(tmp_path: Path) -> None:
    assert findings.format_findings_for_injection([]) == ""


def test_cli_append_and_read(tmp_path: Path) -> None:
    p = _project(tmp_path)
    cp = subprocess.run(
        [
            sys.executable,
            str(SRC_LIB / "findings.py"),
            "append",
            str(p),
            "vec_x",
            "stage_y",
            "cli notes",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "appended" in cp.stdout
    cp2 = subprocess.run(
        [
            sys.executable,
            str(SRC_LIB / "findings.py"),
            "read",
            str(p),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    items = json.loads(cp2.stdout)
    assert len(items) == 1
    assert items[0]["task_id"] == "vec_x"
