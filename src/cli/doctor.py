#!/usr/bin/env python3
"""doctor.py — implements `cc-autopipe doctor` per SPEC.md §12.9.

Runs a checklist of install/config prerequisites and prints each as
ok / warn / fail with a remediation hint. Designed to be idempotent
and fast (<2s without --no-quota network round-trip).

Checks:
  1. claude binary present and >= CLAUDE_CODE_MIN_VERSION
  2. python3 ≥ 3.11
  3. jq present
  4. ruff present (build-only, downgraded to warn)
  5. shellcheck present (build-only, warn)
  6. ~/.cc-autopipe/secrets.env exists and chmod 600
  7. hooks executable (src/hooks/*.sh)
  8. OAuth token readable (Linux file or macOS Keychain)
  9. TG send-test (requires secrets.env; skipped silently if absent)
 10. oauth/usage endpoint reachable

Flags:
  --offline   Skip 9 + 10 (any check that hits the network).
              Tests use this. macOS users without Keychain creds
              should also use it for a clean local report.
  --json      Emit a machine-readable document instead of the
              human-formatted checklist. Exit code unchanged.

macOS-specific note printed up front: the first Keychain access in
a session may prompt — approve to allow OAuth token reading.

Refs: SPEC.md §12.9, OPEN_QUESTIONS.md Q4 (Keychain prompt observed
behaviour)
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_SRC = Path(__file__).resolve().parent.parent
_LIB = _SRC / "lib"
_HOOKS = _SRC / "hooks"
sys.path.insert(0, str(_LIB))
import quota as quota_lib  # noqa: E402

OK = "ok"
WARN = "warn"
FAIL = "fail"
SKIP = "skip"

# ANSI codes only when stdout is a tty.
_USE_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
_COLORS = {OK: "32", WARN: "33", FAIL: "31", SKIP: "2"}
_GLYPHS = {OK: "✓", WARN: "!", FAIL: "✗", SKIP: "·"}


def _c(code: str, text: str) -> str:
    if not _USE_COLOR:
        return text
    return f"\033[{code}m{text}\033[0m"


def _user_home() -> Path:
    return Path(
        os.environ.get("CC_AUTOPIPE_USER_HOME", str(Path.home() / ".cc-autopipe"))
    )


def _engine_home() -> Path:
    env = os.environ.get("CC_AUTOPIPE_HOME")
    if env:
        return Path(env)
    return _SRC


def _min_claude_version() -> str:
    f = _engine_home() / "CLAUDE_CODE_MIN_VERSION"
    try:
        return f.read_text(encoding="utf-8").strip()
    except OSError:
        return "2.1.115"


@dataclass
class Check:
    name: str
    status: str  # one of OK / WARN / FAIL / SKIP
    detail: str = ""
    hint: str = ""
    extras: dict[str, Any] = field(default_factory=dict)


def _version_ge(found: str, required: str) -> bool:
    """Compare dotted versions (loose; padded with 0s)."""

    def split(v: str) -> list[int]:
        return [int(x) for x in re.findall(r"\d+", v)]

    a = split(found)
    b = split(required)
    n = max(len(a), len(b))
    a += [0] * (n - len(a))
    b += [0] * (n - len(b))
    return a >= b


def check_claude_binary() -> Check:
    bin_path = shutil.which("claude")
    if not bin_path:
        return Check(
            "claude binary",
            FAIL,
            "not found on PATH",
            hint="Install Claude Code 2.1.115+ from anthropic.com/claude-code",
        )
    try:
        cp = subprocess.run(
            [bin_path, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return Check(
            "claude binary",
            WARN,
            f"`claude --version` failed: {exc}",
            hint="Try running `claude --version` manually to diagnose.",
        )
    out = (cp.stdout + cp.stderr).strip()
    m = re.search(r"\b(\d+\.\d+\.\d+)\b", out)
    if not m:
        return Check(
            "claude binary",
            WARN,
            f"version unparseable: {out!r}",
            hint=f"Need >= {_min_claude_version()}.",
        )
    version = m.group(1)
    required = _min_claude_version()
    if _version_ge(version, required):
        return Check("claude binary", OK, f"{version} (>= {required})")
    return Check(
        "claude binary",
        FAIL,
        f"{version} (< {required})",
        hint="Upgrade with `claude self-update` or via your installer.",
    )


def check_python() -> Check:
    v = sys.version_info
    actual = f"{v.major}.{v.minor}.{v.micro}"
    if (v.major, v.minor) >= (3, 11):
        return Check("python3", OK, actual)
    return Check(
        "python3",
        FAIL,
        f"{actual} (< 3.11)",
        hint="Engine code uses 3.11+ stdlib features (datetime.fromisoformat).",
    )


def _which_tool(tool: str) -> Check:
    p = shutil.which(tool)
    if p:
        return Check(tool, OK, p)
    return Check(
        tool,
        FAIL if tool == "jq" else WARN,
        "not found on PATH",
        hint=f"Install via your package manager (e.g. `brew install {tool}`).",
    )


def check_jq() -> Check:
    return _which_tool("jq")


def check_ruff() -> Check:
    c = _which_tool("ruff")
    # ruff is build-only; downgrade absence to WARN.
    if c.status == FAIL:
        c.status = WARN
        c.hint = "Build-only: pip install ruff (skip for runtime-only hosts)."
    return c


def check_shellcheck() -> Check:
    c = _which_tool("shellcheck")
    if c.status == FAIL:
        c.status = WARN
        c.hint = "Build-only: brew install shellcheck (skip for runtime hosts)."
    return c


def check_secrets_env() -> Check:
    secrets = _user_home() / "secrets.env"
    if not secrets.exists():
        return Check(
            "secrets.env",
            WARN,
            f"missing at {secrets}",
            hint="Create with TG_BOT_TOKEN/TG_CHAT_ID for Telegram alerts; "
            "absent is fine if you don't want TG.",
        )
    try:
        mode = secrets.stat().st_mode & 0o777
    except OSError as exc:
        return Check("secrets.env", FAIL, f"stat failed: {exc}")
    if mode != 0o600:
        return Check(
            "secrets.env",
            FAIL,
            f"perms {oct(mode)} (must be 0600)",
            hint=f"Run: chmod 600 {secrets}",
        )
    return Check("secrets.env", OK, f"{secrets} (chmod 600)")


def check_hooks_executable() -> Check:
    """All shipped hooks must be executable."""
    missing: list[str] = []
    not_exec: list[str] = []
    expected = ["session-start.sh", "pre-tool-use.sh", "stop.sh", "stop-failure.sh"]
    for name in expected:
        p = _HOOKS / name
        if not p.exists():
            missing.append(name)
            continue
        if not os.access(p, os.X_OK):
            not_exec.append(name)
    if missing:
        return Check(
            "hooks",
            FAIL,
            f"missing: {', '.join(missing)}",
            hint=f"Reinstall (expected at {_HOOKS}/).",
        )
    if not_exec:
        return Check(
            "hooks",
            FAIL,
            f"not executable: {', '.join(not_exec)}",
            hint=f"Run: chmod +x {_HOOKS}/*.sh",
        )
    return Check("hooks", OK, f"{len(expected)} hooks executable")


def check_oauth_token() -> Check:
    """Reads the token via lib/quota.py — never logs it."""
    try:
        token = quota_lib.read_oauth_token()
    except Exception as exc:  # noqa: BLE001
        return Check(
            "OAuth token",
            FAIL,
            f"read raised {type(exc).__name__}: {exc}",
            hint="Check ~/.claude/credentials.json (Linux) or Keychain (macOS).",
        )
    if not token:
        if platform.system() == "Darwin":
            return Check(
                "OAuth token",
                WARN,
                "Keychain returned no token",
                hint="Approve the Keychain prompt, or run: "
                "security find-generic-password -s 'Claude Code-credentials' -w",
            )
        return Check(
            "OAuth token",
            WARN,
            "no credentials file",
            hint="Run `claude login` to populate ~/.claude/credentials.json.",
        )
    # Token observed; show only the prefix to confirm shape without leaking.
    return Check("OAuth token", OK, f"prefix={token[:11]}…")


def check_tg(offline: bool) -> Check:
    if offline:
        return Check("TG send-test", SKIP, "skipped (--offline)")
    secrets = _user_home() / "secrets.env"
    if not secrets.exists():
        return Check(
            "TG send-test",
            SKIP,
            "no secrets.env",
            hint="Create secrets.env with TG creds to enable.",
        )
    tg_sh = _LIB / "tg.sh"
    env = os.environ.copy()
    env["CC_AUTOPIPE_SECRETS_FILE"] = str(secrets)
    try:
        cp = subprocess.run(
            ["bash", str(tg_sh), "[cc-autopipe doctor] hello from doctor"],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return Check("TG send-test", FAIL, f"tg.sh raised {exc}")
    if cp.returncode != 0:
        return Check(
            "TG send-test",
            WARN,
            f"tg.sh rc={cp.returncode}",
            hint="tg.sh always exits 0; non-zero suggests bash/curl issue.",
        )
    return Check("TG send-test", OK, "sent (best-effort)")


def check_quota_endpoint(offline: bool) -> Check:
    if offline:
        return Check("oauth/usage endpoint", SKIP, "skipped (--offline)")
    raw = quota_lib.fetch_quota()
    if raw is None:
        return Check(
            "oauth/usage endpoint",
            WARN,
            "fetch returned None",
            hint="Either token missing or endpoint unreachable. "
            "Engine still works (ratelimit ladder catches actual 429s).",
        )
    five = (raw.get("five_hour") or {}).get("utilization")
    seven = (raw.get("seven_day") or {}).get("utilization")
    return Check(
        "oauth/usage endpoint",
        OK,
        f"reachable; 5h={quota_lib.normalize_utilization(five) * 100:.0f}% "
        f"7d={quota_lib.normalize_utilization(seven) * 100:.0f}%",
    )


def run_all(offline: bool) -> list[Check]:
    return [
        check_claude_binary(),
        check_python(),
        check_jq(),
        check_ruff(),
        check_shellcheck(),
        check_secrets_env(),
        check_hooks_executable(),
        check_oauth_token(),
        check_tg(offline),
        check_quota_endpoint(offline),
    ]


def _print_human(checks: list[Check]) -> None:
    if platform.system() == "Darwin":
        print(
            _c(
                "2",
                "macOS note: first Keychain access in a session may prompt — "
                "approve to allow OAuth token reading.",
            )
        )
        print()
    for c in checks:
        glyph = _GLYPHS[c.status]
        color = _COLORS[c.status]
        line = f"{_c(color, glyph)} {c.name}"
        if c.detail:
            line += f": {c.detail}"
        print(line)
        if c.hint and c.status in (FAIL, WARN):
            print(f"  {_c('2', 'hint:')} {c.hint}")
    print()
    n_fail = sum(1 for c in checks if c.status == FAIL)
    n_warn = sum(1 for c in checks if c.status == WARN)
    n_skip = sum(1 for c in checks if c.status == SKIP)
    n_ok = sum(1 for c in checks if c.status == OK)
    summary = (
        f"{_c('32', f'{n_ok} ok')}, "
        f"{_c('33', f'{n_warn} warn')}, "
        f"{_c('31', f'{n_fail} fail')}"
    )
    if n_skip:
        summary += f", {_c('2', f'{n_skip} skip')}"
    print(summary)


def _exit_code(checks: list[Check]) -> int:
    if any(c.status == FAIL for c in checks):
        return 1
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="cc-autopipe doctor",
        description="Verify cc-autopipe install + config prerequisites.",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="skip network checks (TG send-test, oauth/usage reachability)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit machine-readable JSON instead of human checklist",
    )
    args = parser.parse_args(argv)

    checks = run_all(offline=args.offline)

    if args.json:
        json.dump(
            {
                "checks": [
                    {
                        "name": c.name,
                        "status": c.status,
                        "detail": c.detail,
                        "hint": c.hint,
                        "extras": c.extras,
                    }
                    for c in checks
                ],
                "summary": {
                    "ok": sum(1 for c in checks if c.status == OK),
                    "warn": sum(1 for c in checks if c.status == WARN),
                    "fail": sum(1 for c in checks if c.status == FAIL),
                    "skip": sum(1 for c in checks if c.status == SKIP),
                },
            },
            sys.stdout,
            indent=2,
        )
        sys.stdout.write("\n")
    else:
        _print_human(checks)

    return _exit_code(checks)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
