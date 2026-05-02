"""Integration tests for auto-escalation (Stage L).

Covers SPEC-v1.md §2.5 acceptance:
- Config schema parsed correctly (default + overrides)
- escalated_next_cycle flips to True at trigger threshold
- _build_claude_cmd swaps to opus + --effort when flag is set
- _build_prompt injects ESCALATED CYCLE reminder when flag is set
- Successful escalated cycle reverts the flag (with config switch)
- enabled=false disables the whole mechanism
- Two consecutive failures under escalation eventually FAIL the project

These exercise the orchestrator helpers directly via SourceFileLoader
(same idiom run.py uses for the extension-less orchestrator script).
End-to-end three-failure → escalation chain is covered by the existing
test_orchestrator_claude.py::test_three_consecutive_failures... test.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
ORCHESTRATOR = SRC / "orchestrator"


@pytest.fixture(scope="module")
def orch_mod() -> object:
    spec = importlib.util.spec_from_loader(
        "orchestrator_mod_l",
        importlib.machinery.SourceFileLoader("orchestrator_mod_l", str(ORCHESTRATOR)),
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _seed_project(
    base: Path,
    *,
    consecutive_failures: int = 0,
    escalated: bool = False,
    last_passed: bool = False,
    last_score: float | None = None,
    config_overrides: dict[str, object] | None = None,
) -> Path:
    p = base / "demo"
    cca = p / ".cc-autopipe"
    cca.mkdir(parents=True, exist_ok=True)
    (cca / "memory").mkdir(exist_ok=True)
    (cca / "prd.md").write_text("# PRD\n\n- [ ] item\n")

    cfg_lines = ["models:", '  default: "claude-sonnet-4-6"', "auto_escalation:"]
    defaults = {
        "enabled": True,
        "trigger_consecutive_failures": 3,
        "escalate_to": "claude-opus-4-7",
        "effort": "xhigh",
        "revert_after_success": True,
    }
    defaults.update(config_overrides or {})
    for k, v in defaults.items():
        if isinstance(v, bool):
            cfg_lines.append(f"  {k}: {'true' if v else 'false'}")
        elif isinstance(v, str):
            cfg_lines.append(f'  {k}: "{v}"')
        else:
            cfg_lines.append(f"  {k}: {v}")
    (cca / "config.yaml").write_text("\n".join(cfg_lines) + "\n")

    state_doc = {
        "schema_version": 2,
        "name": "demo",
        "phase": "active",
        "iteration": 0,
        "session_id": None,
        "last_score": last_score,
        "last_passed": last_passed,
        "prd_complete": False,
        "consecutive_failures": consecutive_failures,
        "last_cycle_started_at": None,
        "last_progress_at": None,
        "threshold": 0.85,
        "paused": None,
        "detached": None,
        "current_phase": 1,
        "phases_completed": [],
        "escalated_next_cycle": escalated,
    }
    (cca / "state.json").write_text(json.dumps(state_doc))
    return p


# ---------------------------------------------------------------------------
# Config parsing
# ---------------------------------------------------------------------------


def test_read_config_auto_escalation_defaults_when_block_absent(
    orch_mod: object,
    tmp_path: Path,
) -> None:
    p = tmp_path / "p"
    (p / ".cc-autopipe").mkdir(parents=True)
    (p / ".cc-autopipe" / "config.yaml").write_text("models:\n  default: x\n")
    cfg = orch_mod._read_config_auto_escalation(p)  # type: ignore[attr-defined]
    assert cfg["enabled"] is True
    assert cfg["trigger_consecutive_failures"] == 3
    assert cfg["escalate_to"] == "claude-opus-4-7"
    assert cfg["effort"] == "xhigh"
    assert cfg["revert_after_success"] is True


def test_read_config_auto_escalation_overrides_applied(
    orch_mod: object,
    tmp_path: Path,
) -> None:
    p = _seed_project(
        tmp_path,
        config_overrides={
            "enabled": False,
            "trigger_consecutive_failures": 5,
            "escalate_to": "claude-opus-9-9",
            "effort": "max",
            "revert_after_success": False,
        },
    )
    cfg = orch_mod._read_config_auto_escalation(p)  # type: ignore[attr-defined]
    assert cfg["enabled"] is False
    assert cfg["trigger_consecutive_failures"] == 5
    assert cfg["escalate_to"] == "claude-opus-9-9"
    assert cfg["effort"] == "max"
    assert cfg["revert_after_success"] is False


# ---------------------------------------------------------------------------
# _build_claude_cmd swap
# ---------------------------------------------------------------------------


def test_build_claude_cmd_uses_default_model_when_not_escalated(
    orch_mod: object, tmp_path: Path
) -> None:
    p = _seed_project(tmp_path, escalated=False)
    s = orch_mod.state.read(p)  # type: ignore[attr-defined]
    cmd = orch_mod._build_claude_cmd(p, s)  # type: ignore[attr-defined]
    # --model is followed by the configured default.
    assert "--model" in cmd
    model_idx = cmd.index("--model")
    assert cmd[model_idx + 1] == "claude-sonnet-4-6"
    assert "--effort" not in cmd


def test_build_claude_cmd_uses_opus_when_escalated(
    orch_mod: object, tmp_path: Path
) -> None:
    p = _seed_project(tmp_path, escalated=True)
    s = orch_mod.state.read(p)  # type: ignore[attr-defined]
    cmd = orch_mod._build_claude_cmd(p, s)  # type: ignore[attr-defined]
    model_idx = cmd.index("--model")
    assert cmd[model_idx + 1] == "claude-opus-4-7"
    effort_idx = cmd.index("--effort")
    assert cmd[effort_idx + 1] == "xhigh"


def test_build_claude_cmd_respects_config_escalate_to_override(
    orch_mod: object, tmp_path: Path
) -> None:
    p = _seed_project(
        tmp_path,
        escalated=True,
        config_overrides={"escalate_to": "claude-opus-5-0", "effort": "ultra"},
    )
    s = orch_mod.state.read(p)  # type: ignore[attr-defined]
    cmd = orch_mod._build_claude_cmd(p, s)  # type: ignore[attr-defined]
    model_idx = cmd.index("--model")
    assert cmd[model_idx + 1] == "claude-opus-5-0"
    effort_idx = cmd.index("--effort")
    assert cmd[effort_idx + 1] == "ultra"


def test_build_claude_cmd_omits_effort_when_blank(
    orch_mod: object, tmp_path: Path
) -> None:
    p = _seed_project(
        tmp_path,
        escalated=True,
        config_overrides={"effort": ""},
    )
    s = orch_mod.state.read(p)  # type: ignore[attr-defined]
    cmd = orch_mod._build_claude_cmd(p, s)  # type: ignore[attr-defined]
    assert "--effort" not in cmd


# ---------------------------------------------------------------------------
# _build_prompt reminder
# ---------------------------------------------------------------------------


def test_build_prompt_injects_reminder_on_escalated_cycle(
    orch_mod: object, tmp_path: Path
) -> None:
    p = _seed_project(tmp_path, escalated=True, consecutive_failures=3)
    s = orch_mod.state.read(p)  # type: ignore[attr-defined]
    prompt = orch_mod._build_prompt(p, s)  # type: ignore[attr-defined]
    assert "ESCALATED CYCLE" in prompt
    assert "3 consecutive" in prompt
    assert "Reconsider" in prompt


def test_build_prompt_no_reminder_when_not_escalated(
    orch_mod: object, tmp_path: Path
) -> None:
    p = _seed_project(tmp_path, escalated=False)
    s = orch_mod.state.read(p)  # type: ignore[attr-defined]
    prompt = orch_mod._build_prompt(p, s)  # type: ignore[attr-defined]
    assert "ESCALATED CYCLE" not in prompt


# ---------------------------------------------------------------------------
# enabled=false bypass
# ---------------------------------------------------------------------------


def _real_init(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    """Run a real `cc-autopipe init` so .claude/settings.json wires the
    hooks. Returns (project, user_home, env) ready for an orchestrator
    subprocess call. The 3 subprocess tests below need this because the
    hook chain (stop.sh → update_verify) only fires when settings.json
    points at the engine's hook scripts."""
    import os
    import subprocess

    project = tmp_path / "demo-init"
    user_home = tmp_path / "uhome-init"
    project.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    env = os.environ.copy()
    env["CC_AUTOPIPE_HOME"] = str(SRC)
    env["CC_AUTOPIPE_USER_HOME"] = str(user_home)
    subprocess.run(
        [str(SRC / "helpers" / "cc-autopipe"), "init", str(project)],
        env=env,
        check=True,
        capture_output=True,
    )
    return project, user_home, env


def _orch_env_for_subprocess(env: dict[str, str], *, max_loops: int) -> dict[str, str]:
    env["CC_AUTOPIPE_COOLDOWN_SEC"] = "0"
    env["CC_AUTOPIPE_IDLE_SLEEP_SEC"] = "0"
    env["CC_AUTOPIPE_MAX_LOOPS"] = str(max_loops)
    env["CC_AUTOPIPE_QUOTA_DISABLED"] = "1"
    env["CC_AUTOPIPE_CLAUDE_BIN"] = str(REPO_ROOT / "tools" / "mock-claude.sh")
    env["CC_AUTOPIPE_HOOKS_DIR"] = str(SRC / "hooks")
    env["CC_AUTOPIPE_CYCLE_TIMEOUT_SEC"] = "30"
    return env


def test_disabled_auto_escalation_fails_at_threshold_v05_semantics(
    tmp_path: Path,
) -> None:
    """auto_escalation.enabled=false → 3 consecutive failures FAIL the
    project (v0.5 semantics). No escalated_to_opus event."""
    import subprocess

    project, user_home, env = _real_init(tmp_path)

    cfg = project / ".cc-autopipe" / "config.yaml"
    cfg.write_text(cfg.read_text().replace("enabled: true", "enabled: false"))

    verify = project / ".cc-autopipe" / "verify.sh"
    verify.write_text(
        '#!/bin/bash\necho \'{"passed":false,"score":0.1,"prd_complete":false,"details":{}}\'\n'
    )
    verify.chmod(0o755)

    env = _orch_env_for_subprocess(env, max_loops=3)
    subprocess.run(
        [sys.executable, str(ORCHESTRATOR)],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    final = json.loads((project / ".cc-autopipe" / "state.json").read_text())
    assert final["phase"] == "failed", final
    assert final["consecutive_failures"] >= 3
    assert final["escalated_next_cycle"] is False
    log = (user_home / "log" / "aggregate.jsonl").read_text()
    assert "escalated_to_opus" not in log
    assert "failed" in log


# ---------------------------------------------------------------------------
# Successful cycle reverts the flag
# ---------------------------------------------------------------------------


def test_successful_escalated_cycle_reverts_flag(tmp_path: Path) -> None:
    """After an escalated cycle PASSES, escalated_next_cycle reverts
    to False so subsequent cycles use sonnet again. Real init + real
    hooks + a passing verify.sh."""
    import json as _json
    import subprocess

    project, user_home, env = _real_init(tmp_path)

    # Pre-seed state with escalated_next_cycle=True (as if a prior burst
    # of failures triggered escalation).
    state_path = project / ".cc-autopipe" / "state.json"
    state_doc = _json.loads(state_path.read_text())
    state_doc["escalated_next_cycle"] = True
    state_path.write_text(_json.dumps(state_doc))

    # Verify always passes.
    verify = project / ".cc-autopipe" / "verify.sh"
    verify.write_text(
        '#!/bin/bash\necho \'{"passed":true,"score":0.95,"prd_complete":false,"details":{}}\'\n'
    )
    verify.chmod(0o755)

    env = _orch_env_for_subprocess(env, max_loops=1)
    subprocess.run(
        [sys.executable, str(ORCHESTRATOR)],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    final = _json.loads(state_path.read_text())
    assert final["escalated_next_cycle"] is False, final
    log = (user_home / "log" / "aggregate.jsonl").read_text()
    assert "escalation_reverted" in log


def test_revert_after_success_disabled_keeps_flag(tmp_path: Path) -> None:
    """config.revert_after_success=false → flag stays even after a
    successful cycle. (Operator opt-in for "stay on opus until I say
    otherwise".)"""
    import json as _json
    import subprocess

    project, user_home, env = _real_init(tmp_path)

    # Pre-seed escalated + opt out of revert.
    cfg = project / ".cc-autopipe" / "config.yaml"
    cfg_text = cfg.read_text()
    if "revert_after_success" in cfg_text:
        cfg_text = cfg_text.replace(
            "revert_after_success: true", "revert_after_success: false"
        )
    cfg.write_text(cfg_text)

    state_path = project / ".cc-autopipe" / "state.json"
    state_doc = _json.loads(state_path.read_text())
    state_doc["escalated_next_cycle"] = True
    state_path.write_text(_json.dumps(state_doc))

    verify = project / ".cc-autopipe" / "verify.sh"
    verify.write_text(
        '#!/bin/bash\necho \'{"passed":true,"score":0.95,"prd_complete":false,"details":{}}\'\n'
    )
    verify.chmod(0o755)

    env = _orch_env_for_subprocess(env, max_loops=1)
    subprocess.run(
        [sys.executable, str(ORCHESTRATOR)],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    final = _json.loads(state_path.read_text())
    assert final["escalated_next_cycle"] is True, final


# ---------------------------------------------------------------------------
# resume.py clears the flag
# ---------------------------------------------------------------------------


def test_resume_clears_escalation_flag(tmp_path: Path) -> None:
    import os
    import subprocess

    p = _seed_project(tmp_path, escalated=True, consecutive_failures=4)
    user_home = tmp_path / "uhome"
    user_home.mkdir()
    env = os.environ.copy()
    env["CC_AUTOPIPE_HOME"] = str(SRC)
    env["CC_AUTOPIPE_USER_HOME"] = str(user_home)
    cp = subprocess.run(
        [sys.executable, str(SRC / "cli" / "resume.py"), str(p)],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "escalated_next_cycle: True → False" in cp.stdout
    final = json.loads((p / ".cc-autopipe" / "state.json").read_text())
    assert final["escalated_next_cycle"] is False
    assert final["consecutive_failures"] == 0
