#!/usr/bin/env python3
"""detach_defaults.py — read detach_defaults section from project config.yaml.

cc-autopipe-detach resolves its check-every / max-wait values via this
chain (highest priority first):

  1. CLI arg                  --check-every / --max-wait
  2. Env var                  CC_AUTOPIPE_DEFAULT_CHECK_EVERY /
                              CC_AUTOPIPE_DEFAULT_MAX_WAIT
  3. Project config           <project>/.cc-autopipe/config.yaml
                              detach_defaults: {check_every_sec, max_wait_sec}
  4. Hardcoded fallback       600 / 14400 (in the bash helper)

This module owns step 3. It mirrors the YAML-parsing pattern from
src/orchestrator/prompt.py:_read_yaml_top_block so behaviour stays
consistent across config blocks (no PyYAML dependency).

CLI:
    python3 detach_defaults.py <project_path>

Output: JSON dict with check_every_sec and max_wait_sec, only those
that are present + parseable in config. Empty {} on missing file,
missing block, or unparseable values. Always exits 0 (the bash helper
runs `... 2>/dev/null || echo '{}'` so a hard failure here would mask
the env/CLI overrides).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ALLOWED_KEYS = ("check_every_sec", "max_wait_sec")


def read_detach_defaults(project_path: Path) -> dict[str, int]:
    """Read .cc-autopipe/config.yaml's detach_defaults: block.

    Returns dict with subset of {'check_every_sec', 'max_wait_sec'}.
    Empty dict if section missing, file missing, or parse error.
    Invalid integer values are silently dropped per key (env-var
    fallback should still apply).
    """
    cfg = project_path / ".cc-autopipe" / "config.yaml"
    if not cfg.exists():
        return {}
    try:
        text = cfg.read_text(encoding="utf-8")
    except OSError:
        return {}
    out: dict[str, int] = {}
    in_block = False
    for line in text.splitlines():
        stripped = line.rstrip()
        if stripped == "detach_defaults:":
            in_block = True
            continue
        if in_block:
            # Block ends on a non-indented non-empty line (next top-level
            # YAML key). Empty / comment-only lines stay in the block.
            if stripped and not stripped.startswith(" "):
                in_block = False
                continue
            if ":" not in stripped:
                continue
            key, _, raw = stripped.strip().partition(":")
            if key not in ALLOWED_KEYS:
                continue
            try:
                out[key] = int(raw.strip())
            except (ValueError, TypeError):
                # Bad integer — skip this key and let the resolution
                # chain fall through to env / hardcoded defaults.
                continue
    return out


def main(argv: list[str]) -> int:
    if not argv:
        json.dump({}, sys.stdout)
        return 0
    project = Path(argv[0])
    json.dump(read_detach_defaults(project), sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
