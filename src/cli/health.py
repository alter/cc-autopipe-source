#!/usr/bin/env python3
"""health.py — `cc-autopipe health` CLI surface.

Reads ~/.cc-autopipe/log/health.jsonl (written each cycle by the
orchestrator) and prints a compact summary.

Refs: PROMPT_v1.3-FULL.md GROUP F2.

Usage:
    cc-autopipe health         # last 1h summary
    cc-autopipe health --24h   # last 24h trends
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent / "lib"
sys.path.insert(0, str(_LIB))
import health as health_lib  # noqa: E402


def _format(records: list[dict]) -> str:
    summary = health_lib.summarise(records)
    if summary["total_records"] == 0:
        return "no health records in window"
    lines = [f"Health records in window: {summary['total_records']}"]
    for proj, d in summary["by_project"].items():
        five_h = d.get("5h_pct")
        seven_d = d.get("7d_pct")
        disk = d.get("disk_free_gb")
        bits = [f"  {proj}: cycles={d['cycles']}, phases={d['phases']}"]
        extras: list[str] = []
        if five_h is not None:
            extras.append(f"5h={int(five_h * 100)}%")
        if seven_d is not None:
            extras.append(f"7d={int(seven_d * 100)}%")
        if disk is not None:
            extras.append(f"disk={disk}GB")
        if extras:
            bits.append("    " + ", ".join(extras))
        lines.extend(bits)
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="cc-autopipe health")
    grp = parser.add_mutually_exclusive_group()
    grp.add_argument(
        "--24h",
        dest="last_24h",
        action="store_true",
        help="Summarise the last 24 hours instead of the last 1 hour.",
    )
    grp.add_argument(
        "--json",
        dest="json_out",
        action="store_true",
        help="Emit raw records as JSON for scripting.",
    )
    args = parser.parse_args(argv)

    since = 24 * 3600 if args.last_24h else 3600
    records = health_lib.read_recent_health(since_seconds=since)

    if args.json_out:
        json.dump(records, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0

    print(_format(records))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
