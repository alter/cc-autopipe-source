#!/usr/bin/env python3
"""leaderboard — persistent ranking of validated promotions.

Refs: PROMPT_v1.3.5-hotfix.md GROUP LEADERBOARD-WRITER.

Public surface:
    - append_entry(project, task_id, metrics)     -- add new promotion
    - read_top_n(project, n=20)                   -- return ranked list
    - elo_after_match(rating_a, rating_b, score_a, k=32) -> (a', b')

Files:
    - <project>/data/debug/LEADERBOARD.md         -- human-readable
    - <project>/data/debug/.leaderboard_elo.json  -- machine-readable

LEADERBOARD.md format::

    # Promotion Leaderboard

    Last updated: <ISO timestamp>

    | Rank | task_id | composite | sum_fixed | regime_parity | max_DD | DM_p | DSR | ELO | promotion_date |
    |------|---------|-----------|-----------|---------------|--------|------|-----|-----|----------------|
    | 1 | vec_long_synth_v3 | 0.823 | +245.5% | 0.18 | -8.2% | 0.003 | 1.12 | 1620 | 2026-07-15 |
    ...

After append_entry the engine touches knowledge.md sentinel via
state.touch_knowledge_baseline_mtime (defense-in-depth: every
promotion must be followed by a lessons append next cycle).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

K_FACTOR = 32
INITIAL_ELO = 1500
TOP_N_RETAINED = 20

LEADERBOARD_HEADER = (
    "| Rank | task_id | composite | sum_fixed | regime_parity | max_DD | "
    "DM_p | DSR | ELO | promotion_date |"
)
LEADERBOARD_SEP = (
    "|------|---------|-----------|-----------|---------------|--------|"
    "------|-----|-----|----------------|"
)


def _composite(metrics: dict[str, Any]) -> float:
    """0.5*sum_fixed_norm + 0.3*(1-regime_parity) + 0.2*(max_dd / -100).

    sum_fixed normalized by /1000 (so +500% -> 0.5).
    regime_parity in [0, 1], smaller=better when read as std/mean, so
        we use 1 - parity as the contribution. Missing → 0.
    max_dd: less negative is better, so /-100 flips sign so a -8.2% DD
        contributes +0.082.
    Missing metrics are treated as 0 (penalty for incomplete reports).
    """
    sf = (metrics.get("sum_fixed") or 0) / 1000.0
    rp_raw = metrics.get("regime_parity")
    rp = (1.0 - rp_raw) if rp_raw is not None else 0.0
    dd = (metrics.get("max_dd") or 0) / -100.0
    return round(0.5 * sf + 0.3 * rp + 0.2 * dd, 4)


def elo_after_match(
    rating_a: int, rating_b: int, score_a: float, k: int = K_FACTOR
) -> tuple[int, int]:
    """ELO update for a head-to-head match. score_a in [0, 1]."""
    expected_a = 1 / (1 + 10 ** ((rating_b - rating_a) / 400))
    expected_b = 1 - expected_a
    score_b = 1 - score_a
    new_a = round(rating_a + k * (score_a - expected_a))
    new_b = round(rating_b + k * (score_b - expected_b))
    return new_a, new_b


def _elo_state_path(project: Path) -> Path:
    return project / "data" / "debug" / ".leaderboard_elo.json"


def _load_elo_state(project: Path) -> dict[str, Any]:
    p = _elo_state_path(project)
    if not p.exists():
        return {"ratings": {}, "history": []}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"ratings": {}, "history": []}


def _save_elo_state(project: Path, data: dict[str, Any]) -> None:
    p = _elo_state_path(project)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def _leaderboard_path(project: Path) -> Path:
    return project / "data" / "debug" / "LEADERBOARD.md"


def _parse_pct(s: str) -> float | None:
    s = s.strip().rstrip("%").lstrip("+")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_float(s: str) -> float | None:
    s = s.strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _read_existing_entries(project: Path) -> list[dict[str, Any]]:
    """Best-effort parse of LEADERBOARD.md table rows. Returns [] if
    missing or malformed. Round-trippable with _write_leaderboard_md."""
    p = _leaderboard_path(project)
    if not p.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return []
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        if "---" in s:
            continue
        if "task_id" in s.lower() or "Rank" in s:
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) < 10:
            continue
        try:
            rows.append(
                {
                    "task_id": cells[1],
                    "composite": _parse_float(cells[2]),
                    "sum_fixed": _parse_pct(cells[3]),
                    "regime_parity": _parse_float(cells[4]),
                    "max_dd": _parse_pct(cells[5]),
                    "dm_p_value": _parse_float(cells[6]),
                    "dsr": _parse_float(cells[7]),
                    "elo": int(cells[8]) if cells[8] else INITIAL_ELO,
                    "promotion_date": cells[9],
                }
            )
        except (ValueError, IndexError):
            continue
    return rows


def _fmt_pct(v: Any) -> str:
    if v is None:
        return ""
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.2f}%"


def _fmt_float(v: Any, ndigits: int = 3) -> str:
    if v is None:
        return ""
    return f"{v:.{ndigits}f}"


def _fmt_composite(v: Any) -> str:
    if v is None:
        return ""
    return f"{v:.4f}"


def _write_leaderboard_md(
    path: Path, entries: list[dict[str, Any]], header: str
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        header,
        "",
        f"Last updated: {datetime.now(timezone.utc).isoformat()}",
        "",
        LEADERBOARD_HEADER,
        LEADERBOARD_SEP,
    ]
    for i, e in enumerate(entries, 1):
        lines.append(
            f"| {i} | {e['task_id']} | "
            f"{_fmt_composite(e.get('composite'))} | "
            f"{_fmt_pct(e.get('sum_fixed'))} | "
            f"{_fmt_float(e.get('regime_parity'))} | "
            f"{_fmt_pct(e.get('max_dd'))} | "
            f"{_fmt_float(e.get('dm_p_value'), 4)} | "
            f"{_fmt_float(e.get('dsr'))} | "
            f"{e.get('elo', INITIAL_ELO)} | "
            f"{e.get('promotion_date', '')} |"
        )
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _rank_of(entries: list[dict[str, Any]], task_id: str) -> int | None:
    for i, e in enumerate(entries, 1):
        if e["task_id"] == task_id:
            return i
    return None


def append_entry(
    project: Path, task_id: str, metrics: dict[str, Any]
) -> None:
    """Add new entry, run ELO matchups vs current top-3, re-render
    LEADERBOARD.md, archive entries beyond TOP_N_RETAINED, and reset
    knowledge.md sentinel so the next cycle demands a lessons append.

    Idempotent on task_id: a re-promotion replaces the prior entry
    with the newer metrics (the ELO history retains both events).
    """
    import state as _state  # noqa: PLC0415

    elo_data = _load_elo_state(project)
    new_rating = elo_data["ratings"].get(task_id, INITIAL_ELO)

    composite = _composite(metrics)

    entries = _read_existing_entries(project)
    entries = [e for e in entries if e["task_id"] != task_id]
    new_entry: dict[str, Any] = {
        "task_id": task_id,
        "composite": composite,
        "sum_fixed": metrics.get("sum_fixed"),
        "regime_parity": metrics.get("regime_parity"),
        "max_dd": metrics.get("max_dd"),
        "dm_p_value": metrics.get("dm_p_value"),
        "dsr": metrics.get("dsr"),
        "elo": new_rating,
        "promotion_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    }
    entries.append(new_entry)

    # Sort by composite descending (None → 0).
    entries.sort(key=lambda e: (e.get("composite") or 0), reverse=True)

    # ELO matchups: new entry vs current top-3 (excluding itself).
    top_others = [e for e in entries[:4] if e["task_id"] != task_id][:3]
    for opp in top_others:
        opp_rating = elo_data["ratings"].get(opp["task_id"], INITIAL_ELO)
        c_new = new_entry.get("composite") or 0
        c_opp = opp.get("composite") or 0
        if c_new > c_opp:
            score = 1.0
        elif c_new < c_opp:
            score = 0.0
        else:
            score = 0.5
        new_rating, opp_new = elo_after_match(new_rating, opp_rating, score)
        elo_data["ratings"][opp["task_id"]] = opp_new
        elo_data["history"].append(
            {
                "date": new_entry["promotion_date"],
                "a": task_id,
                "b": opp["task_id"],
                "score_a": score,
            }
        )
    elo_data["ratings"][task_id] = new_rating
    new_entry["elo"] = new_rating
    _save_elo_state(project, elo_data)

    # Refresh ELO column from the latest ratings for every existing
    # entry — the new match may have nudged a top-3 opponent's rating.
    for e in entries:
        e["elo"] = elo_data["ratings"].get(e["task_id"], INITIAL_ELO)
    # Composite is the primary sort; ELO is informational.

    if len(entries) > TOP_N_RETAINED:
        archive_path = (
            project
            / "data"
            / "debug"
            / "ARCHIVE"
            / f"LEADERBOARD_{new_entry['promotion_date']}.md"
        )
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        archived = entries[TOP_N_RETAINED:]
        _write_leaderboard_md(
            archive_path, archived, header="# Archived Leaderboard Entries"
        )
        entries = entries[:TOP_N_RETAINED]

    _write_leaderboard_md(
        _leaderboard_path(project),
        entries,
        header="# Promotion Leaderboard",
    )

    # Reset knowledge.md sentinel — engine will require lessons-update
    # next cycle (defense-in-depth: every promotion = one new lesson).
    try:
        _state.touch_knowledge_baseline_mtime(project)
    except Exception:  # noqa: BLE001 — best-effort, never blocks
        pass

    _state.log_event(
        project,
        "leaderboard_updated",
        task_id=task_id,
        rank=_rank_of(entries, task_id),
    )


def read_top_n(project: Path, n: int = TOP_N_RETAINED) -> list[dict[str, Any]]:
    return _read_existing_entries(project)[:n]
