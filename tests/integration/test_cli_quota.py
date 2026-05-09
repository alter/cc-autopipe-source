"""Integration tests for src/cli/quota.py (`cc-autopipe quota`).

Tests the user-facing CLI layer against tools/mock-quota-server.py.
Covers human output, --json, --raw, --refresh, exit-code 2 on
unavailable data, and the CC_AUTOPIPE_QUOTA_DISABLED escape hatch.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
LIB = SRC / "lib"
QUOTA_PY_CLI = SRC / "cli" / "quota.py"
MOCK_SERVER = REPO_ROOT / "tools" / "mock-quota-server.py"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_server(port: int, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                return True
        except OSError:
            time.sleep(0.05)
    return False


@pytest.fixture
def mock_server(tmp_path: Path) -> tuple[int, str]:
    """Spawn a fresh mock-quota-server on a random port. Returns (port, endpoint_url)."""
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, str(MOCK_SERVER), "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert _wait_for_server(port), f"mock-quota-server didn't bind to {port}"
        yield port, f"http://127.0.0.1:{port}/api/oauth/usage"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()


def _set_admin(port: int, **kv: float) -> None:
    body = json.dumps(kv).encode()
    cp = subprocess.run(
        [
            "curl",
            "-sS",
            "-X",
            "POST",
            f"http://127.0.0.1:{port}/admin/set",
            "-H",
            "Content-Type: application/json",
            "--data-binary",
            "@-",
        ],
        input=body,
        capture_output=True,
        check=True,
    )
    assert b'"ok"' in cp.stdout, cp.stdout


def _quota_env(
    user_home: Path,
    endpoint: str | None,
    *,
    creds_token: str | None = "fake-bearer",
) -> dict[str, str]:
    env = os.environ.copy()
    env["CC_AUTOPIPE_USER_HOME"] = str(user_home)
    if endpoint:
        env["CC_AUTOPIPE_QUOTA_ENDPOINT"] = endpoint

    if creds_token is None:
        env["CC_AUTOPIPE_CREDENTIALS_FILE"] = str(user_home / "no-such-file.json")
    else:
        creds_path = user_home / "credentials.json"
        user_home.mkdir(parents=True, exist_ok=True)
        creds_path.write_text(json.dumps({"accessToken": creds_token}))
        env["CC_AUTOPIPE_CREDENTIALS_FILE"] = str(creds_path)
    return env


def _run_cli_quota(
    args: list[str],
    env: dict[str, str],
    *,
    expect_rc: int | None = None,
    timeout: float = 10.0,
) -> subprocess.CompletedProcess[str]:
    cp = subprocess.run(
        [sys.executable, str(QUOTA_PY_CLI)] + args,
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )
    if expect_rc is not None:
        assert cp.returncode == expect_rc, (
            f"expected rc={expect_rc}, got {cp.returncode}\n"
            f"stdout: {cp.stdout}\nstderr: {cp.stderr}"
        )
    return cp


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_default_human_output_after_refresh(
    mock_server: tuple[int, str], tmp_path: Path
) -> None:
    port, endpoint = mock_server
    user_home = tmp_path / "uhome"
    env = _quota_env(user_home, endpoint)

    _set_admin(port, five_hour=47, seven_day=12)

    # Populate the cache first.
    cp_refresh = _run_cli_quota(["--refresh"], env)
    if sys.platform == "darwin" and cp_refresh.returncode == 2:
        pytest.skip("macOS host without Claude Code Keychain creds")

    # Now run with no args — should read from cache.
    cp = _run_cli_quota([], env, expect_rc=0)
    assert "5h: 47%" in cp.stdout
    assert "7d: 12%" in cp.stdout
    assert "cache age:" in cp.stdout
    assert "(resets " in cp.stdout


def test_json_flag_emits_normalized_object(tmp_path: Path) -> None:
    user_home = tmp_path / "uhome"
    user_home.mkdir(parents=True, exist_ok=True)
    cache = user_home / "quota-cache.json"
    payload = {
        "five_hour": {"utilization": 42, "resets_at": "2026-05-10T14:30:00Z"},
        "seven_day": {"utilization": 13, "resets_at": "2026-05-13T20:00:00Z"},
    }
    cache.write_text(json.dumps(payload))

    # endpoint doesn't matter — cache is fresh.
    env = _quota_env(user_home, endpoint=None)
    cp = _run_cli_quota(["--json"], env, expect_rc=0)
    data = json.loads(cp.stdout)
    assert data["five_hour_pct"] == pytest.approx(0.42)
    assert data["seven_day_pct"] == pytest.approx(0.13)
    assert isinstance(data["five_hour_resets_at"], str)


def test_raw_flag_emits_raw_response(tmp_path: Path) -> None:
    user_home = tmp_path / "uhome"
    user_home.mkdir(parents=True, exist_ok=True)
    cache = user_home / "quota-cache.json"
    payload = {
        "five_hour": {"utilization": 42, "resets_at": "2026-05-10T14:30:00Z"},
        "seven_day": {"utilization": 13, "resets_at": "2026-05-13T20:00:00Z"},
    }
    cache.write_text(json.dumps(payload))

    env = _quota_env(user_home, endpoint=None)
    cp = _run_cli_quota(["--raw"], env, expect_rc=0)
    data = json.loads(cp.stdout)
    # Raw passthrough — integer, not normalised float.
    assert data["five_hour"]["utilization"] == 42


def test_json_and_raw_mutually_exclusive(tmp_path: Path) -> None:
    user_home = tmp_path / "uhome"
    env = _quota_env(user_home, endpoint=None)
    cp = _run_cli_quota(["--json", "--raw"], env)
    assert cp.returncode != 0


def test_refresh_with_json_bypasses_cache(
    mock_server: tuple[int, str], tmp_path: Path
) -> None:
    port, endpoint = mock_server
    user_home = tmp_path / "uhome"
    user_home.mkdir(parents=True, exist_ok=True)

    # Write a stale cache with 99% utilisation.
    cache = user_home / "quota-cache.json"
    stale = {
        "five_hour": {"utilization": 99, "resets_at": "2026-05-10T14:30:00Z"},
        "seven_day": {"utilization": 99, "resets_at": "2026-05-13T20:00:00Z"},
    }
    cache.write_text(json.dumps(stale))

    _set_admin(port, five_hour=25, seven_day=30)
    env = _quota_env(user_home, endpoint)
    cp = _run_cli_quota(["--refresh", "--json"], env)
    if sys.platform == "darwin" and cp.returncode == 2:
        pytest.skip("macOS host without Claude Code Keychain creds")
    assert cp.returncode == 0
    data = json.loads(cp.stdout)
    assert data["five_hour_pct"] == pytest.approx(0.25)


def test_unavailable_returns_rc2_with_hint(tmp_path: Path) -> None:
    user_home = tmp_path / "uhome"
    user_home.mkdir(parents=True, exist_ok=True)
    env = _quota_env(user_home, endpoint=None, creds_token=None)
    cp = _run_cli_quota([], env)
    if sys.platform == "darwin" and cp.returncode == 0:
        pytest.skip("macOS host with Keychain creds present; can't test token-missing branch")
    assert cp.returncode == 2
    assert cp.stdout == ""
    assert "quota unavailable" in cp.stderr
    assert "claude" in cp.stderr


def test_disabled_env_returns_rc2(tmp_path: Path) -> None:
    user_home = tmp_path / "uhome"
    user_home.mkdir(parents=True, exist_ok=True)
    env = _quota_env(user_home, endpoint=None)
    env["CC_AUTOPIPE_QUOTA_DISABLED"] = "1"
    cp = _run_cli_quota([], env, expect_rc=2)
    assert "disabled" in cp.stderr
