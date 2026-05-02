#!/usr/bin/env python3
"""service.py — install/uninstall systemd (Linux) or launchd (macOS) units.

Refs: SPEC-v1.md §2.6.

Provides four subcommands wired through the bash dispatcher:

  cc-autopipe install-systemd      Linux  — copies + enables a user unit
  cc-autopipe install-launchd      macOS  — copies + loads a user agent
  cc-autopipe uninstall-systemd    Linux  — disables + removes the unit
  cc-autopipe uninstall-launchd    macOS  — unloads + removes the plist

Each command:
  - Reads the matching template under $CC_AUTOPIPE_HOME/init/.
  - Substitutes __USER__, __HOME__, __CC_AUTOPIPE_HOME__, __PATH__.
  - Writes to the OS-canonical user location:
      systemd: ~/.config/systemd/user/cc-autopipe.service
      launchd: ~/Library/LaunchAgents/com.cc-autopipe.plist
  - Prints the operator follow-up command (systemctl --user enable + start,
    or launchctl load).

The engine never auto-starts a service in v1.0 — that's an operator
decision. We just make the install ergonomic.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _engine_home() -> Path:
    env = os.environ.get("CC_AUTOPIPE_HOME")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent


def _systemd_target_dir() -> Path:
    """User-scope systemd unit directory."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "systemd" / "user"


def _launchd_target_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def _substitute(template_text: str, *, engine_home: Path, home: Path) -> str:
    """Apply the four __PLACEHOLDER__ substitutions used by both templates."""
    out = template_text
    out = out.replace("__USER__", os.environ.get("USER", "user"))
    out = out.replace("__HOME__", str(home))
    out = out.replace("__CC_AUTOPIPE_HOME__", str(engine_home))
    out = out.replace("__PATH__", os.environ.get("PATH", "/usr/bin:/bin"))
    return out


def install_systemd(args: argparse.Namespace) -> int:
    template = _engine_home() / "init" / "cc-autopipe.service.template"
    if not template.exists():
        sys.stderr.write(f"install-systemd: template missing: {template}\n")
        return 1
    target_dir = Path(args.target_dir) if args.target_dir else _systemd_target_dir()
    target = target_dir / "cc-autopipe.service"
    target_dir.mkdir(parents=True, exist_ok=True)
    body = _substitute(
        template.read_text(encoding="utf-8"),
        engine_home=_engine_home(),
        home=Path(args.home or str(Path.home())),
    )
    target.write_text(body, encoding="utf-8")
    print(f"✓ wrote systemd unit: {target}")
    print("  Next steps:")
    print("    systemctl --user daemon-reload")
    print("    systemctl --user enable cc-autopipe.service")
    print("    systemctl --user start cc-autopipe.service")
    print("    journalctl --user -u cc-autopipe -f   # follow logs")
    return 0


def uninstall_systemd(args: argparse.Namespace) -> int:
    target_dir = Path(args.target_dir) if args.target_dir else _systemd_target_dir()
    target = target_dir / "cc-autopipe.service"
    if not target.exists():
        print(f"systemd unit not present at {target} — nothing to uninstall")
        return 0
    try:
        target.unlink()
    except OSError as exc:
        sys.stderr.write(f"uninstall-systemd: could not remove {target}: {exc}\n")
        return 1
    print(f"✓ removed systemd unit: {target}")
    print("  Next steps:")
    print("    systemctl --user stop cc-autopipe.service 2>/dev/null || true")
    print("    systemctl --user disable cc-autopipe.service 2>/dev/null || true")
    print("    systemctl --user daemon-reload")
    return 0


def install_launchd(args: argparse.Namespace) -> int:
    template = _engine_home() / "init" / "com.cc-autopipe.plist.template"
    if not template.exists():
        sys.stderr.write(f"install-launchd: template missing: {template}\n")
        return 1
    target_dir = Path(args.target_dir) if args.target_dir else _launchd_target_dir()
    target = target_dir / "com.cc-autopipe.plist"
    target_dir.mkdir(parents=True, exist_ok=True)
    body = _substitute(
        template.read_text(encoding="utf-8"),
        engine_home=_engine_home(),
        home=Path(args.home or str(Path.home())),
    )
    target.write_text(body, encoding="utf-8")
    print(f"✓ wrote launchd plist: {target}")
    print("  Next steps:")
    print(f"    launchctl load {target}")
    print("    launchctl list | grep cc-autopipe")
    print(
        "    tail -f ~/.cc-autopipe/log/launchd.log   # follow logs (after first run)"
    )
    return 0


def uninstall_launchd(args: argparse.Namespace) -> int:
    target_dir = Path(args.target_dir) if args.target_dir else _launchd_target_dir()
    target = target_dir / "com.cc-autopipe.plist"
    if not target.exists():
        print(f"launchd plist not present at {target} — nothing to uninstall")
        return 0
    try:
        target.unlink()
    except OSError as exc:
        sys.stderr.write(f"uninstall-launchd: could not remove {target}: {exc}\n")
        return 1
    print(f"✓ removed launchd plist: {target}")
    print("  Next steps:")
    print(f"    launchctl unload {target} 2>/dev/null || true")
    return 0


SUBCOMMANDS = {
    "install-systemd": install_systemd,
    "uninstall-systemd": uninstall_systemd,
    "install-launchd": install_launchd,
    "uninstall-launchd": uninstall_launchd,
}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="cc-autopipe service",
        description=(
            "Install/uninstall systemd or launchd units for cc-autopipe. "
            "Pick the subcommand matching your platform."
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in SUBCOMMANDS:
        sp = sub.add_parser(name)
        sp.add_argument(
            "--target-dir",
            default=None,
            help="Override the install location (test escape hatch).",
        )
        sp.add_argument(
            "--home",
            default=None,
            help="Override $HOME used for log paths (test escape hatch).",
        )
    args = parser.parse_args(argv)
    return SUBCOMMANDS[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
