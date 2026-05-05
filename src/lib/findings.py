#!/usr/bin/env python3
"""findings.py — append/read project findings index for v1.3 memory.

Refs: PROMPT_v1.3-FULL.md GROUP A1.

A "finding" is one stage_completed event captured at the moment a Stop
hook detects a new entry in `state.json.current_task.stages_completed`.
The append-only file `<project>/.cc-autopipe/findings_index.md` survives
across context compactions: SessionStart hook injects the top-N most
recent entries so Claude has a memory of what was tried and verdicted.

Format (line-based markdown headers + bullet body, append-only):

    ## 2026-05-04T17:24:06Z | vec_meta | stage_e_verdict
    - **Task:** vec_meta
    - **Stage completed:** stage_e_verdict
    - **Notes:** REJECT — val AUC=0.5311 near-random
    - **Artifacts:** data/debug/CAND_meta_PROMOTION.md

CLI:

    python3 findings.py append <project> <task_id> <stage> <notes> [artifact ...]
    python3 findings.py read <project> [--top-n 20]
    python3 findings.py read-for-task <project> <task_id> [--n 5]
    python3 findings.py inject <project>
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

FINDINGS_REL = ".cc-autopipe/findings_index.md"

_HEADER_RE = re.compile(r"^##\s+([^|]+)\|\s*([^|]+)\|\s*(.+?)\s*$")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _findings_path(project_dir: Path) -> Path:
    return project_dir / FINDINGS_REL


def _read_existing(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _last_entry_header(text: str) -> tuple[str, str, str] | None:
    """Return (ts, task_id, stage) of the most recent ## header, or None."""
    last: tuple[str, str, str] | None = None
    for line in text.splitlines():
        m = _HEADER_RE.match(line)
        if m:
            last = (m.group(1).strip(), m.group(2).strip(), m.group(3).strip())
    return last


def append_finding(
    project_dir: Path,
    task_id: str,
    stage: str,
    notes: str,
    artifact_paths: list[str] | None = None,
    ts: str | None = None,
) -> bool:
    """Append a stage_completed entry. Idempotent: if the last header in
    the file already matches (task_id, stage), this is a no-op.

    Returns True if a new entry was appended, False if deduped or skipped.
    """
    if not task_id or not stage:
        return False
    project_dir = Path(project_dir)
    path = _findings_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)

    existing = _read_existing(path)
    last = _last_entry_header(existing)
    if last is not None and last[1] == task_id and last[2] == stage:
        return False

    timestamp = ts or _now_iso()
    artifact_paths = artifact_paths or []
    artifact_str = ", ".join(artifact_paths) if artifact_paths else "(none)"

    one_line_notes = " ".join((notes or "").split()).strip() or "(none)"

    block = (
        f"## {timestamp} | {task_id} | {stage}\n"
        f"- **Task:** {task_id}\n"
        f"- **Stage completed:** {stage}\n"
        f"- **Notes:** {one_line_notes}\n"
        f"- **Artifacts:** {artifact_str}\n"
        "\n"
    )

    if existing and not existing.endswith("\n"):
        existing += "\n"
    new_text = existing + block
    try:
        path.write_text(new_text, encoding="utf-8")
    except OSError as exc:
        print(f"[findings] could not write {path}: {exc}", file=sys.stderr)
        return False
    return True


def read_findings(project_dir: Path, top_n: int = 20) -> list[dict[str, Any]]:
    """Return up to top_n most recent findings (newest first).

    Each entry is a dict {ts, task_id, stage, notes, artifacts, raw_block}.
    Malformed sections are skipped silently.
    """
    project_dir = Path(project_dir)
    path = _findings_path(project_dir)
    text = _read_existing(path)
    if not text:
        return []

    entries: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    body_lines: list[str] = []

    def _flush() -> None:
        nonlocal current, body_lines
        if current is None:
            return
        for line in body_lines:
            stripped = line.strip()
            if stripped.startswith("- **Notes:**"):
                current["notes"] = stripped[len("- **Notes:**"):].strip()
            elif stripped.startswith("- **Artifacts:**"):
                current["artifacts"] = stripped[len("- **Artifacts:**"):].strip()
        current["raw_block"] = "\n".join(
            [f"## {current['ts']} | {current['task_id']} | {current['stage']}"]
            + body_lines
        )
        entries.append(current)
        current = None
        body_lines = []

    for raw in text.splitlines():
        m = _HEADER_RE.match(raw)
        if m:
            _flush()
            current = {
                "ts": m.group(1).strip(),
                "task_id": m.group(2).strip(),
                "stage": m.group(3).strip(),
                "notes": "",
                "artifacts": "",
            }
        else:
            if current is not None and raw.strip():
                body_lines.append(raw)
    _flush()

    entries.reverse()
    return entries[:top_n]


def read_findings_for_task(
    project_dir: Path, task_id: str, n: int = 5
) -> list[dict[str, Any]]:
    """Return last n findings whose task_id matches. Used by GROUP H."""
    if not task_id:
        return []
    all_entries = read_findings(project_dir, top_n=10000)
    matching = [e for e in all_entries if e.get("task_id") == task_id]
    return matching[:n]


def format_findings_for_injection(findings: list[dict[str, Any]]) -> str:
    """Render the recent-findings block injected at SessionStart."""
    if not findings:
        return ""
    lines = [f"=== Recent findings (last {len(findings)} stages) ==="]
    for f in findings:
        lines.append(
            f"- {f['ts']} | {f['task_id']} | {f['stage']}: "
            f"{f.get('notes', '') or '(no notes)'}"
        )
    lines.append("===")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="findings.py")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_app = sub.add_parser("append")
    p_app.add_argument("project")
    p_app.add_argument("task_id")
    p_app.add_argument("stage")
    p_app.add_argument("notes", nargs="?", default="")
    p_app.add_argument("artifacts", nargs="*", default=[])

    p_read = sub.add_parser("read")
    p_read.add_argument("project")
    p_read.add_argument("--top-n", type=int, default=20)

    p_for_task = sub.add_parser("read-for-task")
    p_for_task.add_argument("project")
    p_for_task.add_argument("task_id")
    p_for_task.add_argument("--n", type=int, default=5)

    p_inj = sub.add_parser("inject")
    p_inj.add_argument("project")
    p_inj.add_argument("--top-n", type=int, default=20)

    args = parser.parse_args(argv)

    if args.cmd == "append":
        ok = append_finding(
            Path(args.project),
            args.task_id,
            args.stage,
            args.notes,
            list(args.artifacts) or None,
        )
        print("appended" if ok else "skipped")
        return 0
    if args.cmd == "read":
        items = read_findings(Path(args.project), top_n=args.top_n)
        json.dump(items, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0
    if args.cmd == "read-for-task":
        items = read_findings_for_task(Path(args.project), args.task_id, n=args.n)
        json.dump(items, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0
    if args.cmd == "inject":
        items = read_findings(Path(args.project), top_n=args.top_n)
        out = format_findings_for_injection(items)
        if out:
            print(out)
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
