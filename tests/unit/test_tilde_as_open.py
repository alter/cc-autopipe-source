"""v1.5.6 TILDE-IS-OPEN: backlog `[~]` lines are demoted to `[ ]`
(actionable). `_count_open_backlog` returns the combined count and
emits a `tilde_demoted_to_open` audit event whenever any `[~]` are
found, so the operator can spot agents abusing the convention.

AI-trade 2026-05-12 incident: the agent self-blocked two phase-5
tasks via `[~]` and engine sat idle for ~8 hours respecting the
marker. Engine now refuses to honor it.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
LIB = SRC / "lib"
for p in (str(SRC), str(LIB)):
    if p not in sys.path:
        sys.path.insert(0, p)

import state  # noqa: E402

recovery = importlib.import_module("orchestrator.recovery")


def _project(tmp_path: Path) -> Path:
    p = tmp_path / "demo"
    (p / ".cc-autopipe").mkdir(parents=True)
    return p


def _read_aggregate(user_home: Path) -> list[dict]:
    log = user_home / "log" / "aggregate.jsonl"
    if not log.exists():
        return []
    return [json.loads(ln) for ln in log.read_text().splitlines() if ln.strip()]


def test_count_open_includes_tilde(tmp_path: Path, monkeypatch) -> None:
    """Mixed backlog: 3 `[ ]` + 2 `[~]` + 5 `[x]` → 5 actionable."""
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    p = _project(tmp_path)
    (p / "backlog.md").write_text(
        "# Backlog\n\n"
        "- [ ] [implement] [P0] vec_a — alpha\n"
        "- [ ] [implement] [P1] vec_b — beta\n"
        "- [ ] [implement] [P2] vec_c — gamma\n"
        "- [~] [research]  [P0] vec_d — self-blocked attempt 1\n"
        "- [~] [research]  [P0] vec_e — self-blocked attempt 2\n"
        "- [x] [implement] [P0] vec_f — done 1\n"
        "- [x] [implement] [P0] vec_g — done 2\n"
        "- [x] [implement] [P0] vec_h — done 3\n"
        "- [x] [implement] [P0] vec_i — done 4\n"
        "- [x] [implement] [P0] vec_j — done 5\n",
        encoding="utf-8",
    )
    assert recovery._count_open_backlog(p) == 5


def test_count_open_emits_tilde_demoted_event(
    tmp_path: Path, monkeypatch
) -> None:
    """Any `[~]` encountered emits a `tilde_demoted_to_open` event with
    the correct count, so the operator can grep aggregate.jsonl."""
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    p = _project(tmp_path)
    (p / "backlog.md").write_text(
        "- [ ] [P0] vec_a — open\n"
        "- [~] [P0] vec_b — blocked attempt\n"
        "- [~] [P0] vec_c — blocked attempt\n",
        encoding="utf-8",
    )
    recovery._count_open_backlog(p)

    events = [
        e for e in _read_aggregate(user_home)
        if e.get("event") == "tilde_demoted_to_open"
    ]
    assert len(events) == 1
    assert events[0]["count"] == 2
    assert "v1.5.6" in events[0]["reason"]


def test_count_open_no_event_when_no_tilde(
    tmp_path: Path, monkeypatch
) -> None:
    """A backlog with only `[ ]` and `[x]` should not emit the tilde
    event — it would otherwise flood aggregate.jsonl on every sweep."""
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    p = _project(tmp_path)
    (p / "backlog.md").write_text(
        "- [ ] [P0] vec_a — open\n"
        "- [x] [P0] vec_b — done\n",
        encoding="utf-8",
    )
    recovery._count_open_backlog(p)
    events = [
        e for e in _read_aggregate(user_home)
        if e.get("event") == "tilde_demoted_to_open"
    ]
    assert events == []


def test_research_is_open_task_line_includes_tilde() -> None:
    """research._is_open_task_line treats `[~]` as open so
    detect_prd_complete refuses to mark a project complete while
    `[~]` tasks remain."""
    research = importlib.import_module("orchestrator.research")
    assert research._is_open_task_line("- [ ] [P0] vec_a") is True
    assert research._is_open_task_line("- [~] [P0] vec_a") is True
    assert research._is_open_task_line("- [x] [P0] vec_a") is False
    assert research._is_open_task_line("  - [~] indented tilde") is True
