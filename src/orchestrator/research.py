#!/usr/bin/env python3
"""orchestrator.research — D1 PRD-complete detection + D2 research mode
+ anti-duplication enforcement.

Refs: PROMPT_v1.3-FULL.md GROUP D.

Public surface:
  - detect_prd_complete(project_path)        D1
  - activate_research_mode(project_path, s)  D2
  - check_quota_gate()                       D2 quota cap
  - validate_research_plan(project_path, s, cycle_started_iso)  D3
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from orchestrator._runtime import _log, _now_iso, _parse_iso_utc
import quota as quota_lib  # noqa: E402
import state  # noqa: E402

QUOTA_GATE_PCT = 0.70
RESEARCH_WINDOW_DAYS = 7
MAX_RESEARCH_ITERS_PER_WINDOW = 3


def _is_open_task_line(line: str) -> bool:
    """A backlog line that counts as open: starts with `- [ ]` (note the
    space inside brackets — `[x]` and `[~]` and `[!]` are all closed/
    in-progress markers and don't gate PRD completion)."""
    stripped = line.lstrip()
    return stripped.startswith("- [ ]")


def detect_prd_complete(project_path: Path) -> bool:
    """Return True iff project's backlog.md has zero `- [ ]` open lines.

    Looks at <project>/backlog.md first (canonical location) then
    <project>/.cc-autopipe/backlog.md (fallback). Missing backlog → False
    (we can't infer completion).
    """
    project_path = Path(project_path)
    candidates = [
        project_path / "backlog.md",
        project_path / ".cc-autopipe" / "backlog.md",
    ]
    backlog = next((p for p in candidates if p.exists()), None)
    if backlog is None:
        return False
    try:
        text = backlog.read_text(encoding="utf-8")
    except OSError:
        return False
    if not text.strip():
        return False
    return not any(_is_open_task_line(line) for line in text.splitlines())


def _quota_seven_day_pct() -> float | None:
    try:
        q = quota_lib.read_cached()
    except Exception:  # noqa: BLE001
        return None
    if q is None:
        return None
    try:
        return float(q.seven_day_pct)
    except (AttributeError, TypeError, ValueError):
        return None


def check_quota_gate() -> bool:
    """Return True iff research mode may activate. Quota gate suspends
    research at >70% 7d so we don't burn the remaining budget on
    speculative candidates."""
    pct = _quota_seven_day_pct()
    if pct is None:
        return True  # unknown quota → permissive (mock-claude/test envs)
    return pct < QUOTA_GATE_PCT


def _prune_iteration_window(s: state.State) -> list[str]:
    """Drop research-iteration timestamps older than RESEARCH_WINDOW_DAYS.

    Returns the pruned list (same instance modified in place is fine,
    but returned for clarity).
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=RESEARCH_WINDOW_DAYS)
    pruned: list[str] = []
    for ts in s.research_iterations_this_window:
        parsed = _parse_iso_utc(ts)
        if parsed is None:
            continue
        if parsed >= cutoff:
            pruned.append(ts)
    s.research_iterations_this_window = pruned
    return pruned


def _research_plan_target_path(project_path: Path, ts: str) -> Path:
    safe_ts = ts.replace(":", "").replace("-", "")
    return project_path / "data" / "debug" / f"RESEARCH_PLAN_{safe_ts}.md"


def activate_research_mode(project_path: Path, s: state.State) -> str:
    """Decide whether to activate research mode this cycle.

    Returns one of:
      "active"           — flags set, plan target written into state
      "suspended_quota"  — quota >70%, research deferred
      "capped"           — already 3 iterations in 7d window
    """
    iterations = _prune_iteration_window(s)
    if len(iterations) >= MAX_RESEARCH_ITERS_PER_WINDOW:
        state.write(project_path, s)
        state.log_event(
            project_path,
            "research_mode_capped",
            iterations_in_window=len(iterations),
        )
        return "capped"

    if not check_quota_gate():
        state.write(project_path, s)
        state.log_event(
            project_path,
            "research_mode_suspended_quota",
            seven_day_pct=_quota_seven_day_pct(),
        )
        return "suspended_quota"

    ts = _now_iso()
    target = _research_plan_target_path(project_path, ts)
    s.research_mode_active = True
    s.research_plan_required = True
    s.research_plan_target = str(target)
    s.research_iterations_this_window = list(iterations) + [ts]
    state.write(project_path, s)
    state.log_event(
        project_path,
        "research_mode_active",
        plan_target=str(target),
        iterations_in_window=len(s.research_iterations_this_window),
    )
    _log(
        f"{project_path.name}: research mode active "
        f"(iter {len(s.research_iterations_this_window)}, "
        f"plan target {target.name})"
    )
    return "active"


def maybe_activate_after_cycle(project_path: Path, s: state.State) -> str | None:
    """Post-cycle hook: if backlog is now empty (`prd_complete_detected`
    becomes True) AND research mode isn't already active, run the
    activation gate. Returns the activate_research_mode result, or None
    if gate didn't apply.
    """
    just_completed = False
    if detect_prd_complete(project_path):
        if not s.prd_complete_detected:
            just_completed = True
        s.prd_complete_detected = True
        state.write(project_path, s)
        if just_completed:
            state.log_event(project_path, "prd_complete")
            _log(f"{project_path.name}: prd_complete detected")
    else:
        if s.prd_complete_detected:
            # Backlog gained items again (e.g. research mode added some).
            s.prd_complete_detected = False
            state.write(project_path, s)
        return None

    if s.research_mode_active:
        return None
    return activate_research_mode(project_path, s)


def _list_open_backlog_lines(backlog_path: Path) -> list[tuple[int, str]]:
    """Return (line_idx, line) for every open `- [ ]` entry."""
    out: list[tuple[int, str]] = []
    try:
        text = backlog_path.read_text(encoding="utf-8")
    except OSError:
        return out
    for i, line in enumerate(text.splitlines()):
        if _is_open_task_line(line):
            out.append((i, line))
    return out


def _backlog_path(project_path: Path) -> Path | None:
    candidates = [
        project_path / "backlog.md",
        project_path / ".cc-autopipe" / "backlog.md",
    ]
    return next((p for p in candidates if p.exists()), None)


def validate_research_plan(
    project_path: Path,
    s: state.State,
    cycle_started_iso: str | None,
    pre_open_lines: list[str] | None = None,
) -> str:
    """v1.3 D3: enforce that any new backlog entries are accompanied by
    a RESEARCH_PLAN_<ts>.md.

    Caller passes `pre_open_lines` — the list of `- [ ]` lines observed
    BEFORE the cycle ran. Lines present after the cycle but absent from
    pre_open_lines are "new". If new lines exist AND
    research_plan_required is True AND no RESEARCH_PLAN_*.md matching
    `s.research_plan_target` exists → quarantine the new lines to
    UNVALIDATED_BACKLOG_<ts>.md and remove them from backlog.md.

    If a plan file exists matching the target → clear
    research_plan_required (kept research_mode_active until backlog
    gains entries — which signals Claude has filed the plan).

    Returns:
      "ok"          — nothing to do (no plan required, no new lines)
      "filed"       — plan was filed; flags cleared
      "violation"   — new lines quarantined
      "no_plan_required" — backlog mutated but plan not required
    """
    if not s.research_plan_required:
        return "no_plan_required"

    target_str = s.research_plan_target or ""
    target_exists = bool(target_str) and Path(target_str).exists()
    if target_exists:
        s.research_plan_required = False
        state.write(project_path, s)
        state.log_event(
            project_path,
            "research_plan_filed",
            target=target_str,
        )
        _log(f"{project_path.name}: research plan filed at {target_str}")
        return "filed"

    backlog = _backlog_path(project_path)
    if backlog is None:
        return "ok"

    current_open = [line for _, line in _list_open_backlog_lines(backlog)]
    pre_set = set(pre_open_lines or [])
    new_lines = [ln for ln in current_open if ln not in pre_set]

    if not new_lines:
        return "ok"

    # Plan not yet filed but Claude added backlog entries → quarantine.
    quar_ts = (cycle_started_iso or _now_iso()).replace(":", "").replace("-", "")
    quar = (
        project_path
        / ".cc-autopipe"
        / f"UNVALIDATED_BACKLOG_{quar_ts}.md"
    )
    quar.parent.mkdir(parents=True, exist_ok=True)
    body = (
        "# Unvalidated backlog additions\n\n"
        "Engine quarantined the entries below because they were added\n"
        "during research mode without a RESEARCH_PLAN_*.md filed first.\n\n"
        f"Expected plan path: `{target_str}`\n\n"
        "After writing the plan, move the surviving entries back into\n"
        "`backlog.md` manually, or wait for the next cycle if Claude\n"
        "files the plan and re-adds them.\n\n"
        "## Quarantined entries\n\n"
    ) + "\n".join(new_lines) + "\n"
    quar.write_text(body, encoding="utf-8")

    # Strip the new lines from backlog.md.
    try:
        text = backlog.read_text(encoding="utf-8")
        kept_lines = [
            ln for ln in text.splitlines() if ln not in set(new_lines)
        ]
        backlog.write_text("\n".join(kept_lines) + "\n", encoding="utf-8")
    except OSError:
        pass

    state.log_event(
        project_path,
        "research_plan_violation",
        quarantined_count=len(new_lines),
        quarantine_path=str(quar),
    )
    _log(
        f"{project_path.name}: research_plan_violation — quarantined "
        f"{len(new_lines)} backlog entries to {quar.name}"
    )
    return "violation"


def build_research_mode_block(project_path: Path) -> str:
    """v1.3 D2: mandatory injection block for research mode.

    Caller (session_start_helper) decides whether to call this — it
    inspects state.research_mode_active. If False, does not inject.
    """
    try:
        s = state.read(project_path)
    except Exception:  # noqa: BLE001 — hook contract
        return ""
    if not s.research_mode_active:
        return ""

    target = s.research_plan_target or "data/debug/RESEARCH_PLAN_<ts>.md"
    return "\n".join(
        [
            "=== RESEARCH MODE ACTIVE ===",
            "",
            "PRD complete. All open tasks resolved (rejected or accepted).",
            "Roman is offline.",
            "",
            "Before adding ANY new candidate to backlog.md, write the",
            "research plan:",
            "",
            f"  {target}",
            "",
            "Required structure:",
            "",
            "## Exhausted directions (read findings_index.md to fill this)",
            "",
            "For each cluster of past attempts:",
            "- Cluster name (e.g. \"transformer-family on primary 8-feature\")",
            "- Past task_ids in this cluster: <vec_meta, vec_tbm, vec_patchtst>",
            "- Verdict pattern: <e.g. all REJECTED with similar AUC ~0.53>",
            "- Why this cluster is exhausted: <one sentence>",
            "",
            "## New vectors",
            "",
            "For EACH new vector you propose:",
            "",
            "### vec_<name>",
            "- **Differs from <past_id>** because: <STRUCTURAL reason —",
            "  different paradigm, different data, different objective. NOT",
            "  cosmetic differences like 'larger model' or 'more features').",
            "- **Hypothesis:** <what we expect to be true>",
            "- **Falsification:** <what would prove the hypothesis wrong>",
            "- **Cost estimate:** <approximate cycles + quota>",
            "",
            "## Self-check before adding to backlog",
            "",
            "Read your plan back. For each new vector ask: \"If <past_failure>",
            "failed, does this differ STRUCTURALLY or just COSMETICALLY?\"",
            "Cosmetic differences re-fail. Drop those vectors.",
            "",
            "## Then add survivors to backlog",
            "",
            "After RESEARCH_PLAN.md is written and self-checked, add the",
            "surviving vectors to backlog.md as `- [ ] [implement] [P1]",
            "vec_<name> — ...`. Also write `data/debug/HYPO_<name>.md`",
            "per vector. End your turn.",
            "",
            "Engine validates: backlog mutations without RESEARCH_PLAN_*.md",
            "are quarantined to UNVALIDATED_BACKLOG_<ts>.md and the engine",
            "re-injects this block until the plan is filed.",
            "",
            "Quota cap: research mode is gated. If 7d quota > 70%, research",
            "mode is suspended. Limit: max 3 research-mode iterations per",
            "7d window.",
            "===",
        ]
    )
