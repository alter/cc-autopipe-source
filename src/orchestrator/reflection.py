#!/usr/bin/env python3
"""orchestrator.reflection — META_REFLECT mechanism for verify-pattern
failures.

Refs: PROMPT_v1.3-FULL.md GROUP H.

When 3+ consecutive verify_failed entries appear on the same task+stage,
the engine writes a META_REFLECT_<task>_<stage>_<ts>.md file describing
the failure pattern + relevant findings/knowledge excerpts, and demands
Claude pick one of four decisions: continue / modify / skip / defer.

The decision lands in META_DECISION_<task>_<stage>_<ts>.md (same dir),
which the engine reads on the next cycle to apply the action.

Public API:
    write_meta_reflect(...)            -> Path
    read_meta_decision(...)            -> dict | None
    apply_meta_decision(...)           -> None
    build_meta_reflect_block(...)      -> str  (used by SessionStart)
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from orchestrator._runtime import _log, _now_iso
import findings as findings_lib  # noqa: E402
import knowledge as knowledge_lib  # noqa: E402
import state  # noqa: E402

DECISION_RE = re.compile(r"^decision:\s*(\w+)\s*$", re.IGNORECASE)
REASON_RE = re.compile(r"^reason:\s*(.*?)\s*$", re.IGNORECASE | re.DOTALL)
NEW_APPROACH_RE = re.compile(r"^new_approach:\s*(.*?)\s*$", re.IGNORECASE | re.DOTALL)


def _safe_token(s: str) -> str:
    """Sanitise to a filename-safe token (alnum, _, -)."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", s).strip("_") or "x"


def _ts_compact(ts: str) -> str:
    return ts.replace(":", "").replace("-", "")


def write_meta_reflect(
    project_dir: Path,
    task_id: str,
    stage: str,
    failures: list[dict[str, Any]],
    findings_excerpt: str = "",
    knowledge_excerpt: str = "",
    attempt: int = 1,
    ts: str | None = None,
) -> Path:
    """Write META_REFLECT_<task>_<stage>_<ts>.md and return its Path."""
    project_dir = Path(project_dir)
    ts = ts or _now_iso()
    target_dir = project_dir / ".cc-autopipe" / "meta_reflect"
    target_dir.mkdir(parents=True, exist_ok=True)
    name = f"META_REFLECT_{_safe_token(task_id)}_{_safe_token(stage)}_{_ts_compact(ts)}.md"
    path = target_dir / name

    failure_lines = []
    for f in failures or []:
        err = f.get("error", "?")
        ts_f = f.get("ts", "")
        details = f.get("details") or f.get("reason") or ""
        failure_lines.append(f"- {ts_f} | {err} | {details}")
    failures_block = "\n".join(failure_lines) if failure_lines else "- (none recorded)"

    body = (
        f"# Meta-reflection: {task_id} stage {stage}\n\n"
        f"**Triggered:** {ts}\n"
        f"**Attempt:** {attempt}\n"
        f"**Failure pattern:** {len(failures or [])} consecutive verify_failed "
        f"on this task+stage\n\n"
        f"## Recent failures (last {len(failures or [])})\n"
        f"{failures_block}\n\n"
        f"## Findings on this task (from findings_index.md)\n"
        f"{findings_excerpt or '(no matching findings)'}\n\n"
        f"## Relevant knowledge (from knowledge.md)\n"
        f"{knowledge_excerpt or '(none)'}\n\n"
        "## MANDATORY ANALYSIS\n\n"
        "Helicopter view. Before any other action, write\n"
        f"`META_DECISION_{_safe_token(task_id)}_{_safe_token(stage)}_<ts>.md`\n"
        "in this same directory with one of these decisions:\n\n"
        "### Option A: continue (different approach)\n"
        "Task is correct, approach is wrong. Update CURRENT_TASK.md with\n"
        "a NEW approach (different architecture, different params,\n"
        "different data slice). Explain in META_DECISION what changed and\n"
        "why this should fail differently.\n\n"
        "### Option B: modify (refine task)\n"
        "Task as written is too broad / too narrow / wrong scope. Update\n"
        "the backlog entry text and CURRENT_TASK.md with a tighter task.\n"
        "Engine resumes with new task.\n\n"
        "### Option C: skip (won't fix)\n"
        "Task is structurally unresolvable in this project. Mark\n"
        "[~won't-fix] in backlog with reason. Document so future research\n"
        "mode doesn't re-propose it.\n\n"
        "### Option D: defer (block on something)\n"
        "Task needs an external prerequisite that doesn't exist yet. Park\n"
        "[~deferred] with a clear unblocker. Move on.\n\n"
        "## META_DECISION format\n\n"
        "```\n"
        "decision: <continue|modify|skip|defer>\n"
        "reason: <one paragraph, why this decision over the others>\n"
        "new_approach: <only if continue/modify — describe what's different>\n"
        "```\n\n"
        "End your turn after writing META_DECISION. Engine will read it\n"
        "next cycle and act.\n"
    )
    path.write_text(body, encoding="utf-8")
    return path


def read_meta_decision(
    project_dir: Path, target_md_path: str | Path
) -> dict[str, Any] | None:
    """Look for META_DECISION_<task>_<stage>_*.md in same dir as target.

    Returns {decision, reason, new_approach, path} or None.
    Multiple matches → pick the newest by mtime.
    """
    project_dir = Path(project_dir)
    target = Path(target_md_path)
    if not target.parent.exists():
        return None
    # The META_REFLECT name is META_REFLECT_<task>_<stage>_<ts>.md.
    # Match decisions on the same task+stage prefix (any ts).
    name = target.name
    if not name.startswith("META_REFLECT_"):
        return None
    rest = name[len("META_REFLECT_") :]
    # Strip trailing _<ts>.md to extract task_stage prefix.
    parts = rest.rsplit("_", 1)
    if len(parts) != 2:
        return None
    task_stage = parts[0]
    pattern = f"META_DECISION_{task_stage}_*.md"

    matches = sorted(
        target.parent.glob(pattern),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not matches:
        return None
    chosen = matches[0]
    try:
        text = chosen.read_text(encoding="utf-8")
    except OSError:
        return None
    decision = None
    reason = ""
    new_approach = ""
    for line in text.splitlines():
        m = DECISION_RE.match(line)
        if m:
            decision = m.group(1).strip().lower()
            continue
        m2 = REASON_RE.match(line)
        if m2 and not reason:
            reason = m2.group(1).strip()
            continue
        m3 = NEW_APPROACH_RE.match(line)
        if m3 and not new_approach:
            new_approach = m3.group(1).strip()
    if decision not in {"continue", "modify", "skip", "defer"}:
        return None
    return {
        "decision": decision,
        "reason": reason,
        "new_approach": new_approach,
        "path": str(chosen),
    }


def _backlog_path(project_dir: Path) -> Path | None:
    candidates = [
        project_dir / "backlog.md",
        project_dir / ".cc-autopipe" / "backlog.md",
    ]
    return next((p for p in candidates if p.exists()), None)


def _mark_backlog(project_dir: Path, task_id: str, marker: str) -> bool:
    """Replace the leading `- [ ]`/`- [~]` marker on lines containing the
    task_id with `- [<marker>]`. Returns True if any line was modified.
    """
    path = _backlog_path(project_dir)
    if path is None:
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    out_lines: list[str] = []
    changed = False
    for line in text.splitlines():
        stripped = line.lstrip()
        if (
            (stripped.startswith("- [ ]") or stripped.startswith("- [~]"))
            and task_id in line
        ):
            # Preserve any leading whitespace.
            indent = line[: len(line) - len(stripped)]
            after_marker = stripped[5:]  # skip `- [X]`
            new_line = f"{indent}- [{marker}]{after_marker}"
            out_lines.append(new_line)
            changed = True
        else:
            out_lines.append(line)
    if changed:
        try:
            path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
        except OSError:
            return False
    return changed


def apply_meta_decision(
    project_dir: Path, decision: dict[str, Any], task_id: str
) -> None:
    """Apply the decision according to PROMPT_v1.3-FULL.md H2:

      continue / modify → no-op (Claude updated CURRENT_TASK.md
                                 with new approach)
      skip              → mark backlog item [~won't-fix],
                          clear current_task in state
      defer             → mark backlog item [~deferred],
                          clear current_task, log reason
    """
    project_dir = Path(project_dir)
    d = (decision or {}).get("decision", "")
    if d in ("continue", "modify"):
        return  # caller resumes normal cycle
    if d == "skip":
        _mark_backlog(project_dir, task_id, "~won't-fix")
        s = state.read(project_dir)
        s.current_task = None
        state.write(project_dir, s)
        state.log_event(
            project_dir,
            "meta_decision_applied",
            decision="skip",
            task_id=task_id,
            reason=decision.get("reason", ""),
        )
        return
    if d == "defer":
        _mark_backlog(project_dir, task_id, "~deferred")
        s = state.read(project_dir)
        s.current_task = None
        state.write(project_dir, s)
        state.log_event(
            project_dir,
            "meta_decision_applied",
            decision="defer",
            task_id=task_id,
            reason=decision.get("reason", ""),
        )
        return


def trigger_meta_reflect(
    project_path: Path | str,
    s: state.State,
    failures: list[dict[str, Any]],
) -> tuple[str, Path | None]:
    """Decide whether to write a META_REFLECT for this verify-pattern fail.

    Returns (action, path) where action is one of:
      "triggered"   — META_REFLECT written, state updated
      "fallback"    — already attempted twice; caller falls back to HUMAN_NEEDED
      "skipped"     — no current_task or stage to anchor reflection on
    """
    project_path = Path(project_path)
    if s.current_task is None or not s.current_task.id:
        return "skipped", None
    task_id = s.current_task.id
    stage = s.current_task.stage or "unspecified"

    if s.meta_reflect_attempts >= 2:
        return "fallback", None

    findings_excerpt = ""
    try:
        items = findings_lib.read_findings_for_task(project_path, task_id, n=5)
        findings_excerpt = "\n".join(
            f"- {it['ts']} | {it['stage']}: {it.get('notes', '')}"
            for it in items
        ) or "(no findings recorded for this task)"
    except Exception:  # noqa: BLE001
        findings_excerpt = "(error reading findings)"

    knowledge_excerpt = ""
    try:
        knowledge_excerpt = (
            knowledge_lib.read_relevant_excerpt(project_path, task_id) or ""
        )
    except Exception:  # noqa: BLE001
        knowledge_excerpt = ""

    target = write_meta_reflect(
        project_path,
        task_id,
        stage,
        failures=failures,
        findings_excerpt=findings_excerpt,
        knowledge_excerpt=knowledge_excerpt,
        attempt=s.meta_reflect_attempts + 1,
    )

    s.meta_reflect_pending = True
    s.meta_reflect_target = str(target)
    s.meta_reflect_started_at = _now_iso()
    s.meta_reflect_attempts += 1
    s.consecutive_failures = 0  # reset so next cycle isn't immediately re-failed
    state.write(project_path, s)
    state.log_event(
        project_path,
        "meta_reflect_triggered",
        target=str(target),
        attempt=s.meta_reflect_attempts,
        task_id=task_id,
        stage=stage,
    )
    _log(
        f"{project_path.name}: meta_reflect_triggered "
        f"(attempt {s.meta_reflect_attempts}, target {target.name})"
    )
    return "triggered", target


def detect_and_apply_decision(project_path: Path, s: state.State) -> bool:
    """Post-cycle: if META_DECISION exists for the pending reflect, apply
    it and clear state. Returns True iff a decision was applied.
    """
    if not s.meta_reflect_pending or not s.meta_reflect_target:
        return False
    decision = read_meta_decision(project_path, s.meta_reflect_target)
    if decision is None:
        return False
    task_id = (s.current_task.id if s.current_task else "") or ""
    apply_meta_decision(project_path, decision, task_id)
    # Re-read in case apply_meta_decision wrote.
    s2 = state.read(project_path)
    s2.meta_reflect_pending = False
    s2.meta_reflect_target = None
    s2.meta_reflect_started_at = None
    s2.meta_reflect_attempts = 0
    state.write(project_path, s2)
    state.log_event(
        project_path,
        "meta_decision_processed",
        decision=decision["decision"],
        path=decision["path"],
    )
    return True


def build_meta_reflect_block(project_path: Path) -> str:
    """v1.3 H4: mandatory block injected at SessionStart when reflection
    is pending and decision is not yet on disk.
    """
    try:
        s = state.read(project_path)
    except Exception:  # noqa: BLE001
        return ""
    if not s.meta_reflect_pending:
        return ""
    target = s.meta_reflect_target or ""
    decision = read_meta_decision(project_path, target) if target else None
    if decision is not None:
        return (
            "=== Meta-reflection decision detected ===\n"
            f"Decision: {decision['decision']}. Engine will apply on this cycle.\n"
            "==="
        )
    return "\n".join(
        [
            "=== MANDATORY META-REFLECTION ===",
            "",
            "You triggered a meta-reflection on a previous cycle. Read the",
            "file below BEFORE doing anything else. Do not start any other",
            "work until you have written META_DECISION.",
            "",
            f"File to read: {target}",
            "Expected output: META_DECISION_<task>_<stage>_<ts>.md in the",
            "same directory.",
            "",
            "This is not optional. Engine keeps re-injecting this block",
            "until META_DECISION is written.",
            "===",
        ]
    )
