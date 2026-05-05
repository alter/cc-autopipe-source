#!/usr/bin/env python3
"""orchestrator.prompt — prompt + claude command construction.

Includes:
  - _build_prompt:        compose the main prompt sent via -p
  - _build_claude_cmd:    final argv list for claude
  - config readers:       auto_escalation, in_progress, improver, model
  - small helpers:        _read_truncated, _read_top_open_tasks, _agents_json_path
"""

from __future__ import annotations

import os
from pathlib import Path

# bare-name imports satisfied by _runtime's sys.path setup
from orchestrator._runtime import _user_home  # noqa: F401  (kept for parity)
import prd as prd_lib  # noqa: E402
import state  # noqa: E402

DEFAULT_MAX_TURNS = 35

# Stage L auto-escalation defaults — match the template in
# src/templates/.cc-autopipe/config.yaml so a project that omits the
# auto_escalation block gets the same behaviour as the template.
AUTO_ESC_DEFAULTS: dict[str, object] = {
    "enabled": True,
    "trigger_consecutive_failures": 3,
    "escalate_to": "claude-opus-4-7",
    "effort": "xhigh",
    "revert_after_success": True,
}

# Stage N improver defaults — same template-mirroring rationale.
IMPROVER_DEFAULTS: dict[str, object] = {
    "enabled": True,
    "trigger_every_n_successes": 5,
}

# v1.2 Bug B in_progress defaults. SPEC-v1.2.md "Engine config":
#   in_progress:
#     max_in_progress_cycles: 12
#     cooldown_multiplier: 3
# When state.last_in_progress is True the orchestrator extends the
# inter-project cooldown by the multiplier; once consecutive_in_progress
# hits the cap, the engine forces a normal-failure accounting on the
# project (see process_project tail).
IN_PROGRESS_DEFAULTS: dict[str, object] = {
    "max_in_progress_cycles": 12,
    "cooldown_multiplier": 3,
}


def _read_truncated(path: Path, max_bytes: int) -> str:
    try:
        return path.read_text(encoding="utf-8")[:max_bytes]
    except OSError:
        return ""


def _read_top_open_tasks(backlog: Path, n: int) -> str:
    if not backlog.exists():
        return ""
    out: list[str] = []
    for line in backlog.read_text(encoding="utf-8").splitlines():
        if line.startswith("- [ ]"):
            out.append(line)
            if len(out) >= n:
                break
    return "\n".join(out)


def _build_prompt(project_path: Path, s: state.State) -> str:
    """Construct the prompt sent to Claude. Per SPEC.md §6.1 build_prompt."""
    cca = project_path / ".cc-autopipe"
    parts: list[str] = []
    parts.append(f"# Project: {s.name or project_path.name}\n")
    parts.append(f"Iteration {s.iteration}. Phase: {s.phase}.\n")

    # If the PRD has phases (Stage J), focus the agent on the current
    # phase's items. Single-phase PRDs (no `### Phase N:` headers) skip
    # this block and the agent sees the full PRD excerpt below.
    phases = prd_lib.read_phases(cca / "prd.md")
    current_phase_obj = None
    if phases:
        current_phase_obj = next(
            (p for p in phases if p.number == s.current_phase), None
        )
        if current_phase_obj is not None:
            parts.append(
                f"Current PRD phase: **{s.current_phase} — {current_phase_obj.name}** "
                f"({current_phase_obj.unchecked_count} of "
                f"{current_phase_obj.total_items} items remaining). "
                f"Phases completed so far: {s.phases_completed or 'none'}.\n"
            )
    parts.append("\n")

    if (cca / "checkpoint.md").exists():
        parts.append(
            "**RESUME FROM CHECKPOINT:** Read .cc-autopipe/checkpoint.md FIRST. "
            "Continue from there.\n\n"
        )

    if s.escalated_next_cycle:
        parts.append(
            "**ESCALATED CYCLE:** Previous cycles failed "
            f"({s.consecutive_failures} consecutive failures). "
            "Reconsider your approach — the prior solution did not "
            "land. Audit assumptions, add diagnostic prints, narrow the "
            "failure mode before re-attempting.\n\n"
        )

    if s.improver_due:
        parts.append(
            "**IMPROVER TRIGGER:** N successful cycles since the last "
            "skill-crystallisation pass. Before the next backlog task, "
            "invoke the `improver` subagent via the task tool — it will "
            "review recent progress reports and write reusable skills "
            "into `.claude/skills/<name>/SKILL.md`. The skills directory "
            "already exists.\n\n"
        )

    if current_phase_obj is not None:
        parts.append(
            f"## Current phase ({s.current_phase}: {current_phase_obj.name})\n"
        )
        parts.append(current_phase_obj.body)
        parts.append("\n")
    else:
        parts.append("## PRD (excerpt)\n")
        parts.append(_read_truncated(cca / "prd.md", 2048))
        parts.append("\n")

    parts.append("\n## Project context (excerpt)\n")
    parts.append(_read_truncated(cca / "context.md", 1024))
    parts.append("\n\n## Next backlog tasks\n")
    parts.append(_read_top_open_tasks(project_path / "backlog.md", 5) or "(none)")
    parts.append("\n")

    if s.last_score is not None:
        parts.append(f"\nLast verify: passed={s.last_passed}, score={s.last_score}\n")

    parts.append("\n## Instructions\n")
    parts.append(
        "Pick the topmost open task. Implement. Run "
        ".cc-autopipe/verify.sh before declaring done. If the task is "
        "large, save progress with cc-autopipe-checkpoint near turn 25.\n"
    )
    return "".join(parts)


def _read_config_model(project_path: Path, default: str) -> str:
    cfg = project_path / ".cc-autopipe" / "config.yaml"
    if not cfg.exists():
        return default
    in_models = False
    for line in cfg.read_text(encoding="utf-8").splitlines():
        stripped = line.rstrip()
        if stripped == "models:":
            in_models = True
            continue
        if in_models:
            if not stripped.startswith(" "):
                in_models = False
                continue
            text = stripped.strip()
            if text.startswith("default:"):
                value = text.split(":", 1)[1].strip().strip('"').strip("'")
                if value:
                    return value
    return default


def _coerce_yaml_value(text: str) -> object:
    """Tiny YAML scalar coercion — bool / int / quoted string / bare string.
    Good enough for the auto_escalation block; kept narrow so we don't
    grow a YAML dependency."""
    text = text.strip()
    lowered = text.lower()
    if lowered in ("true", "yes"):
        return True
    if lowered in ("false", "no"):
        return False
    try:
        return int(text)
    except ValueError:
        pass
    return text.strip('"').strip("'")


def _read_yaml_top_block(
    project_path: Path,
    block_name: str,
    defaults: dict[str, object],
) -> dict[str, object]:
    """Generic top-level-block parser shared by auto_escalation + improver.

    Returns merged dict (defaults + overrides). Missing block → defaults.
    Tolerant of typos: unknown keys ignored, malformed values fall back
    to defaults at first parse error.
    """
    out = dict(defaults)
    cfg = project_path / ".cc-autopipe" / "config.yaml"
    if not cfg.exists():
        return out
    in_block = False
    try:
        text = cfg.read_text(encoding="utf-8")
    except OSError:
        return out
    block_header = f"{block_name}:"
    for line in text.splitlines():
        stripped = line.rstrip()
        if stripped == block_header:
            in_block = True
            continue
        if in_block:
            if stripped and not stripped.startswith(" "):
                in_block = False
                continue
            if ":" not in stripped:
                continue
            key, _, raw = stripped.strip().partition(":")
            if key in defaults:
                out[key] = _coerce_yaml_value(raw)
    return out


def _read_config_auto_escalation(project_path: Path) -> dict[str, object]:
    """Parse the `auto_escalation:` block from config.yaml. Defaults
    apply when the block (or individual keys) is absent. See
    AUTO_ESC_DEFAULTS for the merged shape."""
    return _read_yaml_top_block(project_path, "auto_escalation", AUTO_ESC_DEFAULTS)


def _read_config_in_progress(project_path: Path) -> dict[str, object]:
    """Parse the v1.2 `in_progress:` block from config.yaml. See
    IN_PROGRESS_DEFAULTS."""
    return _read_yaml_top_block(project_path, "in_progress", IN_PROGRESS_DEFAULTS)


def _read_config_improver(project_path: Path) -> dict[str, object]:
    """Parse the `improver:` block from config.yaml (Stage N).
    Defaults apply when absent."""
    return _read_yaml_top_block(project_path, "improver", IMPROVER_DEFAULTS)


def _agents_json_path(project_path: Path) -> Path | None:
    candidate = project_path / ".cc-autopipe" / "agents.json"
    return candidate if candidate.exists() else None


def _build_claude_cmd(project_path: Path, s: state.State) -> list[str]:
    """Per SPEC.md §6.1 build_claude_cmd."""
    claude_bin = os.environ.get("CC_AUTOPIPE_CLAUDE_BIN", "claude")
    cmd: list[str] = [claude_bin]

    if s.session_id:
        cmd += ["--resume", s.session_id]

    cmd += [
        "-p",
        _build_prompt(project_path, s),
        "--dangerously-skip-permissions",
        # claude 2.1.123+ rejects `-p --output-format stream-json` without
        # --verbose: "When using --print, --output-format=stream-json
        # requires --verbose". Discovered during first Stage G run.
        "--verbose",
        "--max-turns",
        str(DEFAULT_MAX_TURNS),
        "--output-format",
        "stream-json",
    ]
    agents = _agents_json_path(project_path)
    if agents is not None:
        cmd += ["--agents", str(agents)]

    if s.escalated_next_cycle:
        esc = _read_config_auto_escalation(project_path)
        cmd += ["--model", str(esc["escalate_to"])]
        effort = str(esc.get("effort") or "")
        if effort:
            cmd += ["--effort", effort]
    else:
        cmd += [
            "--model",
            _read_config_model(project_path, "claude-sonnet-4-6"),
        ]
    return cmd
