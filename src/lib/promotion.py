#!/usr/bin/env python3
"""promotion — parse and validate PROMOTION.md reports + on_promotion_success hook.

Refs: PROMPT_v1.3.5-hotfix.md GROUP PROMOTION-PARSER.

PROMOTION.md v2.0 required structure (per AI-trade rules.md
"PROMOTION report format v2.0"):

    - Verdict line: '**Verdict: PROMOTED**' or '**Verdict: REJECTED**'
    - § Long-only verification
    - § Regime-stratified PnL
    - § Statistical significance
    - § Walk-forward stability
    - § No-lookahead audit
    - Plus all v1.2 sections (Acceptance, Evidence, etc.) — not enforced
      by this module.

Public surface:
    - promotion_path(project, task_id)        -> Path
    - parse_verdict(promotion_path)           -> 'PROMOTED' | 'REJECTED' | None
    - validate_v2_sections(promotion_path)    -> tuple[bool, list[str]]
    - parse_metrics(promotion_path)           -> dict
    - on_promotion_success(project, item, metrics) -> None
    - quarantine_invalid(project, item, missing) -> None
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

REQUIRED_V2_SECTIONS = [
    "Long-only verification",
    "Regime-stratified PnL",
    "Statistical significance",
    "Walk-forward stability",
    "No-lookahead audit",
]

VERDICT_RE = re.compile(
    r"^\*\*Verdict:\s*(PROMOTED|REJECTED)\*\*\s*$",
    re.MULTILINE | re.IGNORECASE,
)


def _promotion_basename(task_id: str) -> str:
    """AI-trade convention: PROMOTION files drop 'vec_long_' / 'vec_' prefix.
    'vec_long_only_baseline' -> 'long_only_baseline'
    'vec_meta'               -> 'meta'
    """
    base = task_id
    for pfx in ('vec_long_', 'vec_'):
        if base.startswith(pfx):
            base = base[len(pfx):]
            break
    return base


def promotion_path(project: Path, task_id: str) -> Path:
    return project / 'data' / 'debug' / f'CAND_{_promotion_basename(task_id)}_PROMOTION.md'


def parse_verdict(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = VERDICT_RE.search(text)
    if not m:
        return None
    return m.group(1).upper()


def validate_v2_sections(path: Path) -> tuple[bool, list[str]]:
    """Returns (all_present, missing_list).

    Each section is matched as `## | ### | ####` heading containing the
    section name (case-insensitive). The test is intentionally loose:
    `### Regime-stratified PnL (5 regimes)` and `## REGIME-STRATIFIED PNL`
    both pass.
    """
    if not path.exists():
        return False, list(REQUIRED_V2_SECTIONS)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False, list(REQUIRED_V2_SECTIONS)
    missing: list[str] = []
    for sec in REQUIRED_V2_SECTIONS:
        pat = re.compile(
            r"^#{2,4}\s+.*" + re.escape(sec),
            re.MULTILINE | re.IGNORECASE,
        )
        if not pat.search(text):
            missing.append(sec)
    return (not missing), missing


def parse_metrics(path: Path) -> dict[str, Any]:
    """Best-effort numeric extraction from PROMOTION.md.

    Missing fields default to None. Engine consumers should treat None
    as 'unknown', NOT 'zero' — composite scoring penalises None as 0
    contribution but never raises on missing keys.
    """
    out: dict[str, Any] = {
        "sum_fixed": None,
        "regime_parity": None,
        "max_dd": None,
        "dm_p_value": None,
        "dsr": None,
    }
    if not path.exists():
        return out
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out

    m = re.search(
        r"sum[_\s]?fixed\s*[:=]?\s*([+-]?\d+(?:\.\d+)?)\s*%",
        text,
        re.IGNORECASE,
    )
    if m:
        out["sum_fixed"] = float(m.group(1))

    m = re.search(
        r"regime[_\s]?parity\s*[:=]?\s*(\d+(?:\.\d+)?)",
        text,
        re.IGNORECASE,
    )
    if m:
        out["regime_parity"] = float(m.group(1))

    m = re.search(
        r"max[_\s]?DD\s*[:=]?\s*([+-]?\d+(?:\.\d+)?)\s*%",
        text,
        re.IGNORECASE,
    )
    if m:
        out["max_dd"] = float(m.group(1))

    m = re.search(
        r"DM[_\s]?p(?:[_\s]?value)?\s*[:=]?\s*(\d+(?:\.\d+)?)",
        text,
        re.IGNORECASE,
    )
    if m:
        out["dm_p_value"] = float(m.group(1))

    m = re.search(r"\bDSR\b\s*[:=]?\s*(\d+(?:\.\d+)?)", text, re.IGNORECASE)
    if m:
        out["dsr"] = float(m.group(1))

    return out


def _ablation_children_for(parent_id: str, parent_priority: int) -> list[str]:
    """Generate 5 backlog lines for ablation children.

    parent_priority is the integer priority from BacklogItem (0=P0,
    1=P1, ...). Children get parent priority + 1, capped at P3 (the
    lowest tier). PROMPT specifies "P3 caps to P3" so very-low-priority
    parents don't spawn even-lower-priority children that would
    starve.
    """
    new_pri = min(parent_priority + 1, 3)
    pri = f"P{new_pri}"
    return [
        f"- [ ] [implement] [{pri}] {parent_id}_ab_drop_top — "
        f"Drop top SHAP feature group from parent {parent_id}, retrain, "
        f"measure delta. Acceptance: sum_fixed delta documented; if "
        f"≥+5pp → flag as new candidate parent.",
        f"- [ ] [implement] [{pri}] {parent_id}_ab_loss — "
        f"Swap loss function (CE↔focal or focal↔PnL surrogate) on parent "
        f"{parent_id} architecture. Acceptance: AUC + sum_fixed comparison.",
        f"- [ ] [implement] [{pri}] {parent_id}_ab_seq — "
        f"Halve or double sequence length on parent {parent_id}. "
        f"Acceptance: receptive-field sensitivity documented.",
        f"- [ ] [implement] [{pri}] {parent_id}_ab_seed — "
        f"Same arch as parent {parent_id}, different random seed. "
        f"Acceptance: variance estimate vs vec_long_baseline_seed_var "
        f"noise floor.",
        f"- [ ] [implement] [{pri}] {parent_id}_ab_eth — "
        f"Cross-asset replication of parent {parent_id} on ETH. "
        f"Acceptance: parity with BTC reference; document any "
        f"regime-specific divergence.",
    ]


def _resolve_backlog_path(project: Path) -> Path | None:
    """Return the project's backlog.md path or None when no backlog
    file exists. Mirrors orchestrator.research's resolution: prefers
    <project>/backlog.md, falls back to .cc-autopipe/backlog.md."""
    primary = project / "backlog.md"
    if primary.exists():
        return primary
    fallback = project / ".cc-autopipe" / "backlog.md"
    if fallback.exists():
        return fallback
    return None


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def on_promotion_success(project: Path, item: Any, metrics: dict[str, Any]) -> None:
    """Atomic backlog mutation: append 5 ablation children. Fire LB hook.

    `item` may be a backlog.BacklogItem or any object exposing `id` and
    `priority` attributes. The leaderboard hook (lib.leaderboard) is
    imported lazily so this module remains usable in environments that
    lack the leaderboard module (tests, partial deployments).
    """
    import state as _state  # noqa: PLC0415

    target = _resolve_backlog_path(project)
    if target is None:
        _state.log_event(
            project,
            "promotion_children_skipped",
            task_id=getattr(item, "id", ""),
            reason="backlog_missing",
        )
    else:
        children = _ablation_children_for(
            getattr(item, "id"), int(getattr(item, "priority", 1))
        )
        text = target.read_text(encoding="utf-8")
        # Insert children at end of body, but BEFORE a "## Done" section
        # if one exists. This keeps the backlog sorted as
        # active → ablations → done.
        insertion_marker = "## Done"
        if insertion_marker in text:
            head, _, tail = text.partition(insertion_marker)
            new_text = (
                head.rstrip()
                + "\n\n"
                + "\n".join(children)
                + "\n\n"
                + insertion_marker
                + tail
            )
        else:
            new_text = text.rstrip() + "\n\n" + "\n".join(children) + "\n"
        _atomic_write(target, new_text)
        _state.log_event(
            project,
            "ablation_children_spawned",
            parent=getattr(item, "id"),
            count=len(children),
        )

    # Leaderboard hook is best-effort — a missing module must not
    # prevent the promotion path from completing.
    try:
        import leaderboard as _lb  # noqa: PLC0415

        _lb.append_entry(project, getattr(item, "id"), metrics)
    except Exception as exc:  # noqa: BLE001
        _state.log_event(
            project,
            "leaderboard_append_skipped",
            task_id=getattr(item, "id", ""),
            reason=repr(exc),
        )


def quarantine_invalid(
    project: Path, item: Any, missing: list[str]
) -> None:
    """Revert backlog task to [~] and write quarantine marker.

    Engine treats this like a meta_reflect-pending state: the next
    cycle's prompt will surface the missing sections via the standard
    backlog top-N injection. Operator (or claude on the next turn)
    completes the missing sections, then re-marks [x].
    """
    import state as _state  # noqa: PLC0415

    quar = project / "data" / "debug" / f"UNVALIDATED_PROMOTION_{getattr(item, 'id')}.md"
    quar.parent.mkdir(parents=True, exist_ok=True)
    body = (
        f"# Unvalidated promotion: {getattr(item, 'id')}\n\n"
        "Verdict was PROMOTED but the following v2.0 PROMOTION sections "
        "are missing:\n\n"
        + "\n".join(f"- {s}" for s in missing)
        + "\n\nEngine reverted backlog mark to [~]. Add missing sections "
        f"to `data/debug/CAND_{getattr(item, 'id')}_PROMOTION.md`, then "
        "re-mark [x].\n"
    )
    quar.write_text(body, encoding="utf-8")

    target = _resolve_backlog_path(project)
    if target is not None:
        text = target.read_text(encoding="utf-8")
        # Match the specific task line: `- [x] [implement] [P?] <id> ...`
        # with a word-boundary guard so we don't match a longer id.
        pattern = re.compile(
            r"^(\s*-\s*)\[x\](\s*\[implement\]\s*\[\w+\]\s*"
            + re.escape(getattr(item, "id"))
            + r"\b)",
            re.MULTILINE,
        )
        new_text, n = pattern.subn(r"\1[~]\2", text, count=1)
        if n:
            _atomic_write(target, new_text)

    _state.log_event(
        project,
        "promotion_invalid",
        task_id=getattr(item, "id"),
        missing_sections=",".join(missing),
    )
