# SPEC-v1.md — Extension to SPEC.md for v0.5.1 and v1.0

**Reads alongside:** SPEC.md (v0.5 product spec)
**Status:** Implementation-ready
**Audience:** Claude Code agent implementing v0.5.1 + v1.0 across 4 batches

This document EXTENDS SPEC.md. Where they conflict, SPEC-v1.md wins.

---

## Part 1: v0.5.1 — Cleanup batch (Batch a)

Three fixes accumulated during v0.5 build that didn't block production
but should be addressed before v1.0 features.

### 1.1 Q15: Project rules.md template requires atomic commits

**Problem:** Stage G hello-fullstack ran successfully but produced ZERO
git commits inside the project repo. Generated code lives in working
tree without history. Engine's own AGENTS.md required atomic commits
for the build, but project-level `rules.md` template (used by
`cc-autopipe init`) does not.

**Fix:**
Update `src/templates/.cc-autopipe/rules.md.example` (the template
copied by `cc-autopipe init`) to include:

```markdown
## Workflow discipline

- After each implemented component (single file or tightly-related
  pair), commit atomically with descriptive message.
- Do NOT batch unrelated changes into single commit.
- Commit message format: `<scope>: <imperative summary>`.
- Tests for a component get separate commit from implementation.
- After completing a backlog task, commit before marking [x].
- Never `git push` — agent commits locally only; push is human action.

## Forbidden git operations

- `git push` (any remote)
- `git tag` (release tagging is human action)
- `git reset --hard` to undo recent commits without justification
- `git commit --amend` after the commit was made
```

### 1.2 verify.sh template grep bug

**Problem:** verify.sh.example template uses
```bash
UNCHECKED=$(grep -c '^- \[ \]' "$PRD" 2>/dev/null || echo 0)
```

When grep finds 0 matches, exit code is 1, the `|| echo 0` fires AFTER
grep already wrote `0` to stdout. Result is `"0\n0"` which fails
integer comparison `[ "$UNCHECKED" -eq 0 ]`.

**Fix in `src/templates/.cc-autopipe/verify.sh.example`:**
```bash
UNCHECKED=$(grep -c '^- \[ \]' "$PRD" 2>/dev/null || true)
UNCHECKED=${UNCHECKED:-0}
```

Verify after fix: in fresh project with all PRD items checked,
`bash .cc-autopipe/verify.sh | jq .prd_complete` returns `true`.

### 1.3 cc-autopipe stop subcommand

**Problem:** SPEC.md §12.3 declared `cc-autopipe stop`. v0.5 deferred it
because singleton lock + SIGTERM provides equivalent functionality. But
users (Roman) expect a documented command per `--help` listing.

**Implementation:**
Add `cc-autopipe stop` subcommand:

```python
# src/cli/stop.py
def stop(args):
    """Stop running orchestrator gracefully via SIGTERM."""
    pid_file = user_home / "orchestrator.pid"
    if not pid_file.exists():
        print("orchestrator: not running", file=sys.stderr)
        return 0  # Not an error
    
    try:
        pid = int(pid_file.read_text().strip().split()[0])
    except (ValueError, OSError):
        print(f"orchestrator: stale PID file at {pid_file}", file=sys.stderr)
        return 1
    
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        print(f"orchestrator: PID {pid} not running (stale lock)", file=sys.stderr)
        return 0
    except PermissionError:
        print(f"orchestrator: PID {pid} owned by another user", file=sys.stderr)
        return 1
    
    # Wait up to N seconds for graceful exit
    timeout = args.timeout if hasattr(args, 'timeout') else 60
    for _ in range(timeout * 2):
        try:
            os.kill(pid, 0)  # Check if alive
        except ProcessLookupError:
            print(f"orchestrator: stopped (PID {pid})")
            return 0
        time.sleep(0.5)
    
    # Force kill if still alive
    print(f"orchestrator: SIGTERM timeout, sending SIGKILL", file=sys.stderr)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    return 0
```

Wire into `src/helpers/cc-autopipe` dispatcher.

### 1.4 v0.5.1 acceptance

- [ ] rules.md.example template updated with workflow discipline section
- [ ] verify.sh.example template grep bug fixed
- [ ] `cc-autopipe stop` subcommand implemented
- [ ] `cc-autopipe stop` tested: graceful, force-kill, no orchestrator, stale PID
- [ ] All 6 v0.5 smokes still pass
- [ ] Tag v0.5.1

---

## Part 2: v1.0 — Production hardening

### 2.1 Stage H: DETACHED state

**Goal:** allow long-running operations (>10 minutes) without holding
orchestrator slot. Operations launch in background, orchestrator
releases slot, periodically polls for completion via cheap check_cmd.

#### 2.1.1 Architecture

```
ACTIVE → (cc-autopipe-detach called) → DETACHED → (check_cmd succeeds) → ACTIVE
                                            ↓
                                    (max_wait_sec exceeded) → FAILED
```

State.json schema addition:
```json
{
  "phase": "detached",
  "detached": {
    "reason": "training model",
    "started_at": "2026-05-15T10:00:00Z",
    "check_cmd": "ls models/checkpoint_*.pt | wc -l | grep -q '^[1-9]'",
    "check_every_sec": 600,
    "max_wait_sec": 14400,
    "last_check_at": null,
    "checks_count": 0
  }
}
```

#### 2.1.2 cc-autopipe-detach helper

`src/helpers/cc-autopipe-detach`:

```bash
#!/bin/bash
# Usage: cc-autopipe-detach --reason "<text>" --check-every <sec> \
#                           --check-cmd "<cheap bash>" [--max-wait <sec>]
#
# Called by Claude inside a session before launching a long-running
# background task. Updates state.json to phase=detached, releases slot.
#
# Exits 0 on success. Engine takes over from there.

set -euo pipefail

REASON=""
CHECK_EVERY="${CC_AUTOPIPE_DEFAULT_CHECK_EVERY:-600}"
CHECK_CMD=""
MAX_WAIT="${CC_AUTOPIPE_DEFAULT_MAX_WAIT:-14400}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --reason) REASON="$2"; shift 2 ;;
        --check-every) CHECK_EVERY="$2"; shift 2 ;;
        --check-cmd) CHECK_CMD="$2"; shift 2 ;;
        --max-wait) MAX_WAIT="$2"; shift 2 ;;
        *) echo "Unknown arg: $1" >&2; exit 1 ;;
    esac
done

[ -z "$REASON" ] && { echo "--reason required" >&2; exit 1; }
[ -z "$CHECK_CMD" ] && { echo "--check-cmd required" >&2; exit 1; }

PROJECT_DIR="$(pwd)"
STATE="$PROJECT_DIR/.cc-autopipe/state.json"

if [ ! -f "$STATE" ]; then
    echo "Not in a cc-autopipe project (no .cc-autopipe/state.json)" >&2
    exit 1
fi

python3 "${CC_AUTOPIPE_HOME}/lib/state.py" set-detached "$PROJECT_DIR" \
    --reason "$REASON" \
    --check-cmd "$CHECK_CMD" \
    --check-every "$CHECK_EVERY" \
    --max-wait "$MAX_WAIT"

echo "Detached. Engine will resume when check_cmd succeeds."
```

#### 2.1.3 Orchestrator: DETACHED handling

In `process_project`, BEFORE current ACTIVE-phase logic:

```python
if s.phase == "detached":
    now = datetime.now(timezone.utc)
    started = parse_iso(s.detached["started_at"])
    
    # Max wait timeout
    if (now - started).total_seconds() > s.detached["max_wait_sec"]:
        s.phase = "failed"
        state.write(project_path, s)
        state.log_event(project_path, "detached_timeout",
                        elapsed=(now - started).total_seconds())
        notify_tg(f"[{project_path.name}] DETACHED timeout — check check_cmd")
        return
    
    # Check interval not yet reached
    last_check = parse_iso(s.detached["last_check_at"]) if s.detached["last_check_at"] else started
    if (now - last_check).total_seconds() < s.detached["check_every_sec"]:
        return  # Wait for next iteration
    
    # Run check_cmd
    rc = subprocess.run(
        ["bash", "-c", s.detached["check_cmd"]],
        cwd=project_path,
        timeout=30,
        capture_output=True,
    ).returncode
    
    s.detached["last_check_at"] = now.isoformat().replace("+00:00", "Z")
    s.detached["checks_count"] += 1
    state.write(project_path, s)
    
    if rc == 0:
        # Operation complete, transition to ACTIVE
        state.log_event(project_path, "detach_resumed",
                        checks=s.detached["checks_count"])
        s.phase = "active"
        s.detached = None
        state.write(project_path, s)
        # Fall through to normal ACTIVE processing
    else:
        return  # Still running, wait next iteration
```

#### 2.1.4 PreToolUse: relax long-bash block when nohup detected

Current `src/hooks/pre-tool-use.sh` blocks any long bash without nohup.
v1.0 keeps that, but if `cc-autopipe-detach` was just called recently,
allow nohup-launched commands:

```bash
# Allow nohup-launched commands that are followed by cc-autopipe-detach
if echo "$CMD" | grep -qE 'nohup.*&[[:space:]]*$' && \
   echo "$CMD" | grep -qE 'cc-autopipe-detach'; then
    exit 0  # Explicitly allowed
fi
```

#### 2.1.5 Stage H acceptance

- [ ] `cc-autopipe-detach` helper in src/helpers/, executable
- [ ] `state.py` `set_detached()` function + CLI subcommand
- [ ] orchestrator `process_project` handles DETACHED phase
- [ ] PreToolUse allows nohup+detach pattern
- [ ] Test: project transitions ACTIVE→DETACHED→ACTIVE on check_cmd success
- [ ] Test: project transitions DETACHED→FAILED on max_wait timeout
- [ ] Test: orchestrator does not hold slot during DETACHED
- [ ] Smoke `stage-h.sh`: full flow with mock check_cmd
- [ ] Real test: 30-second sleep loop simulating long task

### 2.2 Stage I: Researcher and Reporter subagents

#### 2.2.1 Researcher

Add to `src/templates/.cc-autopipe/agents.json`:

```json
{
  "researcher": {
    "description": "Web research before implementation. Use when backlog task is tagged [research] or when implementation requires unfamiliar API/library knowledge.",
    "prompt": "You research the web. For each topic: WebSearch (3-5 queries), synthesize findings into research/<topic>.md with sections: Summary (3 sentences), Key Findings (bulleted, specific), Implementation Recommendations, Sources with relevance scores. Do NOT implement code.",
    "tools": ["WebSearch", "WebFetch", "Write", "Read"],
    "model": "sonnet",
    "effort": "medium",
    "maxTurns": 12
  }
}
```

Main session delegates via `task` tool when:
- Task tagged `[research]` in backlog
- Implementation requires non-obvious external knowledge
- Pre-existing research/ directory has nothing relevant

#### 2.2.2 Reporter

Add to `src/templates/.cc-autopipe/agents.json`:

```json
{
  "reporter": {
    "description": "Per-iteration progress report. Run after a productive cycle (>=2 backlog items completed).",
    "prompt": "Read memory/progress.jsonl, last 5 verify scores, current backlog. Write reports/iteration-NNN.md with: Tasks Completed (bulleted), Tasks Blocked (with reasons), Score Trend, Lessons Learned (1-3 bullets), Backlog Status (N tasks remaining), Recommended Next Focus.",
    "tools": ["Read", "Write"],
    "model": "haiku",
    "maxTurns": 5,
    "background": true
  }
}
```

Main session delegates via `task` tool at end of cycle if cycle was productive.

#### 2.2.3 Stage I acceptance

- [ ] Researcher subagent added to template
- [ ] Reporter subagent added to template
- [ ] Project init (`cc-autopipe init`) provisions both subagents
- [ ] Test: existing v0.5.1 projects still work without re-init
- [ ] Smoke `stage-i.sh`: subagents documented as available, main agent
      can invoke them via task tool (mock-claude scenario)

### 2.3 Stage J: Phase split for large PRDs

#### 2.3.1 Problem

Long PRDs (50+ tasks) exhaust context, accumulate cruft in MEMORY.md,
and make verify.sh slow. Solution: split PRD into named phases, each
with its own backlog and acceptance criteria.

#### 2.3.2 PRD format extension

```markdown
# PRD: <project>

## Phases

### Phase 1: Foundation
**Acceptance:** All Phase 1 items checked AND verify.sh score >= 0.85

- [ ] Item 1.1
- [ ] Item 1.2
- [ ] Item 1.3

### Phase 2: API
**Acceptance:** All Phase 2 items checked AND verify.sh score >= 0.85

- [ ] Item 2.1
- [ ] Item 2.2

### Phase 3: Frontend
**Acceptance:** ...

- [ ] Item 3.1
```

#### 2.3.3 State extension

```json
{
  "current_phase": 1,
  "phases_completed": []
}
```

#### 2.3.4 Orchestrator behavior

When current phase items all checked AND verify passes:
1. Move current_phase to phases_completed
2. Increment current_phase
3. Archive completed backlog tasks to `backlog-archive.md`
4. Reset session_id (start fresh context for new phase)
5. Log `phase_transition` event
6. TG notification

If all phases complete: project DONE.

#### 2.3.5 Stage J acceptance

- [ ] PRD parser recognizes `### Phase N:` headers
- [ ] state.json schema extended with current_phase
- [ ] Orchestrator transitions phases correctly
- [ ] Session reset on phase transition (fresh context)
- [ ] backlog-archive.md created and populated
- [ ] Smoke `stage-j.sh`: 3-phase mock PRD progresses through all
- [ ] Backward compat: PRDs without phases work as single phase

### 2.4 Stage K: Weekly cap proactive monitoring

#### 2.4.1 Problem

5h cap is fine — orchestrator pre-flight check catches it. 7d cap
sneaks up: by the time pre-flight catches >95%, you've already spent
the week. Need proactive warning.

#### 2.4.2 Background monitor

Add `src/lib/quota_monitor.py`:

```python
# Daemon thread inside orchestrator
def quota_monitor_loop():
    """Background: every 30 min, check 7d quota.
    Warn at 70%, 80%, 90%. Emergency-pause-all at 95%.
    """
    while not shutdown:
        try:
            q = quota.read_cached()
            if q is None:
                time.sleep(1800)
                continue
            
            pct = q.seven_day_pct
            today = date.today().isoformat()
            warned_path = user_home / f"7d-warn-{today}.flag"
            
            if pct >= 0.95:
                # Already handled by pre-flight, just TG
                if not (user_home / f"7d-emergency-{today}.flag").exists():
                    notify_tg(f"7d quota at {int(pct*100)}% — all projects pausing")
                    (user_home / f"7d-emergency-{today}.flag").touch()
            elif pct >= 0.90:
                if not (user_home / f"7d-warn90-{today}.flag").exists():
                    notify_tg(f"7d quota at {int(pct*100)}% — DANGER, slow down")
                    (user_home / f"7d-warn90-{today}.flag").touch()
            elif pct >= 0.80:
                if not (user_home / f"7d-warn80-{today}.flag").exists():
                    notify_tg(f"7d quota at {int(pct*100)}% — warning")
                    (user_home / f"7d-warn80-{today}.flag").touch()
            elif pct >= 0.70:
                if not (user_home / f"7d-warn70-{today}.flag").exists():
                    notify_tg(f"7d quota at {int(pct*100)}% — heads up")
                    (user_home / f"7d-warn70-{today}.flag").touch()
        except Exception as e:
            log_warning(f"quota_monitor error: {e}")
        
        time.sleep(1800)  # 30 minutes
```

Started as daemon thread in orchestrator main().

#### 2.4.3 Stage K acceptance

- [ ] quota_monitor module
- [ ] Daemon thread starts with orchestrator
- [ ] TG warnings at 70/80/90% (one per day per threshold)
- [ ] Emergency at 95% (transitions all to PAUSED)
- [ ] Test: simulate quota progression, verify warnings fire correctly
- [ ] Smoke `stage-k.sh`: monitor lifecycle, warning dedup logic

### 2.5 Stage L: Auto-escalation to Opus

#### 2.5.1 Mechanism

When `consecutive_failures >= 3` AND auto-escalation enabled:
- Next cycle uses `--model claude-opus-4-7 --effort xhigh`
- Inject reminder in prompt: "Previous Sonnet cycles failed. Reconsider approach."
- After successful Opus cycle: revert to Sonnet for following cycles
- Log `escalated_to_opus` event

Per-project config in config.yaml:
```yaml
auto_escalation:
  enabled: true
  trigger_consecutive_failures: 3
  escalate_to: claude-opus-4-7
  effort: xhigh
  revert_after_success: true
```

Default: enabled=true.

#### 2.5.2 Stage L acceptance

- [ ] Config schema includes auto_escalation section
- [ ] Orchestrator checks consecutive_failures and escalates per config
- [ ] Reminder text injected in prompt on escalation cycle
- [ ] Reverts to default model after successful cycle
- [ ] Tests cover: escalation triggers, reminder injection, revert
- [ ] Smoke `stage-l.sh`: 3 mock failures → opus model in cmd args

### 2.6 Stage M: systemd / launchd integration

#### 2.6.1 Linux systemd

`src/init/cc-autopipe.service.template`:

```ini
[Unit]
Description=cc-autopipe orchestrator
After=network.target

[Service]
Type=simple
User=__USER__
WorkingDirectory=__HOME__
Environment="CC_AUTOPIPE_HOME=__CC_AUTOPIPE_HOME__"
Environment="PATH=__PATH__"
ExecStart=__CC_AUTOPIPE_HOME__/orchestrator
Restart=on-failure
RestartSec=30
StandardOutput=append:__HOME__/.cc-autopipe/log/systemd.log
StandardError=append:__HOME__/.cc-autopipe/log/systemd.log

[Install]
WantedBy=default.target
```

Install via `cc-autopipe install-systemd`:
```bash
# Substitutes placeholders, copies to ~/.config/systemd/user/
# Reloads daemon, prints enable/start instructions
```

#### 2.6.2 macOS launchd

`src/init/com.cc-autopipe.plist.template`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0">
<dict>
    <key>Label</key><string>com.cc-autopipe</string>
    <key>ProgramArguments</key>
    <array>
        <string>__CC_AUTOPIPE_HOME__/orchestrator</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>CC_AUTOPIPE_HOME</key><string>__CC_AUTOPIPE_HOME__</string>
        <key>PATH</key><string>__PATH__</string>
    </dict>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key><false/>
    </dict>
    <key>StandardOutPath</key><string>__HOME__/.cc-autopipe/log/launchd.log</string>
    <key>StandardErrorPath</key><string>__HOME__/.cc-autopipe/log/launchd.log</string>
</dict>
</plist>
```

Install via `cc-autopipe install-launchd`.

#### 2.6.3 Stage M acceptance

- [ ] systemd .service template + install command (Linux)
- [ ] launchd .plist template + install command (macOS)
- [ ] Test installation creates correct files in correct locations
- [ ] Test uninstallation removes them cleanly
- [ ] Documentation in README.md or QUICKSTART.md
- [ ] Smoke `stage-m.sh`: install + uninstall on host platform

### 2.7 Stage N: Skill crystallization

#### 2.7.1 Concept

After successful task completion, `improver` agent identifies reusable
patterns and writes them to `<project>/.claude/skills/<name>/SKILL.md`.
Future Claude sessions in that project auto-discover skills via Claude
Code 2.1.108+ skill discovery.

#### 2.7.2 Improver agent

Add to `src/templates/.cc-autopipe/agents.json`:

```json
{
  "improver": {
    "description": "Reflect on completed cycles, propose skills. Run after every 5 successful cycles or when explicitly invoked.",
    "prompt": "Read last 5 reports/iteration-*.md and memory/success_patterns.jsonl. Identify: 1) Patterns used in 2+ tasks, 2) Solutions to non-obvious problems, 3) Project-specific conventions. For each: write .claude/skills/<short-name>/SKILL.md following Claude Code skill format. Do NOT modify project source code.",
    "tools": ["Read", "Write"],
    "model": "sonnet",
    "effort": "medium",
    "maxTurns": 10,
    "background": true
  }
}
```

Triggered by orchestrator after every Nth successful cycle.

#### 2.7.3 Stage N acceptance

- [ ] Improver subagent added to template
- [ ] Orchestrator triggers improver every N cycles (configurable)
- [ ] Skills directory `.claude/skills/` created if absent
- [ ] Test: improver creates SKILL.md after mock cycle history
- [ ] Smoke `stage-n.sh`: skill discovered and named correctly

---

## Part 3: Cross-cutting concerns for v1.0

### 3.1 Updated state.json schema (full v1.0)

```json
{
  "schema_version": 2,
  "name": "...",
  "phase": "active|paused|done|failed|detached",
  "iteration": 0,
  "current_phase": 1,
  "phases_completed": [],
  "session_id": "...",
  "last_score": 0.0,
  "last_passed": false,
  "prd_complete": false,
  "consecutive_failures": 0,
  "last_cycle_started_at": null,
  "last_progress_at": null,
  "threshold": 0.85,
  "paused": null,
  "detached": null,
  "escalated_next_cycle": false
}
```

Migration: state.py reads schema_version. If 1, auto-upgrade in-place
on next write (add new fields with default values).

### 3.2 Backward compatibility

All v0.5 projects must continue to work after v1.0 upgrade:
- Single-phase PRDs (no `### Phase N:` headers) treated as one phase
- Missing detached/escalated fields default to None/false
- v0.5 agents.json (without researcher/reporter/improver) works as-is
- Existing state.json migrates on first write

### 3.3 New OPEN_QUESTIONS.md entries expected

Pre-populated for v1.0 work:

- Q16: Does Claude Code 2.1.108+ skill discovery actually load
       project-local `.claude/skills/`? (Stage N investigation)
- Q17: launchd permission for SystemNetworking on macOS — need to
       handle? (Stage M investigation)
- Q18: How to test PreToolUse relaxation for nohup+detach pattern
       without real long-running operations? (Stage H investigation)
- Q19: Quota monitor daemon interaction with singleton lock — does
       monitor count as "orchestrator activity" for stale detection?
       (Stage K investigation)

### 3.4 Implementation footprint estimate

| Stage | Component | Lines |
|---|---|---|
| Batch a | rules.md.example update | +15 |
| Batch a | verify.sh.example fix | +1 |
| Batch a | cc-autopipe stop | ~80 |
| Batch a | tests for stop | ~80 |
| Batch b | Stage H (DETACHED) | ~250 |
| Batch b | Stage I (R/R subagents) | ~80 (templates) + tests |
| Batch b | Stage J (Phase split) | ~200 |
| Batch c | Stage K (Quota monitor) | ~150 |
| Batch c | Stage L (Auto-escalation) | ~120 |
| Batch d | Stage M (systemd/launchd) | ~180 |
| Batch d | Stage N (Skill crystallization) | ~100 |
| Tests for everything above | ~600-800 |
| Smoke validators (5 new) | ~250 |

Total v1.0 addition: ~2200-2500 lines of code, bringing engine to
~6200-6500 lines.

---

## End of SPEC-v1.md

Read AGENTS-v1.md for implementation workflow with automated gates.
