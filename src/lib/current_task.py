#!/usr/bin/env python3
"""current_task.py — parse/write `.cc-autopipe/CURRENT_TASK.md`.

Refs: SPEC-v1.2.md Bug A, Bug F.

CURRENT_TASK.md is the bidirectional channel between Claude (which
writes the file at the start of work and updates it as the task
progresses) and the engine (which reads the file in the Stop hook
and projects it into state.json.current_task).

Format (line-oriented `key: value`):

    task: cand_imbloss_v2
    stage: backtests
    stages_completed: hypothesis, training
    artifact: data/models/exp_cand_imbloss_v2/
    artifact: data/reports/cand_imbloss_v2/backtests.md
    notes: Training done, gap=18.2pp, lr=1.45. Starting 5-period backtest.

Recognized keys (all optional):

    task              → id
    stage             → stage
    stages_completed  → stages_completed (list, comma-sep or [a, b, c])
    artifact          → artifact_paths   (list; multiple `artifact:` lines accumulate)
    artifact_paths    → artifact_paths   (alias; same parsing)
    notes             → claude_notes     (string; multi-line supported via continuation)

Unknown keys are silently ignored. Empty input → empty dict (id=None,
treated as "no current task").

The output dict shape matches state.CurrentTask.from_dict input.

CLI:

    python3 current_task.py parse <CURRENT_TASK.md>            # JSON to stdout
    python3 current_task.py write <CURRENT_TASK.md> <JSON>     # write file
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# Recognized leading keys. A line that starts with `^<key>:` opens a new
# key/value pair; subsequent lines without that pattern are treated as
# continuation of the previous key's value (mostly for multi-line notes).
KEY_PATTERN = re.compile(r"^([a-z_][a-z0-9_]*):\s*(.*)$")

# Subset of keys we parse. Anything outside this set is dropped.
_RECOGNIZED = {
    "task",
    "stage",
    "stages_completed",
    "artifact",
    "artifact_paths",
    "notes",
}


def _parse_list_value(raw: str) -> list[str]:
    """Parse `[a, b, c]` or `a, b, c` or `a` into a clean list[str]."""
    s = raw.strip()
    if not s:
        return []
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1]
    parts = [p.strip() for p in s.split(",")]
    return [p for p in parts if p]


def parse_text(text: str) -> dict[str, Any]:
    """Parse CURRENT_TASK.md content; returns a dict suitable for
    state.CurrentTask.from_dict.

    All keys are optional. Missing top-level keys → fields absent
    from result (caller supplies defaults).
    """
    out: dict[str, Any] = {}
    artifact_paths: list[str] = []
    current_key: str | None = None
    current_value_parts: list[str] = []

    def flush() -> None:
        nonlocal current_key, current_value_parts
        if current_key is None:
            return
        value = "\n".join(current_value_parts).rstrip()
        if current_key == "task":
            out["id"] = value
        elif current_key == "stage":
            out["stage"] = value
        elif current_key == "stages_completed":
            out["stages_completed"] = _parse_list_value(value)
        elif current_key in {"artifact", "artifact_paths"}:
            # Single line: append. Multi-line: each line is a path.
            for line in value.splitlines():
                line = line.strip()
                if line:
                    artifact_paths.append(line)
        elif current_key == "notes":
            out["claude_notes"] = value
        current_key = None
        current_value_parts = []

    for raw_line in text.splitlines():
        m = KEY_PATTERN.match(raw_line)
        if m and m.group(1) in _RECOGNIZED:
            flush()
            current_key = m.group(1)
            current_value_parts = [m.group(2)]
        else:
            if current_key is not None:
                # Continuation line — keep the trailing newline behavior
                # by joining via "\n" in flush().
                current_value_parts.append(raw_line)
            # else: free-floating line before the first recognized key,
            # ignore (lets users prepend a markdown title if they want).
    flush()

    if artifact_paths:
        out["artifact_paths"] = artifact_paths
    return out


def parse_file(path: str | Path) -> dict[str, Any]:
    """Read a CURRENT_TASK.md from disk. Missing/empty file → {}."""
    p = Path(path)
    if not p.exists():
        return {}
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return {}
    if not text.strip():
        return {}
    return parse_text(text)


def render(data: dict[str, Any]) -> str:
    """Render a dict back to the line-oriented format. Round-trippable
    with parse_text for the keys we recognize."""
    lines: list[str] = []
    if data.get("id"):
        lines.append(f"task: {data['id']}")
    if data.get("stage"):
        lines.append(f"stage: {data['stage']}")
    stages = data.get("stages_completed") or []
    if stages:
        lines.append(f"stages_completed: {', '.join(str(s) for s in stages)}")
    for art in data.get("artifact_paths") or []:
        lines.append(f"artifact: {art}")
    notes = data.get("claude_notes")
    if notes:
        lines.append(f"notes: {notes}")
    return "\n".join(lines) + ("\n" if lines else "")


def write_file(path: str | Path, data: dict[str, Any]) -> None:
    """Write CURRENT_TASK.md atomically (tmpfile + replace)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(render(data), encoding="utf-8")
    tmp.replace(p)


def _project_current_task_md(project_path: str | Path) -> Path:
    return Path(project_path) / ".cc-autopipe" / "CURRENT_TASK.md"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="current_task.py")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_parse = sub.add_parser("parse", help="Parse CURRENT_TASK.md, emit JSON")
    p_parse.add_argument("path")

    p_write = sub.add_parser("write", help="Write CURRENT_TASK.md from JSON")
    p_write.add_argument("path")
    p_write.add_argument("json_data", help="JSON object string")

    args = parser.parse_args(argv)

    if args.cmd == "parse":
        data = parse_file(args.path)
        json.dump(data, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0

    if args.cmd == "write":
        data = json.loads(args.json_data)
        if not isinstance(data, dict):
            print("write: expected JSON object", file=sys.stderr)
            return 2
        write_file(args.path, data)
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
