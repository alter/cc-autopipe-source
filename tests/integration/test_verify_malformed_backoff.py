"""Integration tests for v1.3.12 VERIFY-MALFORMED-BACKOFF.

`verify_malformed` events (verify.sh outputting non-JSON, typically
the `|| echo 0` double-zero bug) now route through `inc_malformed`
instead of `inc_failures`. After
`MALFORMED_HUMAN_NEEDED_THRESHOLD` consecutive malformed events,
state.py writes `HUMAN_NEEDED.md` with a script-fix recipe. A
passing verify resets `consecutive_malformed`; a genuine verify
failure does NOT.

Coverage:
  1. 2 malformed events → counter=2, no HUMAN_NEEDED.md
  2. 3 malformed events → counter=3, HUMAN_NEEDED.md written w/ guidance
  3. After threshold, passing verify → both counters cleared, file kept
  4. Failing verify does NOT reset consecutive_malformed
  5. reset-malformed CLI: counter=5 → 0
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
LIB = SRC / "lib"
for _p in (str(SRC), str(LIB)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import state  # noqa: E402


def _project(tmp_path: Path) -> Path:
    p = tmp_path / "demo"
    (p / ".cc-autopipe" / "memory").mkdir(parents=True)
    return p


# ---------------------------------------------------------------------------
# Test 1: 2 malformed → counter=2, no HUMAN_NEEDED.md
# ---------------------------------------------------------------------------

def test_two_malformed_no_human_needed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    project = _project(tmp_path)
    state.write(project, state.State.fresh("demo"))

    state.inc_malformed(project)
    state.inc_malformed(project)

    s = state.read(project)
    assert s.consecutive_malformed == 2
    assert s.consecutive_failures == 0  # malformed must NOT touch failures

    assert not (project / ".cc-autopipe" / "HUMAN_NEEDED.md").exists()


# ---------------------------------------------------------------------------
# Test 2: 3 malformed → HUMAN_NEEDED.md with `|| echo 0` and `|| true`
# ---------------------------------------------------------------------------

def test_three_malformed_writes_human_needed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    project = _project(tmp_path)
    state.write(project, state.State.fresh("demo"))

    state.inc_malformed(project)
    state.inc_malformed(project)
    state.inc_malformed(project)

    s = state.read(project)
    assert s.consecutive_malformed == 3
    assert s.consecutive_failures == 0

    human_needed = project / ".cc-autopipe" / "HUMAN_NEEDED.md"
    assert human_needed.exists()
    body = human_needed.read_text(encoding="utf-8")
    assert "|| echo 0" in body
    assert "|| true" in body
    assert "verify.sh" in body
    # Reset-malformed instructions must point at the CLI surface
    assert "reset-malformed" in body


# ---------------------------------------------------------------------------
# Test 3: passing verify resets malformed counter; HUMAN_NEEDED kept
# ---------------------------------------------------------------------------

def test_passing_verify_resets_malformed_keeps_human_needed(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    project = _project(tmp_path)
    state.write(project, state.State.fresh("demo"))

    for _ in range(3):
        state.inc_malformed(project)
    human_needed = project / ".cc-autopipe" / "HUMAN_NEEDED.md"
    assert human_needed.exists()

    # Now verify.sh starts producing valid JSON and passes.
    state.update_verify(project, passed=True, score=0.9, prd_complete=False)

    s = state.read(project)
    assert s.consecutive_malformed == 0
    assert s.consecutive_failures == 0

    # The human still needs to read the file — engine must NOT auto-delete.
    assert human_needed.exists(), (
        "HUMAN_NEEDED.md auto-deleted; the operator may never see it"
    )


# ---------------------------------------------------------------------------
# Test 4: failing verify does NOT reset consecutive_malformed
# ---------------------------------------------------------------------------

def test_failing_verify_does_not_reset_malformed(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    project = _project(tmp_path)
    state.write(project, state.State.fresh("demo"))

    state.inc_malformed(project)
    state.inc_malformed(project)
    assert state.read(project).consecutive_malformed == 2

    # Genuine verify failure (script ran fine, work didn't pass).
    state.update_verify(project, passed=False, score=0.4, prd_complete=False)

    s = state.read(project)
    assert s.consecutive_malformed == 2  # untouched by failure path
    assert s.consecutive_failures == 1   # logic-failure counter advanced


# ---------------------------------------------------------------------------
# Test 5: reset-malformed CLI: 5 → 0
# ---------------------------------------------------------------------------

def test_reset_malformed_cli(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(tmp_path / "uhome"))
    project = _project(tmp_path)
    s = state.State.fresh("demo")
    s.consecutive_malformed = 5
    state.write(project, s)

    state_py = SRC / "lib" / "state.py"
    out = subprocess.run(
        [sys.executable, str(state_py), "reset-malformed", str(project)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip() == "0"
    assert state.read(project).consecutive_malformed == 0
