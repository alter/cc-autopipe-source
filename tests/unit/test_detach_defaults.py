"""Unit tests for src/lib/detach_defaults.py.

Covers the v1.3.1 DETACH-CONFIG resolution chain step 3
(project config.yaml → detach_defaults block parser).
"""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
LIB = SRC / "lib"
for p in (str(SRC), str(LIB)):
    if p not in sys.path:
        sys.path.insert(0, p)

dd = importlib.import_module("detach_defaults")


def _project(base: Path, body: str | None) -> Path:
    p = base / "demo"
    cca = p / ".cc-autopipe"
    cca.mkdir(parents=True, exist_ok=True)
    if body is not None:
        (cca / "config.yaml").write_text(body, encoding="utf-8")
    return p


def test_no_config_file_returns_empty(tmp_path: Path) -> None:
    p = tmp_path / "demo"
    (p / ".cc-autopipe").mkdir(parents=True)
    assert dd.read_detach_defaults(p) == {}


def test_no_block_returns_empty(tmp_path: Path) -> None:
    p = _project(
        tmp_path,
        "schema_version: 1\nname: demo\n",
    )
    assert dd.read_detach_defaults(p) == {}


def test_full_block_returns_both_keys(tmp_path: Path) -> None:
    p = _project(
        tmp_path,
        "name: demo\n"
        "detach_defaults:\n"
        "  check_every_sec: 900\n"
        "  max_wait_sec: 43200\n",
    )
    out = dd.read_detach_defaults(p)
    assert out == {"check_every_sec": 900, "max_wait_sec": 43200}


def test_partial_block_returns_only_present_key(tmp_path: Path) -> None:
    p = _project(
        tmp_path,
        "detach_defaults:\n  max_wait_sec: 86400\n",
    )
    out = dd.read_detach_defaults(p)
    assert out == {"max_wait_sec": 86400}


def test_invalid_integer_dropped(tmp_path: Path) -> None:
    p = _project(
        tmp_path,
        "detach_defaults:\n"
        "  check_every_sec: not-a-number\n"
        "  max_wait_sec: 86400\n",
    )
    out = dd.read_detach_defaults(p)
    assert out == {"max_wait_sec": 86400}


def test_unknown_keys_ignored(tmp_path: Path) -> None:
    p = _project(
        tmp_path,
        "detach_defaults:\n"
        "  random_key: 1234\n"
        "  max_wait_sec: 28800\n",
    )
    out = dd.read_detach_defaults(p)
    assert out == {"max_wait_sec": 28800}


def test_block_followed_by_other_top_level_block(tmp_path: Path) -> None:
    """A subsequent top-level block must terminate detach_defaults
    parsing — otherwise auto_escalation keys could leak in."""
    p = _project(
        tmp_path,
        "detach_defaults:\n"
        "  check_every_sec: 900\n"
        "auto_escalation:\n"
        "  enabled: true\n"
        "  max_wait_sec: 99999\n",  # MUST be ignored — it's under auto_escalation
    )
    out = dd.read_detach_defaults(p)
    assert out == {"check_every_sec": 900}


def test_empty_block_returns_empty(tmp_path: Path) -> None:
    p = _project(
        tmp_path,
        "detach_defaults:\nname: demo\n",
    )
    assert dd.read_detach_defaults(p) == {}


def test_cli_no_arg_emits_empty_json() -> None:
    result = subprocess.run(
        [sys.executable, str(LIB / "detach_defaults.py")],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout) == {}


def test_cli_emits_json(tmp_path: Path) -> None:
    p = _project(
        tmp_path,
        "detach_defaults:\n  max_wait_sec: 43200\n",
    )
    result = subprocess.run(
        [sys.executable, str(LIB / "detach_defaults.py"), str(p)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout) == {"max_wait_sec": 43200}


def test_cli_missing_project_dir_emits_empty(tmp_path: Path) -> None:
    """Path that doesn't exist must still exit 0 with {}."""
    result = subprocess.run(
        [sys.executable, str(LIB / "detach_defaults.py"), str(tmp_path / "nope")],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout) == {}


def test_cli_key_mode_emits_value(tmp_path: Path) -> None:
    """--key NAME emits just the int value (so the bash helper can use
    `$(... --key max_wait_sec)` without jq)."""
    p = _project(
        tmp_path,
        "detach_defaults:\n"
        "  check_every_sec: 900\n"
        "  max_wait_sec: 43200\n",
    )
    result = subprocess.run(
        [
            sys.executable,
            str(LIB / "detach_defaults.py"),
            str(p),
            "--key",
            "max_wait_sec",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "43200"


def test_cli_key_mode_missing_key_emits_nothing(tmp_path: Path) -> None:
    """Missing key → empty stdout (so bash $(... --key X) yields '')."""
    p = _project(
        tmp_path,
        "detach_defaults:\n  check_every_sec: 900\n",
    )
    result = subprocess.run(
        [
            sys.executable,
            str(LIB / "detach_defaults.py"),
            str(p),
            "--key",
            "max_wait_sec",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == ""
