"""claude_settings — backup/restore of ~/.claude/settings.json hooks.

Roman's global Claude Code hooks (PreToolUse + UserPromptSubmit) at
~/.claude/settings.json conflict with cc-autopipe-managed sessions:
they block routine bash commands and inject compliance reminders that
interfere with engine-driven Claude runs.

This module provides:
  - disable_global_hooks_with_backup() — called by `cc-autopipe start`
    to back up the file (idempotent) and rewrite it without the
    `hooks` key.
  - restore_global_hooks_from_backup() — called by `cc-autopipe stop`
    to copy the backup back over settings.json and delete the backup.

Other top-level keys (permissions, skipDangerousModePermissionPrompt,
etc.) are preserved untouched. Only `hooks` is removed.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

SETTINGS_RELPATH = ".claude/settings.json"
BACKUP_RELPATH = ".claude/settings.json.cc-autopipe-bak"


def _resolve_paths(home: Path | None) -> tuple[Path, Path]:
    base = home if home is not None else Path.home()
    return base / SETTINGS_RELPATH, base / BACKUP_RELPATH


def disable_global_hooks_with_backup(home: Path | None = None) -> dict:
    """Back up ~/.claude/settings.json and rewrite without `hooks`.

    Behaviour:
      - Missing settings.json → {'action': 'no_settings', ...}
      - Malformed JSON → {'action': 'parse_error', ...} (no modification)
      - Has `hooks` → backup (if not already present), rewrite without
        `hooks` → {'action': 'backed_up', 'original_had_hooks': True}
      - No `hooks` → still create backup if missing (idempotent for the
        round-trip restore), rewrite same content →
        {'action': 'no_hooks_to_disable', 'original_had_hooks': False}

    Idempotency: if the backup already exists, do not overwrite — that
    means a previous start did not clean up. The OLD backup is the
    pristine copy worth preserving. We still strip `hooks` from the
    current file so the engine run is clean.
    """
    settings_path, backup_path = _resolve_paths(home)

    if not settings_path.exists():
        return {
            "action": "no_settings",
            "backup_path": None,
            "original_had_hooks": False,
        }

    try:
        raw = settings_path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        print(
            f"claude_settings: could not parse {settings_path}: {exc!r}",
            file=sys.stderr,
        )
        return {
            "action": "parse_error",
            "backup_path": None,
            "original_had_hooks": False,
        }

    if not isinstance(data, dict):
        # settings.json is a JSON value but not an object — treat like
        # parse_error: don't risk mutating a shape we don't understand.
        return {
            "action": "parse_error",
            "backup_path": None,
            "original_had_hooks": False,
        }

    had_hooks = "hooks" in data

    if not backup_path.exists():
        try:
            shutil.copy2(settings_path, backup_path)
        except OSError as exc:
            print(
                f"claude_settings: could not back up {settings_path}: {exc!r}",
                file=sys.stderr,
            )
            return {
                "action": "parse_error",
                "backup_path": None,
                "original_had_hooks": had_hooks,
            }
    else:
        # v1.3 F3: surface stale-bypass-backup warning when an old backup
        # is still on disk from a previous unclean shutdown. >24h is
        # suspicious — likely the engine has been crashing / never
        # cleanly stopping. Don't overwrite (the OLD backup is the real
        # operator settings).
        try:
            import time

            age_sec = time.time() - backup_path.stat().st_mtime
            if age_sec > 24 * 3600:
                print(
                    f"claude_settings: stale bypass backup detected at "
                    f"{backup_path} ({int(age_sec / 3600)}h old) — "
                    "preserving as-is",
                    file=sys.stderr,
                )
        except OSError:
            pass

    cleaned = {k: v for k, v in data.items() if k != "hooks"}
    try:
        settings_path.write_text(json.dumps(cleaned, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        print(
            f"claude_settings: could not write {settings_path}: {exc!r}",
            file=sys.stderr,
        )
        return {
            "action": "parse_error",
            "backup_path": str(backup_path) if backup_path.exists() else None,
            "original_had_hooks": had_hooks,
        }

    return {
        "action": "backed_up" if had_hooks else "no_hooks_to_disable",
        "backup_path": str(backup_path),
        "original_had_hooks": had_hooks,
    }


def restore_global_hooks_from_backup(home: Path | None = None) -> dict:
    """Restore settings.json from the backup, then delete the backup.

    Behaviour:
      - No backup → {'action': 'no_backup', 'restored_from': None}
      - Backup exists → copy → settings.json, unlink backup,
        {'action': 'restored', 'restored_from': str(backup_path)}
      - On copy error → leave backup in place,
        {'action': 'restore_failed', ...}
    """
    settings_path, backup_path = _resolve_paths(home)

    if not backup_path.exists():
        return {"action": "no_backup", "restored_from": None}

    try:
        shutil.copy2(backup_path, settings_path)
    except OSError as exc:
        print(
            f"claude_settings: could not restore {settings_path} "
            f"from {backup_path}: {exc!r}",
            file=sys.stderr,
        )
        return {
            "action": "restore_failed",
            "restored_from": str(backup_path),
        }

    try:
        backup_path.unlink()
    except OSError as exc:
        # Restore succeeded; backup deletion failed. Not fatal — next
        # start's idempotency rule (preserve existing backup) means a
        # stale backup just sits there. Surface a warning.
        print(
            f"claude_settings: restored {settings_path} but could not "
            f"delete backup {backup_path}: {exc!r}",
            file=sys.stderr,
        )

    return {"action": "restored", "restored_from": str(backup_path)}
