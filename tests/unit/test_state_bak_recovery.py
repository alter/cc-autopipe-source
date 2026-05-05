"""Unit tests for state.json.bak corruption recovery (v1.3 C3)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_LIB = REPO_ROOT / "src" / "lib"
sys.path.insert(0, str(SRC_LIB))

import state  # noqa: E402


def _project(tmp_path: Path) -> Path:
    p = tmp_path / "demo"
    (p / ".cc-autopipe").mkdir(parents=True)
    return p


def test_write_creates_bak(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    p = _project(tmp_path)
    s = state.State.fresh(p.name)
    s.iteration = 7
    state.write(p, s)
    bak = p / ".cc-autopipe" / "state.json.bak"
    assert bak.exists()
    raw = json.loads(bak.read_text())
    assert raw["iteration"] == 7


def test_corruption_recovers_from_bak(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    p = _project(tmp_path)
    s = state.State.fresh(p.name)
    s.iteration = 42
    s.session_id = "from-bak"
    state.write(p, s)
    # Corrupt the live file.
    sjson = p / ".cc-autopipe" / "state.json"
    sjson.write_text("{ not valid json")
    # state.read should restore from .bak.
    s2 = state.read(p)
    assert s2.iteration == 42
    assert s2.session_id == "from-bak"


def test_no_bak_returns_fresh_when_corrupt(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    p = _project(tmp_path)
    sjson = p / ".cc-autopipe" / "state.json"
    sjson.parent.mkdir(parents=True, exist_ok=True)
    sjson.write_text("{ corrupt")
    # No bak ever existed.
    s = state.read(p)
    assert s.iteration == 0
    assert s.phase == "active"


def test_bak_corrupt_falls_back_to_fresh(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    p = _project(tmp_path)
    cca = p / ".cc-autopipe"
    cca.mkdir(parents=True, exist_ok=True)
    (cca / "state.json").write_text("{ corrupt")
    (cca / "state.json.bak").write_text("{ also corrupt")
    s = state.read(p)
    assert s.iteration == 0


def test_successful_read_refreshes_bak(tmp_path: Path, monkeypatch) -> None:
    """A successful read of state.json should also refresh state.json.bak
    (so a corruption shortly after still has a recent backup)."""
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    p = _project(tmp_path)
    s = state.State.fresh(p.name)
    s.iteration = 1
    state.write(p, s)
    # Delete the bak.
    bak = p / ".cc-autopipe" / "state.json.bak"
    bak.unlink()
    # Reading should re-create it.
    state.read(p)
    assert bak.exists()


def test_promotion_clears_bak_after_recovery(tmp_path: Path, monkeypatch) -> None:
    """When state.json was corrupt and .bak was used, the recovery
    promotes .bak → state.json (so subsequent reads succeed without
    re-recovery)."""
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    p = _project(tmp_path)
    s = state.State.fresh(p.name)
    s.iteration = 5
    state.write(p, s)
    sjson = p / ".cc-autopipe" / "state.json"
    bak = p / ".cc-autopipe" / "state.json.bak"
    sjson.write_text("{ broken")
    s2 = state.read(p)
    assert s2.iteration == 5
    # state.json should now be valid (promoted from .bak).
    raw = json.loads(sjson.read_text())
    assert raw["iteration"] == 5
    # Subsequent read should NOT need recovery.
    s3 = state.read(p)
    assert s3.iteration == 5
    # And on next read .bak gets refreshed too.
    assert bak.exists()
