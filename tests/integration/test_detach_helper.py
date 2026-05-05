"""Integration tests for src/helpers/cc-autopipe-detach.

Covers the v1.3.1 DETACH-CONFIG resolution chain end-to-end:

    CLI arg > env var > config.yaml > hardcoded fallback

Each test seeds a project, invokes the helper, then reads
state.json.detached to assert the resolved values landed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
HELPER = SRC / "helpers" / "cc-autopipe-detach"


def _seed_project(base: Path, *, config_block: str | None = None) -> Path:
    p = base / "demo"
    cca = p / ".cc-autopipe"
    cca.mkdir(parents=True, exist_ok=True)
    if config_block is not None:
        (cca / "config.yaml").write_text(config_block, encoding="utf-8")
    return p


def _run_helper(
    project: Path,
    env: dict[str, str],
    extra_args: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    cmd = [
        "bash",
        str(HELPER),
        "--reason",
        "test",
        "--check-cmd",
        "true",
        "--project",
        str(project),
    ]
    if extra_args:
        cmd.extend(extra_args)
    full_env = os.environ.copy()
    full_env["CC_AUTOPIPE_HOME"] = str(SRC)
    full_env.update(env)
    return subprocess.run(cmd, capture_output=True, text=True, env=full_env)


def _detached_block(project: Path) -> dict:
    s = json.loads((project / ".cc-autopipe" / "state.json").read_text())
    return s["detached"]


def test_hardcoded_fallback_when_no_env_no_config(tmp_path: Path) -> None:
    """No env vars, no config.yaml block → 600 / 14400 (the v1.0
    fallback) lands in state.detached."""
    p = _seed_project(tmp_path)
    result = _run_helper(p, env={})
    assert result.returncode == 0, result.stderr
    d = _detached_block(p)
    assert d["check_every_sec"] == 600
    assert d["max_wait_sec"] == 14400


def test_config_block_overrides_hardcoded(tmp_path: Path) -> None:
    """Project sets per-project ML training defaults — those land
    in state.detached without env or CLI overrides."""
    p = _seed_project(
        tmp_path,
        config_block=(
            "detach_defaults:\n"
            "  check_every_sec: 900\n"
            "  max_wait_sec: 43200\n"
        ),
    )
    result = _run_helper(p, env={})
    assert result.returncode == 0, result.stderr
    d = _detached_block(p)
    assert d["check_every_sec"] == 900
    assert d["max_wait_sec"] == 43200


def test_env_beats_config(tmp_path: Path) -> None:
    """Env var wins over config block."""
    p = _seed_project(
        tmp_path,
        config_block=(
            "detach_defaults:\n  max_wait_sec: 43200\n"
        ),
    )
    result = _run_helper(
        p,
        env={
            "CC_AUTOPIPE_DEFAULT_MAX_WAIT": "86400",
        },
    )
    assert result.returncode == 0, result.stderr
    d = _detached_block(p)
    assert d["max_wait_sec"] == 86400


def test_cli_arg_beats_env(tmp_path: Path) -> None:
    """--max-wait CLI arg wins over env var."""
    p = _seed_project(tmp_path)
    result = _run_helper(
        p,
        env={"CC_AUTOPIPE_DEFAULT_MAX_WAIT": "86400"},
        extra_args=["--max-wait", "172800"],
    )
    assert result.returncode == 0, result.stderr
    d = _detached_block(p)
    assert d["max_wait_sec"] == 172800


def test_cli_arg_beats_config(tmp_path: Path) -> None:
    """--max-wait CLI arg wins over config block."""
    p = _seed_project(
        tmp_path,
        config_block="detach_defaults:\n  max_wait_sec: 43200\n",
    )
    result = _run_helper(p, env={}, extra_args=["--max-wait", "172800"])
    assert result.returncode == 0, result.stderr
    d = _detached_block(p)
    assert d["max_wait_sec"] == 172800


def test_partial_config_falls_back_for_missing_key(tmp_path: Path) -> None:
    """Config sets only max_wait_sec — check_every_sec falls through to
    hardcoded 600."""
    p = _seed_project(
        tmp_path,
        config_block="detach_defaults:\n  max_wait_sec: 28800\n",
    )
    result = _run_helper(p, env={})
    assert result.returncode == 0, result.stderr
    d = _detached_block(p)
    assert d["check_every_sec"] == 600  # fallback
    assert d["max_wait_sec"] == 28800  # config


def test_config_with_invalid_int_falls_back(tmp_path: Path) -> None:
    """Garbage value in config doesn't break the helper — falls
    through to hardcoded fallback for that key."""
    p = _seed_project(
        tmp_path,
        config_block=(
            "detach_defaults:\n"
            "  check_every_sec: not-a-number\n"
            "  max_wait_sec: 43200\n"
        ),
    )
    result = _run_helper(p, env={})
    assert result.returncode == 0, result.stderr
    d = _detached_block(p)
    assert d["check_every_sec"] == 600  # invalid → fallback
    assert d["max_wait_sec"] == 43200  # config kept


def test_env_falls_back_to_config_when_unset(tmp_path: Path) -> None:
    """If env var is not set, config block still applies."""
    p = _seed_project(
        tmp_path,
        config_block="detach_defaults:\n  check_every_sec: 1200\n",
    )
    # Important: explicitly unset the env vars in case the runner has them.
    env = {}
    for k in ("CC_AUTOPIPE_DEFAULT_CHECK_EVERY", "CC_AUTOPIPE_DEFAULT_MAX_WAIT"):
        env[k] = ""  # empty string treated as unset by ${VAR:-...}
    result = _run_helper(p, env=env)
    assert result.returncode == 0, result.stderr
    d = _detached_block(p)
    assert d["check_every_sec"] == 1200  # from config
