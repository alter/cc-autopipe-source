#!/usr/bin/env python3
"""quota.py — `cc-autopipe quota` CLI surface.

Reads Claude Code's oauth/usage endpoint (via src/lib/quota.py) and
prints a human-readable summary of the 5-hour and 7-day utilisation
buckets, or emits machine-readable JSON for scripting.

Refs: SPEC.md §6.3, §9.

Usage:
    cc-autopipe quota              # human-readable summary
    cc-autopipe quota --json       # normalised Quota object as JSON
    cc-autopipe quota --raw        # raw endpoint response as JSON
    cc-autopipe quota --refresh    # force fresh fetch (combine with any mode)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent / "lib"
sys.path.insert(0, str(_LIB))
import quota as quota_lib  # noqa: E402

_UNAVAILABLE_MSG = (
    "cc-autopipe: quota unavailable (no token, endpoint unreachable, or disabled)\n"
    "Hint: run `claude` and authenticate, then retry.\n"
)


def _format_human(q: quota_lib.Quota, age: float | None) -> str:
    def _pct(f: float) -> str:
        return f"{int(round(f * 100))}%"

    def _resets(dt) -> str:
        if dt is None:
            return "(resets unknown)"
        return f"(resets {dt.strftime('%Y-%m-%dT%H:%M:%SZ')})"

    lines = [
        f"5h: {_pct(q.five_hour_pct)} {_resets(q.five_hour_resets_at)}",
        f"7d: {_pct(q.seven_day_pct)} {_resets(q.seven_day_resets_at)}",
    ]
    if age is None:
        lines.append("cache age: n/a")
    else:
        lines.append(f"cache age: {int(round(age))}s")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="cc-autopipe quota")
    grp = parser.add_mutually_exclusive_group()
    grp.add_argument(
        "--json",
        dest="json_out",
        action="store_true",
        help="Emit normalised Quota object as JSON.",
    )
    grp.add_argument(
        "--raw",
        dest="raw_out",
        action="store_true",
        help="Emit raw endpoint response dict as JSON.",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Force a fresh fetch, skipping the cache.",
    )
    args = parser.parse_args(argv)

    if args.raw_out:
        raw = quota_lib.read_raw(refresh=args.refresh)
        if raw is None:
            sys.stderr.write(_UNAVAILABLE_MSG)
            return 2
        json.dump(raw, sys.stdout)
        sys.stdout.write("\n")
        return 0

    if args.json_out:
        raw = quota_lib.read_raw(refresh=args.refresh)
        if raw is None:
            sys.stderr.write(_UNAVAILABLE_MSG)
            return 2
        q = quota_lib.Quota.from_dict(raw)
        json.dump(q.to_jsonable(), sys.stdout)
        sys.stdout.write("\n")
        return 0

    # Default: human-readable summary.
    raw = quota_lib.read_raw(refresh=args.refresh)
    if raw is None:
        sys.stderr.write(_UNAVAILABLE_MSG)
        return 2
    q = quota_lib.Quota.from_dict(raw)
    age = quota_lib.cache_age_sec()
    print(_format_human(q, age))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
