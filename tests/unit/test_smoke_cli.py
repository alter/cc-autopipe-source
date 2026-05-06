"""Unit tests for src/cli/smoke.py (v1.3.3 Group M).

Three terminal outcomes from cc-autopipe-smoke:
  - SMOKE_OK exit 0  (script rc=0 within timeout, OR alive past min-alive)
  - SMOKE_FAIL exit 1 (script rc!=0 within timeout)
  - misuse exit 2     (missing/non-executable script, bad config)

Tests the python entry-point directly to keep wall time low — the bash
wrapper is exercised by tests/smoke/v133/.
"""

from __future__ import annotations

import io
import stat
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src" / "cli"))

import smoke  # noqa: E402


def _make_script(tmp_path: Path, name: str, body: str, executable: bool = True) -> Path:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    if executable:
        st = p.stat()
        p.chmod(st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return p


def test_smoke_ok_when_script_exits_zero(tmp_path: Path) -> None:
    script = _make_script(tmp_path, "ok.sh", "#!/bin/bash\necho hello\nexit 0\n")
    out = io.StringIO()
    err = io.StringIO()
    rc = smoke.smoke(
        script,
        timeout_sec=10,
        min_alive_sec=2,
        stdout_writer=out,
        stderr_writer=err,
    )
    assert rc == 0
    assert "SMOKE_OK: script completed successfully" in out.getvalue()


def test_smoke_fail_when_script_exits_nonzero(tmp_path: Path) -> None:
    script = _make_script(tmp_path, "fail.sh", '#!/bin/bash\necho "boom" >&2\nexit 7\n')
    out = io.StringIO()
    err = io.StringIO()
    rc = smoke.smoke(
        script,
        timeout_sec=10,
        min_alive_sec=2,
        stdout_writer=out,
        stderr_writer=err,
    )
    assert rc == 1
    assert "SMOKE_FAIL: script exited with rc=7" in out.getvalue()
    assert "boom" in err.getvalue()


def test_smoke_ok_when_script_alive_past_min_alive(tmp_path: Path) -> None:
    script = _make_script(tmp_path, "long.sh", "#!/bin/bash\nsleep 60\n")
    out = io.StringIO()
    err = io.StringIO()
    rc = smoke.smoke(
        script,
        timeout_sec=4,
        min_alive_sec=2,
        stdout_writer=out,
        stderr_writer=err,
    )
    assert rc == 0
    assert "SMOKE_OK: script alive past min-alive threshold" in out.getvalue()


def test_smoke_kills_long_running_process_tree(tmp_path: Path) -> None:
    """After --timeout-sec, the smoke runner must kill the script and
    any descendants. Verify by spawning a child via `&` and checking
    nothing remains."""
    marker = tmp_path / "child.alive"
    script = _make_script(
        tmp_path,
        "spawn.sh",
        "#!/bin/bash\n"
        "( while true; do touch '%s'; sleep 0.2; done ) &\n"
        "wait\n" % marker,
    )
    out = io.StringIO()
    err = io.StringIO()
    rc = smoke.smoke(
        script,
        timeout_sec=3,
        min_alive_sec=2,
        stdout_writer=out,
        stderr_writer=err,
    )
    assert rc == 0
    # After kill, the touch loop should stop. Capture the mtime, wait,
    # confirm it doesn't advance.
    import time as _t

    if marker.exists():
        before = marker.stat().st_mtime
        _t.sleep(1.0)
        after = marker.stat().st_mtime
        assert before == after, "background child still alive after smoke killed parent"


def test_smoke_misuse_when_script_missing(tmp_path: Path) -> None:
    err = io.StringIO()
    rc = smoke.smoke(
        tmp_path / "does-not-exist.sh",
        timeout_sec=10,
        min_alive_sec=2,
        stderr_writer=err,
    )
    assert rc == 2
    assert "not found" in err.getvalue()


def test_smoke_misuse_when_script_not_executable(tmp_path: Path) -> None:
    script = _make_script(
        tmp_path, "noexec.sh", "#!/bin/bash\nexit 0\n", executable=False
    )
    err = io.StringIO()
    rc = smoke.smoke(
        script,
        timeout_sec=10,
        min_alive_sec=2,
        stderr_writer=err,
    )
    assert rc == 2
    assert "not executable" in err.getvalue()


def test_smoke_misuse_when_timeout_below_min_alive(tmp_path: Path) -> None:
    script = _make_script(tmp_path, "ok.sh", "#!/bin/bash\nexit 0\n")
    err = io.StringIO()
    rc = smoke.smoke(
        script,
        timeout_sec=2,
        min_alive_sec=10,
        stderr_writer=err,
    )
    assert rc == 2
    assert "misconfig" in err.getvalue()


def test_smoke_main_cli_returns_zero_for_ok_script(tmp_path: Path, capsys) -> None:
    script = _make_script(tmp_path, "ok.sh", "#!/bin/bash\nexit 0\n")
    rc = smoke.main(
        [
            str(script),
            "--timeout-sec",
            "10",
            "--min-alive-sec",
            "2",
            "--workdir",
            str(tmp_path),
        ]
    )
    assert rc == 0
    captured = capsys.readouterr()
    assert "SMOKE_OK" in captured.out
