#!/usr/bin/env python3
"""transient.py — classify subprocess failures, probe network reachability.

Refs: PROMPT_v1.3.4-hotfix.md GROUP R.

Distinguishes three categories:
  - transient   — retry with backoff; do NOT increment consecutive_failures
  - structural  — fail immediately; existing v1.3.3 path applies
  - unknown     — preserve v1.3.3 behavior (treat as structural failure)

The classifier is deliberately narrow. Adding patterns that match
arbitrary words like "error" or "failed" would mask real bugs by
silently retrying structural problems.
"""

from __future__ import annotations

import re
import socket
import urllib.error  # noqa: F401  -- referenced by classify_failure docstring
from typing import Literal

# Patterns that indicate a transient (retry-able) failure. All matched
# case-insensitively against stderr. Order matters only for performance;
# correctness does not depend on it.
TRANSIENT_STDERR_PATTERNS = (
    r"Server is temporarily limiting requests",
    r"Rate.?limited",  # "rate-limited" / "rate limited"
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
    "|".join(f"({p})" for p in TRANSIENT_STDERR_PATTERNS),
    re.IGNORECASE,
)

# rc codes that strongly suggest network/transient cause when paired
# with empty / generic stderr. These do NOT auto-classify as transient
# alone (must also have suspicious stderr or empty stderr); we use them
# as a tiebreaker.
LIKELY_TRANSIENT_RC = frozenset(
    {
        6,  # curl: couldn't resolve host
        7,  # curl: failed to connect
        28,  # curl: operation timeout
        52,  # curl: empty reply
        56,  # curl: failure receiving network data
        124,  # GNU timeout: command timed out
    }
)

# Structural error patterns — these explicitly are NOT transient even
# if stderr otherwise looks suspicious. Auth failures, syntax errors,
# missing binaries.
STRUCTURAL_STDERR_PATTERNS = (
    r"401\s+Unauthorized",
    r"403\s+Forbidden",
    r"invalid.{0,5}api.{0,5}key",
    r"authentication\s+failed",
    r"command\s+not\s+found",
    r"No\s+such\s+file\s+or\s+directory",
    r"Permission\s+denied",
    r"requires\s+--verbose",  # the v0.5 Stage G bug — structural, not transient
)

_STRUCTURAL_RE = re.compile(
    "|".join(f"({p})" for p in STRUCTURAL_STDERR_PATTERNS),
    re.IGNORECASE,
)


def classify_failure(
    rc: int, stderr: str | None
) -> Literal["transient", "structural", "unknown"]:
    """Classify a subprocess failure. rc=0 should never call this.

    Order:
      1. Structural patterns (auth, syntax, missing-binary) win — never retry.
      2. Transient patterns (rate-limited, network blip, 5xx).
      3. Tiebreaker: rc in LIKELY_TRANSIENT_RC AND stderr is empty/generic.
      4. Default: unknown — fall through to existing v1.3.3 structural path.
    """
    text = stderr or ""

    if _STRUCTURAL_RE.search(text):
        return "structural"

    if _TRANSIENT_RE.search(text):
        return "transient"

    if rc in LIKELY_TRANSIENT_RC and len(text.strip()) < 100:
        return "transient"

    return "unknown"


def is_anthropic_reachable(
    host: str = "api.anthropic.com", timeout_sec: float = 5.0
) -> bool:
    """Cheap reachability probe: open TCP socket to host:443.

    Does NOT make an HTTP request — that would burn quota check pings.
    Just verifies the routing + DNS path is alive. False on any error
    (DNS fail, refused, timeout). Caller treats False as "defer cycle".
    """
    try:
        with socket.create_connection((host, 443), timeout=timeout_sec):
            return True
    except (socket.timeout, socket.gaierror, ConnectionRefusedError, OSError):
        return False


def is_internet_reachable(timeout_sec: float = 3.0) -> bool:
    """Fallback probe: reach a generic public DNS endpoint.

    Used to distinguish "anthropic specifically down" from "no internet
    at all" (router reboot). Returns True if 1.1.1.1:53 is reachable.
    """
    try:
        with socket.create_connection(("1.1.1.1", 53), timeout=timeout_sec):
            return True
    except (socket.timeout, OSError):
        return False
