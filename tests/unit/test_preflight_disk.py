"""Unit tests for preflight._preflight_disk + _read_disk_config (C2)."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
LIB = SRC / "lib"
for p in (str(SRC), str(LIB)):
    if p not in sys.path:
        sys.path.insert(0, p)

import disk as disk_lib  # noqa: E402
import state  # noqa: E402

preflight = importlib.import_module("orchestrator.preflight")


def _project(tmp_path: Path, cfg_text: str | None = None) -> Path:
    p = tmp_path / "demo"
    (p / ".cc-autopipe").mkdir(parents=True)
    if cfg_text is not None:
        (p / ".cc-autopipe" / "config.yaml").write_text(cfg_text)
    return p


def test_disk_config_defaults_when_no_yaml(tmp_path: Path) -> None:
    p = _project(tmp_path)
    cfg = preflight._read_disk_config(p)
    assert cfg["disk_auto_cleanup"] is True
    assert cfg["disk_min_free_gb"] == 5.0
    assert cfg["disk_keep_checkpoints_per_dir"] == 3


def test_disk_config_overrides(tmp_path: Path) -> None:
    p = _project(
        tmp_path,
        "disk_auto_cleanup: false\n"
        "disk_min_free_gb: 10\n"
        "disk_keep_checkpoints_per_dir: 5\n",
    )
    cfg = preflight._read_disk_config(p)
    assert cfg["disk_auto_cleanup"] is False
    assert cfg["disk_min_free_gb"] == 10.0
    assert cfg["disk_keep_checkpoints_per_dir"] == 5


def test_disk_config_malformed_falls_back(tmp_path: Path) -> None:
    p = _project(
        tmp_path,
        "disk_min_free_gb: not_a_number\ndisk_keep_checkpoints_per_dir: x\n",
    )
    cfg = preflight._read_disk_config(p)
    assert cfg["disk_min_free_gb"] == 5.0  # fallback
    assert cfg["disk_keep_checkpoints_per_dir"] == 3  # fallback


def test_preflight_disk_ok_when_space_ample(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    p = _project(tmp_path, "disk_min_free_gb: 0\n")
    s = state.State.fresh(p.name)
    state.write(p, s)
    out = preflight._preflight_disk(p, s)
    assert out == "ok"


def test_preflight_disk_runs_cleanup_then_ok(
    tmp_path: Path, monkeypatch
) -> None:
    """Simulate: first probe says low, cleanup runs, second probe says ok."""
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    p = _project(tmp_path)
    s = state.State.fresh(p.name)
    state.write(p, s)
    # Seed three checkpoints in a model dir so cleanup has something
    # to remove (otherwise removed=0 still triggers paused branch).
    base = p / "data" / "models" / "exp_a"
    base.mkdir(parents=True)
    for e in (1, 2, 3, 4, 5):
        (base / f"checkpoint_epoch_{e}.pt").write_text(str(e))

    states = iter([
        {"free_gb": 1.0, "used_pct": 0.99, "ok": False},
        {"free_gb": 99.0, "used_pct": 0.10, "ok": True},
    ])

    def fake_check(_project, min_free_gb=0.0):
        return next(states)

    monkeypatch.setattr(disk_lib, "check_disk_space", fake_check)
    out = preflight._preflight_disk(p, s)
    assert out == "cleaned"


def test_preflight_disk_paused_when_cleanup_insufficient(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    p = _project(tmp_path)
    s = state.State.fresh(p.name)
    state.write(p, s)

    def fake_check(*_a, **_k):
        return {"free_gb": 0.5, "used_pct": 0.99, "ok": False}

    monkeypatch.setattr(disk_lib, "check_disk_space", fake_check)
    out = preflight._preflight_disk(p, s)
    assert out == "paused"
    s2 = state.read(p)
    assert s2.phase == "paused"
    assert s2.paused is not None
    assert s2.paused.reason == "disk_full"


def test_preflight_disk_disabled_cleanup_pauses_immediately(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    p = _project(tmp_path, "disk_auto_cleanup: false\n")
    s = state.State.fresh(p.name)
    state.write(p, s)
    # Fake the probe so test is deterministic regardless of host disk.
    cleanup_calls = []

    def fake_check(*_a, **_k):
        return {"free_gb": 0.5, "used_pct": 0.99, "ok": False}

    def fake_cleanup(*_a, **_k):
        cleanup_calls.append(1)
        return []

    monkeypatch.setattr(disk_lib, "check_disk_space", fake_check)
    monkeypatch.setattr(disk_lib, "cleanup_old_checkpoints", fake_cleanup)

    out = preflight._preflight_disk(p, s)
    assert out == "paused"
    s2 = state.read(p)
    assert s2.phase == "paused"
    # Auto-cleanup disabled → cleanup_old_checkpoints must NOT have been called.
    assert cleanup_calls == []
