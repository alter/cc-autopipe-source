"""Integration tests for cc-autopipe install-systemd / install-launchd
and their uninstall counterparts (Stage M).

Covers SPEC-v1.md §2.6 acceptance:
- install creates the correct file in the correct location
- uninstall removes it cleanly
- placeholder substitution lands the right paths
- idempotent uninstall (rc=0 even when absent)
- unknown subcommand → argparse error
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
SERVICE_PY = SRC / "cli" / "service.py"
DISPATCHER = SRC / "helpers" / "cc-autopipe"


def _run(
    args: list[str],
    *,
    extra_env: dict[str, str] | None = None,
    expect_rc: int | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["CC_AUTOPIPE_HOME"] = str(SRC)
    if extra_env:
        env.update(extra_env)
    cp = subprocess.run(
        [sys.executable, str(SERVICE_PY), *args],
        capture_output=True,
        text=True,
        env=env,
    )
    if expect_rc is not None:
        assert cp.returncode == expect_rc, (
            f"expected rc={expect_rc}, got {cp.returncode}\n"
            f"stdout: {cp.stdout}\nstderr: {cp.stderr}"
        )
    return cp


def _run_dispatcher(
    args: list[str],
    *,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["CC_AUTOPIPE_HOME"] = str(SRC)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [str(DISPATCHER), *args],
        capture_output=True,
        text=True,
        env=env,
    )


# ---------------------------------------------------------------------------
# install-systemd
# ---------------------------------------------------------------------------


def test_install_systemd_writes_unit_file(tmp_path: Path) -> None:
    target = tmp_path / "systemd-target"
    fake_home = tmp_path / "fake-home"
    cp = _run(
        ["install-systemd", "--target-dir", str(target), "--home", str(fake_home)],
        expect_rc=0,
    )
    unit = target / "cc-autopipe.service"
    assert unit.exists()
    body = unit.read_text()
    assert "[Unit]" in body
    assert "[Service]" in body
    assert "[Install]" in body
    # Substitutions landed (placeholders all gone).
    assert "__USER__" not in body
    assert "__HOME__" not in body
    assert "__CC_AUTOPIPE_HOME__" not in body
    assert "__PATH__" not in body
    # Concrete values present.
    assert str(SRC) in body  # CC_AUTOPIPE_HOME
    assert str(fake_home) in body  # log paths under HOME
    assert "next steps" in cp.stdout.lower()


def test_install_systemd_creates_target_dir_if_missing(tmp_path: Path) -> None:
    target = tmp_path / "deep" / "nest" / "systemd"
    _run(
        ["install-systemd", "--target-dir", str(target), "--home", str(tmp_path)],
        expect_rc=0,
    )
    assert (target / "cc-autopipe.service").exists()


def test_uninstall_systemd_removes_unit(tmp_path: Path) -> None:
    target = tmp_path / "systemd"
    _run(
        ["install-systemd", "--target-dir", str(target), "--home", str(tmp_path)],
        expect_rc=0,
    )
    assert (target / "cc-autopipe.service").exists()
    _run(["uninstall-systemd", "--target-dir", str(target)], expect_rc=0)
    assert not (target / "cc-autopipe.service").exists()


def test_uninstall_systemd_idempotent_when_absent(tmp_path: Path) -> None:
    target = tmp_path / "empty-systemd"
    target.mkdir()
    cp = _run(["uninstall-systemd", "--target-dir", str(target)], expect_rc=0)
    assert "nothing to uninstall" in cp.stdout.lower()


# ---------------------------------------------------------------------------
# install-launchd
# ---------------------------------------------------------------------------


def test_install_launchd_writes_plist(tmp_path: Path) -> None:
    target = tmp_path / "launchd-target"
    fake_home = tmp_path / "fake-home"
    cp = _run(
        ["install-launchd", "--target-dir", str(target), "--home", str(fake_home)],
        expect_rc=0,
    )
    plist = target / "com.cc-autopipe.plist"
    assert plist.exists()
    body = plist.read_text()
    assert "<?xml" in body
    assert "<plist" in body
    assert "<key>Label</key>" in body
    assert "com.cc-autopipe" in body
    # Substitutions.
    assert "__CC_AUTOPIPE_HOME__" not in body
    assert "__HOME__" not in body
    assert "__PATH__" not in body
    assert str(SRC) in body
    assert str(fake_home) in body
    assert "launchctl load" in cp.stdout


def test_uninstall_launchd_removes_plist(tmp_path: Path) -> None:
    target = tmp_path / "launchd"
    _run(
        ["install-launchd", "--target-dir", str(target), "--home", str(tmp_path)],
        expect_rc=0,
    )
    assert (target / "com.cc-autopipe.plist").exists()
    _run(["uninstall-launchd", "--target-dir", str(target)], expect_rc=0)
    assert not (target / "com.cc-autopipe.plist").exists()


def test_uninstall_launchd_idempotent_when_absent(tmp_path: Path) -> None:
    target = tmp_path / "empty-launchd"
    target.mkdir()
    cp = _run(["uninstall-launchd", "--target-dir", str(target)], expect_rc=0)
    assert "nothing to uninstall" in cp.stdout.lower()


# ---------------------------------------------------------------------------
# Argparse / dispatcher
# ---------------------------------------------------------------------------


def test_unknown_subcommand_argparse_errors(tmp_path: Path) -> None:
    cp = _run(["bogus-action"])
    assert cp.returncode != 0


def test_dispatcher_exposes_all_four_subcommands(tmp_path: Path) -> None:
    """`cc-autopipe --help` must list the install-* / uninstall-* names
    in the v1.0 service-install section."""
    cp = _run_dispatcher(["--help"])
    assert cp.returncode == 0
    for name in (
        "install-systemd",
        "uninstall-systemd",
        "install-launchd",
        "uninstall-launchd",
    ):
        assert name in cp.stdout, f"--help missing {name}"


def test_dispatcher_routes_install_systemd(tmp_path: Path) -> None:
    target = tmp_path / "via-dispatcher"
    cp = _run_dispatcher(
        [
            "install-systemd",
            "--target-dir",
            str(target),
            "--home",
            str(tmp_path),
        ],
    )
    assert cp.returncode == 0, cp.stderr
    assert (target / "cc-autopipe.service").exists()


def test_dispatcher_routes_install_launchd(tmp_path: Path) -> None:
    target = tmp_path / "via-dispatcher-l"
    cp = _run_dispatcher(
        [
            "install-launchd",
            "--target-dir",
            str(target),
            "--home",
            str(tmp_path),
        ],
    )
    assert cp.returncode == 0, cp.stderr
    assert (target / "com.cc-autopipe.plist").exists()
