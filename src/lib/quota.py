#!/usr/bin/env python3
"""quota.py — reads Claude Code's oauth/usage endpoint with caching.

Refs: SPEC.md §6.3, §9, OPEN_QUESTIONS.md Q1, Q4

Two layers of degradation:
- read_oauth_token: returns None if creds missing (Linux file or macOS
  Keychain). Caller falls back to ratelimit ladder.
- fetch_quota: returns None on token missing OR network failure OR
  malformed response. Caller treats this as "unknown, proceed with
  caution".

The endpoint is undocumented (Q1). If Anthropic changes the response
shape, fetch_quota's parser keys (.five_hour.utilization, .resets_at,
.seven_day.…) will read missing keys as None / 0.0 — read_cached then
returns a Quota with degraded values rather than crashing.

Test override: CC_AUTOPIPE_QUOTA_ENDPOINT replaces the api.anthropic.com
URL so tools/mock-quota-server.py can be hit instead. CC_AUTOPIPE_USER_HOME
overrides ~/.cc-autopipe so the cache lands in pytest's tmp_path.

CLI surface used by hooks (stop-failure.sh per SPEC §9.3):
    python3 quota.py read       Print raw JSON to stdout if available
    python3 quota.py read-cached  Same but never re-fetches
    python3 quota.py refresh    Force-fetch and update cache

read prints the raw quota dict (suitable for jq parsing); on any
failure it prints nothing and exits non-zero.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

CACHE_TTL_SEC = 60
DEFAULT_ENDPOINT = "https://api.anthropic.com/api/oauth/usage"
USER_AGENT = "claude-code/2.1.115"
BETA_HEADER = "oauth-2025-04-20"
KEYCHAIN_SERVICE = "Claude Code-credentials"


def _user_home() -> Path:
    return Path(
        os.environ.get("CC_AUTOPIPE_USER_HOME", str(Path.home() / ".cc-autopipe"))
    )


def _cache_path() -> Path:
    return _user_home() / "quota-cache.json"


def _endpoint() -> str:
    return os.environ.get("CC_AUTOPIPE_QUOTA_ENDPOINT", DEFAULT_ENDPOINT)


def _log(msg: str) -> None:
    print(f"[quota] {msg}", file=sys.stderr, flush=True)


def _parse_iso(s: str | None) -> datetime | None:
    """Parse ISO 8601 — accepts both '...Z' and '+00:00' offsets."""
    if not s:
        return None
    try:
        # fromisoformat in 3.11+ handles 'Z' suffix; older needs replace.
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Credential reading
# ---------------------------------------------------------------------------


def read_oauth_token() -> str | None:
    """Returns OAuth bearer token from Claude Code credentials.

    Linux/WSL: ~/.claude/credentials.json (field 'accessToken')
    macOS:     Keychain via `security find-generic-password -w`

    Returns None on any failure — caller falls back to ladder.
    """
    if platform.system() == "Darwin":
        try:
            result = subprocess.run(
                [
                    "security",
                    "find-generic-password",
                    "-s",
                    KEYCHAIN_SERVICE,
                    "-w",
                ],
                capture_output=True,
                text=True,
                timeout=3,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
            _log(f"keychain read failed: {exc!r}")
            return None
        if result.returncode != 0:
            return None
        out = result.stdout.strip()
        if not out:
            return None
        return _extract_access_token(out) or (out if not out.startswith("{") else None)

    # Linux + WSL + everything-not-Darwin: read JSON from disk.
    creds_path = Path(
        os.environ.get(
            "CC_AUTOPIPE_CREDENTIALS_FILE",
            str(Path.home() / ".claude" / "credentials.json"),
        )
    )
    try:
        raw = creds_path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return None
    return _extract_access_token(raw)


def _extract_access_token(raw: str) -> str | None:
    """Pull `accessToken` from a Claude Code creds blob.

    Observed shapes (verified against Roman's macOS Keychain on
    2026-04-29 during Q4 investigation):
        - {"claudeAiOauth": {"accessToken": "sk-ant-oat01-..."}}
        - {"accessToken": "sk-ant-oat01-..."}    (older Linux file)

    Returns None if neither shape matches. Never logs the token.
    """
    raw = raw.strip()
    if not raw or not raw.startswith("{"):
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    # Prefer the nested macOS shape, then the old top-level shape.
    nested = data.get("claudeAiOauth")
    if isinstance(nested, dict):
        token = nested.get("accessToken")
        if isinstance(token, str) and token:
            return token
    token = data.get("accessToken")
    if isinstance(token, str) and token:
        return token
    return None


# ---------------------------------------------------------------------------
# Endpoint fetch
# ---------------------------------------------------------------------------


def fetch_quota() -> dict[str, Any] | None:
    """Calls oauth/usage. Returns the raw response dict, or None on failure."""
    token = read_oauth_token()
    if not token:
        return None
    req = urllib.request.Request(
        _endpoint(),
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": USER_AGENT,
            "anthropic-beta": BETA_HEADER,
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310
            body = resp.read()
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        _log(f"endpoint fetch failed: {exc!r}")
        return None
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        _log(f"endpoint returned non-JSON: {exc!r}")
        return None
    if not isinstance(data, dict):
        _log(f"endpoint returned non-object: {type(data).__name__}")
        return None
    return data


# ---------------------------------------------------------------------------
# Cache + Quota dataclass
# ---------------------------------------------------------------------------


@dataclass
class Quota:
    five_hour_pct: float  # 0.0 to 1.0
    five_hour_resets_at: Optional[datetime]
    seven_day_pct: float
    seven_day_resets_at: Optional[datetime]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Quota":
        five = d.get("five_hour") or {}
        seven = d.get("seven_day") or {}
        return cls(
            five_hour_pct=float(five.get("utilization") or 0.0),
            five_hour_resets_at=_parse_iso(five.get("resets_at")),
            seven_day_pct=float(seven.get("utilization") or 0.0),
            seven_day_resets_at=_parse_iso(seven.get("resets_at")),
        )

    def to_jsonable(self) -> dict[str, Any]:
        def _iso(dt: datetime | None) -> str | None:
            return None if dt is None else dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        return {
            "five_hour_pct": self.five_hour_pct,
            "five_hour_resets_at": _iso(self.five_hour_resets_at),
            "seven_day_pct": self.seven_day_pct,
            "seven_day_resets_at": _iso(self.seven_day_resets_at),
        }


def _read_cache_raw() -> dict[str, Any] | None:
    """Returns the cached raw response dict if the cache is fresh."""
    cache = _cache_path()
    if not cache.exists():
        return None
    try:
        mtime = cache.stat().st_mtime
        if time.time() - mtime > CACHE_TTL_SEC:
            return None
        with cache.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _write_cache(raw: dict[str, Any]) -> None:
    cache = _cache_path()
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_suffix(f".tmp.{os.getpid()}")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(raw, f)
        os.replace(tmp, cache)
    except OSError as exc:
        _log(f"cache write failed: {exc!r}")


def read_raw(*, refresh: bool = False) -> dict[str, Any] | None:
    """Get the raw quota response (preferred for hooks calling jq).

    refresh=False: cached value if fresh, else fetch and cache. None on
        any failure.
    refresh=True: skip cache, force a fresh fetch + update cache. None
        on any failure.

    Test escape hatch: CC_AUTOPIPE_QUOTA_DISABLED=1 short-circuits to
    None without consulting the cache or fetching. Tests that don't
    care about quota set this so the orchestrator's pre-flight check
    doesn't accidentally hit api.anthropic.com.
    """
    if os.environ.get("CC_AUTOPIPE_QUOTA_DISABLED") == "1":
        return None

    if not refresh:
        cached = _read_cache_raw()
        if cached is not None:
            return cached

    raw = fetch_quota()
    if raw is None:
        return None

    _write_cache(raw)
    return raw


def read_cached() -> Quota | None:
    """Returns a parsed Quota with TTL caching. None on any failure.

    The orchestrator's pre-flight check uses this directly.
    """
    raw = read_raw(refresh=False)
    if raw is None:
        return None
    return Quota.from_dict(raw)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    if not argv:
        _log("usage: quota.py {read|read-cached|refresh}")
        return 2
    cmd = argv[0]
    if cmd == "read":
        raw = read_raw(refresh=False)
        if raw is None:
            return 1
        json.dump(raw, sys.stdout)
        sys.stdout.write("\n")
        return 0
    if cmd == "read-cached":
        cached = _read_cache_raw()
        if cached is None:
            return 1
        json.dump(cached, sys.stdout)
        sys.stdout.write("\n")
        return 0
    if cmd == "refresh":
        raw = read_raw(refresh=True)
        if raw is None:
            return 1
        json.dump(raw, sys.stdout)
        sys.stdout.write("\n")
        return 0
    _log(f"unknown command: {cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))


# Convenience for callers that want to know how recent the cache is.
def cache_age_sec() -> float | None:
    cache = _cache_path()
    if not cache.exists():
        return None
    try:
        return time.time() - cache.stat().st_mtime
    except OSError:
        return None


_ = (timezone, datetime)  # silence unused-import warnings for re-exports
