"""Integration tests for the global-hooks backup/restore lifecycle.

Spawns a real orchestrator subprocess with HOME redirected to tmp_path,
verifies that:
  - on start: backup is created and `hooks` is stripped from
    settings.json,
  - on `cc-autopipe stop`: settings.json is restored byte-identical
    and the backup is deleted,
  - the not-running idempotent stop path still triggers restore (covers
    crash recovery without a live orchestrator).

Refs: instruction-hotfix.md
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
LIB = SRC / "lib"
ORCHESTRATOR = SRC / "orchestrator"
STOP_PY = SRC / "cli" / "stop.py"

sys.path.insert(0, str(LIB))
import locking  # noqa: E402

SAMPLE_PAYLOAD = {
    "permissions": {"allow": ["Bash"], "deny": []},
    "skipDangerousModePermissionPrompt": True,
    "hooks": {
        "PreToolUse": [
            {
                "matcher": "Bash",
                "hooks": [{"type": "command", "command": "echo block"}],
            }
        ],
        "UserPromptSubmit": [
            {"hooks": [{"type": "command", "command": "echo remind"}]}
        ],
    },
}


def _seed_settings(home: Path, payload: dict) -> Path:
    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return settings


def _backup_path(home: Path) -> Path:
    return home / ".claude" / "settings.json.cc-autopipe-bak"


def _engine_env(user_home: Path, fake_home: Path, **overrides: str) -> dict[str, str]:
    env = os.environ.copy()
    env["CC_AUTOPIPE_HOME"] = str(SRC)
    env["CC_AUTOPIPE_USER_HOME"] = str(user_home)
    env["HOME"] = str(fake_home)  # claude_settings uses Path.home()
    env["NO_COLOR"] = "1"
    env.update(overrides)
    return env


def _orch_env(user_home: Path, fake_home: Path, **overrides: str) -> dict[str, str]:
    env = _engine_env(user_home, fake_home, **overrides)
    env["CC_AUTOPIPE_COOLDOWN_SEC"] = "1"
    env["CC_AUTOPIPE_IDLE_SLEEP_SEC"] = "1"
    env["CC_AUTOPIPE_CLAUDE_BIN"] = "/usr/bin/true"
    env["CC_AUTOPIPE_QUOTA_DISABLED"] = "1"
    env.pop("CC_AUTOPIPE_MAX_LOOPS", None)
    return env


def _wait_for_lock_held(user_home: Path, timeout_sec: float = 5.0) -> None:
    pid_path = user_home / "orchestrator.pid"
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if pid_path.exists():
            payload = locking.read_lock_payload(pid_path)
            if payload and isinstance(payload.get("pid"), int):
                snap = locking.lock_status(pid_path)
                if snap.get("held"):
                    return
        time.sleep(0.05)
    raise AssertionError(f"orchestrator never acquired lock at {pid_path}")


def _wait_for_backup(backup_path: Path, timeout_sec: float = 5.0) -> None:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if backup_path.exists():
            return
        time.sleep(0.05)
    raise AssertionError(f"backup never created at {backup_path}")


def _start_reaper(proc: subprocess.Popen) -> threading.Thread:
    t = threading.Thread(target=proc.wait, daemon=True)
    t.start()
    return t


# ---------------------------------------------------------------------------


def test_orchestrator_start_creates_backup_and_strips_hooks(tmp_path: Path) -> None:
    user_home = tmp_path / "uhome"
    user_home.mkdir()
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    settings = _seed_settings(fake_home, SAMPLE_PAYLOAD)
    bak = _backup_path(fake_home)

    proc = subprocess.Popen(
        [sys.executable, str(ORCHESTRATOR)],
        env=_orch_env(user_home, fake_home),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    reaper = _start_reaper(proc)
    try:
        _wait_for_lock_held(user_home)
        _wait_for_backup(bak)

        # Backup matches original.
        backed_up = json.loads(bak.read_text(encoding="utf-8"))
        assert backed_up == SAMPLE_PAYLOAD

        # Live settings.json had `hooks` stripped, other keys preserved.
        live = json.loads(settings.read_text(encoding="utf-8"))
        assert "hooks" not in live
        assert live["permissions"] == SAMPLE_PAYLOAD["permissions"]
        assert live["skipDangerousModePermissionPrompt"] is True
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        reaper.join(timeout=5)


def test_stop_restores_settings_byte_identical(tmp_path: Path) -> None:
    user_home = tmp_path / "uhome"
    user_home.mkdir()
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    settings = _seed_settings(fake_home, SAMPLE_PAYLOAD)
    original_bytes = settings.read_bytes()
    bak = _backup_path(fake_home)

    proc = subprocess.Popen(
        [sys.executable, str(ORCHESTRATOR)],
        env=_orch_env(user_home, fake_home),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    reaper = _start_reaper(proc)
    try:
        _wait_for_lock_held(user_home)
        _wait_for_backup(bak)

        # Stop the orchestrator — should restore + delete backup.
        cp = subprocess.run(
            [sys.executable, str(STOP_PY), "--timeout", "10"],
            capture_output=True,
            text=True,
            env=_engine_env(user_home, fake_home),
        )
        assert cp.returncode == 0, f"stop failed: {cp.stderr}"
        assert "restored global Claude hooks" in cp.stdout, cp.stdout
        reaper.join(timeout=5)

        # settings.json restored byte-identical, backup deleted.
        assert settings.read_bytes() == original_bytes
        assert not bak.exists()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def test_stop_when_not_running_still_restores_after_crash(tmp_path: Path) -> None:
    """Operator-recovery case: orchestrator died via SIGKILL, leaving
    settings.json hookless and the backup on disk. `cc-autopipe stop`
    with no live orchestrator must still restore."""
    user_home = tmp_path / "uhome"
    user_home.mkdir()
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    settings = _seed_settings(fake_home, SAMPLE_PAYLOAD)
    original_bytes = settings.read_bytes()
    bak = _backup_path(fake_home)

    # Simulate crash: backup is on disk, live file has had hooks stripped.
    bak.parent.mkdir(parents=True, exist_ok=True)
    bak.write_bytes(original_bytes)
    cleaned = {k: v for k, v in SAMPLE_PAYLOAD.items() if k != "hooks"}
    settings.write_text(json.dumps(cleaned, indent=2) + "\n", encoding="utf-8")

    cp = subprocess.run(
        [sys.executable, str(STOP_PY)],
        capture_output=True,
        text=True,
        env=_engine_env(user_home, fake_home),
    )
    assert cp.returncode == 0, f"stop failed: {cp.stderr}"
    assert "not running" in cp.stderr.lower()
    assert "restored global Claude hooks" in cp.stdout

    assert settings.read_bytes() == original_bytes
    assert not bak.exists()


def test_stop_when_no_settings_json_does_not_crash(tmp_path: Path) -> None:
    """Operator who never had ~/.claude/settings.json: stop must not
    error or print warnings about hooks."""
    user_home = tmp_path / "uhome"
    user_home.mkdir()
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    # No settings.json seeded.

    cp = subprocess.run(
        [sys.executable, str(STOP_PY)],
        capture_output=True,
        text=True,
        env=_engine_env(user_home, fake_home),
    )
    assert cp.returncode == 0, f"stop failed: {cp.stderr}"
    # Quiet success on the no-backup branch.
    assert "restored global Claude hooks" not in cp.stdout
    assert "WARN" not in cp.stdout
