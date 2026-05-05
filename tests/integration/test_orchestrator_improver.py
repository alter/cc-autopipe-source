"""Integration tests for the improver / skill-crystallisation hook (Stage N).

Covers SPEC-v1.md §2.7 acceptance:
- improver subagent shipped in template + provisioned by init
- _read_config_improver returns defaults when block absent + honors
  per-project overrides
- After N successful cycles, orchestrator:
    - creates .claude/skills/ if missing
    - sets state.improver_due = True
    - resets the success counter
    - logs improver_trigger_due
- _build_prompt injects the IMPROVER TRIGGER hint when improver_due
- The trigger is one-shot: persists improver_due=False after the
  prompt is built (so a failed escalated cycle doesn't double-fire)
- Disabled config bypasses the whole mechanism

The actual subagent execution (writing SKILL.md) is handled by the
main agent via the task tool; orchestrator's job is the bookkeeping +
dir prep + prompt hint.
"""

from __future__ import annotations

import importlib.machinery
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
LIB = SRC / "lib"
DISPATCHER = SRC / "helpers" / "cc-autopipe"
# Path to the orchestrator package dir; `python3 <dir>` runs __main__.py.
ORCHESTRATOR = SRC / "orchestrator"


@pytest.fixture(scope="module")
def orch_mod() -> object:
    """Compatibility fixture against the v1.3 package layout."""
    for p in (str(SRC), str(LIB)):
        if p not in sys.path:
            sys.path.insert(0, p)
    prompt = importlib.import_module("orchestrator.prompt")
    state_mod = importlib.import_module("state")
    return SimpleNamespace(
        _read_config_improver=prompt._read_config_improver,
        _build_prompt=prompt._build_prompt,
        state=state_mod,
    )


def _real_init(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    project = tmp_path / "demo-imp"
    user_home = tmp_path / "uhome-imp"
    project.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    env = os.environ.copy()
    env["CC_AUTOPIPE_HOME"] = str(SRC)
    env["CC_AUTOPIPE_USER_HOME"] = str(user_home)
    subprocess.run(
        [str(DISPATCHER), "init", str(project)],
        env=env,
        check=True,
        capture_output=True,
    )
    return project, user_home, env


def _orch_env(env: dict[str, str], *, max_loops: int) -> dict[str, str]:
    env["CC_AUTOPIPE_COOLDOWN_SEC"] = "0"
    env["CC_AUTOPIPE_IDLE_SLEEP_SEC"] = "0"
    env["CC_AUTOPIPE_MAX_LOOPS"] = str(max_loops)
    env["CC_AUTOPIPE_QUOTA_DISABLED"] = "1"
    env["CC_AUTOPIPE_CLAUDE_BIN"] = str(REPO_ROOT / "tools" / "mock-claude.sh")
    env["CC_AUTOPIPE_HOOKS_DIR"] = str(SRC / "hooks")
    env["CC_AUTOPIPE_CYCLE_TIMEOUT_SEC"] = "30"
    return env


# ---------------------------------------------------------------------------
# Template + init provisioning
# ---------------------------------------------------------------------------


def test_template_carries_improver_subagent() -> None:
    raw = json.loads((SRC / "templates" / ".cc-autopipe" / "agents.json").read_text())
    assert "improver" in raw
    spec = raw["improver"]
    assert spec["model"] == "sonnet"
    assert "WebSearch" not in spec["tools"]  # improver doesn't browse
    assert set(spec["tools"]) == {"Read", "Write"}
    # SPEC-v1.md §2.7.2 requires the prompt to mention .claude/skills.
    assert ".claude/skills/" in spec["prompt"]


# ---------------------------------------------------------------------------
# Config parsing
# ---------------------------------------------------------------------------


def test_read_config_improver_defaults_when_block_absent(
    orch_mod: object, tmp_path: Path
) -> None:
    p = tmp_path / "p"
    (p / ".cc-autopipe").mkdir(parents=True)
    (p / ".cc-autopipe" / "config.yaml").write_text("models:\n  default: x\n")
    cfg = orch_mod._read_config_improver(p)  # type: ignore[attr-defined]
    assert cfg["enabled"] is True
    assert cfg["trigger_every_n_successes"] == 5


def test_read_config_improver_overrides_applied(
    orch_mod: object, tmp_path: Path
) -> None:
    p = tmp_path / "p"
    (p / ".cc-autopipe").mkdir(parents=True)
    (p / ".cc-autopipe" / "config.yaml").write_text(
        "improver:\n  enabled: false\n  trigger_every_n_successes: 10\n"
    )
    cfg = orch_mod._read_config_improver(p)  # type: ignore[attr-defined]
    assert cfg["enabled"] is False
    assert cfg["trigger_every_n_successes"] == 10


# ---------------------------------------------------------------------------
# Prompt-side hint
# ---------------------------------------------------------------------------


def test_build_prompt_omits_improver_hint_when_not_due(
    orch_mod: object, tmp_path: Path
) -> None:
    p, _user_home, _env = _real_init(tmp_path)
    s = orch_mod.state.read(p)  # type: ignore[attr-defined]
    assert s.improver_due is False
    prompt = orch_mod._build_prompt(p, s)  # type: ignore[attr-defined]
    assert "IMPROVER TRIGGER" not in prompt


def test_build_prompt_injects_improver_hint_when_due(
    orch_mod: object, tmp_path: Path
) -> None:
    p, _user_home, _env = _real_init(tmp_path)
    s = orch_mod.state.read(p)  # type: ignore[attr-defined]
    s.improver_due = True
    orch_mod.state.write(p, s)  # type: ignore[attr-defined]
    prompt = orch_mod._build_prompt(p, s)  # type: ignore[attr-defined]
    assert "IMPROVER TRIGGER" in prompt
    assert "improver" in prompt
    assert ".claude/skills" in prompt


# ---------------------------------------------------------------------------
# End-to-end trigger after N successful cycles
# ---------------------------------------------------------------------------


def test_n_successful_cycles_trigger_improver(tmp_path: Path) -> None:
    """5 consecutive successful cycles → improver_due flag set + skills
    dir created + improver_trigger_due event in aggregate log + counter
    reset to 0."""
    project, user_home, env = _real_init(tmp_path)

    # Always-passing verify so each cycle counts as a success.
    verify = project / ".cc-autopipe" / "verify.sh"
    verify.write_text(
        "#!/bin/bash\n"
        'echo \'{"passed":true,"score":0.95,"prd_complete":false,"details":{}}\'\n'
    )
    verify.chmod(0o755)

    # Lower the trigger to 3 to keep the test fast.
    cfg = project / ".cc-autopipe" / "config.yaml"
    cfg.write_text(
        cfg.read_text().replace(
            "trigger_every_n_successes: 5", "trigger_every_n_successes: 3"
        )
    )

    # 3 cycles trigger the flag; cycle 4 consumes it via the prompt hint
    # and persists improver_due=False.
    env = _orch_env(env, max_loops=4)
    subprocess.run(
        [sys.executable, str(ORCHESTRATOR)],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    final = json.loads((project / ".cc-autopipe" / "state.json").read_text())
    # 4 successful cycles, trigger=3 → cycle 3 fires (counter resets to 0
    # then cycle 4 increments it to 1). Counter is "successes since last
    # trigger", so 1 is the expected value here.
    assert final["successful_cycles_since_improver"] == 1, final
    assert final["improver_due"] is False, (
        "improver_due must be one-shot — cycle 4's _build_prompt "
        "consumed the flag and the orchestrator persisted False"
    )

    skills_dir = project / ".claude" / "skills"
    assert skills_dir.exists() and skills_dir.is_dir(), (
        "orchestrator must create .claude/skills/ on trigger"
    )

    log = (user_home / "log" / "aggregate.jsonl").read_text()
    assert "improver_trigger_due" in log
    # And the prompt-injection event we log when consuming the flag.
    assert "improver_invoked_in_prompt" in log


def test_disabled_improver_never_triggers(tmp_path: Path) -> None:
    project, user_home, env = _real_init(tmp_path)

    cfg = project / ".cc-autopipe" / "config.yaml"
    cfg.write_text(cfg.read_text().replace("enabled: true", "enabled: false"))

    verify = project / ".cc-autopipe" / "verify.sh"
    verify.write_text(
        "#!/bin/bash\n"
        'echo \'{"passed":true,"score":0.95,"prd_complete":false,"details":{}}\'\n'
    )
    verify.chmod(0o755)

    env = _orch_env(env, max_loops=6)  # well past default trigger of 5
    subprocess.run(
        [sys.executable, str(ORCHESTRATOR)],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )

    final = json.loads((project / ".cc-autopipe" / "state.json").read_text())
    assert final["successful_cycles_since_improver"] == 0, (
        "counter must stay at 0 when improver is disabled"
    )
    assert final["improver_due"] is False
    skills_dir = project / ".claude" / "skills"
    assert not skills_dir.exists(), "skills dir must NOT be created when disabled"

    log_path = user_home / "log" / "aggregate.jsonl"
    log = log_path.read_text() if log_path.exists() else ""
    assert "improver_trigger_due" not in log


def test_failing_cycles_do_not_increment_improver_counter(tmp_path: Path) -> None:
    project, user_home, env = _real_init(tmp_path)

    verify = project / ".cc-autopipe" / "verify.sh"
    verify.write_text(
        "#!/bin/bash\n"
        'echo \'{"passed":false,"score":0.1,"prd_complete":false,"details":{}}\'\n'
    )
    verify.chmod(0o755)

    env = _orch_env(env, max_loops=2)  # don't go to 3+ which would FAIL
    subprocess.run(
        [sys.executable, str(ORCHESTRATOR)],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    final = json.loads((project / ".cc-autopipe" / "state.json").read_text())
    assert final["successful_cycles_since_improver"] == 0
    assert final["improver_due"] is False
