# cc-autopipe v0.5 — Technical Specification

**Version:** 0.5.0  
**Date:** April 28, 2026  
**Status:** Implementation-ready  
**Audience:** Engineer implementing the system (human or Claude)

---

## Table of contents

1. [Executive summary](#1-executive-summary)
2. [Goals and non-goals](#2-goals-and-non-goals)
3. [User stories](#3-user-stories)
4. [System architecture](#4-system-architecture)
5. [File layout and conventions](#5-file-layout-and-conventions)
6. [Component specifications](#6-component-specifications)
7. [Data formats](#7-data-formats)
8. [Lifecycle and state machine](#8-lifecycle-and-state-machine)
9. [Quota management](#9-quota-management)
10. [Hooks](#10-hooks)
11. [Subagents](#11-subagents)
12. [CLI surface](#12-cli-surface)
13. [Security model](#13-security-model)
14. [Failure handling](#14-failure-handling)
15. [Observability](#15-observability)
16. [Implementation order](#16-implementation-order)
17. [Acceptance criteria](#17-acceptance-criteria)
18. [Out of scope](#18-out-of-scope)
19. [Open questions](#19-open-questions)

---

## 1. Executive summary

`cc-autopipe` is a supervisor process for Claude Code 2.1+ headless mode. It runs Claude Code sessions on autonomous projects, manages state, recovers from failures, and surfaces issues via Telegram. The pipeline reads a PRD, plans, implements, verifies, reports, and self-improves until termination conditions are met.

**Key constraints:**

- Uses MAX 20x subscription only. No API billing. No Claude Agent SDK / Deep Agents.
- Sequential single-slot execution (no parallel sessions in v0.5). Issue #53922 documents server throttling at 4+ parallel sessions.
- Claude Code is the harness. We orchestrate, we do not reimplement.
- File system is the database. JSONL append-only logs, markdown for tasks/PRD/rules, JSON for state.
- External `verify.sh` gates progress. No self-graded scores.
- Idempotent recovery from any state (kill -9 anywhere is safe).
- Hooks enforce critical rules (`exit 2`). CLAUDE.md hints with ~70% adherence.
- Project repos are untrusted. Hooks live in `~/cc-autopipe/`, project's `.claude/settings.json` is gitignored.
- Quota awareness via undocumented `oauth/usage` endpoint, with adaptive ladder fallback.

**Primary deliverable:** v0.5 runs hello-fullstack project end-to-end from `cc-autopipe init` to PRD completion in <4 hours, surviving kill -9 and 429 events.

---

## 2. Goals and non-goals

### 2.1 v0.5 goals

- **G1.** Run hello-fullstack autonomously from PRD to DONE.
- **G2.** Survive `kill -9` mid-execution and resume correctly.
- **G3.** Survive 429 mid-execution with TG alert and auto-resume.
- **G4.** Survive machine reboot without state loss.
- **G5.** Detect quota exhaustion before sending (5h and 7d).
- **G6.** Block dangerous actions deterministically (secrets, long bash, state.json writes).
- **G7.** Provide one-screen `cc-autopipe status` for situational awareness.
- **G8.** Total quota burn for hello-fullstack cycle: <100 Sonnet messages.

### 2.2 v0.5 non-goals

- Multi-project parallelism (v2)
- DETACHED state for long operations (v1)
- Researcher and reporter subagents (v1)
- Parallel implementations via worktrees (v2)
- doobidoo MCP memory (v2)
- Path-scoped CLAUDE.md (v1+)
- Multi-machine coordination (v2)
- Pre-compact survival mechanism (v1)
- Auto-escalation to Opus on failures (v1)
- Web dashboard (out of scope)

### 2.3 Quality bars

- **Reliability:** Pipeline must run for 4 hours without orchestrator crash.
- **Recovery time:** From kill -9 to next cycle resume: <60 seconds.
- **Observability:** Any failure must be diagnosable from `~/.cc-autopipe/log/aggregate.jsonl` within 5 minutes.
- **Code budget:** Total implementation under 1500 lines (Python + bash).

---

## 3. User stories

### 3.1 Initialize a new project
```
As Roman, I cd to a fresh project directory and run `cc-autopipe init`.
The command creates .cc-autopipe/ from templates, registers the project in
~/.cc-autopipe/projects.list, generates a minimal CLAUDE.md, and configures
.claude/settings.json with absolute paths to engine hooks. It prints next
steps: edit prd.md, context.md, verify.sh.
```

### 3.2 Start the orchestrator
```
Roman runs `cc-autopipe start`. The orchestrator runs in foreground (or via
systemd in v1). It picks the first ACTIVE project, runs one cycle, sleeps 30s,
picks next. Telegram alerts on DONE/FAILED/PAUSED.
```

### 3.3 Check status
```
Roman runs `cc-autopipe status`. Output:
  Project              Phase    Iter  Score  Last activity
  hello-fullstack      ACTIVE   12    0.78   30s ago
  legal-parser         PAUSED   45    0.65   12min ago (resume in 18m)
  trading-bot          DONE     78    0.96   2h ago
```

### 3.4 Recover from crash
```
Roman accidentally runs `kill -9 $(pgrep cc-autopipe)`. He runs
`cc-autopipe start` again. The orchestrator detects stale lock, releases it,
and resumes from saved state.json. The active project's session_id is reused
via `claude --resume`, and the latest checkpoint.md guides continuation.
```

### 3.5 Hit rate limit
```
Project hits 429. StopFailure hook reads quota from oauth/usage endpoint,
sees 5h reset at 18:30, transitions project to PAUSED, sends TG: "[project]
quota exhausted, resume at 18:30". Orchestrator skips this project until
18:30 + 60s, then transitions back to ACTIVE.
```

### 3.6 Dangerous action attempted
```
Claude tries to run `rm -rf ~/.cc-autopipe/secrets.env`. PreToolUse hook
matches secret-path pattern, exits 2 with reason. Claude receives block,
adapts. Incident logged to failures.jsonl. No TG alert (this is normal
defense, not failure).
```

### 3.7 Block on stuck task
```
Project fails verify 3 times in a row. Orchestrator marks state FAILED,
creates HUMAN_NEEDED.md describing last error, sends TG. Roman reviews,
fixes, runs `cc-autopipe resume <project>` to retry.
```

---

## 4. System architecture

### 4.1 Process model

```
┌──────────────────────────────────────────────────────────────┐
│ Host machine (Ubuntu/macOS)                                  │
│                                                              │
│  ┌────────────────────────┐                                 │
│  │ orchestrator (Python)  │  Single long-running process    │
│  │ PID: stored in         │  Reads projects.list            │
│  │ ~/.cc-autopipe/        │  FIFO loop, 1 slot              │
│  │ orchestrator.pid       │  Sleeps 30s between iterations  │
│  └─────────┬──────────────┘                                 │
│            │                                                 │
│            │ spawns                                          │
│            ▼                                                 │
│  ┌────────────────────────┐                                 │
│  │ claude -p (subprocess) │  Per cycle                      │
│  │ --max-turns 35         │  Headless                       │
│  │ --resume <id> | (none) │  PID tracked                    │
│  │ --agents <json>        │  Timeout 60min wall clock      │
│  └─────────┬──────────────┘                                 │
│            │                                                 │
│            │ triggers                                        │
│            ▼                                                 │
│  ┌────────────────────────┐                                 │
│  │ Hooks (bash scripts)   │  4 hooks                        │
│  │ ~/cc-autopipe/hooks/   │  Live in engine, not project    │
│  │ Run inside claude      │  timeout 30s wrapping           │
│  │ subprocess context     │                                 │
│  └────────────────────────┘                                 │
└──────────────────────────────────────────────────────────────┘
```

### 4.2 Why this architecture

**Single orchestrator process:** simplest concurrency model. We're not building distributed system. State on disk, lock file, recovery is trivial.

**One slot:** Issue #53922 — server throttles 4+ parallel sessions. We're a low-throughput supervisor, not a high-throughput scheduler. Sequential is correct trade-off in v0.5.

**Hooks live in engine:** CVE-2026-21852 documented RCE through repo-controlled `.claude/settings.json`. We never let project repo control hook scripts. Project's `.claude/settings.json` references absolute paths to `~/cc-autopipe/hooks/`, gitignored.

**`claude -p` per cycle:** Each invocation is a clean unit of work. Easier to recover, log, attribute. No long-running interactive sessions.

### 4.3 Component diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                     Engine (~/cc-autopipe/)                     │
│                                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐ │
│  │ orchestrator │──│ lib/         │──│ helpers/             │ │
│  │   (Python)   │  │ - state.py   │  │ - cc-autopipe        │ │
│  │              │  │ - quota.py   │  │ - cc-autopipe-       │ │
│  │              │  │ - tg.sh      │  │   checkpoint         │ │
│  │              │  │ - compat.sh  │  │ - cc-autopipe-block  │ │
│  └──────────────┘  └──────────────┘  └──────────────────────┘ │
│                                                                 │
│  ┌──────────────┐  ┌──────────────┐                           │
│  │ hooks/       │  │ templates/   │                           │
│  │ - session-   │  │ .cc-autopipe │                           │
│  │   start.sh   │  │   skeleton   │                           │
│  │ - pre-tool-  │  │              │                           │
│  │   use.sh     │  │              │                           │
│  │ - stop.sh    │  │              │                           │
│  │ - stop-      │  │              │                           │
│  │   failure.sh │  │              │                           │
│  └──────────────┘  └──────────────┘                           │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ reads/writes
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    User config (~/.cc-autopipe/)                │
│                                                                 │
│  - secrets.env       TG creds                                   │
│  - projects.list     Active projects FIFO                       │
│  - shared-knowledge.md  Cross-project insights (manual)         │
│  - log/aggregate.jsonl  All events                              │
│  - orchestrator.pid  Singleton lock                             │
│  - ratelimit.json    Backoff state                              │
│  - quota-cache.json  Last quota fetch (60s TTL)                 │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ manages
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│             Per-project (<project>/.cc-autopipe/)               │
│                                                                 │
│  - config.yaml       Project type, limits                       │
│  - prd.md            Acceptance criteria                        │
│  - context.md        Stack, constraints                         │
│  - verify.sh         External verifier (executable)             │
│  - rules.md          Project-specific rules                     │
│  - agents.json       --agents JSON                              │
│  - state.json        Phase, session_id, iteration               │
│  - lock              Heartbeat lock (PID + timestamp)           │
│  - checkpoint.md     Mid-task continuation hint                 │
│  - HUMAN_NEEDED.md   Created when stuck (TG alerts)             │
│  - memory/                                                      │
│    - progress.jsonl  Tool calls log                             │
│    - failures.jsonl  Errors log                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 4.4 Why these specific files

- **state.json** — single source of truth for project state. Everything else recoverable.
- **lock** — separate from state.json so we can flock it without race on state writes.
- **checkpoint.md** — markdown so Claude reads it naturally; engine never parses it semantically.
- **HUMAN_NEEDED.md** — file existence is the signal; content is for human eyes.
- **progress.jsonl / failures.jsonl** — append-only, never rewritten, easy to tail/grep.
- **shared-knowledge.md** — flat markdown for v0.5 (vs doobidoo for v2). Curated by human after Claude proposes additions.

---

## 5. File layout and conventions

### 5.1 Engine layout (read-only after install)

```
~/cc-autopipe/
├── orchestrator                    Python 3, executable, ~400 lines
├── helpers/
│   ├── cc-autopipe                 Bash dispatcher to subcommands
│   ├── cc-autopipe-checkpoint      Bash, called by Claude to save state
│   └── cc-autopipe-block           Bash, marks task blocked + alerts
├── hooks/
│   ├── session-start.sh            ~50 lines bash
│   ├── pre-tool-use.sh             ~120 lines bash
│   ├── stop.sh                     ~80 lines bash
│   └── stop-failure.sh             ~60 lines bash
├── lib/
│   ├── state.py                    State.json atomic R/W (~150 lines)
│   ├── quota.py                    OAuth usage endpoint (~80 lines)
│   ├── ratelimit.py                Backoff ladder (~50 lines)
│   ├── tg.sh                       TG fire-and-forget (~30 lines)
│   └── compat.sh                   Linux/macOS shim (~40 lines)
├── templates/
│   └── .cc-autopipe/               Project skeleton
│       ├── config.yaml
│       ├── prd.md.example
│       ├── context.md.example
│       ├── verify.sh.example
│       ├── rules.md.example
│       ├── agents.json
│       └── settings.json.template  For .claude/settings.json
├── install.sh                      Bash installer
├── VERSION                         "0.5.0"
└── CLAUDE_CODE_MIN_VERSION         "2.1.115"
```

### 5.2 User-level layout (read-write at runtime)

```
~/.cc-autopipe/
├── secrets.env                     chmod 600
├── projects.list                   One absolute path per line
├── shared-knowledge.md             Manually curated, in git (see §5.4)
├── machine.id                      hostname-uuid, written at install
├── orchestrator.pid                Lock file
├── ratelimit.json                  Backoff state
├── quota-cache.json                60s TTL cache
└── log/
    └── aggregate.jsonl             All events from all projects
```

### 5.3 Per-project layout

```
<project>/
├── .cc-autopipe/                   gitignored except prd.md, context.md, rules.md
│   ├── config.yaml                 IN GIT (project owners commit)
│   ├── prd.md                      IN GIT
│   ├── context.md                  IN GIT
│   ├── verify.sh                   IN GIT (executable)
│   ├── rules.md                    IN GIT
│   ├── agents.json                 IN GIT (project-specific overrides)
│   ├── state.json                  GITIGNORED
│   ├── lock                        GITIGNORED
│   ├── checkpoint.md               GITIGNORED
│   ├── HUMAN_NEEDED.md             GITIGNORED, created on demand
│   └── memory/                     GITIGNORED
│       ├── progress.jsonl
│       └── failures.jsonl
├── .claude/
│   └── settings.json               GITIGNORED (engine writes absolute paths)
├── CLAUDE.md                       IN GIT (small, references baseline)
├── MEMORY.md                       GITIGNORED (Claude Code auto-memory)
├── backlog.md                      IN GIT
├── reports/                        IN GIT (archived progress)
└── <project files>
```

`cc-autopipe init` adds these to `.gitignore` automatically:
```
.cc-autopipe/state.json
.cc-autopipe/lock
.cc-autopipe/checkpoint.md
.cc-autopipe/HUMAN_NEEDED.md
.cc-autopipe/memory/
.claude/settings.json
MEMORY.md
```

### 5.4 ~/.cc-autopipe/ as git repo

`~/.cc-autopipe/` is initialized as git repo by `install.sh`. Contents:
- `secrets.env` — gitignored
- `projects.list` — committed (so reinit on another machine knows projects)
- `shared-knowledge.md` — committed (cross-project insights)
- `log/`, `*.json` — gitignored

User responsibility: push to private remote periodically. Engine doesn't auto-commit.

### 5.5 Naming conventions

- **Files in `.cc-autopipe/`:** lowercase, hyphens (kebab-case). Markdown extension.
- **State keys in JSON:** snake_case (e.g., `last_score`, `consecutive_failures`).
- **Helpers and hooks:** kebab-case (e.g., `cc-autopipe-checkpoint`, `pre-tool-use.sh`).
- **Python modules:** snake_case (e.g., `state.py`).
- **Environment variables:** `CC_AUTOPIPE_*` prefix (e.g., `CC_AUTOPIPE_HOME`, `CC_AUTOPIPE_PROJECT`).

---

## 6. Component specifications

### 6.1 orchestrator

**Language:** Python 3.11+  
**Size budget:** ~400 lines  
**Entry point:** `~/cc-autopipe/orchestrator`, called via `cc-autopipe start`.

**Responsibilities:**
1. Hold singleton lock (`~/.cc-autopipe/orchestrator.pid`).
2. Read `projects.list`, iterate FIFO.
3. For each project: check phase, decide action.
4. Spawn `claude -p` subprocess with proper args.
5. Monitor subprocess: track PID, enforce wall-clock timeout.
6. Update `aggregate.jsonl` with cycle events.
7. Sleep 30s between iterations (cooldown).
8. Trap SIGTERM for graceful shutdown.

**Main loop:**
```python
def main():
    acquire_singleton_lock()  # exit if another orchestrator running
    setup_signal_handlers()
    
    while not shutdown_requested:
        for project_path in read_projects_list():
            if shutdown_requested:
                break
            try:
                process_project(project_path)
            except Exception as e:
                log_error(project_path, e)
                # Continue with next project, don't crash orchestrator
            
            time.sleep(COOLDOWN_SEC)  # default 30
        
        if no_projects_active():
            time.sleep(IDLE_SLEEP_SEC)  # default 60
    
    log_info("orchestrator shutdown gracefully")
    release_singleton_lock()
```

**Per-project flow:**
```python
def process_project(project_path):
    state = state_lib.read(project_path)
    
    # Pre-flight checks
    if state.phase == "done" or state.phase == "failed":
        return  # skip permanently
    
    if state.phase == "paused":
        if datetime.now() < state.paused.resume_at:
            return  # not yet
        state.phase = "active"
        state_lib.write(project_path, state)
    
    # Check quota before spending
    quota = quota_lib.read_cached()
    if quota and quota.five_hour.pct > 0.95:
        transition_to_paused(project_path, quota.five_hour.resets_at, "5h_pre_check")
        return
    if quota and quota.seven_day.pct > 0.90:
        transition_to_paused(project_path, quota.seven_day.resets_at, "7d_pre_check")
        notify_tg(f"7d quota at {int(quota.seven_day.pct*100)}%, all projects pausing")
        return
    
    # Acquire project lock
    lock = acquire_project_lock(project_path)
    if not lock:
        return  # someone else has it (stale lock detection inside)
    
    try:
        run_cycle(project_path, state)
    finally:
        release_project_lock(lock)


def run_cycle(project_path, state):
    cmd = build_claude_cmd(project_path, state)
    
    state.iteration += 1
    state.last_cycle_started_at = datetime.now()
    state_lib.write(project_path, state)
    
    log_event(project_path, "cycle_start", iteration=state.iteration)
    
    rc = run_subprocess_with_heartbeat(
        cmd,
        cwd=project_path,
        timeout_sec=3600,  # wall clock
        heartbeat_path=f"{project_path}/.cc-autopipe/lock"
    )
    
    # State was updated by hooks during execution
    # Re-read for final decision
    state = state_lib.read(project_path)
    
    if state.last_score is not None and state.last_score >= state.threshold and state.prd_complete:
        state.phase = "done"
        state_lib.write(project_path, state)
        notify_tg(f"[{project_path}] PRD complete, score {state.last_score}")
    elif state.consecutive_failures >= 3:
        state.phase = "failed"
        state_lib.write(project_path, state)
        write_human_needed(project_path)
        notify_tg(f"[{project_path}] FAILED after 3 consecutive failures")
    
    log_event(project_path, "cycle_end", phase=state.phase, score=state.last_score)
```

**`build_claude_cmd`:**
```python
def build_claude_cmd(project_path, state):
    cmd = ["claude"]
    
    if state.session_id and session_jsonl_exists(state.session_id):
        cmd += ["--resume", state.session_id]
    
    cmd += [
        "-p", build_prompt(project_path, state),
        "--dangerously-skip-permissions",
        "--max-turns", "35",
        "--output-format", "stream-json",
        "--agents", read_agents_json(project_path),
    ]
    
    # Model from config.yaml
    cfg = read_config(project_path)
    cmd += ["--model", cfg.models.default]
    
    return cmd


def build_prompt(project_path, state):
    """Constructs the prompt sent to Claude.
    
    Sources:
    - PRD (truncated to first 2KB)
    - context.md (first 1KB)
    - Current backlog (next 5 [ ] tasks)
    - Last verify result if any
    - Pointer to checkpoint.md if exists
    """
    parts = []
    
    parts.append(f"# Project: {state.name}\n")
    parts.append(f"Iteration {state.iteration}. Phase: {state.phase}.\n\n")
    
    if checkpoint_exists(project_path):
        parts.append("**RESUME FROM CHECKPOINT:** Read .cc-autopipe/checkpoint.md FIRST. Continue from there.\n\n")
    
    parts.append("## PRD (excerpt)\n")
    parts.append(read_truncated(f"{project_path}/.cc-autopipe/prd.md", 2048))
    parts.append("\n")
    
    parts.append("## Next backlog tasks\n")
    parts.append(read_top_open_tasks(f"{project_path}/backlog.md", 5))
    parts.append("\n")
    
    if state.last_score is not None:
        parts.append(f"\nLast verify: passed={state.last_passed}, score={state.last_score}\n")
    
    parts.append("\n## Instructions\n")
    parts.append("Pick top open task. Implement. Run .cc-autopipe/verify.sh before declaring done. ")
    parts.append("If task is large, save progress with cc-autopipe-checkpoint helper near turn 25.\n")
    
    return "".join(parts)
```

**Heartbeat monitoring:**
```python
def run_subprocess_with_heartbeat(cmd, cwd, timeout_sec, heartbeat_path):
    proc = subprocess.Popen(cmd, cwd=cwd, ...)
    write_heartbeat(heartbeat_path, proc.pid)
    
    started = time.time()
    while True:
        rc = proc.poll()
        if rc is not None:
            return rc
        
        if time.time() - started > timeout_sec:
            log_warning(f"timeout {timeout_sec}s exceeded, killing pid={proc.pid}")
            proc.kill()
            return -1
        
        write_heartbeat(heartbeat_path, proc.pid)  # keep updating
        time.sleep(10)
```

### 6.2 lib/state.py

**Atomic state.json read/write.**

```python
SCHEMA_VERSION = 1

def read(project_path):
    """Read state.json. On corruption, attempt recovery."""
    path = f"{project_path}/.cc-autopipe/state.json"
    try:
        with open(path) as f:
            data = json.load(f)
        return State.from_dict(data)
    except (FileNotFoundError, json.JSONDecodeError):
        # Try once more (might be mid-write)
        time.sleep(0.5)
        try:
            with open(path) as f:
                return State.from_dict(json.load(f))
        except Exception:
            # Truly corrupted — reset, alert
            log_error(f"state.json unrecoverable, resetting: {path}")
            notify_tg(f"[{project_path}] state.json corrupted, reset to iteration=0")
            return State.fresh(project_path)


def write(project_path, state):
    """Atomic write via tmpfile + rename."""
    path = f"{project_path}/.cc-autopipe/state.json"
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w") as f:
        json.dump(state.to_dict(), f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.rename(tmp, path)


@dataclass
class State:
    schema_version: int = SCHEMA_VERSION
    name: str = ""
    phase: str = "active"  # active | paused | done | failed
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


@dataclass
class Paused:
    resume_at: str  # ISO 8601
    reason: str
```

### 6.3 lib/quota.py

**Reads OAuth usage endpoint with caching.**

```python
import json
import os
import platform
import subprocess
import time
import urllib.request
from datetime import datetime, timezone

CACHE_PATH = os.path.expanduser("~/.cc-autopipe/quota-cache.json")
CACHE_TTL_SEC = 60
ENDPOINT = "https://api.anthropic.com/api/oauth/usage"
USER_AGENT = "claude-code/2.1.115"
BETA_HEADER = "oauth-2025-04-20"


def read_oauth_token():
    """Returns OAuth bearer token from Claude Code credentials.
    
    Linux/WSL: ~/.claude/credentials.json (field 'accessToken')
    macOS: Keychain via `security find-generic-password`
    
    Returns None on any failure (caller falls back to ladder).
    """
    if platform.system() == "Darwin":
        try:
            result = subprocess.run(
                ["security", "find-generic-password",
                 "-s", "Claude Code-credentials", "-w"],
                capture_output=True, text=True, timeout=3
            )
            if result.returncode == 0:
                creds = json.loads(result.stdout.strip())
                return creds.get("accessToken")
        except Exception:
            return None
    else:
        try:
            with open(os.path.expanduser("~/.claude/credentials.json")) as f:
                creds = json.load(f)
            return creds.get("accessToken")
        except Exception:
            return None


def fetch_quota():
    """Calls oauth/usage endpoint. Returns dict or None on failure."""
    token = read_oauth_token()
    if not token:
        return None
    
    req = urllib.request.Request(
        ENDPOINT,
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": USER_AGENT,
            "anthropic-beta": BETA_HEADER,
            "Accept": "application/json",
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def read_cached():
    """Returns Quota object or None.
    
    Caches for 60s in ~/.cc-autopipe/quota-cache.json.
    On endpoint failure, returns None (caller treats as 'unknown,
    proceed with caution').
    """
    try:
        if os.path.exists(CACHE_PATH):
            mtime = os.path.getmtime(CACHE_PATH)
            if time.time() - mtime < CACHE_TTL_SEC:
                with open(CACHE_PATH) as f:
                    return Quota.from_dict(json.load(f))
    except Exception:
        pass
    
    raw = fetch_quota()
    if not raw:
        return None
    
    try:
        with open(CACHE_PATH, "w") as f:
            json.dump(raw, f)
    except Exception:
        pass
    
    return Quota.from_dict(raw)


@dataclass
class Quota:
    five_hour_pct: float  # 0.0 to 1.0
    five_hour_resets_at: datetime
    seven_day_pct: float
    seven_day_resets_at: datetime
    
    @classmethod
    def from_dict(cls, d):
        return cls(
            five_hour_pct=d.get("five_hour", {}).get("utilization", 0.0),
            five_hour_resets_at=parse_iso(d.get("five_hour", {}).get("resets_at")),
            seven_day_pct=d.get("seven_day", {}).get("utilization", 0.0),
            seven_day_resets_at=parse_iso(d.get("seven_day", {}).get("resets_at")),
        )
```

**Caveat:** `oauth/usage` is undocumented. If Anthropic changes it, `read_cached()` returns None, and we fall back to ladder-only behavior. We log a warning to `aggregate.jsonl` once per hour when this happens.

### 6.4 lib/ratelimit.py

**Backoff ladder for cases where quota.py is None or returns nothing useful.**

```python
LADDER_SEC = [300, 900, 3600]  # 5min, 15min, 1h
RESET_AFTER_SEC = 21600  # 6h with no 429 — reset counter


def register_429():
    state = load_state()
    now = time.time()
    
    if now - state.get("last_429_ts", 0) > RESET_AFTER_SEC:
        state["count"] = 0
    
    idx = min(state["count"], len(LADDER_SEC) - 1)
    wait_sec = LADDER_SEC[idx]
    state["count"] += 1
    state["last_429_ts"] = now
    save_state(state)
    
    return wait_sec


def get_resume_at(quota_resume_at=None):
    """If quota gave us exact resets_at, use it. Otherwise ladder."""
    if quota_resume_at:
        return quota_resume_at
    wait_sec = register_429()
    return datetime.now() + timedelta(seconds=wait_sec)
```

The ladder is intentionally short (max 1h). Server throttling (issue #53922) typically clears within minutes. True quota exhaustion is caught by `quota.py` pre-flight before we even hit 429.

### 6.5 lib/tg.sh

**Fire-and-forget Telegram notification.**

```bash
#!/bin/bash
# Usage: tg.sh "message text"
# Returns: always 0 (never blocks pipeline on TG failure)

source ~/.cc-autopipe/secrets.env 2>/dev/null

if [ -z "${TG_BOT_TOKEN}" ] || [ -z "${TG_CHAT_ID}" ]; then
    exit 0
fi

MSG="${1}"
[ -z "$MSG" ] && exit 0

# Truncate very long messages
if [ ${#MSG} -gt 3000 ]; then
    MSG="${MSG:0:2900}... [truncated]"
fi

curl -s -X POST \
    --max-time 3 \
    "https://api.telegram.org/bot${TG_BOT_TOKEN}/sendMessage" \
    -d "chat_id=${TG_CHAT_ID}" \
    -d "text=${MSG}" \
    -d "disable_web_page_preview=true" \
    > /dev/null 2>&1 || true

exit 0
```

### 6.6 lib/compat.sh

**Linux/macOS shim for `date`, `stat`, `flock`.**

```bash
#!/bin/bash

# Detect platform once
case "$(uname -s)" in
    Darwin) CC_AUTOPIPE_OS="macos" ;;
    Linux)  CC_AUTOPIPE_OS="linux" ;;
    *)      CC_AUTOPIPE_OS="unknown" ;;
esac
export CC_AUTOPIPE_OS

# date: ISO 8601 from epoch
date_from_epoch() {
    local ts=$1
    if [ "$CC_AUTOPIPE_OS" = "macos" ]; then
        date -u -r "$ts" +"%Y-%m-%dT%H:%M:%SZ"
    else
        date -u -d "@$ts" +"%Y-%m-%dT%H:%M:%SZ"
    fi
}

# stat: file mtime as epoch
file_mtime() {
    local path=$1
    if [ "$CC_AUTOPIPE_OS" = "macos" ]; then
        stat -f %m "$path"
    else
        stat -c %Y "$path"
    fi
}
```

---

## 7. Data formats

### 7.1 state.json

```json
{
  "schema_version": 1,
  "name": "hello-fullstack",
  "phase": "active",
  "iteration": 12,
  "session_id": "session-abc-123",
  "last_score": 0.78,
  "last_passed": false,
  "prd_complete": false,
  "consecutive_failures": 1,
  "last_cycle_started_at": "2026-04-28T15:00:00Z",
  "last_progress_at": "2026-04-28T15:24:00Z",
  "threshold": 0.85,
  "paused": null
}
```

When `phase == "paused"`:
```json
{
  ...
  "phase": "paused",
  "paused": {
    "resume_at": "2026-04-28T18:30:00Z",
    "reason": "rate_limit_5h"
  }
}
```

### 7.2 progress.jsonl (per-project)

```jsonl
{"ts":"2026-04-28T15:00:00Z","event":"cycle_start","iteration":12}
{"ts":"2026-04-28T15:00:05Z","event":"hook_session_start","duration_ms":120}
{"ts":"2026-04-28T15:01:30Z","event":"tool_call","tool":"Read","duration_ms":50,"summary":"src/api/main.py"}
{"ts":"2026-04-28T15:02:10Z","event":"tool_call","tool":"Bash","duration_ms":2300,"summary":"pytest tests/"}
{"ts":"2026-04-28T15:24:00Z","event":"verify","passed":false,"score":0.78}
{"ts":"2026-04-28T15:24:05Z","event":"cycle_end","phase":"active"}
```

### 7.3 failures.jsonl (per-project)

```jsonl
{"ts":"2026-04-28T15:24:00Z","error":"verify_failed","details":{"score":0.78,"coverage":68}}
{"ts":"2026-04-28T16:10:00Z","error":"hook_pretooluse_blocked","tool":"Bash","reason":"long_op_detected"}
```

### 7.4 ~/.cc-autopipe/log/aggregate.jsonl

All projects, all events, single file.

```jsonl
{"ts":"2026-04-28T15:00:00Z","project":"hello-fullstack","event":"cycle_start","iteration":12}
{"ts":"2026-04-28T15:24:05Z","project":"hello-fullstack","event":"cycle_end","phase":"active","score":0.78}
{"ts":"2026-04-28T15:30:00Z","project":"legal-parser","event":"paused","reason":"rate_limit_5h","resume_at":"2026-04-28T18:30:00Z"}
{"ts":"2026-04-28T16:15:00Z","project":"hello-fullstack","event":"done","score":0.94}
```

Used by `cc-autopipe status` and `cc-autopipe tail`.

### 7.5 config.yaml

```yaml
schema_version: 1
name: hello-fullstack
type: fullstack-app
language: python+typescript
runtime:
  python: "3.11"
  node: "20"

limits:
  max_iterations: 200
  max_hours: 72
  watchdog_timeout_min: 10

termination:
  threshold: 0.85          # last_score >= threshold AND prd_complete
  on_iterations: 200       # hard stop
  on_hours: 72             # hard stop

models:
  default: "claude-sonnet-4-6"
  io_worker: "claude-haiku-4-5"
  verifier: "claude-haiku-4-5"

paths:
  src: "."
  tests: "."
```

### 7.6 backlog.md format

Engine parses by regex. Rules:
- Open: `- [ ] [tag1] [tag2] Description — Acceptance: ...`
- Done: `- [x] ...`
- Blocked: `- [!] ...`
- In progress: `- [~] ...`

Tags engine recognizes: `[architect]` (escalate to Opus, v1+), `[parallel-impl]` (v2+), `[blocked]` (skip).

### 7.7 verify.sh contract

**Input:** runs in project root.  
**Output:** JSON to stdout. Required fields:

```json
{
  "passed": true,
  "score": 0.94,
  "prd_complete": false,
  "details": {
    "tests_pass": true,
    "coverage_pct": 87,
    "any_other_diagnostic": "..."
  }
}
```

**Engine validates:**
- Top-level keys: `passed` (bool), `score` (number 0-1), `prd_complete` (bool), `details` (object).
- Missing/wrong type → treated as `passed: false, score: 0.0`. Logged as `verify_malformed`.

**Timeout:** 60 seconds wrapping verify.sh in Stop hook.

### 7.8 .claude/settings.json template

Engine writes this on `cc-autopipe init`:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "/home/roman/cc-autopipe/hooks/session-start.sh",
            "timeout": 30
          }
        ]
      }
    ],
    "PreToolUse": [
      {
        "matcher": ".*",
        "hooks": [
          {
            "type": "command",
            "command": "/home/roman/cc-autopipe/hooks/pre-tool-use.sh",
            "timeout": 30
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "/home/roman/cc-autopipe/hooks/stop.sh",
            "timeout": 90
          }
        ]
      }
    ],
    "StopFailure": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "/home/roman/cc-autopipe/hooks/stop-failure.sh",
            "timeout": 30
          }
        ]
      }
    ]
  }
}
```

Absolute paths only. Engine stores these in `~/.cc-autopipe/machine.id` config and uses them in template substitution.

### 7.9 agents.json (template)

```json
{
  "io-worker": {
    "description": "Use ONLY for file reads >500 lines OR multi-step IO chains. For trivial reads, main session uses Read directly.",
    "prompt": "You are an IO worker. Read files, run bash, return concise summaries with line numbers. Do NOT synthesize.",
    "tools": ["Read", "Bash", "Glob", "Grep"],
    "model": "haiku",
    "maxTurns": 8
  },
  "verifier": {
    "description": "Run .cc-autopipe/verify.sh and return JSON.",
    "prompt": "Run .cc-autopipe/verify.sh. Parse stdout JSON. Return {passed, score, prd_complete, details}.",
    "tools": ["Bash", "Read"],
    "model": "haiku",
    "maxTurns": 3
  }
}
```

### 7.10 secrets.env

```bash
# Telegram
TG_BOT_TOKEN=1234567890:AAAA-BBBB
TG_CHAT_ID=123456789

# Engine config (optional, defaults are fine)
CC_AUTOPIPE_COOLDOWN_SEC=30
CC_AUTOPIPE_IDLE_SLEEP_SEC=60
CC_AUTOPIPE_MAX_TURNS=35
CC_AUTOPIPE_CYCLE_TIMEOUT_SEC=3600
```

Permissions: `chmod 600`.

---

## 8. Lifecycle and state machine

### 8.1 Project phase transitions

```
                  (cc-autopipe init)
                          │
                          ▼
                    ┌──────────┐
                    │  ACTIVE  │
                    └─────┬────┘
                          │
            ┌─────────────┼──────────────┬──────────────┐
            │             │              │              │
       (429 hit /     (PRD complete  (3 consecutive  (manual
        quota >95%)    + score >=     failures)        intervention)
            │           threshold)        │              │
            ▼              ▼              ▼              ▼
       ┌────────┐     ┌────────┐    ┌────────┐    ┌─────────┐
       │ PAUSED │     │  DONE  │    │ FAILED │    │ STOPPED │
       └────┬───┘     └────────┘    └────┬───┘    └─────────┘
            │                            │
       (resume_at                     (cc-autopipe
        passed)                        resume)
            │                            │
            └──────────► ACTIVE ◄────────┘
```

States:
- **ACTIVE** — eligible to run cycles
- **PAUSED** — waiting for `resume_at` (rate limit, manual)
- **DONE** — terminal, success
- **FAILED** — terminal, needs human intervention
- **STOPPED** — manually stopped (not in v0.5; project removed from list instead)

### 8.2 Cycle within ACTIVE

```
Pick from FIFO queue
    │
    ▼
Pre-flight: quota check, lock acquire
    │
    ▼
Build prompt, build cmd
    │
    ▼
Spawn `claude -p ...` subprocess
    │
    ├─► SessionStart hook fires → injects context
    │
    ├─► Claude works (tool calls, possibly subagents)
    │   ├─► PreToolUse hook on each → may block
    │   └─► Each tool result → progress.jsonl
    │
    ├─► Stop event fires:
    │   └─► stop.sh runs verify.sh
    │       └─► Updates state.json with score, passed, prd_complete
    │
    └─► OR StopFailure fires (API error):
        └─► stop-failure.sh:
            ├─► If 429: read quota.py, transition to PAUSED
            └─► Else: increment consecutive_failures, TG alert
    
Subprocess exits
    │
    ▼
Orchestrator re-reads state.json
    │
    ▼
Decide: continue, mark DONE, mark FAILED
    │
    ▼
Release lock, sleep COOLDOWN_SEC, next project
```

### 8.3 Locking model

Two locks:

**Singleton orchestrator lock** — `~/.cc-autopipe/orchestrator.pid`
- Acquired on `cc-autopipe start`
- Contains PID
- Stale detection: read PID, `kill -0` check
- Released on graceful shutdown (SIGTERM trap)

**Per-project lock** — `<project>/.cc-autopipe/lock`
- Acquired before spawning `claude -p`
- Contains PID + heartbeat timestamp
- Heartbeat updated every 10s during subprocess
- Stale if `kill -0` fails OR last heartbeat >120s old
- Released after subprocess exits

Both use `flock` (Linux/macOS via brew).

### 8.4 Recovery scenarios

**Scenario: kill -9 mid-cycle**
1. Orchestrator killed. `claude -p` subprocess possibly still running (orphan).
2. User runs `cc-autopipe start`.
3. New orchestrator detects `~/.cc-autopipe/orchestrator.pid` exists, PID dead → release.
4. Iterates projects. Sees `<project>/.cc-autopipe/lock` exists, PID dead OR heartbeat stale → release.
5. State.json may say `phase=active`. Iteration count may be off by one. Acceptable.
6. Continues. Worst case: re-runs one cycle that was already in progress.

**Scenario: machine reboot**
1. All processes gone.
2. systemd/launchd starts orchestrator on boot (configured in v1; manual in v0.5).
3. Same as above.

**Scenario: claude subprocess crashes**
1. Subprocess exits with non-zero code, no Stop hook fired.
2. Orchestrator detects via subprocess.poll().
3. State.json unchanged (no hook updated it).
4. Orchestrator: `consecutive_failures += 1`, log to aggregate.jsonl, continue.

---

## 9. Quota management

### 9.1 Strategy

Two layers:

**Layer 1 (preferred): `lib/quota.py`** reads `oauth/usage` endpoint, gives us 5h and 7d utilization with exact reset times.

**Layer 2 (fallback): `lib/ratelimit.py`** ladder of 5min/15min/1h waits when we hit 429 without good quota data.

### 9.2 Pre-flight check

Before each `claude -p` spawn:

```python
quota = quota_lib.read_cached()  # 60s TTL

if quota:
    if quota.five_hour_pct > 0.95:
        pause_until(quota.five_hour_resets_at, "5h_pre_check")
        return
    if quota.seven_day_pct > 0.90:
        pause_until(quota.seven_day_resets_at, "7d_pre_check")
        notify_tg("7d quota at 90%, all projects pausing")
        return
    if quota.five_hour_pct > 0.80:
        # Warning only, don't pause
        log_warning(f"5h quota at {int(quota.five_hour_pct*100)}%")
else:
    # quota.py returned None — endpoint failed
    # Fall through, rely on StopFailure if 429 hits
    pass
```

### 9.3 On 429 (StopFailure hook)

```bash
# stop-failure.sh receives error_details
ERROR=$(echo "$INPUT" | jq -r '.error')
if [ "$ERROR" = "rate_limit" ] || [ "$ERROR" = "429" ]; then
    # Try quota first
    QUOTA_JSON=$(python3 ~/cc-autopipe/lib/quota.py read 2>/dev/null)
    RESET_AT=$(echo "$QUOTA_JSON" | jq -r '.five_hour.resets_at // empty')
    
    if [ -z "$RESET_AT" ]; then
        # Fall back to ladder
        WAIT_SEC=$(python3 ~/cc-autopipe/lib/ratelimit.py register-429)
        RESET_AT=$(python3 -c "from datetime import datetime, timedelta, timezone; print((datetime.now(timezone.utc) + timedelta(seconds=$WAIT_SEC)).isoformat())")
    fi
    
    python3 ~/cc-autopipe/lib/state.py set-paused "$(pwd)" "$RESET_AT" "rate_limit"
    bash ~/cc-autopipe/lib/tg.sh "[$(basename $(pwd))] 429, resume at $RESET_AT"
fi
```

### 9.4 Resume from PAUSED

Orchestrator on each iteration:
```python
if state.phase == "paused":
    if datetime.now() < state.paused.resume_at:
        return  # not yet
    state.phase = "active"
    state_lib.write(project_path, state)
    log_event(project_path, "resumed_from_pause")
```

Always add 60s safety margin to `resume_at` to avoid hitting the limit again immediately.

---

## 10. Hooks

### 10.1 session-start.sh

**Purpose:** inject context summary at the start of every `claude -p` invocation. This is the task-boundary trigger.

**Input:** stdin JSON from Claude Code (session_id, cwd, etc).  
**Output:** plain text to stdout, becomes part of context.  
**Exit code:** always 0.

**Behavior:**
1. Read state.json for current phase, iteration, last score, consecutive_failures.
2. Read backlog.md for open task count.
3. Read last 3 lines of failures.jsonl.
4. If checkpoint.md exists, instruct Claude to read it first.
5. Output ~200 tokens of context.

**Pseudo:**
```bash
#!/bin/bash
PROJECT=$(pwd)
STATE="$PROJECT/.cc-autopipe/state.json"
BACKLOG="$PROJECT/backlog.md"

PHASE=$(jq -r '.phase' "$STATE" 2>/dev/null || echo "unknown")
ITER=$(jq -r '.iteration' "$STATE" 2>/dev/null || echo "0")
SCORE=$(jq -r '.last_score // "n/a"' "$STATE" 2>/dev/null)
FAILURES=$(jq -r '.consecutive_failures' "$STATE" 2>/dev/null || echo "0")
OPEN=$(grep -c '^- \[ \]' "$BACKLOG" 2>/dev/null || echo "0")

cat <<EOF
=== cc-autopipe context ===
Project: $(jq -r '.name' "$PROJECT/.cc-autopipe/config.yaml" 2>/dev/null)
Phase: $PHASE | Iteration: $ITER | Last score: $SCORE | Consecutive failures: $FAILURES
Open backlog tasks: $OPEN

EOF

if [ -f "$PROJECT/.cc-autopipe/checkpoint.md" ]; then
    echo "**RESUME:** Read .cc-autopipe/checkpoint.md FIRST and continue from there."
    echo ""
fi

if [ -f "$PROJECT/.cc-autopipe/memory/failures.jsonl" ]; then
    echo "Recent failures (last 3):"
    tail -3 "$PROJECT/.cc-autopipe/memory/failures.jsonl" | \
        jq -r '"  - \(.error): \(.details // "")"' 2>/dev/null
fi

# Log this hook fired
python3 ~/cc-autopipe/lib/state.py log-event "$PROJECT" hook_session_start
exit 0
```

### 10.2 pre-tool-use.sh

**Purpose:** deterministic enforcement of critical rules. Block dangerous actions with `exit 2`.

**Input:** stdin JSON `{tool_name, tool_input, ...}`.  
**Output:** stderr for block reason, stdout for additional context.  
**Exit code:** 0 = allow, 2 = block, 1 = error (treated as allow with warning).

**Block rules:**

1. **Bash: secrets in command**
   ```regex
   secrets\.env|\.aws/credentials|id_rsa|\.ssh/.*key|TG_BOT_TOKEN
   ```
   Reason: "Refusing command that touches secrets."

2. **Bash: destructive operations**
   ```regex
   git push.*--force|git push.*main|rm -rf [/~]|dd if=
   ```
   Reason: "Refusing destructive operation."

3. **Bash: long-running commands without nohup**
   ```regex
   (npm install|pip install|docker build|pytest --slow|python.*train)
   ```
   AND no `nohup` or trailing `&` in command. v0.5: just block; v1: suggest cc-autopipe-detach.
   Reason: "Long operation detected. Split into smaller steps for v0.5."

4. **Write/Edit: state.json**
   - file_path matches `*.cc-autopipe/state.json`
   - Reason: "state.json is engine-managed. Use cc-autopipe-checkpoint."

5. **Write/Edit: secrets in content**
   ```regex
   sk-ant-[a-zA-Z0-9]|TG_BOT_TOKEN=[0-9]+|ghp_[a-zA-Z0-9]|aws_secret
   ```
   Reason: "Refusing to write apparent secret."

6. **Write/Edit: .claude/settings.json modification**
   - file_path matches `*.claude/settings.json`
   - Reason: "settings.json is engine-managed. Hooks are in ~/cc-autopipe/."

**Pseudo:**
```bash
#!/bin/bash
INPUT=$(cat)
TOOL=$(echo "$INPUT" | jq -r '.tool_name // empty')
PROJECT=$(pwd)

block() {
    echo "$1" >&2
    # Log the block
    echo "{\"ts\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",\"error\":\"hook_pretooluse_blocked\",\"tool\":\"$TOOL\",\"reason\":\"$1\"}" \
        >> "$PROJECT/.cc-autopipe/memory/failures.jsonl"
    exit 2
}

case "$TOOL" in
    Bash)
        CMD=$(echo "$INPUT" | jq -r '.tool_input.command // empty')
        
        echo "$CMD" | grep -qE 'secrets\.env|\.aws/credentials|id_rsa|\.ssh/.*key|TG_BOT_TOKEN' && \
            block "secrets reference in command"
        
        echo "$CMD" | grep -qE 'git push.*--force|git push.*main|rm -rf [/~]|dd if=' && \
            block "destructive operation"
        
        # Long-op heuristic
        if echo "$CMD" | grep -qE '(npm install|pip install|docker build|pytest --slow|python.*train.*\.py)'; then
            if ! echo "$CMD" | grep -qE 'nohup|&[[:space:]]*$'; then
                block "long operation without nohup. Split into smaller steps in v0.5"
            fi
        fi
        ;;
    
    Write|Edit)
        FILE=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')
        CONTENT=$(echo "$INPUT" | jq -r '.tool_input.content // .tool_input.new_string // ""')
        
        [[ "$FILE" == *".cc-autopipe/state.json" ]] && \
            block "state.json is engine-managed"
        
        [[ "$FILE" == *".claude/settings.json" ]] && \
            block "settings.json is engine-managed"
        
        echo "$CONTENT" | grep -qE 'sk-ant-[a-zA-Z0-9_]{30,}|TG_BOT_TOKEN=[0-9]+|ghp_[a-zA-Z0-9]{30,}' && \
            block "refusing to write apparent secret"
        ;;
esac

exit 0
```

### 10.3 stop.sh

**Purpose:** run verify.sh, parse JSON, update state.

**Input:** stdin JSON from Claude Code (session_id, etc).  
**Output:** stdout decisions (mostly empty); state updates via state.py.  
**Exit code:** 0.

**Behavior:**
1. Run `.cc-autopipe/verify.sh` with timeout 60s.
2. Validate JSON output (must have `passed`, `score`, `prd_complete`).
3. On valid output: update state.json with score, passed, prd_complete.
4. On invalid/missing output: increment consecutive_failures, log to failures.jsonl.
5. If consecutive_failures resets (passed=true): set to 0.

**Pseudo:**
```bash
#!/bin/bash
PROJECT=$(pwd)
INPUT=$(cat)
SESSION=$(echo "$INPUT" | jq -r '.session_id // empty')

# Save session_id for next resume
[ -n "$SESSION" ] && python3 ~/cc-autopipe/lib/state.py set-session-id "$PROJECT" "$SESSION"

VERIFY="$PROJECT/.cc-autopipe/verify.sh"
if [ ! -x "$VERIFY" ]; then
    echo "{\"ts\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",\"error\":\"verify_missing\"}" \
        >> "$PROJECT/.cc-autopipe/memory/failures.jsonl"
    python3 ~/cc-autopipe/lib/state.py inc-failures "$PROJECT"
    exit 0
fi

RAW=$(timeout 60 "$VERIFY" 2>&1)
RC=$?

# Validate JSON
echo "$RAW" | jq -e '(.passed | type == "boolean") and (.score | type == "number") and (.prd_complete | type == "boolean")' >/dev/null 2>&1
if [ $? -ne 0 ]; then
    echo "{\"ts\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",\"error\":\"verify_malformed\",\"rc\":$RC,\"output\":$(echo "$RAW" | jq -Rs .)}" \
        >> "$PROJECT/.cc-autopipe/memory/failures.jsonl"
    python3 ~/cc-autopipe/lib/state.py inc-failures "$PROJECT"
    exit 0
fi

PASSED=$(echo "$RAW" | jq -r '.passed')
SCORE=$(echo "$RAW" | jq -r '.score')
PRD_DONE=$(echo "$RAW" | jq -r '.prd_complete')

python3 ~/cc-autopipe/lib/state.py update-verify "$PROJECT" \
    --passed "$PASSED" --score "$SCORE" --prd-complete "$PRD_DONE"

echo "{\"ts\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",\"event\":\"verify\",\"passed\":$PASSED,\"score\":$SCORE,\"prd_complete\":$PRD_DONE}" \
    >> "$PROJECT/.cc-autopipe/memory/progress.jsonl"

exit 0
```

### 10.4 stop-failure.sh

**Purpose:** handle API errors. Specifically 429 → PAUSED transition.

**Input:** stdin JSON `{error, error_details, ...}`.  
**Output:** state updates.  
**Exit code:** 0.

See §9.3 for pseudo. Behavior:
1. Match `error` field.
2. For `rate_limit` / `429`: use quota.py for resume_at, fall back to ladder. Set state.phase = "paused".
3. For other errors: increment consecutive_failures, TG alert.

---

## 11. Subagents

### 11.1 What we ship in v0.5

Two subagents in `templates/.cc-autopipe/agents.json`:

- **io-worker** (Haiku): Reads files >500 lines, multi-step IO chains, parses bulky stdout.
- **verifier** (Haiku): Runs verify.sh, returns parsed JSON.

That's it. No researcher, no reporter. Main session does WebSearch directly when needed.

### 11.2 Why minimal

- Each subagent spawn costs ~700 tokens overhead (context init + summary back).
- For small tasks, delegation costs more than it saves.
- Researcher is useful for big synthesis jobs; v0.5 doesn't have many of those.
- Reporter is non-critical for v0.5; main session writes one-line entries to reports/.

### 11.3 When main session delegates to io-worker

In `rules.md` template:
```markdown
Delegate to io-worker subagent ONLY when:
- File expected >500 lines
- Bash command output expected >5KB
- 3+ chained IO calls in sequence (read+grep+read)

Otherwise: use Read/Bash directly.
```

This is a soft rule (CLAUDE.md context). 70% adherence is acceptable here — wrong delegation is a token-cost optimization issue, not a correctness issue.

---

## 12. CLI surface

All commands under `cc-autopipe`:

### 12.1 `cc-autopipe init`

**Synopsis:** `cc-autopipe init [--force]`

**Behavior:**
1. Verify cwd is a git repo (warning only if not).
2. Refuse if `.cc-autopipe/` exists, unless `--force`.
3. Copy `~/cc-autopipe/templates/.cc-autopipe/` to cwd.
4. Generate state.json with phase=active, iteration=0.
5. Write `.claude/settings.json` with absolute hook paths.
6. Append cwd to `~/.cc-autopipe/projects.list` (if not already there).
7. Add gitignore entries.
8. Print next steps:
   ```
   ✓ cc-autopipe initialized
   Next steps:
     1. Edit .cc-autopipe/prd.md (define what to build)
     2. Edit .cc-autopipe/context.md (stack, constraints)
     3. Edit .cc-autopipe/verify.sh and chmod +x
     4. Edit .cc-autopipe/rules.md (project-specific rules)
     5. Run: cc-autopipe run . --once  (test mode)
     6. When ready: cc-autopipe start
   ```

### 12.2 `cc-autopipe start`

**Synopsis:** `cc-autopipe start [--background]`

**Behavior:**
1. Acquire singleton lock.
2. Run main loop in foreground (default).
3. SIGTERM/SIGINT trap → graceful shutdown after current cycle.
4. With `--background`: nohup detach (deferred to v1, error message in v0.5).

### 12.3 `cc-autopipe stop`

**Synopsis:** `cc-autopipe stop`

**Behavior:**
1. Read singleton lock PID.
2. Send SIGTERM.
3. Wait up to 60s for graceful exit.
4. If still running, send SIGKILL.

### 12.4 `cc-autopipe status`

**Synopsis:** `cc-autopipe status [--json]`

**Behavior:**

Default output (one-screen overview):
```
cc-autopipe v0.5.0 | Orchestrator: running (PID 12345, uptime 2h 34m)
5h quota: 67% (resets 18:30) | 7d quota: 42% (resets Sat)

PROJECT              PHASE    ITER  SCORE   LAST ACTIVITY
hello-fullstack      ACTIVE   12    0.78    30s ago
legal-parser         PAUSED   45    0.65    12m ago (resume in 18m)
trading-bot          DONE     78    0.96    2h ago

Recent events (last 5):
  15:24 hello-fullstack  verify_failed (score 0.78)
  15:25 hello-fullstack  cycle_end
  15:26 hello-fullstack  cycle_start (iter 13)
  15:30 legal-parser     paused (rate_limit_5h)
  16:15 hello-fullstack  done (score 0.94)
```

With `--json`: machine-readable.

### 12.5 `cc-autopipe tail`

**Synopsis:** `cc-autopipe tail`

**Behavior:** `tail -f ~/.cc-autopipe/log/aggregate.jsonl`, formatted human-readably.

### 12.6 `cc-autopipe run <project> --once`

**Synopsis:** `cc-autopipe run <path> --once`

**Behavior:** runs ONE cycle for the specified project, then exits. Used for testing. Bypasses singleton lock.

### 12.7 `cc-autopipe resume <project>`

**Synopsis:** `cc-autopipe resume <path>`

**Behavior:**
1. Read state.json.
2. If phase in {paused, failed}: set phase = active.
3. Reset consecutive_failures to 0.
4. Remove `.cc-autopipe/HUMAN_NEEDED.md` if exists.
5. Print confirmation.

### 12.8 `cc-autopipe block <project>`

**Synopsis:** `cc-autopipe block <path> [--reason "..."]`

**Behavior:**
1. Set phase = failed.
2. Write HUMAN_NEEDED.md with reason.
3. TG alert.

### 12.9 `cc-autopipe doctor`

**Synopsis:** `cc-autopipe doctor`

**Behavior:** verifies install:
- claude binary present and version >= CLAUDE_CODE_MIN_VERSION
- jq, flock present
- Python 3.11+ present
- ~/.cc-autopipe/secrets.env exists, chmod 600, TG creds present
- ~/cc-autopipe/hooks/* are executable
- ~/.claude/credentials.json readable (or macOS Keychain accessible)
- TG notification works (sends test message)
- oauth/usage endpoint reachable

Reports each as ✓ or ✗ with remediation hint.

---

## 13. Security model

### 13.1 Threat model

**In scope:**
- Curious or careless Claude actions (e.g., reading secrets file)
- Repo with malicious `.claude/settings.json`
- Prompt injection via PRD, context, web fetch
- Accidental secret leakage to logs, files, commit messages

**Out of scope:**
- Adversarial Claude (we trust Anthropic's alignment)
- Compromised Claude Code binary (system-level threat)
- Compromised host machine (system-level threat)
- Network adversary intercepting OAuth token (mitigated by HTTPS)

### 13.2 Defenses

**Defense 1: Hooks live outside repo.**
- Engine in `~/cc-autopipe/`, project repos cannot supply hook scripts.
- `.claude/settings.json` written by engine with absolute paths.
- Project repo's own `.claude/settings.json` is gitignored AND PreToolUse blocks Write/Edit on it.

**Defense 2: PreToolUse blocks secret access.**
- Pattern matches in command/content for known secret formats.
- Logs each block to failures.jsonl for audit.

**Defense 3: Secrets never enter Claude context.**
- secrets.env is sourced by tg.sh in subshell, not exported globally.
- SessionStart hook doesn't read or print secrets.
- Engine never passes secrets via env to claude subprocess.

**Defense 4: OAuth token is read-only used.**
- `quota.py` reads token, makes one HTTP request, doesn't log token.
- Token never touches Claude context.

**Defense 5: No remote execution by default.**
- All MCP servers we configure run locally (none in v0.5).
- WebFetch and WebSearch are native Claude tools, sandboxed by Anthropic.

### 13.3 Known gaps in v0.5

- Prompt injection in PRD/context.md: cannot prevent, can only mitigate physical impact via PreToolUse blocks.
- Output of WebSearch/WebFetch: not sanitized in v0.5 (researcher subagent in v1 will sanitize).
- Multi-tenant abuse on same MAX subscription: out of scope.

---

## 14. Failure handling

See `03-failure-modes.md` from previous round for full matrix. Summary of v0.5 coverage:

| Category | v0.5 | v1+ |
|---|---|---|
| Rate limit (5h, 7d) | Pre-flight + StopFailure | + Active monitoring |
| Process crash | Heartbeat lock detection | + auto-restart |
| State corruption | Atomic write + recovery | + journal log |
| Verify malformed | Schema validation | + auto-repair |
| Hook crash | timeout 30s | + hook health monitoring |
| Network failure | Fire-and-forget TG | + offline queue |
| Disk full | Pre-flight check | + auto cleanup |
| Context rot | --max-turns 35 cap | + pre-compact survival |
| Session ID expired | Fresh session fallback | (same) |

**One v0.5 limitation:** if both quota.py and the 429 ladder fail (e.g., persistent network issue), pipeline can loop quickly retrying. Mitigation: minimum 30s cooldown between cycles, plus consecutive_failures cap at 3. Worst case: ~3 retries in 90s, then FAILED with TG alert.

---

## 15. Observability

### 15.1 Three log layers

**Layer 1: per-project**
- `<project>/.cc-autopipe/memory/progress.jsonl` — tool calls, hook fires, verify results
- `<project>/.cc-autopipe/memory/failures.jsonl` — errors, blocks, malformed verify

**Layer 2: aggregate**
- `~/.cc-autopipe/log/aggregate.jsonl` — every state transition across all projects

**Layer 3: orchestrator stdout**
- When `cc-autopipe start` runs in foreground, prints to stdout
- `cc-autopipe tail` follows aggregate.jsonl

### 15.2 What goes where

| Event | progress.jsonl | failures.jsonl | aggregate.jsonl | TG |
|---|---|---|---|---|
| cycle_start | ✓ | | ✓ | |
| cycle_end | ✓ | | ✓ | |
| tool_call | ✓ | | | |
| hook_fired | ✓ | | | |
| verify_pass | ✓ | | | |
| verify_fail | ✓ | ✓ | | |
| verify_malformed | | ✓ | ✓ | |
| pretooluse_blocked | | ✓ | | |
| paused (429) | | | ✓ | ✓ |
| resumed | | | ✓ | |
| done | | | ✓ | ✓ |
| failed | | | ✓ | ✓ |
| state_corrupted | | | ✓ | ✓ |

### 15.3 TG message conventions

Format: `[<project>] <event>: <details>`

Examples:
- `[hello-fullstack] PRD complete, score 0.94`
- `[legal-parser] 429, resume at 18:30Z`
- `[trading-bot] FAILED after 3 consecutive failures. See HUMAN_NEEDED.md`
- `7d quota at 91%, all projects pausing until Sat`

---

## 16. Implementation order

Suggested order to implement v0.5:

### Stage A: foundations (~250 lines)
1. `lib/compat.sh` — platform detection
2. `lib/state.py` — atomic state.json R/W with schema
3. `lib/tg.sh` — TG fire-and-forget
4. `install.sh` — minimal: copy files, set perms
5. `helpers/cc-autopipe` — bash dispatcher with subcommand routing

### Stage B: orchestrator skeleton (~150 lines)
1. `orchestrator` — main loop with FIFO, no locking yet
2. `cc-autopipe init` — create .cc-autopipe/ from templates
3. `cc-autopipe status` — read state.json from each project, format

### Stage C: hooks (~200 lines)
1. `hooks/session-start.sh` — context summary injection
2. `hooks/stop.sh` — verify.sh runner with schema validation
3. `hooks/pre-tool-use.sh` — block dangerous actions
4. `hooks/stop-failure.sh` — 429 handling with state transitions

### Stage D: locking and recovery (~100 lines)
1. Singleton orchestrator lock with stale detection
2. Per-project lock with heartbeat
3. Crash recovery on `cc-autopipe start`
4. Test: kill -9 mid-cycle, verify recovery

### Stage E: quota awareness (~150 lines)
1. `lib/quota.py` — oauth/usage endpoint with Keychain support
2. `lib/ratelimit.py` — fallback ladder
3. Pre-flight check in orchestrator
4. Wire stop-failure.sh to use quota.py first

### Stage F: helpers and CLI (~150 lines)
1. `helpers/cc-autopipe-checkpoint`
2. `helpers/cc-autopipe-block`
3. `cc-autopipe resume`
4. `cc-autopipe doctor`
5. `cc-autopipe tail`
6. `cc-autopipe run <project> --once`

### Stage G: hello-fullstack smoke test (project itself, not engine code)
1. Initialize hello-fullstack with cc-autopipe init
2. Fill prd.md with full-stack acceptance criteria
3. Fill context.md
4. Write verify.sh covering pytest + npm build + docker compose check
5. Run cc-autopipe start
6. Watch via cc-autopipe status / tail

**Total budget:** ~1000-1100 lines for engine, plus ~200 lines for hello-fullstack project setup.

---

## 17. Acceptance criteria

### 17.1 Engine v0.5

- [ ] `cc-autopipe doctor` passes all checks on fresh Ubuntu 22.04 install.
- [ ] `cc-autopipe doctor` passes all checks on fresh macOS 14 install.
- [ ] `cc-autopipe init` succeeds in empty git repo.
- [ ] `cc-autopipe init --force` overwrites .cc-autopipe/ correctly.
- [ ] `cc-autopipe start` acquires lock, prevents second instance.
- [ ] kill -9 mid-cycle, restart → resumes within 60s, no state loss.
- [ ] State.json corruption → recovers with TG alert and reset to iteration=0.
- [ ] verify.sh malformed JSON → marked as failure, consecutive_failures incremented.
- [ ] verify.sh hangs >60s → killed, marked as failure.
- [ ] Hook script hangs >30s → killed by Claude Code timeout.
- [ ] PreToolUse blocks `cat ~/.cc-autopipe/secrets.env`.
- [ ] PreToolUse blocks Write to .cc-autopipe/state.json.
- [ ] PreToolUse blocks `pip install` without nohup.
- [ ] StopFailure on 429 transitions to PAUSED with correct resume_at.
- [ ] Pre-flight quota check (>95% 5h) transitions to PAUSED before sending request.
- [ ] TG receives notifications for: DONE, FAILED, PAUSED, state_corrupted.
- [ ] aggregate.jsonl has every state transition for every project.
- [ ] `cc-autopipe status` displays correct quota and project states.

### 17.2 Hello-fullstack smoke test

- [ ] PRD reaches DONE in <4 hours of pipeline runtime.
- [ ] Total Sonnet 4.6 message burn: <100.
- [ ] All 4 hooks fired (verified in progress.jsonl).
- [ ] Pipeline survives manually-induced 429 with proper resume.
- [ ] Final docker-compose up brings up working app.
- [ ] frontend → API → backend flow works end-to-end manually.

### 17.3 Stress tests (optional but recommended)

- [ ] Run for 24h on hello-fullstack (no real work, just loop survival).
- [ ] Inject random kill -9 every 30 minutes for 4 hours.
- [ ] Disconnect internet for 5 minutes mid-cycle, verify graceful failure.
- [ ] Fill disk to <50MB free, verify SessionStart refuses.

---

## 18. Out of scope

Explicitly NOT included in v0.5:

| Feature | Defer to | Why |
|---|---|---|
| Multi-project parallel | v2 | Server throttling at 4+ sessions |
| DETACHED state | v1 | Long ops just blocked in v0.5 |
| Researcher subagent | v1 | Not needed for hello-fullstack |
| Reporter subagent | v1 | Main session writes inline |
| Worktree parallel-impl | v2 | 3× quota cost, edge cases huge |
| doobidoo MCP memory | v2 | Heavy infra for low-frequency need |
| Path-scoped CLAUDE.md | v1 | Adds complexity to init |
| Multi-machine coordination | v2 | Account-wide rate limit blocks it |
| Pre-compact survival | v1 | --max-turns 35 mostly avoids compact |
| Auto-escalation to Opus | v1 | Manual flag in v0.5 |
| systemd/launchd integration | v1 | Manual start in v0.5 |
| Web dashboard | never | Terminal sufficient |
| Slack/Discord | never | TG sufficient |
| Per-project CLAUDE.md auto-import | v1 | Manual @-import in v0.5 |
| Session JSONL cleanup | v1 | Disk space is plentiful |

---

## 19. Open questions

These need answers before or during implementation:

### Q1. Exact format of `oauth/usage` response in Apr 2026
We have a sample from codelynx.dev (~Oct 2025). Format may have changed. Mitigation: quota.py is defensive, returns None on parse failure, falls through to ladder.

### Q2. Behavior of --resume when JSONL file deleted
If `~/.claude/projects/*/{session_id}.jsonl` is missing, does `claude --resume` error or start fresh? We assume error → caught in orchestrator, fresh session started. Verify in stage D.

### Q3. Claude Code 2.1+ hook input/output format stability
Specifically, `Stop` event passing `session_id` reliably across versions. Spec assumes yes; verify in stage C.

### Q4. macOS Keychain permission prompt
First call to `security find-generic-password -s "Claude Code-credentials" -w` may prompt user. Verify behavior; document in install.sh if interactive prompt is needed.

### Q5. Behavior of `--max-turns 35` when checkpoint exists
If Claude resumes via checkpoint and immediately finishes one task, is "max-turns" counter reset on each `claude -p`? Documentation suggests yes (per-invocation). Verify.

### Q6. backlog.md tag semantics
v0.5 only acts on `[ ]` open and `[x]` done. Tags `[architect]`, `[parallel-impl]` are placeholders for v1+. Spec says engine ignores them in v0.5; verify no parsing breaks if present.

### Q7. Newline handling in TG messages
Telegram requires `\n` not literal newlines in some cases. tg.sh wraps with `-d "text=$MSG"` — verify multiline messages render correctly.

### Q8. Locking on macOS
`flock` from coreutils on macOS works differently than Linux. Verify in compat.sh, possibly use `shlock` or different primitive on Darwin.

---

## End of specification

Total spec: ~1100 lines (this document) describing ~1100 lines of implementation. 1:1 ratio is reasonable for production-grade engineering.

Next step after review: implement Stage A (foundations), validate, then proceed sequentially through Stages B-G.
