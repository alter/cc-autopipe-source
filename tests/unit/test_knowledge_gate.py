"""Unit tests for src/lib/knowledge_gate.py (v1.3.3 Group N).

Covers:
- gate is a no-op when no verdict has fired
- gate exits 3 when knowledge.md is missing after a verdict
- gate exits 3 when knowledge.md mtime is older than verdict timestamp
- gate exits 0 when knowledge.md was touched after the verdict
- malformed verdict timestamp does not crash (treated as "no verdict")
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src" / "lib"))

import knowledge_gate  # noqa: E402
import state  # noqa: E402


def _seed(tmp_path: Path) -> Path:
    p = tmp_path / "demo"
    (p / ".cc-autopipe" / "memory").mkdir(parents=True, exist_ok=True)
    return p


def _now_minus_sec(offset_sec: int) -> str:
    dt = datetime.now(timezone.utc) - timedelta(seconds=offset_sec)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_gate_passes_when_no_verdict_recorded(tmp_path: Path) -> None:
    project = _seed(tmp_path)
    s = state.State.fresh("demo")
    state.write(project, s)
    rc, msg = knowledge_gate.check(project)
    assert rc == 0
    assert msg == ""


def test_gate_blocks_when_knowledge_md_missing(tmp_path: Path) -> None:
    project = _seed(tmp_path)
    s = state.State.fresh("demo")
    s.last_verdict_event_at = _now_minus_sec(60)
    s.last_verdict_task_id = "vec_meta"
    state.write(project, s)
    rc, msg = knowledge_gate.check(project)
    assert rc == 3
    assert "BLOCKED" in msg
    assert "vec_meta" in msg
    assert "knowledge.md" in msg


def test_gate_blocks_when_knowledge_older_than_verdict(tmp_path: Path) -> None:
    project = _seed(tmp_path)
    knowledge = project / ".cc-autopipe" / "knowledge.md"
    knowledge.write_text("old content\n", encoding="utf-8")
    # Force knowledge.md mtime 1 hour before verdict.
    one_hour_ago = time.time() - 3600
    os.utime(knowledge, (one_hour_ago, one_hour_ago))

    s = state.State.fresh("demo")
    # Verdict 30 seconds ago — newer than the knowledge.md mtime.
    s.last_verdict_event_at = _now_minus_sec(30)
    s.last_verdict_task_id = "vec_rl"
    state.write(project, s)

    rc, msg = knowledge_gate.check(project)
    assert rc == 3
    assert "BLOCKED" in msg
    assert "older than last verdict" in msg
    assert "vec_rl" in msg


def test_gate_passes_when_knowledge_updated_after_verdict(tmp_path: Path) -> None:
    project = _seed(tmp_path)
    s = state.State.fresh("demo")
    # Verdict 1 hour ago.
    s.last_verdict_event_at = _now_minus_sec(3600)
    s.last_verdict_task_id = "vec_meta"
    state.write(project, s)

    # Knowledge.md touched just now (mtime > verdict).
    knowledge = project / ".cc-autopipe" / "knowledge.md"
    knowledge.write_text("entry appended\n", encoding="utf-8")

    rc, msg = knowledge_gate.check(project)
    assert rc == 0, f"expected pass, got rc={rc} msg={msg!r}"


def test_gate_tolerates_malformed_verdict_timestamp(tmp_path: Path) -> None:
    project = _seed(tmp_path)
    s = state.State.fresh("demo")
    s.last_verdict_event_at = "not-a-timestamp"
    s.last_verdict_task_id = "vec_x"
    state.write(project, s)
    rc, msg = knowledge_gate.check(project)
    # Malformed → treated as "no verdict recorded" rather than crashing.
    assert rc == 0
    assert msg == ""


def test_main_cli_exits_3_on_block(tmp_path: Path, capsys) -> None:
    project = _seed(tmp_path)
    s = state.State.fresh("demo")
    s.last_verdict_event_at = _now_minus_sec(60)
    s.last_verdict_task_id = "task_x"
    state.write(project, s)
    rc = knowledge_gate.main([str(project)])
    captured = capsys.readouterr()
    assert rc == 3
    assert "BLOCKED" in captured.err


def test_main_cli_returns_1_when_project_missing(tmp_path: Path) -> None:
    rc = knowledge_gate.main([str(tmp_path / "does-not-exist")])
    assert rc == 1
