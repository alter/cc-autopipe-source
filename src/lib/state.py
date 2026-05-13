#!/usr/bin/env python3
"""state.py — atomic state.json read/write for cc-autopipe.

Refs: SPEC.md §6.2, §7.1, §8.

Single source of truth for per-project phase, iteration, last verify
result. Atomic writes via tmpfile + rename. Read recovers from corrupted
or partially-written JSON by retrying once, then resetting to a fresh
state.

Also exposes a small CLI used by the bash hooks:

    python3 state.py read <project>
    python3 state.py log-event <project> <event_name> [k=v ...]
    python3 state.py set-session-id <project> <session_id>
    python3 state.py inc-failures <project>
    python3 state.py inc-malformed <project>
    python3 state.py reset-malformed <project>
    python3 state.py update-verify <project> --passed BOOL --score FLOAT --prd-complete BOOL
    python3 state.py set-paused <project> <resume_at_iso> <reason>
    python3 state.py clear-paused <project>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

SCHEMA_VERSION = 7
STATE_FILENAME = "state.json"
PROGRESS_FILENAME = "progress.jsonl"
FAILURES_FILENAME = "failures.jsonl"

# v0.5 → v1.0 schema bump (SPEC-v1.md §3.1). New fields:
#   - detached:                          Optional[dict]  per Stage H
#   - current_phase:                     int             per Stage J (default 1)
#   - phases_completed:                  list[int]       per Stage J (default [])
#   - escalated_next_cycle:              bool            per Stage L (False)
#   - successful_cycles_since_improver:  int             per Stage N (default 0)
#   - improver_due:                      bool            per Stage N (False)
#
# v1.0 → v1.2 schema bump (SPEC-v1.2.md, Bug A + Bug B). New fields:
#   - current_task:            Optional[CurrentTask]  per Bug A (default None)
#   - last_in_progress:        bool                   per Bug B (False)
#   - consecutive_in_progress: int                    per Bug B (0)
#
# v1.3.2 → v1.3.3 schema bump (PROMPT_v1.3.3-hotfix.md). Additive only:
#   - Detached.pipeline_log_path:        Optional[str]   (Group L liveness)
#   - Detached.stale_after_sec:          Optional[int]   (Group L liveness)
#   - State.last_verdict_event_at:       Optional[str]   (Group N gate)
#   - State.last_verdict_task_id:        Optional[str]   (Group N gate)
#
# v1.3.3 → v1.3.4 schema bump (PROMPT_v1.3.4-hotfix.md). Additive only:
#   - State.consecutive_transient_failures: int          (Group R counter)
#   - State.last_transient_at:              Optional[str] (Group R audit ts)
# (PROMPT_v1.3.4 §R2 said 4→5 but v1.3.3 already shipped at 5; we bump
# 5→6 here so old v1.3.3 state files migrate via the same dataclass-
# defaults path used everywhere else.)
#
# v1.3.4 → v1.3.12 schema bump (PROMPT_v1.3.12-hotfix.md, group
# VERIFY-MALFORMED-BACKOFF). Additive only:
#   - State.consecutive_malformed: int                   (default 0)
# Tracks consecutive `verify_malformed` events separately from the
# logic-failure path so a buggy verify.sh (`|| echo 0` instead of
# `|| true`) never burns the auto-escalation budget.
#
# Pre-v3 state files migrate transparently — `read()` fills defaults via
# the dataclass field defaults; `write()` then persists schema_version=
# SCHEMA_VERSION (current).


def _user_home() -> Path:
    """Returns ~/.cc-autopipe, overridable via CC_AUTOPIPE_USER_HOME for tests."""
    override = os.environ.get("CC_AUTOPIPE_USER_HOME")
    if override:
        return Path(override)
    return Path.home() / ".cc-autopipe"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _log(msg: str) -> None:
    print(f"[state.py] {msg}", file=sys.stderr)


@dataclass
class Paused:
    resume_at: str  # ISO 8601 UTC
    reason: str

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Paused":
        return cls(resume_at=str(d["resume_at"]), reason=str(d.get("reason", "")))


@dataclass
class Detached:
    """Long-running operation in flight per SPEC-v1.md §2.1.

    Engine periodically (every check_every_sec) runs check_cmd. Success
    transitions back to ACTIVE; max_wait_sec elapsed transitions to
    FAILED. Operations launch via `cc-autopipe-detach` from inside a
    claude session before nohup-ing the long task.
    """

    reason: str
    started_at: str  # ISO 8601 UTC
    check_cmd: str
    check_every_sec: int
    max_wait_sec: int
    last_check_at: Optional[str] = None
    checks_count: int = 0
    # v1.3.3 Group L liveness check. Both default None → stale detection
    # disabled, behaviour identical to v1.3.2.
    pipeline_log_path: Optional[str] = None
    stale_after_sec: Optional[int] = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Detached":
        stale = d.get("stale_after_sec")
        return cls(
            reason=str(d.get("reason", "")),
            started_at=str(d.get("started_at", "")),
            check_cmd=str(d.get("check_cmd", "")),
            check_every_sec=int(d.get("check_every_sec", 600)),
            max_wait_sec=int(d.get("max_wait_sec", 14400)),
            last_check_at=d.get("last_check_at"),
            checks_count=int(d.get("checks_count", 0)),
            pipeline_log_path=d.get("pipeline_log_path"),
            stale_after_sec=int(stale) if stale is not None else None,
        )


@dataclass
class CurrentTask:
    """The backlog item Claude is actively working on per SPEC-v1.2.md Bug A.

    Mirrors `.cc-autopipe/CURRENT_TASK.md` written by Claude. Stop hook
    reads that file and updates state.json.current_task; SessionStart
    hook reads state.json.current_task and injects a context block at
    the top of the next cycle's prompt.

    `id` matches a `[~]` task in backlog.md. `stage` is free-form text.
    `stages_completed` lets verify.sh do progressive scoring (Bug F).
    `artifact_paths` tells verify.sh where Claude's outputs land.
    """

    id: Optional[str] = None
    started_at: Optional[str] = None
    stage: str = ""
    stages_completed: list[str] = field(default_factory=list)
    artifact_paths: list[str] = field(default_factory=list)
    claude_notes: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CurrentTask":
        # Tolerant of partial dicts written by older clients or by hand.
        stages = d.get("stages_completed") or []
        if not isinstance(stages, list):
            stages = [str(stages)]
        artifacts = d.get("artifact_paths") or []
        if not isinstance(artifacts, list):
            artifacts = [str(artifacts)]
        return cls(
            id=d.get("id"),
            started_at=d.get("started_at"),
            stage=str(d.get("stage", "")),
            stages_completed=[str(x) for x in stages],
            artifact_paths=[str(x) for x in artifacts],
            claude_notes=str(d.get("claude_notes", "")),
        )


@dataclass
class State:
    schema_version: int = SCHEMA_VERSION
    name: str = ""
    phase: str = "active"  # active | paused | done | failed | detached
    iteration: int = 0
    session_id: Optional[str] = None
    last_score: Optional[float] = None
    last_passed: Optional[bool] = None
    prd_complete: bool = False
    consecutive_failures: int = 0
    last_cycle_started_at: Optional[str] = None
    # v1.5.3 ORPHAN-PROMOTION-RESCAN: timestamp of the most recent
    # successfully-completed cycle_end (excluding SIGTERM-interrupted
    # flushes). Used as the mtime cutoff when scanning data/debug for
    # PROMOTION files that were never validated because their parent
    # cycle was killed before post_cycle_delta ran.
    last_cycle_ended_at: Optional[str] = None
    last_progress_at: Optional[str] = None
    threshold: float = 0.85
    paused: Optional[Paused] = None
    detached: Optional[Detached] = None
    current_phase: int = 1
    phases_completed: list[int] = field(default_factory=list)
    escalated_next_cycle: bool = False
    successful_cycles_since_improver: int = 0
    improver_due: bool = False
    # v1.2 additions (Bug A + Bug B). Defaults preserve backward compat
    # for v1 / v2 state files: missing keys → these defaults.
    current_task: Optional[CurrentTask] = None
    last_in_progress: bool = False
    consecutive_in_progress: int = 0
    # v1.3 additions (PROMPT_v1.3-FULL.md). Defaults preserve backward
    # compat: any pre-v3 / pre-v4 state file missing these gets defaults.
    last_observed_stage: Optional[str] = None
    last_activity_at: Optional[str] = None
    recovery_attempts: int = 0
    research_mode_active: bool = False
    research_plan_required: bool = False
    research_plan_target: Optional[str] = None
    research_iterations_this_window: list[str] = field(default_factory=list)
    prd_complete_detected: bool = False
    knowledge_update_pending: bool = False
    knowledge_baseline_mtime: Optional[float] = None
    knowledge_pending_reason: Optional[str] = None
    meta_reflect_pending: bool = False
    meta_reflect_target: Optional[str] = None
    meta_reflect_started_at: Optional[str] = None
    meta_reflect_attempts: int = 0
    # v1.3.3 Group N knowledge gate. Last verdict event tracked here so
    # cc-autopipe-detach can refuse to detach until knowledge.md mtime
    # advances past the verdict timestamp.
    last_verdict_event_at: Optional[str] = None
    last_verdict_task_id: Optional[str] = None
    # v1.3.3 Group L: when a stale-pipeline detection auto-resumes a
    # detached project, the transient reason is stashed here so the
    # next cycle's prompt can tell Claude it was woken up to investigate
    # a silent pipeline death. _build_prompt clears this after one bake.
    last_detach_resume_reason: Optional[str] = None
    # v1.3.4 Group R: transient failure tracking, distinct from
    # consecutive_failures so smart escalation (which targets structural
    # problems) doesn't fire on network blips.
    consecutive_transient_failures: int = 0
    last_transient_at: Optional[str] = None
    # v1.3.7 STUCK-WITH-PROGRESS: backlog `[x]` count snapshotted at
    # cycle_start. Counted again at stuck-check time; a positive delta
    # is filesystem evidence that Claude closed at least one task in
    # the cycle, even when verify.sh rc=1 makes the cycle look stuck.
    # None when no backlog file exists or the cycle hasn't started yet.
    cycle_backlog_x_count_at_start: Optional[int] = None
    # v1.3.12 VERIFY-MALFORMED-BACKOFF: consecutive `verify_malformed`
    # events. Tracked separately from `consecutive_failures` so a buggy
    # verify.sh script (the classic `|| echo 0` double-zero) cannot burn
    # the auto-escalation budget. Reset by a passing verify.
    consecutive_malformed: int = 0
    # v1.5.6 IDLE-INJECT-EXPAND-BACKLOG: timestamp of the last
    # `meta_expand_backlog` injection (per project). Throttles the
    # injector to once per 4h so a defiant agent that ignores the
    # meta-task can't cause spam loops.
    last_meta_expand_at: Optional[str] = None
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "schema_version": self.schema_version,
            "name": self.name,
            "phase": self.phase,
            "iteration": self.iteration,
            "session_id": self.session_id,
            "last_score": self.last_score,
            "last_passed": self.last_passed,
            "prd_complete": self.prd_complete,
            "consecutive_failures": self.consecutive_failures,
            "last_cycle_started_at": self.last_cycle_started_at,
            "last_cycle_ended_at": self.last_cycle_ended_at,
            "last_progress_at": self.last_progress_at,
            "threshold": self.threshold,
            "paused": asdict(self.paused) if self.paused else None,
            "detached": asdict(self.detached) if self.detached else None,
            "current_phase": self.current_phase,
            "phases_completed": list(self.phases_completed),
            "escalated_next_cycle": self.escalated_next_cycle,
            "successful_cycles_since_improver": (self.successful_cycles_since_improver),
            "improver_due": self.improver_due,
            "current_task": asdict(self.current_task) if self.current_task else None,
            "last_in_progress": self.last_in_progress,
            "consecutive_in_progress": self.consecutive_in_progress,
            "last_observed_stage": self.last_observed_stage,
            "last_activity_at": self.last_activity_at,
            "recovery_attempts": self.recovery_attempts,
            "research_mode_active": self.research_mode_active,
            "research_plan_required": self.research_plan_required,
            "research_plan_target": self.research_plan_target,
            "research_iterations_this_window": list(
                self.research_iterations_this_window
            ),
            "prd_complete_detected": self.prd_complete_detected,
            "knowledge_update_pending": self.knowledge_update_pending,
            "knowledge_baseline_mtime": self.knowledge_baseline_mtime,
            "knowledge_pending_reason": self.knowledge_pending_reason,
            "meta_reflect_pending": self.meta_reflect_pending,
            "meta_reflect_target": self.meta_reflect_target,
            "meta_reflect_started_at": self.meta_reflect_started_at,
            "meta_reflect_attempts": self.meta_reflect_attempts,
            "last_verdict_event_at": self.last_verdict_event_at,
            "last_verdict_task_id": self.last_verdict_task_id,
            "last_detach_resume_reason": self.last_detach_resume_reason,
            "consecutive_transient_failures": self.consecutive_transient_failures,
            "last_transient_at": self.last_transient_at,
            "cycle_backlog_x_count_at_start": self.cycle_backlog_x_count_at_start,
            "consecutive_malformed": self.consecutive_malformed,
            "last_meta_expand_at": self.last_meta_expand_at,
        }
        d.update(self.extras)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "State":
        known = {f.name for f in fields(cls)} - {"extras"}
        kwargs: dict[str, Any] = {}
        for k in known:
            if k in d:
                kwargs[k] = d[k]
        if isinstance(kwargs.get("paused"), dict):
            kwargs["paused"] = Paused.from_dict(kwargs["paused"])
        elif kwargs.get("paused") is None:
            kwargs["paused"] = None
        if isinstance(kwargs.get("detached"), dict):
            kwargs["detached"] = Detached.from_dict(kwargs["detached"])
        elif kwargs.get("detached") is None:
            kwargs["detached"] = None
        if isinstance(kwargs.get("current_task"), dict):
            kwargs["current_task"] = CurrentTask.from_dict(kwargs["current_task"])
        elif kwargs.get("current_task") is None:
            kwargs["current_task"] = None
        # Migration: any pre-v3 state file (schema_version 1 or 2) is
        # missing some fields. Dataclass defaults supply them; we force
        # schema_version=SCHEMA_VERSION here so write() persists v3.
        kwargs["schema_version"] = SCHEMA_VERSION
        extras = {k: v for k, v in d.items() if k not in known}
        return cls(extras=extras, **kwargs)

    @classmethod
    def fresh(cls, name: str) -> "State":
        return cls(name=name)


def _state_path(project_path: str | os.PathLike[str]) -> Path:
    return Path(project_path) / ".cc-autopipe" / STATE_FILENAME


def _bak_path(state_path: Path) -> Path:
    return state_path.with_suffix(state_path.suffix + ".bak")


def _try_load_json(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, ValueError, OSError):
        return None


def read(project_path: str | os.PathLike[str]) -> State:
    """Read state.json with v1.3 corruption recovery.

    Order of operations:
      1. Try state.json. If valid → return. (Successful load triggers a
         best-effort copy to state.json.bak.)
      2. If state.json is corrupt → retry once after 0.5s (mid-write
         race protection). If still corrupt → fall through.
      3. Try state.json.bak. If valid → return (with warning log).
      4. Otherwise → return fresh State.

    Returns a freshly-initialised State if both files are missing or
    unrecoverably corrupt.
    """
    path = _state_path(project_path)
    name = Path(project_path).name

    for attempt in (1, 2):
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            s = State.from_dict(data)
            # Best-effort: keep .bak in sync with last successful read.
            try:
                _refresh_bak(path)
            except OSError:
                pass
            return s
        except FileNotFoundError:
            return State.fresh(name)
        except (json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
            if attempt == 1:
                _log(f"state.json read attempt 1 failed: {exc}; retrying")
                time.sleep(0.5)
                continue
            _log(f"state.json unrecoverable at {path}: {exc}; trying .bak")

    # Both attempts on state.json failed → try .bak.
    bak = _bak_path(path)
    bak_data = _try_load_json(bak) if bak.exists() else None
    if isinstance(bak_data, dict):
        try:
            s = State.from_dict(bak_data)
            _log(f"state.json restored from {bak}")
            # Promote .bak back to state.json so subsequent reads succeed.
            try:
                bak.replace(path)
            except OSError:
                pass
            return s
        except (KeyError, TypeError, ValueError) as exc:
            _log(f"state.json.bak unparseable at {bak}: {exc}; resetting")

    return State.fresh(name)


def _refresh_bak(state_path: Path) -> None:
    """Copy current state.json to state.json.bak. Best-effort."""
    if not state_path.exists():
        return
    bak = _bak_path(state_path)
    try:
        # Read+write rather than os.replace to keep the original in place.
        contents = state_path.read_bytes()
        tmp = bak.with_suffix(bak.suffix + f".tmp.{os.getpid()}")
        tmp.write_bytes(contents)
        os.replace(tmp, bak)
    except OSError:
        # Bak refresh is best-effort; never fatal.
        try:
            tmp = bak.with_suffix(bak.suffix + f".tmp.{os.getpid()}")
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def write(project_path: str | os.PathLike[str], state: State) -> None:
    """Atomic write via tmpfile + os.replace, refresh .bak after.

    Single-writer model: orchestrator + hooks coordinate via the
    per-project lock (Stage D). Within that, write() is safe under
    concurrent processes — os.replace is atomic on POSIX.

    v1.3 C3: after the atomic rename succeeds, copy the new state.json
    to state.json.bak so a future corruption can be recovered.
    """
    path = _state_path(project_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".tmp.{os.getpid()}.{int(time.time() * 1000)}")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(state.to_dict(), f, indent=2, sort_keys=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    try:
        _refresh_bak(path)
    except OSError:
        pass


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    """Append one JSON line. Creates parent dirs if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def log_event(
    project_path: str | os.PathLike[str], event: str, **fields_kv: Any
) -> None:
    """Append to per-project progress.jsonl AND ~/.cc-autopipe/log/aggregate.jsonl."""
    ts = _now_iso()
    project = Path(project_path)
    project_record: dict[str, Any] = {"ts": ts, "event": event, **fields_kv}
    aggregate_record: dict[str, Any] = {
        "ts": ts,
        "project": project.name,
        "event": event,
        **fields_kv,
    }
    append_jsonl(
        project / ".cc-autopipe" / "memory" / PROGRESS_FILENAME, project_record
    )
    append_jsonl(_user_home() / "log" / "aggregate.jsonl", aggregate_record)


def log_failure(
    project_path: str | os.PathLike[str], error: str, **fields_kv: Any
) -> None:
    """Append to per-project failures.jsonl."""
    record: dict[str, Any] = {"ts": _now_iso(), "error": error, **fields_kv}
    append_jsonl(
        Path(project_path) / ".cc-autopipe" / "memory" / FAILURES_FILENAME, record
    )


# ---------------------------------------------------------------------------
# Mutators used by hooks (CLI surface).
# ---------------------------------------------------------------------------


def set_session_id(project_path: str | os.PathLike[str], session_id: str) -> None:
    s = read(project_path)
    s.session_id = session_id
    s.last_progress_at = _now_iso()
    write(project_path, s)


def inc_failures(project_path: str | os.PathLike[str]) -> int:
    s = read(project_path)
    s.consecutive_failures += 1
    s.last_progress_at = _now_iso()
    write(project_path, s)
    return s.consecutive_failures


# v1.3.12 VERIFY-MALFORMED-BACKOFF threshold. Three consecutive
# `verify_malformed` events strongly suggest verify.sh is structurally
# broken (the classic `|| echo 0` double-zero bug); a HUMAN_NEEDED.md
# is written so the operator sees a specific fix.
MALFORMED_HUMAN_NEEDED_THRESHOLD = 3


def _write_malformed_human_needed(project_path: Path, count: int) -> None:
    """Write `.cc-autopipe/HUMAN_NEEDED.md` with verify.sh fix guidance.

    Called once `consecutive_malformed >= MALFORMED_HUMAN_NEEDED_THRESHOLD`
    so the operator sees the specific bash bug (`|| echo 0` →
    `|| true`) rather than spending hours triaging Opus auto-escalations.
    The file is NOT auto-deleted — the human must read it, fix verify.sh,
    and reset the malformed counter.
    """
    p = project_path / ".cc-autopipe" / "HUMAN_NEEDED.md"
    ts = _now_iso()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        f"# HUMAN_NEEDED — verify.sh producing invalid JSON ({count} consecutive)\n\n"
        f"Generated: {ts}\n\n"
        "## Symptom\n\n"
        f"`verify_malformed` fired {count} times in a row. "
        "verify.sh is outputting non-JSON, causing the engine to log "
        "failures for every cycle even though Claude's work may be "
        "correct.\n\n"
        "## Most common cause\n\n"
        "```bash\n"
        "# WRONG — grep -c exits rc=1 on zero matches AND prints '0';\n"
        "# '|| echo 0' then prints a second '0' → two lines → invalid JSON\n"
        "UNCHECKED=$(grep -c '^- \\[ \\]' \"$PRD\" || echo 0)\n\n"
        "# CORRECT\n"
        "UNCHECKED=$(grep -c '^- \\[ \\]' \"$PRD\" 2>/dev/null || true)\n"
        "```\n\n"
        "## Fix\n\n"
        "1. Replace every `|| echo 0` in `.cc-autopipe/verify.sh` with `|| true`.\n"
        "2. Run `.cc-autopipe/verify.sh` manually — confirm it outputs valid JSON.\n"
        "3. Delete this file.\n"
        "4. Reset the malformed counter: "
        "`python3 ~/.cc-autopipe/lib/state.py reset-malformed <project-path>`\n",
        encoding="utf-8",
    )


def inc_malformed(project_path: str | os.PathLike[str]) -> int:
    """Increment `consecutive_malformed`. Does NOT touch `consecutive_failures`.

    A `verify_malformed` event is a verify.sh script bug (non-JSON
    output), not a Claude logic failure. Routing it through this
    counter prevents the auto-escalation ladder (Opus + `phase=failed`)
    from firing on a `|| echo 0` typo. After
    `MALFORMED_HUMAN_NEEDED_THRESHOLD` consecutive malformed events
    we write `HUMAN_NEEDED.md` with a specific fix recipe; the human
    must read it and reset the counter.
    """
    s = read(project_path)
    s.consecutive_malformed += 1
    s.last_progress_at = _now_iso()
    write(project_path, s)
    if s.consecutive_malformed >= MALFORMED_HUMAN_NEEDED_THRESHOLD:
        _write_malformed_human_needed(Path(project_path), s.consecutive_malformed)
    return s.consecutive_malformed


def reset_malformed(project_path: str | os.PathLike[str]) -> int:
    """Reset `consecutive_malformed` to 0. Returns the new (always 0).

    Also called automatically by `update_verify` when verify passes; the
    CLI surface is for the human to call after fixing verify.sh.
    """
    s = read(project_path)
    s.consecutive_malformed = 0
    s.last_progress_at = _now_iso()
    write(project_path, s)
    return s.consecutive_malformed


def update_verify(
    project_path: str | os.PathLike[str],
    passed: bool,
    score: float,
    prd_complete: bool,
    in_progress: bool = False,
) -> None:
    """Apply verify result to state.

    SPEC-v1.2.md Bug B: when verify reports `in_progress=True`, the
    cycle is "still cooking" — Claude has work running, but not yet
    verifiable. Engine should NOT count it toward consecutive_failures
    (otherwise long ML training looks like 3+ silent failures and
    auto-escalation kicks in). Instead increment consecutive_in_progress
    so the orchestrator can extend cooldown.

    Mutually exclusive paths:
      - in_progress=True  → bump consecutive_in_progress, leave failures
        counter alone (passed flag is informational).
      - in_progress=False (default) → existing v1.0 semantics:
        passed=True resets consecutive_failures + consecutive_in_progress,
        passed=False increments consecutive_failures and resets
        consecutive_in_progress (a real fail breaks any in-progress
        streak).
    """
    s = read(project_path)
    s.last_passed = passed
    s.last_score = score
    s.prd_complete = prd_complete
    s.last_in_progress = in_progress
    if in_progress:
        s.consecutive_in_progress += 1
    elif passed:
        s.consecutive_failures = 0
        s.consecutive_in_progress = 0
        # v1.3.12 VERIFY-MALFORMED-BACKOFF: a passing verify proves
        # verify.sh is producing valid JSON again, so the malformed
        # streak ends here. A genuine `passed=False` does NOT reset
        # this counter — only well-formed JSON does.
        s.consecutive_malformed = 0
    else:
        s.consecutive_failures += 1
        s.consecutive_in_progress = 0
    s.last_progress_at = _now_iso()
    write(project_path, s)


def set_paused(
    project_path: str | os.PathLike[str], resume_at: str, reason: str
) -> None:
    s = read(project_path)
    s.phase = "paused"
    s.paused = Paused(resume_at=resume_at, reason=reason)
    s.last_progress_at = _now_iso()
    write(project_path, s)


def clear_paused(project_path: str | os.PathLike[str]) -> tuple[bool, str]:
    """v1.5.2 STATE-CLI-CLEAR-PAUSED: symmetric inverse of set_paused.

    Clears the `paused` block and routes phase to `done` when
    `prd_complete=True`, else `active`. Returns (changed, new_phase).
    Idempotent — a project that is already not paused returns
    (False, current_phase) and writes nothing.
    """
    s = read(project_path)
    if s.paused is None:
        return False, s.phase
    s.paused = None
    s.phase = "done" if s.prd_complete else "active"
    s.last_progress_at = _now_iso()
    write(project_path, s)
    log_event(project_path, "paused_cleared", new_phase=s.phase)
    return True, s.phase


def set_detached(
    project_path: str | os.PathLike[str],
    *,
    reason: str,
    check_cmd: str,
    check_every_sec: int,
    max_wait_sec: int,
    pipeline_log_path: Optional[str] = None,
    stale_after_sec: Optional[int] = None,
) -> None:
    """Transition a project to phase=detached with the given check_cmd.

    Called by `cc-autopipe-detach` from inside a claude session before
    nohup-ing a long task. Engine releases the slot until check_cmd
    succeeds (poll cadence: check_every_sec) or max_wait_sec elapses.

    v1.3.3 Group L: optional `pipeline_log_path` + `stale_after_sec`
    enable liveness detection — the engine forces a recovery cycle if
    the pipeline log mtime gap exceeds the threshold while check_cmd
    is still failing. Both default None (liveness disabled).

    v1.3.3 Group N: clears `last_verdict_event_at` / `last_verdict_task_id`
    on success — the knowledge gate fires once per verdict, not on every
    subsequent detach.
    """
    s = read(project_path)
    s.phase = "detached"
    s.detached = Detached(
        reason=reason,
        started_at=_now_iso(),
        check_cmd=check_cmd,
        check_every_sec=int(check_every_sec),
        max_wait_sec=int(max_wait_sec),
        last_check_at=None,
        checks_count=0,
        pipeline_log_path=pipeline_log_path,
        stale_after_sec=int(stale_after_sec) if stale_after_sec is not None else None,
    )
    s.last_verdict_event_at = None
    s.last_verdict_task_id = None
    s.last_progress_at = _now_iso()
    write(project_path, s)


def touch_knowledge_baseline_mtime(project_path: str | os.PathLike[str]) -> None:
    """v1.3.5: arm the knowledge.md sentinel after a leaderboard update.

    Sets `knowledge_baseline_mtime` to the file's current mtime and
    flips `knowledge_update_pending=True` so the SessionStart hook's
    mandatory-update block fires next cycle until knowledge.md mtime
    advances. Defense-in-depth: every validated promotion must be
    followed by a lessons append, regardless of whether Claude
    remembered to do it inline.

    No-op when knowledge.md does not exist — there's nothing to baseline.
    """
    project = Path(project_path)
    knowledge_md = project / ".cc-autopipe" / "knowledge.md"
    if not knowledge_md.exists():
        return
    s = read(project_path)
    s.knowledge_baseline_mtime = knowledge_md.stat().st_mtime
    s.knowledge_update_pending = True
    s.last_progress_at = _now_iso()
    write(project_path, s)


def complete_phase(project_path: str | os.PathLike[str]) -> int:
    """Move current_phase to phases_completed and increment.

    Returns the new current_phase. Used by orchestrator's phase-split
    logic in Stage J. Engine calls this after a phase's items are all
    checked AND verify passes.
    """
    s = read(project_path)
    if s.current_phase not in s.phases_completed:
        s.phases_completed.append(s.current_phase)
    s.current_phase += 1
    # Reset session id so next cycle starts fresh on the new phase.
    s.session_id = None
    s.last_progress_at = _now_iso()
    write(project_path, s)
    return s.current_phase


# ---------------------------------------------------------------------------
# CLI dispatch.
# ---------------------------------------------------------------------------


def _parse_bool(s: str) -> bool:
    v = s.strip().lower()
    if v in {"true", "1", "yes", "y"}:
        return True
    if v in {"false", "0", "no", "n", "null", ""}:
        return False
    raise argparse.ArgumentTypeError(f"invalid bool: {s!r}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="state.py")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_read = sub.add_parser("read", help="Print state.json as JSON to stdout")
    p_read.add_argument("project")

    p_log = sub.add_parser(
        "log-event", help="Append event to progress + aggregate logs"
    )
    p_log.add_argument("project")
    p_log.add_argument("event")
    p_log.add_argument("kv", nargs="*", help='Optional "k=v" pairs')

    p_sid = sub.add_parser("set-session-id")
    p_sid.add_argument("project")
    p_sid.add_argument("session_id")

    p_inc = sub.add_parser("inc-failures")
    p_inc.add_argument("project")

    # v1.3.12 VERIFY-MALFORMED-BACKOFF: separate counter from
    # consecutive_failures so verify.sh script bugs don't drive the
    # auto-escalation ladder.
    p_inc_mal = sub.add_parser("inc-malformed")
    p_inc_mal.add_argument("project")

    p_reset_mal = sub.add_parser("reset-malformed")
    p_reset_mal.add_argument("project")

    p_upd = sub.add_parser("update-verify")
    p_upd.add_argument("project")
    p_upd.add_argument("--passed", required=True, type=_parse_bool)
    p_upd.add_argument("--score", required=True, type=float)
    p_upd.add_argument("--prd-complete", required=True, type=_parse_bool)
    p_upd.add_argument(
        "--in-progress",
        type=_parse_bool,
        default=False,
        help="v1.2 Bug B: cycle is still in progress, do not count as failure.",
    )

    p_paused = sub.add_parser("set-paused")
    p_paused.add_argument("project")
    p_paused.add_argument("resume_at")
    p_paused.add_argument("reason")

    p_clear_paused = sub.add_parser(
        "clear-paused",
        help="Clear paused state. Phase → done if prd_complete else active.",
    )
    p_clear_paused.add_argument("project")

    p_detached = sub.add_parser("set-detached")
    p_detached.add_argument("project")
    p_detached.add_argument("--reason", required=True)
    p_detached.add_argument("--check-cmd", required=True)
    p_detached.add_argument("--check-every", type=int, default=600)
    p_detached.add_argument("--max-wait", type=int, default=14400)
    p_detached.add_argument(
        "--pipeline-log",
        default=None,
        help="absolute path to pipeline log for liveness monitoring (Group L)",
    )
    p_detached.add_argument(
        "--stale-after-sec",
        type=int,
        default=None,
        help="trigger detach_pipeline_stale if log mtime gap exceeds this",
    )

    sub.add_parser("complete-phase").add_argument("project")

    p_rebuild = sub.add_parser(
        "rebuild-leaderboard",
        help=(
            "v1.5.5: regenerate LEADERBOARD.md from every "
            "CAND_*_PROMOTION.md in data/debug/. One-shot operator "
            "recovery after the v1.5.5 CANONICAL_MAP fix. Truncates "
            "the existing file and re-appends from current parser "
            "semantics; prints {scanned, appended, failed} counts."
        ),
    )
    p_rebuild.add_argument("project")

    # v1.5.8 MAY-13-RECOVERY-SCRIPT: operator-driven rollback for the
    # v1.5.7 gate gap. See src/lib/recovery_revert_fake_closures.py for
    # the actual logic; this subparser delegates so the CLI surface
    # stays in one place.
    p_rfc = sub.add_parser(
        "revert-fake-closures",
        help=(
            "v1.5.8: revert backlog [x] rows whose PROMOTION file is "
            "missing or older than --since. Dry-run by default; pass "
            "--apply to mutate. Used once on AI-trade to clean up the "
            "~351 May-13 fake closures the pre-v1.5.8 gate missed."
        ),
    )
    p_rfc.add_argument("project")
    p_rfc.add_argument(
        "since_iso",
        help="ISO timestamp; closures with no fresh PROMOTION after "
             "this point are reverted",
    )
    p_rfc.add_argument(
        "--apply",
        action="store_true",
        help="Actually rewrite backlog.md (omit for dry-run)",
    )

    args = parser.parse_args(argv)

    if args.cmd == "read":
        s = read(args.project)
        json.dump(s.to_dict(), sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    if args.cmd == "log-event":
        kv: dict[str, Any] = {}
        for item in args.kv:
            if "=" not in item:
                _log(f"ignoring malformed kv: {item!r}")
                continue
            k, v = item.split("=", 1)
            kv[k] = v
        log_event(args.project, args.event, **kv)
        return 0

    if args.cmd == "set-session-id":
        set_session_id(args.project, args.session_id)
        return 0

    if args.cmd == "inc-failures":
        n = inc_failures(args.project)
        print(n)
        return 0

    if args.cmd == "inc-malformed":
        n = inc_malformed(args.project)
        print(n)
        return 0

    if args.cmd == "reset-malformed":
        n = reset_malformed(args.project)
        print(n)
        return 0

    if args.cmd == "update-verify":
        update_verify(
            args.project,
            passed=args.passed,
            score=args.score,
            prd_complete=args.prd_complete,
            in_progress=args.in_progress,
        )
        return 0

    if args.cmd == "set-paused":
        set_paused(args.project, args.resume_at, args.reason)
        return 0

    if args.cmd == "clear-paused":
        changed, new_phase = clear_paused(args.project)
        if not changed:
            print(f"already not paused (phase={new_phase})")
        else:
            print(f"unpaused, phase={new_phase}")
        return 0

    if args.cmd == "set-detached":
        set_detached(
            args.project,
            reason=args.reason,
            check_cmd=args.check_cmd,
            check_every_sec=args.check_every,
            max_wait_sec=args.max_wait,
            pipeline_log_path=args.pipeline_log,
            stale_after_sec=args.stale_after_sec,
        )
        return 0

    if args.cmd == "complete-phase":
        new = complete_phase(args.project)
        print(new)
        return 0

    if args.cmd == "rebuild-leaderboard":
        # v1.5.5 LEADERBOARD-REPLAY: scripted contexts call this once
        # post-deploy; no interactive prompt. Print the counts dict
        # plain JSON so an operator can pipe into jq / grep.
        import leaderboard as _lb  # noqa: PLC0415

        counts = _lb.rebuild_from_files(Path(args.project))
        print(json.dumps(counts))
        return 0

    if args.cmd == "revert-fake-closures":
        # v1.5.8 MAY-13-RECOVERY-SCRIPT: delegate to the dedicated
        # module so the logic stays testable in isolation.
        import recovery_revert_fake_closures as _rfc  # noqa: PLC0415

        return _rfc.main(
            [args.project, args.since_iso]
            + (["--apply"] if args.apply else [])
        )

    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
