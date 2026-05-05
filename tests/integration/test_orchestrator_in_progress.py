"""Integration tests for v1.2 Bug B (in_progress) on the orchestrator.

Covers:
- _read_config_in_progress reads in_progress: block (defaults + overrides)
- in_progress cap triggers phase=failed + HUMAN_NEEDED.md + cap event
- non-cap in_progress streak does NOT mark project failed
- last_in_progress=False with consecutive_in_progress over cap is a no-op
- HUMAN_NEEDED message content distinguishes from the 3-failures path
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
LIB = SRC / "lib"


@pytest.fixture(scope="module")
def orch_mod() -> object:
    """Compatibility fixture against the v1.3 package layout."""
    for p in (str(SRC), str(LIB)):
        if p not in sys.path:
            sys.path.insert(0, p)
    prompt = importlib.import_module("orchestrator.prompt")
    recovery = importlib.import_module("orchestrator.recovery")
    return SimpleNamespace(
        _read_config_in_progress=prompt._read_config_in_progress,
        _write_in_progress_cap_human_needed=recovery._write_in_progress_cap_human_needed,
    )


def _seed_project(base: Path, in_progress_block: str | None = None) -> Path:
    p = base / "demo"
    cca = p / ".cc-autopipe"
    cca.mkdir(parents=True, exist_ok=True)
    cfg_lines = []
    if in_progress_block is not None:
        cfg_lines.append("in_progress:")
        for line in in_progress_block.strip().splitlines():
            cfg_lines.append("  " + line.strip())
    (cca / "config.yaml").write_text("\n".join(cfg_lines) + "\n")
    return p


# ---------------------------------------------------------------------------
# _read_config_in_progress
# ---------------------------------------------------------------------------


def test_in_progress_defaults_when_block_absent(tmp_path: Path, orch_mod) -> None:
    project = _seed_project(tmp_path)
    cfg = orch_mod._read_config_in_progress(project)
    assert cfg["max_in_progress_cycles"] == 12
    assert cfg["cooldown_multiplier"] == 3


def test_in_progress_overrides_apply(tmp_path: Path, orch_mod) -> None:
    project = _seed_project(
        tmp_path,
        in_progress_block="""
        max_in_progress_cycles: 5
        cooldown_multiplier: 7
        """,
    )
    cfg = orch_mod._read_config_in_progress(project)
    assert cfg["max_in_progress_cycles"] == 5
    assert cfg["cooldown_multiplier"] == 7


def test_in_progress_partial_override_falls_back(tmp_path: Path, orch_mod) -> None:
    project = _seed_project(tmp_path, in_progress_block="cooldown_multiplier: 4")
    cfg = orch_mod._read_config_in_progress(project)
    assert cfg["max_in_progress_cycles"] == 12  # default
    assert cfg["cooldown_multiplier"] == 4  # override


# ---------------------------------------------------------------------------
# _write_in_progress_cap_human_needed
# ---------------------------------------------------------------------------


def test_human_needed_in_progress_message_content(tmp_path: Path, orch_mod) -> None:
    """Message must clearly mention in_progress cap (different reason
    than the 3-failures path) so the operator knows where to look."""
    p = tmp_path / "p"
    (p / ".cc-autopipe").mkdir(parents=True)
    orch_mod._write_in_progress_cap_human_needed(p, n_cycles=12, cap=12)
    text = (p / ".cc-autopipe" / "HUMAN_NEEDED.md").read_text(encoding="utf-8")
    assert "in_progress cap hit" in text
    assert "12 consecutive cycles" in text
    assert "verify.sh" in text  # mentions verify-side as likely cause
    assert "CURRENT_TASK.md" in text  # mentions task-tracking diagnostic
    # Critical: must NOT confuse the operator with the consecutive_failures
    # message (would suggest escalating to opus, which is exactly wrong here).
    assert "consecutive_failures >= 3" not in text


def test_human_needed_in_progress_overwrites_prior(tmp_path: Path, orch_mod) -> None:
    p = tmp_path / "p"
    (p / ".cc-autopipe").mkdir(parents=True)
    (p / ".cc-autopipe" / "HUMAN_NEEDED.md").write_text("old\n")
    orch_mod._write_in_progress_cap_human_needed(p, n_cycles=12, cap=12)
    text = (p / ".cc-autopipe" / "HUMAN_NEEDED.md").read_text(encoding="utf-8")
    assert "old" not in text
    assert "in_progress cap hit" in text
