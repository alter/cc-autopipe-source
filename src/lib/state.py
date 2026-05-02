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
    python3 state.py update-verify <project> --passed BOOL --score FLOAT --prd-complete BOOL
    python3 state.py set-paused <project> <resume_at_iso> <reason>
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

SCHEMA_VERSION = 2
STATE_FILENAME = "state.json"
PROGRESS_FILENAME = "progress.jsonl"
FAILURES_FILENAME = "failures.jsonl"

# v0.5 → v1.0 schema bump (SPEC-v1.md §3.1). New fields:
#   - detached:               Optional[dict]  per Stage H
#   - current_phase:          int             per Stage J (default 1)
#   - phases_completed:       list[int]       per Stage J (default [])
#   - escalated_next_cycle:   bool            per Stage L (default False)
# v1 state files migrate transparently — `read()` fills defaults via the
# dataclass field defaults; `write()` then persists schema_version=2.


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

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Detached":
        return cls(
            reason=str(d.get("reason", "")),
            started_at=str(d.get("started_at", "")),
            check_cmd=str(d.get("check_cmd", "")),
            check_every_sec=int(d.get("check_every_sec", 600)),
            max_wait_sec=int(d.get("max_wait_sec", 14400)),
            last_check_at=d.get("last_check_at"),
            checks_count=int(d.get("checks_count", 0)),
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
    last_progress_at: Optional[str] = None
    threshold: float = 0.85
    paused: Optional[Paused] = None
    detached: Optional[Detached] = None
    current_phase: int = 1
    phases_completed: list[int] = field(default_factory=list)
    escalated_next_cycle: bool = False
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
            "last_progress_at": self.last_progress_at,
            "threshold": self.threshold,
            "paused": asdict(self.paused) if self.paused else None,
            "detached": asdict(self.detached) if self.detached else None,
            "current_phase": self.current_phase,
            "phases_completed": list(self.phases_completed),
            "escalated_next_cycle": self.escalated_next_cycle,
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
        # v1 → v2 migration: a v1 state file has no current_phase /
        # phases_completed / detached. Dataclass defaults handle the
        # missing fields; schema_version is bumped on next write().
        kwargs["schema_version"] = SCHEMA_VERSION
        extras = {k: v for k, v in d.items() if k not in known}
        return cls(extras=extras, **kwargs)

    @classmethod
    def fresh(cls, name: str) -> "State":
        return cls(name=name)


def _state_path(project_path: str | os.PathLike[str]) -> Path:
    return Path(project_path) / ".cc-autopipe" / STATE_FILENAME


def read(project_path: str | os.PathLike[str]) -> State:
    """Read state.json. On corruption or absence: retry once, then reset.

    Returns a freshly-initialised State if the file is missing or
    unrecoverably corrupt. The caller can persist that fresh state
    immediately by calling write().
    """
    path = _state_path(project_path)
    name = Path(project_path).name

    for attempt in (1, 2):
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return State.from_dict(data)
        except FileNotFoundError:
            return State.fresh(name)
        except (json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
            if attempt == 1:
                # Possibly mid-write — the writer renames atomically, but a
                # reader could in theory see a stale transient. Retry once.
                _log(f"state.json read attempt 1 failed: {exc}; retrying")
                time.sleep(0.5)
                continue
            _log(f"state.json unrecoverable at {path}: {exc}; resetting")
            return State.fresh(name)

    # Unreachable, but keeps type checkers happy.
    return State.fresh(name)


def write(project_path: str | os.PathLike[str], state: State) -> None:
    """Atomic write via tmpfile + os.replace.

    Single-writer model: orchestrator + hooks coordinate via the
    per-project lock (Stage D). Within that, write() is safe under
    concurrent processes — os.replace is atomic on POSIX.
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


def update_verify(
    project_path: str | os.PathLike[str],
    passed: bool,
    score: float,
    prd_complete: bool,
) -> None:
    s = read(project_path)
    s.last_passed = passed
    s.last_score = score
    s.prd_complete = prd_complete
    if passed:
        s.consecutive_failures = 0
    else:
        s.consecutive_failures += 1
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


def set_detached(
    project_path: str | os.PathLike[str],
    *,
    reason: str,
    check_cmd: str,
    check_every_sec: int,
    max_wait_sec: int,
) -> None:
    """Transition a project to phase=detached with the given check_cmd.

    Called by `cc-autopipe-detach` from inside a claude session before
    nohup-ing a long task. Engine releases the slot until check_cmd
    succeeds (poll cadence: check_every_sec) or max_wait_sec elapses.
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
    )
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

    p_upd = sub.add_parser("update-verify")
    p_upd.add_argument("project")
    p_upd.add_argument("--passed", required=True, type=_parse_bool)
    p_upd.add_argument("--score", required=True, type=float)
    p_upd.add_argument("--prd-complete", required=True, type=_parse_bool)

    p_paused = sub.add_parser("set-paused")
    p_paused.add_argument("project")
    p_paused.add_argument("resume_at")
    p_paused.add_argument("reason")

    p_detached = sub.add_parser("set-detached")
    p_detached.add_argument("project")
    p_detached.add_argument("--reason", required=True)
    p_detached.add_argument("--check-cmd", required=True)
    p_detached.add_argument("--check-every", type=int, default=600)
    p_detached.add_argument("--max-wait", type=int, default=14400)

    sub.add_parser("complete-phase").add_argument("project")

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

    if args.cmd == "update-verify":
        update_verify(
            args.project,
            passed=args.passed,
            score=args.score,
            prd_complete=args.prd_complete,
        )
        return 0

    if args.cmd == "set-paused":
        set_paused(args.project, args.resume_at, args.reason)
        return 0

    if args.cmd == "set-detached":
        set_detached(
            args.project,
            reason=args.reason,
            check_cmd=args.check_cmd,
            check_every_sec=args.check_every,
            max_wait_sec=args.max_wait,
        )
        return 0

    if args.cmd == "complete-phase":
        new = complete_phase(args.project)
        print(new)
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
