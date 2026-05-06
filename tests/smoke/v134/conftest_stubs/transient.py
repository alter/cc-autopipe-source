"""Self-contained stub for src/lib/transient.py (v1.3.4 R9 smoke).

Designed to be COPIED INTO `src/lib/transient.py` for the duration of
the smoke run, then restored. The smoke harness handles the swap; do
not import this stub directly.

Behaviour:
  - is_anthropic_reachable: False on the first 3 calls, True afterwards.
    Counter persisted via CC_AUTOPIPE_PROBE_COUNTER_FILE so the count
    survives across orchestrator re-imports between cycles.
  - is_internet_reachable: always False (so the engine logs
    internet_up=false in the network_probe_failed event for greppability).
  - classify_failure: identical implementation to the real module so
    R4 transient routing keeps working when this stub is in place.

We deliberately do NOT exec the real module here — when this file is
copied into src/lib/transient.py, the path-based location heuristic
breaks, so the implementation must be self-contained.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

# Mirrored from the real transient.py at v1.3.4. Keep in sync if the
# real classifier grows new patterns.
TRANSIENT_STDERR_PATTERNS = (
    r"Server is temporarily limiting requests",
    r"Rate.?limited",
    r"rate limit exceeded",
    r"too many requests",
    r"503\s+Service\s+Unavailable",
    r"502\s+Bad\s+Gateway",
    r"504\s+Gateway\s+Time-?out",
    r"Connection\s+(refused|reset|timed\s*out)",
    r"Network\s+is\s+unreachable",
    r"Temporary\s+failure\s+in\s+name\s+resolution",
    r"Cannot\s+resolve\s+host",
    r"getaddrinfo\s+failed",
    r"SSL.*timed\s*out",
    r"EOF\s+occurred\s+in\s+violation",
    r"upstream\s+connect\s+error",
    r"socket\s+hang\s+up",
)
_TRANSIENT_RE = re.compile(
    "|".join(f"({p})" for p in TRANSIENT_STDERR_PATTERNS), re.IGNORECASE
)
LIKELY_TRANSIENT_RC = frozenset({6, 7, 28, 52, 56, 124})
STRUCTURAL_STDERR_PATTERNS = (
    r"401\s+Unauthorized",
    r"403\s+Forbidden",
    r"invalid.{0,5}api.{0,5}key",
    r"authentication\s+failed",
    r"command\s+not\s+found",
    r"No\s+such\s+file\s+or\s+directory",
    r"Permission\s+denied",
    r"requires\s+--verbose",
)
_STRUCTURAL_RE = re.compile(
    "|".join(f"({p})" for p in STRUCTURAL_STDERR_PATTERNS), re.IGNORECASE
)


def classify_failure(
    rc: int, stderr: str | None
) -> Literal["transient", "structural", "unknown"]:
    text = stderr or ""
    if _STRUCTURAL_RE.search(text):
        return "structural"
    if _TRANSIENT_RE.search(text):
        return "transient"
    if rc in LIKELY_TRANSIENT_RC and len(text.strip()) < 100:
        return "transient"
    return "unknown"


def _bump_counter() -> int:
    path = os.environ.get("CC_AUTOPIPE_PROBE_COUNTER_FILE")
    if not path:
        return 1
    try:
        current = int(Path(path).read_text().strip())
    except (FileNotFoundError, ValueError):
        current = 0
    current += 1
    Path(path).write_text(str(current))
    return current


def is_anthropic_reachable(
    host: str = "api.anthropic.com", timeout_sec: float = 5.0
) -> bool:
    """Stub: False, False, False, True, True, ..."""
    return _bump_counter() >= 4


def is_internet_reachable(timeout_sec: float = 3.0) -> bool:
    return False
