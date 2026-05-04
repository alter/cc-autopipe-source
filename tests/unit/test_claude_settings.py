"""Unit tests for src/lib/claude_settings.py — global Claude hook
backup/restore around `cc-autopipe start` / `cc-autopipe stop`.

Each test passes a tmp_path as `home`, so the real ~/.claude/settings.json
is never touched even when run on the operator's box.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_LIB = REPO_ROOT / "src" / "lib"

sys.path.insert(0, str(SRC_LIB))

import claude_settings  # noqa: E402

SAMPLE_HOOKS = {
    "PreToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo block"}]}
    ],
    "UserPromptSubmit": [
        {"hooks": [{"type": "command", "command": "echo remind"}]}
    ],
}


def _seed(home: Path, payload: dict) -> Path:
    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return settings


def _backup_path(home: Path) -> Path:
    return home / ".claude" / "settings.json.cc-autopipe-bak"


# --- disable_global_hooks_with_backup --------------------------------------


def test_disable_no_settings_file(tmp_path: Path) -> None:
    result = claude_settings.disable_global_hooks_with_backup(home=tmp_path)
    assert result["action"] == "no_settings"
    assert result["backup_path"] is None
    assert result["original_had_hooks"] is False
    assert not _backup_path(tmp_path).exists()


def test_disable_with_hooks_present(tmp_path: Path) -> None:
    payload = {
        "permissions": {"allow": ["Bash"]},
        "hooks": SAMPLE_HOOKS,
        "skipDangerousModePermissionPrompt": True,
    }
    settings = _seed(tmp_path, payload)
    result = claude_settings.disable_global_hooks_with_backup(home=tmp_path)

    assert result["action"] == "backed_up"
    assert result["original_had_hooks"] is True
    assert result["backup_path"] == str(_backup_path(tmp_path))

    # Backup preserved original byte-for-byte.
    backed_up = json.loads(_backup_path(tmp_path).read_text(encoding="utf-8"))
    assert backed_up == payload

    # settings.json no longer has `hooks`, but other keys preserved.
    rewritten = json.loads(settings.read_text(encoding="utf-8"))
    assert "hooks" not in rewritten
    assert rewritten["permissions"] == {"allow": ["Bash"]}
    assert rewritten["skipDangerousModePermissionPrompt"] is True


def test_disable_no_hooks_key(tmp_path: Path) -> None:
    """No `hooks` key → still backup (idempotency for restore round-trip)."""
    payload = {"permissions": {"allow": ["Bash"]}}
    settings = _seed(tmp_path, payload)
    result = claude_settings.disable_global_hooks_with_backup(home=tmp_path)

    assert result["action"] == "no_hooks_to_disable"
    assert result["original_had_hooks"] is False
    assert _backup_path(tmp_path).exists()
    # Backup matches the original.
    backed_up = json.loads(_backup_path(tmp_path).read_text(encoding="utf-8"))
    assert backed_up == payload
    # File content unchanged structurally.
    rewritten = json.loads(settings.read_text(encoding="utf-8"))
    assert rewritten == payload


def test_disable_idempotent_backup_not_overwritten(tmp_path: Path) -> None:
    """Pre-existing backup must NOT be overwritten — it represents an
    earlier pristine snapshot from an unclean previous run."""
    pristine = {"permissions": {"allow": ["Bash"]}, "hooks": SAMPLE_HOOKS}
    _seed(tmp_path, pristine)
    bak = _backup_path(tmp_path)
    bak.parent.mkdir(parents=True, exist_ok=True)
    bak.write_text(json.dumps(pristine, indent=2) + "\n", encoding="utf-8")

    # Now mutate the live settings.json with new content (simulating a
    # second start after operator-edit between runs).
    new_payload = {
        "permissions": {"allow": ["Read"]},
        "hooks": {"PreToolUse": [{"hooks": []}]},
    }
    _seed(tmp_path, new_payload)

    result = claude_settings.disable_global_hooks_with_backup(home=tmp_path)
    assert result["action"] == "backed_up"
    # Backup still equals the FIRST snapshot (not overwritten).
    backed_up = json.loads(bak.read_text(encoding="utf-8"))
    assert backed_up == pristine

    # Live settings.json was still cleaned of hooks.
    settings = tmp_path / ".claude" / "settings.json"
    rewritten = json.loads(settings.read_text(encoding="utf-8"))
    assert "hooks" not in rewritten
    assert rewritten["permissions"] == {"allow": ["Read"]}


def test_disable_malformed_json(tmp_path: Path) -> None:
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    raw = "{not: valid json,,,"
    settings.write_text(raw, encoding="utf-8")

    result = claude_settings.disable_global_hooks_with_backup(home=tmp_path)
    assert result["action"] == "parse_error"
    # Did NOT modify the file.
    assert settings.read_text(encoding="utf-8") == raw
    # Did NOT create a backup.
    assert not _backup_path(tmp_path).exists()


def test_disable_non_object_json(tmp_path: Path) -> None:
    """settings.json that is valid JSON but not an object — refuse."""
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text("[1,2,3]", encoding="utf-8")

    result = claude_settings.disable_global_hooks_with_backup(home=tmp_path)
    assert result["action"] == "parse_error"
    assert settings.read_text(encoding="utf-8") == "[1,2,3]"
    assert not _backup_path(tmp_path).exists()


def test_disable_preserves_unknown_keys(tmp_path: Path) -> None:
    """Forward-compat: unknown future keys must survive the rewrite."""
    payload = {
        "hooks": SAMPLE_HOOKS,
        "futureFeatureFlag": {"some": "value"},
        "permissions": {"allow": []},
    }
    settings = _seed(tmp_path, payload)
    claude_settings.disable_global_hooks_with_backup(home=tmp_path)
    rewritten = json.loads(settings.read_text(encoding="utf-8"))
    assert "hooks" not in rewritten
    assert rewritten["futureFeatureFlag"] == {"some": "value"}
    assert rewritten["permissions"] == {"allow": []}


# --- restore_global_hooks_from_backup -------------------------------------


def test_restore_no_backup(tmp_path: Path) -> None:
    result = claude_settings.restore_global_hooks_from_backup(home=tmp_path)
    assert result["action"] == "no_backup"
    assert result["restored_from"] is None


def test_restore_when_backup_exists(tmp_path: Path) -> None:
    payload = {"permissions": {"allow": ["Bash"]}, "hooks": SAMPLE_HOOKS}
    _seed(tmp_path, payload)
    # Disable creates the backup.
    claude_settings.disable_global_hooks_with_backup(home=tmp_path)
    bak = _backup_path(tmp_path)
    assert bak.exists()

    result = claude_settings.restore_global_hooks_from_backup(home=tmp_path)
    assert result["action"] == "restored"
    assert result["restored_from"] == str(bak)
    # Backup deleted.
    assert not bak.exists()
    # settings.json restored to original payload.
    settings = tmp_path / ".claude" / "settings.json"
    restored = json.loads(settings.read_text(encoding="utf-8"))
    assert restored == payload


def test_round_trip_preserves_bytes(tmp_path: Path) -> None:
    """disable → restore → settings.json byte-identical to original."""
    payload = {
        "permissions": {"allow": ["Bash", "Read"], "deny": ["Edit"]},
        "skipDangerousModePermissionPrompt": True,
        "hooks": SAMPLE_HOOKS,
    }
    settings = _seed(tmp_path, payload)
    original_bytes = settings.read_bytes()

    claude_settings.disable_global_hooks_with_backup(home=tmp_path)
    claude_settings.restore_global_hooks_from_backup(home=tmp_path)

    assert settings.read_bytes() == original_bytes
    assert not _backup_path(tmp_path).exists()


def test_restore_after_no_settings_disable_is_noop(tmp_path: Path) -> None:
    """disable saw no settings file → no backup → restore is a noop."""
    disable_result = claude_settings.disable_global_hooks_with_backup(home=tmp_path)
    assert disable_result["action"] == "no_settings"
    restore_result = claude_settings.restore_global_hooks_from_backup(home=tmp_path)
    assert restore_result["action"] == "no_backup"


def test_restore_after_no_hooks_disable_recreates_original(tmp_path: Path) -> None:
    """No-hooks payload still produces a backup, so restore returns to it."""
    payload = {"permissions": {"allow": ["Bash"]}}
    settings = _seed(tmp_path, payload)
    original_bytes = settings.read_bytes()

    claude_settings.disable_global_hooks_with_backup(home=tmp_path)
    # Mutate the live file to simulate a session that touched it.
    settings.write_text("{}\n", encoding="utf-8")

    result = claude_settings.restore_global_hooks_from_backup(home=tmp_path)
    assert result["action"] == "restored"
    assert settings.read_bytes() == original_bytes


def test_disable_then_disable_idempotent_state(tmp_path: Path) -> None:
    """Two disable calls back-to-back leave the system in a sane state:
    backup is the original, live file has no `hooks`, restore brings
    back the original."""
    payload = {"permissions": {"allow": ["Bash"]}, "hooks": SAMPLE_HOOKS}
    settings = _seed(tmp_path, payload)
    original_bytes = settings.read_bytes()

    claude_settings.disable_global_hooks_with_backup(home=tmp_path)
    # Second disable: backup must NOT change; live file already has no
    # `hooks`, so it stays clean.
    second = claude_settings.disable_global_hooks_with_backup(home=tmp_path)
    assert second["action"] == "no_hooks_to_disable"
    assert second["original_had_hooks"] is False

    claude_settings.restore_global_hooks_from_backup(home=tmp_path)
    assert settings.read_bytes() == original_bytes


def test_disable_writes_indent_2(tmp_path: Path) -> None:
    """Output formatting: indent=2 per spec; trailing newline included."""
    payload = {"hooks": SAMPLE_HOOKS, "permissions": {"allow": ["Bash"]}}
    settings = _seed(tmp_path, payload)
    claude_settings.disable_global_hooks_with_backup(home=tmp_path)
    text = settings.read_text(encoding="utf-8")
    assert text.endswith("\n")
    # `indent=2` produces 2-space indented JSON. Smoke-check by looking
    # for a typical 2-space-indented line.
    assert '\n  "permissions"' in text
