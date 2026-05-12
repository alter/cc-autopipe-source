"""Unit tests for v1.5.3 CYCLE-RC-NEGATIVE-VISIBILITY.

When a `cycle_end` event is emitted with rc<0 (subprocess killed by
signal) the event must include `killed_by_signal=<NAME>` so post-mortem
analysis doesn't have to translate raw rc values by hand. Positive
exit codes carry no annotation.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
LIB = SRC / "lib"
for p in (str(SRC), str(LIB)):
    sys.path.insert(0, p)

from orchestrator.cycle import _emit_cycle_end  # noqa: E402
import state  # noqa: E402


def _seed(tmp_path: Path, monkeypatch) -> Path:
    user_home = tmp_path / "uhome"
    (user_home / "log").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    project = tmp_path / "demo"
    (project / ".cc-autopipe" / "memory").mkdir(parents=True, exist_ok=True)
    state.write(project, state.State.fresh("demo"))
    return project


def _last_cycle_end(project: Path) -> dict:
    progress = project / ".cc-autopipe" / "memory" / "progress.jsonl"
    for line in reversed(progress.read_text().splitlines()):
        if not line.strip():
            continue
        ev = json.loads(line)
        if ev.get("event") == "cycle_end":
            return ev
    raise AssertionError("no cycle_end event in progress.jsonl")


def test_cycle_end_rc_negative_annotated_with_signal(
    tmp_path: Path, monkeypatch
) -> None:
    project = _seed(tmp_path, monkeypatch)
    s = state.read(project)
    _emit_cycle_end(
        project, s, rc=-1, score=None, update_last_ended=True
    )
    ev = _last_cycle_end(project)
    assert ev["rc"] == -1
    assert ev["killed_by_signal"] == "SIGHUP"


def test_cycle_end_rc_positive_has_no_signal_annotation(
    tmp_path: Path, monkeypatch
) -> None:
    project = _seed(tmp_path, monkeypatch)
    s = state.read(project)
    _emit_cycle_end(
        project, s, rc=1, score=0.0, update_last_ended=True
    )
    ev = _last_cycle_end(project)
    assert ev["rc"] == 1
    assert "killed_by_signal" not in ev


def test_cycle_end_rc_negative_unknown_signal_uses_fallback(
    tmp_path: Path, monkeypatch
) -> None:
    project = _seed(tmp_path, monkeypatch)
    s = state.read(project)
    _emit_cycle_end(
        project, s, rc=-999, score=None, update_last_ended=False
    )
    ev = _last_cycle_end(project)
    assert ev["rc"] == -999
    assert ev["killed_by_signal"] == "signal_999"


def test_cycle_end_string_rc_skips_signal_annotation(
    tmp_path: Path, monkeypatch
) -> None:
    project = _seed(tmp_path, monkeypatch)
    s = state.read(project)
    _emit_cycle_end(
        project,
        s,
        rc="interrupted",
        score=None,
        update_last_ended=False,
    )
    ev = _last_cycle_end(project)
    assert ev["rc"] == "interrupted"
    assert "killed_by_signal" not in ev


def test_cycle_end_persists_last_cycle_ended_at_when_requested(
    tmp_path: Path, monkeypatch
) -> None:
    project = _seed(tmp_path, monkeypatch)
    s = state.read(project)
    assert s.last_cycle_ended_at is None
    _emit_cycle_end(project, s, rc=0, score=1.0, update_last_ended=True)
    s2 = state.read(project)
    assert s2.last_cycle_ended_at is not None


def test_cycle_end_skips_state_write_when_update_last_ended_false(
    tmp_path: Path, monkeypatch
) -> None:
    project = _seed(tmp_path, monkeypatch)
    s = state.read(project)
    _emit_cycle_end(
        project, s, rc=0, score=1.0, update_last_ended=False
    )
    s2 = state.read(project)
    assert s2.last_cycle_ended_at is None
