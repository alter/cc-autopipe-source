#!/usr/bin/env python3
"""backlog.py — parse top N OPEN tasks from backlog.md by priority.

Refs: SPEC-v1.2.md Bug D "Backlog FIFO discipline (soft enforcement)".

In real-world test (AI-trade ML R&D) Claude was picking lower-priority
backlog items over the top P0/P1 task. SessionStart hook injects the
top 3 OPEN tasks so the agent sees them up front.

backlog.md format (existing v0.5/v1.0 convention):

    - [ ] [implement] [P0] task_alpha — first thing
    - [ ] [implement] [P1] task_beta — second thing
    - [x] [implement] [P0] task_done — already finished
    - [~] [implement] [P0] task_in_progress — Claude marked in-progress

Status markers:
    [ ] — open
    [x] — done
    [~] — in-progress (Claude is working on it now)

Priority: optional `[P0]` / `[P1]` / `[P2]` token; missing → P3 sort
position (lowest priority).

API:
    parse_open_tasks(backlog_path)         → list[BacklogItem]
    top_n(items, n=3)                      → list[BacklogItem]  (sorted)
    parse_top_open(backlog_path, n=3)      → list[BacklogItem]  (read+sort)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# A task line looks like:
#   - [ ] [implement] [P1] task_id — description
# We accept variations:
#   - status box (required)
#   - any number of bracketed tags afterwards (priority lives in one of
#     them, others are roles like [implement] / [research])
#   - id is the first non-bracketed word; description follows after `—`/`-`
TASK_LINE_RE = re.compile(r"^\s*-\s+\[([ x~])\]\s*(?P<rest>.+?)\s*$")
PRIORITY_RE = re.compile(r"\[P([0-9])\]")
TAG_RE = re.compile(r"\[[^\]]+\]")


@dataclass
class BacklogItem:
    status: str  # " ", "x", or "~"
    priority: int  # 0..9, lower = higher priority. Missing → 3.
    id: str
    description: str
    tags: list[str]
    raw_line: str

    @property
    def is_open(self) -> bool:
        return self.status in (" ", "~")


def _parse_line(line: str) -> BacklogItem | None:
    m = TASK_LINE_RE.match(line)
    if not m:
        return None
    status = m.group(1)
    rest = m.group("rest")
    # Pull priority out of any bracketed tag.
    pri_match = PRIORITY_RE.search(rest)
    priority = int(pri_match.group(1)) if pri_match else 3
    # Collect every bracketed tag (e.g. [implement], [P1], [research]).
    tags = TAG_RE.findall(rest)
    # Strip tags to find the id + description. The first non-bracketed
    # word after the tags is the id; everything after `—`/`-`/`:` is
    # the description.
    after_tags = TAG_RE.sub("", rest).strip()
    # Split on the first em-dash / en-dash / hyphen / colon delimiter.
    parts = re.split(r"\s+[—–\-:]\s+", after_tags, maxsplit=1)
    task_id = parts[0].strip()
    description = parts[1].strip() if len(parts) > 1 else ""
    return BacklogItem(
        status=status,
        priority=priority,
        id=task_id,
        description=description,
        tags=tags,
        raw_line=line.rstrip(),
    )


def parse_open_tasks(backlog_path: str | Path) -> list[BacklogItem]:
    """Return all OPEN ([ ] or [~]) tasks from backlog.md, in file
    order. Missing/empty file → []."""
    p = Path(backlog_path)
    if not p.exists():
        return []
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return []
    out: list[BacklogItem] = []
    for line in text.splitlines():
        item = _parse_line(line)
        if item is not None and item.is_open:
            out.append(item)
    return out


def top_n(items: list[BacklogItem], n: int = 3) -> list[BacklogItem]:
    """Sort OPEN items by priority (lower first), preserving file order
    as the secondary key. Returns the first n.

    Stable sort: Python sorted() preserves original order for equal
    keys, so two P1s stay in backlog.md order. Matches operator
    expectation: "top 3 by priority, FIFO within priority".
    """
    return sorted(items, key=lambda it: it.priority)[:n]


def parse_top_open(backlog_path: str | Path, n: int = 3) -> list[BacklogItem]:
    """Convenience: parse_open_tasks → top_n in one call."""
    return top_n(parse_open_tasks(backlog_path), n=n)
