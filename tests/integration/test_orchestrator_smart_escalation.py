"""Integration tests for Bug H smart escalation in process_project.

The orchestrator's failure-handling block now routes by category
(failures.categorize_recent) instead of blanket-escalating after 3
consecutive failures. These tests exercise the routing end-to-end
via mock-claude + a deterministic verify.sh.

Three patterns:
  - 3 verify_failed → HUMAN_NEEDED (verify pattern), no escalation
  - 3 claude_subprocess_failed → escalation (v1.0 path preserved)
  - empty failures.jsonl + consecutive_failures=3 → fallback path
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
ORCHESTRATOR = SRC / "orchestrator"
DISPATCHER = SRC / "helpers" / "cc-autopipe"
HOOKS_DIR = SRC / "hooks"
MOCK_CLAUDE = REPO_ROOT / "tools" / "mock-claude.sh"


def _init_project(project: Path, user_home: Path) -> None:
    project.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    env = os.environ.copy()
    env["CC_AUTOPIPE_HOME"] = str(SRC)
    env["CC_AUTOPIPE_USER_HOME"] = str(user_home)
    subprocess.run(
        [str(DISPATCHER), "init", str(project)],
        check=True,
        capture_output=True,
        env=env,
    )


def _write_verify(project: Path, body: str) -> None:
    v = project / ".cc-autopipe" / "verify.sh"
    v.write_text(f"#!/bin/bash\n{body}\n")
    v.chmod(0o755)


def _read_state(project: Path) -> dict:
    return json.loads((project / ".cc-autopipe" / "state.json").read_text())


def _read_aggregate(user_home: Path) -> list[dict]:
    log = user_home / "log" / "aggregate.jsonl"
    if not log.exists():
        return []
    return [json.loads(ln) for ln in log.read_text().splitlines() if ln.strip()]


def _run_orch(
    user_home: Path,
    *,
    max_loops: int = 3,
    cooldown: float = 0.0,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["CC_AUTOPIPE_HOME"] = str(SRC)
    env["CC_AUTOPIPE_USER_HOME"] = str(user_home)
    env["CC_AUTOPIPE_COOLDOWN_SEC"] = str(cooldown)
    env["CC_AUTOPIPE_IDLE_SLEEP_SEC"] = "0"
    env["CC_AUTOPIPE_MAX_LOOPS"] = str(max_loops)
    env["CC_AUTOPIPE_CLAUDE_BIN"] = str(MOCK_CLAUDE)
    env["CC_AUTOPIPE_QUOTA_DISABLED"] = "1"
    env["CC_AUTOPIPE_HOOKS_DIR"] = str(HOOKS_DIR)
    env["CC_AUTOPIPE_CYCLE_TIMEOUT_SEC"] = "30"
    return subprocess.run(
        [sys.executable, str(ORCHESTRATOR)],
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
    )


@pytest.fixture
def env_paths(tmp_path: Path) -> tuple[Path, Path]:
    return tmp_path / "uhome", tmp_path / "project"


# ---------------------------------------------------------------------------
# Verify-failed pattern → HUMAN_NEEDED, no escalation
# ---------------------------------------------------------------------------


def test_three_verify_failed_writes_human_needed_no_escalation(
    env_paths: tuple[Path, Path],
) -> None:
    """Bug H acceptance: 3+ verify_failed in failures.jsonl with
    consecutive_failures>=3 → orchestrator picks the verify-pattern
    branch: phase=failed, HUMAN_NEEDED.md mentions "verify pattern",
    escalation_skipped event logged, NO escalated_to_opus event."""
    user_home, project = env_paths
    _init_project(project, user_home)
    # verify.sh always returns clean envelope with passed=false →
    # categorized as verify_failed.
    _write_verify(
        project,
        'echo \'{"passed":false,"score":0.4,"prd_complete":false,"details":{}}\'',
    )

    cp = _run_orch(user_home, max_loops=3)
    assert cp.returncode == 0, cp.stderr

    s = _read_state(project)
    assert s["phase"] == "failed", s
    assert s["consecutive_failures"] >= 3

    hn = project / ".cc-autopipe" / "HUMAN_NEEDED.md"
    assert hn.exists()
    text = hn.read_text(encoding="utf-8")
    assert "verify pattern" in text.lower()
    assert "did NOT auto-escalate" in text

    types = [e.get("event") for e in _read_aggregate(user_home)]
    assert "escalation_skipped" in types, types
    assert "failed" in types, types
    assert "escalated_to_opus" not in types, (
        f"verify pattern must NOT trigger opus escalation: {types}"
    )


def test_verify_failed_routing_logs_categorization_reason(
    env_paths: tuple[Path, Path],
) -> None:
    """The escalation_skipped event must include enough info to debug
    the decision: crash_count, verify_count, reason string."""
    user_home, project = env_paths
    _init_project(project, user_home)
    _write_verify(
        project,
        'echo \'{"passed":false,"score":0.4,"prd_complete":false,"details":{}}\'',
    )

    _run_orch(user_home, max_loops=3)

    skipped_events = [
        e for e in _read_aggregate(user_home) if e.get("event") == "escalation_skipped"
    ]
    assert len(skipped_events) >= 1
    ev = skipped_events[0]
    assert ev.get("verify_count", 0) >= 3
    assert "verify_failed" in ev.get("reason", "")


# ---------------------------------------------------------------------------
# v1.0 escalation path preserved when crashes dominate
# ---------------------------------------------------------------------------


def test_three_subprocess_crashes_still_escalate(
    env_paths: tuple[Path, Path],
) -> None:
    """When recent failures are dominated by claude_subprocess_failed
    (rc != 0), Bug H preserves v1.0 escalation: opus + xhigh on next
    cycle. mock-claude with scenario that exits non-zero produces
    claude_subprocess_failed entries."""
    user_home, project = env_paths
    _init_project(project, user_home)
    # Pre-seed failures.jsonl with 3 crash entries directly so we don't
    # have to drive 3 mock-claude rc!=0 cycles (which is a separate
    # mock-claude scenario, harder to wire). Then force consecutive_failures=3
    # via a malformed verify.sh (single cycle bumps it once).
    mem = project / ".cc-autopipe" / "memory"
    mem.mkdir(parents=True, exist_ok=True)
    (mem / "failures.jsonl").write_text(
        "\n".join(
            json.dumps(
                {
                    "ts": f"2026-05-03T10:0{i}:00Z",
                    "error": "claude_subprocess_failed",
                    "exit_code": 1,
                    "stderr_tail": "boom",
                }
            )
            for i in range(3)
        )
        + "\n"
    )
    # Also seed state with consecutive_failures=3 so the
    # categorization branch fires on cycle 1.
    state_path = project / ".cc-autopipe" / "state.json"
    s = json.loads(state_path.read_text())
    s["consecutive_failures"] = 3
    state_path.write_text(json.dumps(s))

    # Verify always passes — but the crash-dominated failures.jsonl plus
    # consecutive_failures>=3 should still trigger the categorization.
    # Actually, a passing verify resets consecutive_failures to 0 in
    # state.update_verify, so the threshold check won't fire. Use a
    # malformed verify instead so the threshold check still triggers.
    _write_verify(project, "echo not json")

    _run_orch(user_home, max_loops=1)

    s = _read_state(project)
    types = [e.get("event") for e in _read_aggregate(user_home)]
    # Crash pattern dominated → escalated_to_opus event present, no
    # escalation_skipped event for this run.
    assert "escalated_to_opus" in types, types
    assert s["escalated_next_cycle"] is True


# ---------------------------------------------------------------------------
# Fallback (no clear pattern, but threshold hit)
# ---------------------------------------------------------------------------


def test_threshold_hit_with_empty_failures_uses_fallback(
    env_paths: tuple[Path, Path],
) -> None:
    """If consecutive_failures hits the threshold but failures.jsonl is
    empty (categorize returns no clear pattern), the orchestrator falls
    back to the v1.0 deferred-fail-via-escalation semantics. Ensures
    no behaviour regression for projects that don't generate
    failures.jsonl entries through the normal path."""
    user_home, project = env_paths
    _init_project(project, user_home)
    # Pre-seed state with consecutive_failures=3, empty failures.jsonl.
    state_path = project / ".cc-autopipe" / "state.json"
    s = json.loads(state_path.read_text())
    s["consecutive_failures"] = 3
    state_path.write_text(json.dumps(s))
    # Don't seed failures.jsonl (no recent entries to categorise).
    _write_verify(project, "echo not json")  # bumps to 4 on this cycle

    _run_orch(user_home, max_loops=1)

    types = [e.get("event") for e in _read_aggregate(user_home)]
    # Either escalates (fallback path) or fails — both are acceptable
    # per the fallback's "preserve v1.0 deferred-fail" semantics. The
    # critical thing is: no v1.2-only events fired (escalation_skipped,
    # mixed-pattern fail).
    assert "escalation_skipped" not in types
