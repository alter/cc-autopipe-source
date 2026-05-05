"""Unit tests for src/watchdog/watchdog.py — PROMPT_v1.3-FULL.md C4."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WATCHDOG_PATH = REPO_ROOT / "src" / "watchdog" / "watchdog.py"

# Load the module directly (it's not on a normal package path).
spec = importlib.util.spec_from_file_location("watchdog_mod", str(WATCHDOG_PATH))
assert spec is not None and spec.loader is not None
watchdog = importlib.util.module_from_spec(spec)
sys.modules["watchdog_mod"] = watchdog
spec.loader.exec_module(watchdog)


def _user_home(tmp_path: Path) -> Path:
    h = tmp_path / "user-home"
    h.mkdir()
    return h


def test_read_pid_json_format(tmp_path: Path) -> None:
    p = tmp_path / "orchestrator.pid"
    p.write_text(json.dumps({"pid": 1234, "started_at": "x"}))
    assert watchdog.read_pid(p) == 1234


def test_read_pid_legacy_int_format(tmp_path: Path) -> None:
    p = tmp_path / "orchestrator.pid"
    p.write_text("9999\n")
    assert watchdog.read_pid(p) == 9999


def test_read_pid_missing_returns_none(tmp_path: Path) -> None:
    p = tmp_path / "missing.pid"
    assert watchdog.read_pid(p) is None


def test_check_alive_self_pid_is_true(tmp_path: Path) -> None:
    """Use the test process itself — it's guaranteed alive."""
    p = tmp_path / "orchestrator.pid"
    p.write_text(json.dumps({"pid": os.getpid()}))
    assert watchdog.check_orchestrator_alive(p) is True


def test_check_alive_nonexistent_pid_is_false(tmp_path: Path) -> None:
    p = tmp_path / "orchestrator.pid"
    # PID 999_999_999 — astronomically unlikely to be in use.
    p.write_text(json.dumps({"pid": 999_999_999}))
    assert watchdog.check_orchestrator_alive(p) is False


def test_run_one_iteration_logs_heartbeat_alive(tmp_path: Path) -> None:
    user_home = _user_home(tmp_path)
    pid_path = user_home / "orchestrator.pid"
    pid_path.write_text(json.dumps({"pid": os.getpid()}))
    out = watchdog.run_one_iteration(user_home)
    assert out["alive"] is True
    assert out["restarted"] is False
    hb = user_home / "log" / "watchdog.jsonl"
    assert hb.exists()
    record = json.loads(hb.read_text().strip().splitlines()[-1])
    assert record["alive"] is True


def test_run_one_iteration_dead_attempts_restart(
    tmp_path: Path, monkeypatch
) -> None:
    user_home = _user_home(tmp_path)
    pid_path = user_home / "orchestrator.pid"
    pid_path.write_text(json.dumps({"pid": 999_999_999}))

    called = []

    def fake_restart(uh):
        called.append(uh)
        return True

    monkeypatch.setattr(watchdog, "restart_orchestrator", fake_restart)
    out = watchdog.run_one_iteration(user_home)
    assert out["alive"] is False
    assert out["restarted"] is True
    assert called == [user_home]


def test_run_one_iteration_no_pid_file_attempts_restart(
    tmp_path: Path, monkeypatch
) -> None:
    """No pid file → orchestrator was never started or PID disappeared.
    Watchdog tries to start it."""
    user_home = _user_home(tmp_path)
    called = []

    def fake_restart(uh):
        called.append(uh)
        return True

    monkeypatch.setattr(watchdog, "restart_orchestrator", fake_restart)
    out = watchdog.run_one_iteration(user_home)
    assert out["alive"] is False
    assert out["restarted"] is True


def test_run_one_iteration_restart_failure_recorded(
    tmp_path: Path, monkeypatch
) -> None:
    user_home = _user_home(tmp_path)
    pid_path = user_home / "orchestrator.pid"
    pid_path.write_text(json.dumps({"pid": 999_999_999}))
    monkeypatch.setattr(watchdog, "restart_orchestrator", lambda _h: False)
    out = watchdog.run_one_iteration(user_home)
    assert out["restarted"] is False
