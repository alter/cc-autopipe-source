"""Unit tests for src/lib/notify.py — Bug G dedup logic."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_LIB = REPO_ROOT / "src" / "lib"

sys.path.insert(0, str(SRC_LIB))

import notify  # noqa: E402


def test_first_call_sends(tmp_path: Path) -> None:
    sent = notify.notify_subprocess_failed_dedup(
        "proj1", 1, "boom", tmp_path, dry_run=True
    )
    assert sent is True


def test_second_call_within_window_deduped(tmp_path: Path) -> None:
    """SPEC-v1.2.md Bug G: max 1 per 10min per project per rc."""
    notify.notify_subprocess_failed_dedup("proj1", 1, "boom", tmp_path, dry_run=True)
    sent2 = notify.notify_subprocess_failed_dedup(
        "proj1", 1, "boom again", tmp_path, dry_run=True
    )
    assert sent2 is False


def test_different_project_not_deduped(tmp_path: Path) -> None:
    """Sentinel is per-project. proj2 alert isn't suppressed by proj1."""
    notify.notify_subprocess_failed_dedup("proj1", 1, "p1", tmp_path, dry_run=True)
    sent2 = notify.notify_subprocess_failed_dedup(
        "proj2", 1, "p2", tmp_path, dry_run=True
    )
    assert sent2 is True


def test_different_rc_not_deduped(tmp_path: Path) -> None:
    """Sentinel is per-rc. rc=2 alert isn't suppressed by rc=1.
    Different exit codes usually mean different problems."""
    notify.notify_subprocess_failed_dedup("proj1", 1, "boom", tmp_path, dry_run=True)
    sent2 = notify.notify_subprocess_failed_dedup(
        "proj1", 2, "different", tmp_path, dry_run=True
    )
    assert sent2 is True


def test_after_window_alert_again(tmp_path: Path) -> None:
    """Once dedup_window has elapsed, the next alert fires again.
    Use a tiny window + age the sentinel manually to keep the test fast."""
    sentinel = notify._sentinel_path(tmp_path, 1, "proj1")
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.touch()
    # Backdate the sentinel to 1 hour ago.
    old = time.time() - 3600
    os.utime(sentinel, (old, old))

    sent = notify.notify_subprocess_failed_dedup(
        "proj1", 1, "boom", tmp_path, dedup_window=600, dry_run=True
    )
    assert sent is True


def test_zero_window_always_sends(tmp_path: Path) -> None:
    """Edge case: dedup_window=0 means no dedup."""
    notify.notify_subprocess_failed_dedup(
        "proj1", 1, "boom", tmp_path, dedup_window=0, dry_run=True
    )
    # Backdate so age > 0.
    sentinel = notify._sentinel_path(tmp_path, 1, "proj1")
    old = time.time() - 1
    os.utime(sentinel, (old, old))
    sent = notify.notify_subprocess_failed_dedup(
        "proj1", 1, "boom", tmp_path, dedup_window=0, dry_run=True
    )
    assert sent is True


def test_sentinel_filename_format(tmp_path: Path) -> None:
    """SPEC-v1.2.md Bug G specifies the exact sentinel path:
    `alert-rc{rc}-{project_name}.last`."""
    notify.notify_subprocess_failed_dedup("myproj", 7, "boom", tmp_path, dry_run=True)
    expected = tmp_path / "alert-rc7-myproj.last"
    assert expected.exists()


def test_message_format_matches_spec(tmp_path: Path) -> None:
    """The formatted message text matches SPEC-v1.2.md Bug G example."""
    msg = notify._format_message("AI-trade", 1, "tail of stderr here")
    assert msg.startswith("[AI-trade] cycle_failed rc=1")
    assert "stderr_tail: tail of stderr here" in msg


def test_message_truncates_stderr_to_300_chars(tmp_path: Path) -> None:
    huge = "x" * 5000
    msg = notify._format_message("p", 1, huge)
    # 300-char tail + format overhead.
    assert "x" * 300 in msg
    assert "x" * 5000 not in msg


def test_message_handles_empty_stderr(tmp_path: Path) -> None:
    msg = notify._format_message("p", 1, "")
    assert "(empty)" in msg


def test_creates_sentinel_dir_if_missing(tmp_path: Path) -> None:
    nested = tmp_path / "nested" / "deep" / "sentinels"
    sent = notify.notify_subprocess_failed_dedup(
        "proj1", 1, "boom", nested, dry_run=True
    )
    assert sent is True
    assert (nested / "alert-rc1-proj1.last").exists()


def test_dry_run_does_not_invoke_tg_sh(tmp_path: Path, monkeypatch) -> None:
    """dry_run=True must not actually run tg.sh. We verify by failing
    if subprocess.run is called."""
    import subprocess as _sp

    called: list[bool] = []

    def boom(*args, **kwargs):
        called.append(True)
        raise AssertionError("subprocess.run should not run in dry_run")

    monkeypatch.setattr(_sp, "run", boom)
    sent = notify.notify_subprocess_failed_dedup("p", 1, "x", tmp_path, dry_run=True)
    assert sent is True
    assert called == []
