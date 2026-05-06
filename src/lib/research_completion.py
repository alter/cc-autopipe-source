#!/usr/bin/env python3
"""research_completion — completion criteria for [research] tasks.

Refs: PROMPT_v1.3.5-hotfix.md GROUP RESEARCH-COMPLETION.

Research tasks produce analysis artifacts (SELECTION_*.md,
NEGATIVE_MINING_*.md, HYPO_*.md, RESEARCH_DIGEST_*.md). They do NOT
produce code, do NOT commit, do NOT have meaningful verify.sh
contracts. Engine completion criterion:

    artifact path matches a known pattern AND file is non-empty AND
    CURRENT_TASK.md stage matches a research-verdict pattern.

Patterns derive from task_id prefix:

    phase_gate_*               -> data/debug/SELECTION_<task_id>.md
    vec_long_meta_negative_*   -> data/debug/NEGATIVE_MINING_*.md
    vec_long_meta_research_*   -> data/debug/RESEARCH_DIGEST_*.md
    *                          -> data/debug/HYPO_<task_id>.md  (fallback)

Public surface:
    - is_research_task(item)              -> bool
    - expected_artifact_glob(item)        -> str  (relative path glob)
    - completion_satisfied(project, item) -> tuple[bool, str]
    - find_top_research_task(project)     -> BacklogItem | None
"""

from __future__ import annotations

from pathlib import Path

from backlog import BacklogItem, parse_top_open

# Stage names that match a research-verdict pattern. CURRENT_TASK.md must
# list at least one of these in stages_completed before the engine
# accepts artifact-only completion. Conservative: missing the verdict
# stage means "Claude has not declared completion yet".
_RESEARCH_VERDICT_STAGES = frozenset(
    {
        "phase_gate_complete",
        "selection_complete",
        "research_digest_complete",
        "negative_mining_complete",
        "hypo_filed",
    }
)


def is_research_task(item: BacklogItem | None) -> bool:
    if item is None:
        return False
    return item.task_type == "research"


def expected_artifact_glob(item: BacklogItem) -> str:
    """Relative path glob the [research] task should produce."""
    tid = item.id
    if tid.startswith("phase_gate_"):
        return f"data/debug/SELECTION_{tid}.md"
    if tid.startswith("vec_long_meta_negative_"):
        return "data/debug/NEGATIVE_MINING_*.md"
    if tid.startswith("vec_long_meta_research_"):
        return "data/debug/RESEARCH_DIGEST_*.md"
    return f"data/debug/HYPO_{tid}.md"


def _read_stages_completed(project: Path) -> list[str]:
    """Read CURRENT_TASK.md stages_completed without importing state.py."""
    import current_task as _ct  # noqa: PLC0415

    md_path = project / ".cc-autopipe" / "CURRENT_TASK.md"
    data = _ct.parse_file(md_path)
    stages = data.get("stages_completed") or []
    if not isinstance(stages, list):
        return [str(stages)]
    return [str(s) for s in stages]


def completion_satisfied(project: Path, item: BacklogItem) -> tuple[bool, str]:
    """True iff [research] task has produced its expected artifact AND
    CURRENT_TASK.md shows a research-verdict stage in stages_completed.

    Returns (ok, reason) — reason populated only on False.
    """
    if not is_research_task(item):
        return False, "not_a_research_task"
    glob_pat = expected_artifact_glob(item)
    matches = [
        m
        for m in project.glob(glob_pat)
        if m.is_file() and m.stat().st_size > 0
    ]
    if not matches:
        return False, f"artifact_missing:{glob_pat}"
    try:
        stages = _read_stages_completed(project)
    except Exception:  # noqa: BLE001 — engine path must not crash
        return False, "current_task_unreadable"
    if not any(s in _RESEARCH_VERDICT_STAGES for s in stages):
        return False, "research_verdict_stage_missing"
    return True, ""


def find_top_research_task(project: Path) -> BacklogItem | None:
    """Return the topmost open [research] backlog item, or None.

    Convenience for the orchestrator: cheaper than parsing the backlog
    twice when the prompt builder also needs it.
    """
    items = parse_top_open(project / "backlog.md", n=10)
    for it in items:
        if is_research_task(it):
            return it
    return None
