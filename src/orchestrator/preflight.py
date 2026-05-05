#!/usr/bin/env python3
"""orchestrator.preflight — pre-cycle quota / pause checks.

Includes:
  - _resume_paused_if_due: PAUSED → ACTIVE on resume_at expiry
  - _preflight_quota:      pause project (or all) on 5h/7d quota
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from orchestrator._runtime import _log
from orchestrator.alerts import _notify_tg, _should_send_7d_alert
from orchestrator.prompt import _coerce_yaml_value
import disk as disk_lib  # noqa: E402
import quota as quota_lib  # noqa: E402
import state  # noqa: E402

DISK_CONFIG_DEFAULTS: dict[str, object] = {
    "disk_auto_cleanup": True,
    "disk_min_free_gb": 5.0,
    "disk_keep_checkpoints_per_dir": 3,
}

# Pre-flight thresholds. Deviates from SPEC §9.2 — see OPEN_QUESTIONS.md Q14.
# Engine pauses only when quota is dangerously close to exhaustion; 90% 7d
# was triggering false-positive pauses with 4 days of headroom remaining.
PREFLIGHT_5H_PAUSE = 0.95
PREFLIGHT_5H_WARN = 0.85
PREFLIGHT_7D_PAUSE = 0.95
PREFLIGHT_7D_WARN = 0.90


def _resume_paused_if_due(s: state.State) -> bool:
    """If state is PAUSED with resume_at in the past, transition to ACTIVE.

    Returns True if a transition happened.
    """
    if s.phase != "paused" or s.paused is None:
        return False
    try:
        resume_at = datetime.strptime(
            s.paused.resume_at, "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    if datetime.now(timezone.utc) >= resume_at:
        s.phase = "active"
        s.paused = None
        return True
    return False


def _preflight_quota(project_path: Path, s: state.State) -> str:
    """Pause the project (or all projects) if we're close to a quota limit.

    Returns one of:
      "ok"         — quota fine OR quota.py returned None (caller proceeds)
      "warn_5h"    — between 85% and 95% on 5h, still proceeds
      "warn_7d"    — between 90% and 95% on 7d, still proceeds
      "paused_5h"  — >=95% 5h, project paused until 5h resets
      "paused_7d"  — >=95% 7d, project paused until 7d resets, TG sent
    """
    q = quota_lib.read_cached()
    if q is None:
        return "ok"

    if q.five_hour_pct >= PREFLIGHT_7D_PAUSE and q.seven_day_pct >= PREFLIGHT_7D_PAUSE:
        # Both thresholds tripped — prefer 7d since it's the longer pause
        # and the broader signal (account-wide quota).
        pass

    if q.seven_day_pct >= PREFLIGHT_7D_PAUSE:
        resume_at = q.seven_day_resets_at
        if resume_at is None:
            resume_at = datetime.now(timezone.utc) + timedelta(hours=1)
        s.phase = "paused"
        s.paused = state.Paused(
            resume_at=resume_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            reason="7d_pre_check",
        )
        state.write(project_path, s)
        state.log_event(
            project_path,
            "paused",
            reason="7d_pre_check",
            seven_day_pct=q.seven_day_pct,
            resume_at=s.paused.resume_at,
        )
        if _should_send_7d_alert():
            _notify_tg(
                f"7d quota at {int(q.seven_day_pct * 100)}%, "
                f"all projects pausing until {s.paused.resume_at}"
            )
        return "paused_7d"

    if q.five_hour_pct >= PREFLIGHT_5H_PAUSE:
        resume_at = q.five_hour_resets_at
        if resume_at is None:
            resume_at = datetime.now(timezone.utc) + timedelta(hours=1)
        s.phase = "paused"
        s.paused = state.Paused(
            resume_at=resume_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            reason="5h_pre_check",
        )
        state.write(project_path, s)
        state.log_event(
            project_path,
            "paused",
            reason="5h_pre_check",
            five_hour_pct=q.five_hour_pct,
            resume_at=s.paused.resume_at,
        )
        return "paused_5h"

    if q.seven_day_pct >= PREFLIGHT_7D_WARN:
        _log(
            f"{project_path.name}: 7d quota at "
            f"{int(q.seven_day_pct * 100)}% — warning, proceeding"
        )
        return "warn_7d"

    if q.five_hour_pct >= PREFLIGHT_5H_WARN:
        _log(
            f"{project_path.name}: 5h quota at "
            f"{int(q.five_hour_pct * 100)}% — warning, proceeding"
        )
        return "warn_5h"

    return "ok"


def _read_disk_config(project_path: Path) -> dict[str, object]:
    """Parse top-level disk_* keys from .cc-autopipe/config.yaml.

    Defaults to DISK_CONFIG_DEFAULTS. Tolerates missing file / missing
    keys / malformed YAML scalars (falls back to default for the bad key).
    """
    out = dict(DISK_CONFIG_DEFAULTS)
    cfg = project_path / ".cc-autopipe" / "config.yaml"
    if not cfg.exists():
        return out
    try:
        text = cfg.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        stripped = line.rstrip()
        if stripped.startswith(" ") or ":" not in stripped:
            continue
        key, _, raw = stripped.partition(":")
        key = key.strip()
        if key in DISK_CONFIG_DEFAULTS:
            out[key] = _coerce_yaml_value(raw)
    try:
        out["disk_min_free_gb"] = float(out["disk_min_free_gb"])
    except (TypeError, ValueError):
        out["disk_min_free_gb"] = float(DISK_CONFIG_DEFAULTS["disk_min_free_gb"])
    try:
        out["disk_keep_checkpoints_per_dir"] = int(
            out["disk_keep_checkpoints_per_dir"]
        )
    except (TypeError, ValueError):
        out["disk_keep_checkpoints_per_dir"] = int(
            DISK_CONFIG_DEFAULTS["disk_keep_checkpoints_per_dir"]
        )
    return out


def _preflight_disk(project_path: Path, s: state.State) -> str:
    """v1.3 C2: pre-cycle disk check with auto-cleanup.

    Returns:
      "ok"        — disk fine, or cleanup recovered enough space
      "cleaned"   — was low, ran cleanup, now ok
      "paused"    — still under threshold after cleanup; phase=paused
    """
    cfg = _read_disk_config(project_path)
    min_free = float(cfg.get("disk_min_free_gb") or 5.0)
    probe = disk_lib.check_disk_space(project_path, min_free_gb=min_free)
    if probe["ok"]:
        return "ok"

    if not bool(cfg.get("disk_auto_cleanup", True)):
        from datetime import datetime, timedelta, timezone

        s.phase = "paused"
        s.paused = state.Paused(
            resume_at=(
                datetime.now(timezone.utc) + timedelta(hours=1)
            ).strftime("%Y-%m-%dT%H:%M:%SZ"),
            reason="disk_full",
        )
        state.write(project_path, s)
        state.log_event(
            project_path,
            "paused",
            reason="disk_full",
            free_gb=probe["free_gb"],
        )
        _notify_tg(
            f"[{project_path.name}] disk full "
            f"({probe['free_gb']:.1f} GB free, threshold {min_free} GB) "
            "— project paused"
        )
        return "paused"

    keep = int(cfg.get("disk_keep_checkpoints_per_dir") or 3)
    removed = disk_lib.cleanup_old_checkpoints(project_path, keep_per_dir=keep)
    state.log_event(
        project_path,
        "disk_cleanup",
        removed_count=len(removed),
        free_gb_before=probe["free_gb"],
    )
    _log(
        f"{project_path.name}: disk cleanup removed {len(removed)} "
        f"checkpoints (free was {probe['free_gb']} GB)"
    )

    probe2 = disk_lib.check_disk_space(project_path, min_free_gb=min_free)
    if probe2["ok"]:
        return "cleaned"

    from datetime import datetime, timedelta, timezone

    s.phase = "paused"
    s.paused = state.Paused(
        resume_at=(
            datetime.now(timezone.utc) + timedelta(hours=1)
        ).strftime("%Y-%m-%dT%H:%M:%SZ"),
        reason="disk_full",
    )
    state.write(project_path, s)
    state.log_event(
        project_path,
        "paused",
        reason="disk_full",
        free_gb=probe2["free_gb"],
        cleanup_removed=len(removed),
    )
    _notify_tg(
        f"[{project_path.name}] disk_full {probe2['free_gb']:.1f} GB free "
        f"after cleanup of {len(removed)} ckpts — project paused"
    )
    return "paused"
