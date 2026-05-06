#!/usr/bin/env python3
"""knowledge.py — read-only access to <project>/.cc-autopipe/knowledge.md.

Refs: PROMPT_v1.3-FULL.md GROUP A2 + GROUP I.

knowledge.md is a markdown file Claude writes by hand via Edit/Write.
Engine never modifies it. The module exists to:
  - read it (truncated to 5KB tail) for SessionStart injection (A3)
  - extract sections relevant to a task_id for META_REFLECT (H4)
  - format for injection

Format Claude is expected to follow (project rules.md instructs this):

    # Project knowledge

    ## Architectures
    - lesson — YYYY-MM-DD

    ## Baselines
    - lesson — YYYY-MM-DD

    ## Diagnostics rules
    - lesson — YYYY-MM-DD
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

KNOWLEDGE_REL = ".cc-autopipe/knowledge.md"
DEFAULT_MAX_BYTES = 5 * 1024  # 5KB tail per the prompt spec

_VERDICT_PATTERNS = (
    "verdict",
    "rejected",
    "promoted",
    "accepted",
    "shipped",
    # v1.3.5: Phase 2 verdict-pattern stages so knowledge.md sentinel
    # arms after these too. Substrings cover both bare names
    # (`phase_gate_complete`) and project-specific prefixes
    # (`synth_track_winner_selected`).
    "phase_gate",
    "selection_complete",
    "research_digest",
    "negative_mining",
    "hypo_filed",
    "track_winner",
)


def is_verdict_stage(stage_name: str) -> bool:
    """v1.3 I2 heuristic: stage name indicates a verdict completion.

    True for any case-insensitive substring match against the
    _VERDICT_PATTERNS tuple. Conservative — adding a new pattern
    requires editing this list (so unrelated stages don't trigger
    knowledge update enforcement by accident).
    """
    if not stage_name:
        return False
    s = stage_name.lower()
    return any(p in s for p in _VERDICT_PATTERNS)


def knowledge_path(project_dir: Path) -> Path:
    return _knowledge_path(project_dir)


def get_mtime_or_zero(project_dir: Path) -> float:
    p = _knowledge_path(project_dir)
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def _knowledge_path(project_dir: Path) -> Path:
    return project_dir / KNOWLEDGE_REL


def read_knowledge(project_dir: Path, max_bytes: int = DEFAULT_MAX_BYTES) -> str:
    """Return knowledge.md content, tail-truncated to max_bytes.

    Newest lessons are appended to the end by Claude, so the tail is
    most informative when the file overflows the cap.
    """
    project_dir = Path(project_dir)
    path = _knowledge_path(project_dir)
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    if not text:
        return ""
    if len(text.encode("utf-8")) <= max_bytes:
        return text
    encoded = text.encode("utf-8")[-max_bytes:]
    try:
        decoded = encoded.decode("utf-8", errors="ignore")
    except UnicodeDecodeError:
        return ""
    if "\n" in decoded:
        # Drop the leading partial line (likely chopped mid-sentence by
        # the byte-slice).
        decoded = decoded.split("\n", 1)[1]
    return decoded


def read_relevant_excerpt(
    project_dir: Path, task_id: str, max_bytes: int = DEFAULT_MAX_BYTES
) -> str:
    """Return knowledge.md sections that mention the task_id (or a substring
    of it) — used by META_REFLECT to surface relevant lessons.

    Heuristic: split file by `\\n## ` headers, return only sections whose
    body contains the task_id (case-insensitive) or any token from it
    (split on `_`/`-`). Falls back to the full truncated file when nothing
    matches.
    """
    text = read_knowledge(project_dir, max_bytes=max_bytes)
    if not text or not task_id:
        return text
    needle = task_id.lower()
    # Tokens used only as a fallback when the full id doesn't match. Cap
    # at length>=4 so generic prefixes like "vec" don't false-positive
    # across unrelated task ids (vec_meta vs. vec_tbm).
    tokens = [t for t in needle.replace("-", "_").split("_") if len(t) >= 4]

    sections: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            if current:
                sections.append("\n".join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        sections.append("\n".join(current))

    matching: list[str] = []
    for sec in sections:
        body = sec.lower()
        if needle in body or any(tok in body for tok in tokens):
            matching.append(sec)

    if matching:
        return "\n\n".join(matching).strip()
    return text


def format_for_injection(content: str) -> str:
    if not content.strip():
        return ""
    return "=== Project knowledge ===\n" + content.rstrip() + "\n==="


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="knowledge.py")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_read = sub.add_parser("read")
    p_read.add_argument("project")
    p_read.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)

    p_inj = sub.add_parser("inject")
    p_inj.add_argument("project")

    p_for_task = sub.add_parser("for-task")
    p_for_task.add_argument("project")
    p_for_task.add_argument("task_id")

    args = parser.parse_args(argv)

    if args.cmd == "read":
        sys.stdout.write(read_knowledge(Path(args.project), max_bytes=args.max_bytes))
        return 0
    if args.cmd == "inject":
        text = read_knowledge(Path(args.project))
        out = format_for_injection(text)
        if out:
            print(out)
        return 0
    if args.cmd == "for-task":
        sys.stdout.write(read_relevant_excerpt(Path(args.project), args.task_id))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
