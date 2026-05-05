"""Unit tests for src/lib/disk.py — PROMPT_v1.3-FULL.md GROUP C2."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_LIB = REPO_ROOT / "src" / "lib"
sys.path.insert(0, str(SRC_LIB))

import disk  # noqa: E402


def _seed_models(tmp_path: Path, exp: str, epochs: list[int]) -> Path:
    base = tmp_path / "proj" / "data" / "models" / exp
    base.mkdir(parents=True, exist_ok=True)
    for e in epochs:
        (base / f"checkpoint_epoch_{e}.pt").write_text(f"x{e}")
    return base


def test_check_disk_space_ok(tmp_path: Path) -> None:
    out = disk.check_disk_space(tmp_path, min_free_gb=0.0)
    assert "free_gb" in out
    assert "used_pct" in out
    assert out["ok"] is True


def test_check_disk_space_fail_above_threshold(tmp_path: Path) -> None:
    # Set an absurdly high threshold so even brand-new tmp_path fails.
    out = disk.check_disk_space(tmp_path, min_free_gb=10**9)
    assert out["ok"] is False


def test_cleanup_keeps_newest_k_per_dir(tmp_path: Path) -> None:
    proj = tmp_path / "proj"
    _seed_models(tmp_path, "exp_a", [1, 2, 3, 4, 5])
    removed = disk.cleanup_old_checkpoints(proj, keep_per_dir=2)
    # Keep epochs 4, 5 → remove 1, 2, 3
    base = proj / "data" / "models" / "exp_a"
    survivors = sorted(p.name for p in base.iterdir())
    assert survivors == ["checkpoint_epoch_4.pt", "checkpoint_epoch_5.pt"]
    assert len(removed) == 3


def test_cleanup_skips_when_under_keep(tmp_path: Path) -> None:
    proj = tmp_path / "proj"
    _seed_models(tmp_path, "exp_a", [1, 2])
    removed = disk.cleanup_old_checkpoints(proj, keep_per_dir=3)
    assert removed == []


def test_cleanup_never_touches_final_or_norm_stats(tmp_path: Path) -> None:
    proj = tmp_path / "proj"
    base = _seed_models(tmp_path, "exp_a", [1, 2, 3, 4, 5])
    # Add files that must be preserved.
    (base / "model_final_2026-05-04.pt").write_text("final")
    (base / "norm_stats.json").write_text("{}")
    (base / "best.pt").write_text("best")
    disk.cleanup_old_checkpoints(proj, keep_per_dir=1)
    survivors = sorted(p.name for p in base.iterdir())
    # Only newest epoch ckpt + protected files survive.
    assert "checkpoint_epoch_5.pt" in survivors
    assert "model_final_2026-05-04.pt" in survivors
    assert "norm_stats.json" in survivors
    assert "best.pt" in survivors
    # All older epoch ckpts removed.
    assert "checkpoint_epoch_1.pt" not in survivors
    assert "checkpoint_epoch_4.pt" not in survivors


def test_cleanup_handles_multiple_experiments(tmp_path: Path) -> None:
    proj = tmp_path / "proj"
    _seed_models(tmp_path, "exp_a", [1, 2, 3, 4])
    _seed_models(tmp_path, "exp_b", [10, 20, 30])
    disk.cleanup_old_checkpoints(proj, keep_per_dir=2)
    a = sorted(p.name for p in (proj / "data/models/exp_a").iterdir())
    b = sorted(p.name for p in (proj / "data/models/exp_b").iterdir())
    assert a == ["checkpoint_epoch_3.pt", "checkpoint_epoch_4.pt"]
    assert b == ["checkpoint_epoch_20.pt", "checkpoint_epoch_30.pt"]


def test_cleanup_dry_run_does_not_delete(tmp_path: Path) -> None:
    proj = tmp_path / "proj"
    base = _seed_models(tmp_path, "exp_a", [1, 2, 3, 4, 5])
    removed = disk.cleanup_old_checkpoints(proj, keep_per_dir=1, dry_run=True)
    assert len(removed) == 4
    survivors = sorted(p.name for p in base.iterdir())
    # Nothing actually deleted in dry-run.
    assert len(survivors) == 5


def test_cleanup_handles_missing_models_dir(tmp_path: Path) -> None:
    # No data/models/ at all.
    proj = tmp_path / "proj"
    proj.mkdir()
    assert disk.cleanup_old_checkpoints(proj) == []
