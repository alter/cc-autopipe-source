"""Unit tests for doctor.check_wsl_systemd — PROMPT_v1.3-FULL.md K1."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCTOR_PATH = REPO_ROOT / "src" / "cli" / "doctor.py"

spec = importlib.util.spec_from_file_location("doctor_mod", str(DOCTOR_PATH))
assert spec is not None and spec.loader is not None
doctor = importlib.util.module_from_spec(spec)
sys.modules["doctor_mod"] = doctor
spec.loader.exec_module(doctor)


def _patch_osrelease(monkeypatch, content: str | None) -> None:
    """Make Path('/proc/sys/kernel/osrelease') behave as if its content
    were `content` (or non-existent if None)."""
    real_path_cls = doctor.Path

    class FakePath(real_path_cls):  # type: ignore[misc]
        def exists(self):
            if str(self) == "/proc/sys/kernel/osrelease":
                return content is not None
            return real_path_cls.exists(self)

        def read_text(self, *args, **kwargs):
            if str(self) == "/proc/sys/kernel/osrelease":
                return content or ""
            return real_path_cls.read_text(self, *args, **kwargs)

    monkeypatch.setattr(doctor, "Path", FakePath)


def test_skip_on_non_linux(monkeypatch) -> None:
    _patch_osrelease(monkeypatch, None)
    out = doctor.check_wsl_systemd()
    assert out.status == doctor.SKIP
    assert "not Linux kernel" in out.detail


def test_skip_on_non_wsl_linux(monkeypatch) -> None:
    _patch_osrelease(monkeypatch, "5.15.0-91-generic\n")
    out = doctor.check_wsl_systemd()
    assert out.status == doctor.SKIP
    assert "not running on WSL" in out.detail


def test_fail_when_systemctl_missing(monkeypatch) -> None:
    _patch_osrelease(monkeypatch, "5.15.0-microsoft-standard-WSL2\n")

    def boom(*_a, **_k):
        raise FileNotFoundError("systemctl")

    monkeypatch.setattr(subprocess, "run", boom)
    out = doctor.check_wsl_systemd()
    assert out.status == doctor.FAIL
    assert "systemctl not available" in out.detail
    assert "deploy/WSL2.md" in out.hint


def test_fail_when_systemctl_returns_nonzero(monkeypatch) -> None:
    _patch_osrelease(monkeypatch, "5.15.0-microsoft-standard-WSL2\n")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_a, **_k: SimpleNamespace(returncode=1, stdout="", stderr=""),
    )
    out = doctor.check_wsl_systemd()
    assert out.status == doctor.FAIL
    assert "rc=1" in out.detail


def test_ok_when_systemctl_works(monkeypatch) -> None:
    _patch_osrelease(monkeypatch, "5.15.0-microsoft-standard-WSL2\n")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_a, **_k: SimpleNamespace(
            returncode=0, stdout="systemd 252", stderr=""
        ),
    )
    out = doctor.check_wsl_systemd()
    assert out.status == doctor.OK


def test_run_all_includes_wsl_systemd() -> None:
    """check_wsl_systemd must appear in run_all so `cc-autopipe doctor`
    surfaces it without flag changes."""
    checks = doctor.run_all(offline=True)
    names = [c.name for c in checks]
    assert "wsl-systemd" in names
