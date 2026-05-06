"""Integration tests: orchestrator.prompt._build_prompt branches on
the topmost open backlog item's task_type.

v1.3.5 Group RESEARCH-COMPLETION.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
LIB = SRC / "lib"


@pytest.fixture(scope="module")
def prompt_mod() -> object:
    for p in (str(SRC), str(LIB)):
        if p not in sys.path:
            sys.path.insert(0, p)
    return importlib.import_module("orchestrator.prompt")


def _seed_project(base: Path, backlog_body: str) -> Path:
    p = base / "demo"
    cca = p / ".cc-autopipe"
    cca.mkdir(parents=True, exist_ok=True)
    (cca / "prd.md").write_text("# PRD\n\n- [ ] x\n")
    (cca / "context.md").write_text("ctx\n")
    (p / "backlog.md").write_text(backlog_body, encoding="utf-8")
    return p


def test_research_task_prompt_block_when_top_is_research(
    prompt_mod: object, tmp_path: Path
) -> None:
    p = _seed_project(
        tmp_path,
        "- [ ] [research] [P0] phase_gate_2_1 — selection\n"
        "- [ ] [implement] [P1] vec_long_lgbm — model\n",
    )
    state_mod = importlib.import_module("state")
    s = state_mod.State.fresh("demo")
    prompt = prompt_mod._build_prompt(p, s)  # type: ignore[attr-defined]
    assert "RESEARCH TASK" in prompt
    assert "phase_gate_2_1" in prompt
    assert "SELECTION_phase_gate_2_1.md" in prompt
    # Ensure the implement-style instructions are NOT also injected.
    assert "Run .cc-autopipe/verify.sh before declaring done" not in prompt


def test_implement_task_prompt_block_when_top_is_implement(
    prompt_mod: object, tmp_path: Path
) -> None:
    p = _seed_project(
        tmp_path,
        "- [ ] [implement] [P0] vec_long_lgbm — model\n"
        "- [ ] [research] [P2] phase_gate_2_1 — selection\n",
    )
    state_mod = importlib.import_module("state")
    s = state_mod.State.fresh("demo")
    prompt = prompt_mod._build_prompt(p, s)  # type: ignore[attr-defined]
    assert "RESEARCH TASK" not in prompt
    assert ".cc-autopipe/verify.sh" in prompt


def test_implement_block_when_backlog_empty(
    prompt_mod: object, tmp_path: Path
) -> None:
    p = _seed_project(tmp_path, "")
    state_mod = importlib.import_module("state")
    s = state_mod.State.fresh("demo")
    prompt = prompt_mod._build_prompt(p, s)  # type: ignore[attr-defined]
    assert "RESEARCH TASK" not in prompt
    assert ".cc-autopipe/verify.sh" in prompt
