#!/usr/bin/env python3
"""tail.py — implements `cc-autopipe tail` per SPEC.md §12.5.

Follows ~/.cc-autopipe/log/aggregate.jsonl with human-readable
formatting. Plain ANSI colors, no third-party deps.

Filters:
  --project NAME      Only events for this project name (basename match).
  --event NAMES       Comma-separated event names to keep
                      (e.g. done,failed,paused).
  --no-follow         Print existing lines and exit.
  -n N                Show last N lines before following (default: 20).

Refs: SPEC.md §12.5, §15.1 (aggregate.jsonl), §15.2 (event names)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

# ANSI codes — only emit if stdout is a tty (or NO_COLOR is unset).
_USE_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _c(code: str, text: str) -> str:
    if not _USE_COLOR:
        return text
    return f"\033[{code}m{text}\033[0m"


# Event-name → ANSI color (terminfo's standard 256-color list isn't
# worth the dependency cost; basic 8 are enough for tail's purpose).
_EVENT_COLORS = {
    "cycle_start": "36",  # cyan
    "cycle_end": "36",
    "verify_passed": "32",  # green
    "verify_failed": "33",  # yellow
    "done": "1;32",  # bold green
    "paused": "33",  # yellow
    "blocked": "1;31",  # bold red
    "stop_failure": "31",  # red
    "stop_failure_unknown": "31",
    "resume": "1;36",
}


def _user_home() -> Path:
    return Path(
        os.environ.get("CC_AUTOPIPE_USER_HOME", str(Path.home() / ".cc-autopipe"))
    )


def _format_record(rec: dict[str, Any]) -> str:
    ts = (rec.get("ts") or "")[11:19]  # HH:MM:SS
    project = rec.get("project") or ""
    event = rec.get("event") or rec.get("error") or "?"

    extras_parts: list[str] = []
    for k, v in rec.items():
        if k in {"ts", "project", "event", "error"}:
            continue
        if v is None:
            continue
        extras_parts.append(f"{k}={v}")
    extras = ", ".join(extras_parts)

    color = _EVENT_COLORS.get(event, "")
    event_str = _c(color, event) if color else event

    line = f"{_c('2', ts)}  {project:<22} {event_str}"
    if extras:
        line += f"  {_c('2', f'({extras})')}"
    return line


def _passes_filters(
    rec: dict[str, Any],
    project_filter: str | None,
    event_filter: set[str] | None,
) -> bool:
    if project_filter and rec.get("project") != project_filter:
        return False
    if event_filter:
        ev = rec.get("event") or rec.get("error") or ""
        if ev not in event_filter:
            return False
    return True


def _emit(
    line: str,
    project_filter: str | None,
    event_filter: set[str] | None,
) -> None:
    line = line.strip()
    if not line:
        return
    try:
        rec = json.loads(line)
    except json.JSONDecodeError:
        return  # skip junk; aggregate.jsonl is authoritative-only
    if not isinstance(rec, dict):
        return
    if not _passes_filters(rec, project_filter, event_filter):
        return
    print(_format_record(rec), flush=True)


def _tail_static(
    log: Path,
    n: int,
    project_filter: str | None,
    event_filter: set[str] | None,
) -> int:
    """Print the last n matching lines, then exit."""
    if not log.exists():
        sys.stderr.write(f"tail: no log at {log}\n")
        return 1
    # Cheap last-N: read all then slice. aggregate.jsonl stays small in v0.5.
    lines = log.read_text(encoding="utf-8").splitlines()
    for line in lines[-n:]:
        _emit(line, project_filter, event_filter)
    return 0


def _tail_follow(
    log: Path,
    n: int,
    project_filter: str | None,
    event_filter: set[str] | None,
) -> int:
    """tail -f equivalent. Re-opens the file if it's truncated/rotated."""
    # Print the tail seed first.
    if log.exists():
        lines = log.read_text(encoding="utf-8").splitlines()
        for line in lines[-n:]:
            _emit(line, project_filter, event_filter)
        offset = log.stat().st_size
    else:
        # Wait for the file to appear (orchestrator may not have started).
        sys.stderr.write(f"tail: waiting for {log}…\n")
        offset = 0
        while not log.exists():
            time.sleep(0.5)
        offset = 0

    while True:
        try:
            size = log.stat().st_size
        except FileNotFoundError:
            time.sleep(0.5)
            continue
        if size < offset:
            # File rotated/truncated — reset and start over.
            offset = 0
        if size > offset:
            with log.open("r", encoding="utf-8") as f:
                f.seek(offset)
                chunk = f.read()
                offset = f.tell()
            for line in chunk.splitlines():
                _emit(line, project_filter, event_filter)
        time.sleep(0.5)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="cc-autopipe tail",
        description="Follow ~/.cc-autopipe/log/aggregate.jsonl, formatted.",
    )
    parser.add_argument(
        "--project",
        metavar="NAME",
        help="filter to this project name (basename match)",
    )
    parser.add_argument(
        "--event",
        metavar="NAMES",
        help="filter to these comma-separated event names",
    )
    parser.add_argument(
        "--no-follow",
        action="store_true",
        help="print existing lines and exit",
    )
    parser.add_argument(
        "-n",
        type=int,
        default=20,
        metavar="N",
        help="show last N lines before following (default: 20)",
    )
    args = parser.parse_args(argv)

    event_filter: set[str] | None = None
    if args.event:
        event_filter = {x.strip() for x in args.event.split(",") if x.strip()}

    log = _user_home() / "log" / "aggregate.jsonl"

    if args.no_follow:
        return _tail_static(log, args.n, args.project, event_filter)

    try:
        return _tail_follow(log, args.n, args.project, event_filter) or 0
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
