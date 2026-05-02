# SPEC-v1.2.md — v1.2 fix pack: production hardening from real-world R&D test

**Reads alongside:** SPEC.md (v0.5), SPEC-v1.md (v1.0)
**Status:** Implementation-ready
**Audience:** Claude Code agent autonomously building v1.2 across 3 batches

This document EXTENDS SPEC.md and SPEC-v1.md. Where they conflict,
SPEC-v1.2.md wins.

---

## Background — what v1.0 missed

v1.0 was validated on hello-fullstack (linear web app). Real-world test
on AI-trade (ML R&D) revealed 8 structural issues that caused engine to
loop, burn quota, and produce no validated work despite 5 cycles.

Root cause patterns:
1. State coupling between Claude session work and engine state.json was
   one-way (Claude updates files, engine doesn't know what Claude is
   doing).
2. verify.sh contract was binary (passed/failed) — no third state for
   "work in progress, not yet verifiable".
3. Failure handling assumed Claude is "weak" (escalate to Opus). Real
   pattern: failures are structural (verify can't see work because
   files aren't where it expects).
4. DETACHED state existed but Claude was never instructed to use it
   for long ML training operations.
5. Backlog discipline (FIFO + priority) was suggestion, not enforced.
6. Multi-cycle tasks (Stage A → B → C of single PRD item) had no
   structured progress tracking — each cycle started from scratch
   conceptually.

v1.2 addresses all 8 (A-H below).

---

## Bug A — state.json must track current task and artifact

### Problem
state.json schema_v2 has no field for "what task is Claude working on
right now". verify.sh has to guess. In R&D, this means verify.sh
fallback to "find newest exp_cand_*/ directory" — fragile.

### Fix

**Schema bump to v3.** New fields:

```json
{
  "schema_version": 3,
  ...
  "current_task": {
    "id": "cand_imbloss_v2",
    "started_at": "2026-05-02T18:54:00Z",
    "stage": "training",
    "stages_completed": [],
    "artifact_paths": [
      "data/models/exp_cand_imbloss_v2/"
    ],
    "claude_notes": "Free-text from Claude about current sub-step"
  },
  ...
}
```

`current_task.id` matches a `[~]` task in backlog.md. `stage` is
free-form (Claude updates via Stop hook). `artifact_paths` is list of
files/dirs Claude is creating — verify.sh reads these to know where
to look.

### Mechanism

- Claude writes `.cc-autopipe/CURRENT_TASK.md` at start of work:
  ```
  task: cand_imbloss_v2
  stage: training
  artifact: data/models/exp_cand_imbloss_v2/
  notes: SwingLoss with class_balance_beta=0.999, training started
  ```
- Stop hook reads this file, updates state.json.current_task accordingly.
- SessionStart hook reads state.json.current_task and injects:
  ```
  === Current task ===
  Task: cand_imbloss_v2
  Stage: training
  Started: 2 hours ago
  Stages completed: []
  Continue this task. Update CURRENT_TASK.md when stage changes.
  ===
  ```
- verify.sh reads `current_task.id` (not `candidate`), uses
  `artifact_paths` to locate files.

### Migration
v2 → v3: state.py reads schema_version. If 2, auto-upgrade on first
write (add current_task=None). Tests cover both schemas during
transition.

---

## Bug B — verify.sh needs `in_progress` status

### Problem
verify.sh returns `{passed: bool, score: float, prd_complete: bool, details: {...}}`.
If passed=false → engine counts as failure. After 3 failures →
auto-escalation. But "in progress" (training started, not done) looks
identical to "broken" (verify can't find files).

### Fix

**Extend verify.sh contract.** New optional field:

```json
{
  "passed": false,
  "score": 0.4,
  "prd_complete": false,
  "in_progress": true,
  "details": {
    "current_stage": "training",
    "expected_completion_in": "20m",
    "next_check_at": "2026-05-02T20:00:00Z",
    ...
  }
}
```

If `in_progress: true`:
- Engine does NOT increment `consecutive_failures`
- Engine logs `cycle_in_progress` event (new event type)
- Engine waits cooldown × N (configurable, default 3×) before next cycle
- Auto-escalation does NOT trigger

If `in_progress` field absent: backward compat, treat as v1.0 behavior.

### When verify returns in_progress

Project's verify.sh decides. Examples for AI-trade:
- training_summary.json missing AND `logs/cand_<name>_train_*.log` updated <10min ago → in_progress
- 5 backtests missing AND <3 of 5 backtest dirs exist → in_progress  
- All artifacts exist but score=0 → NOT in_progress (real failure)

verify.sh can use age of files, presence of incomplete artifacts, etc.

### Engine config

```yaml
# .cc-autopipe/config.yaml
in_progress:
  max_in_progress_cycles: 12  # max consecutive in_progress before forced check
  cooldown_multiplier: 3      # cooldown × N during in_progress
```

After max_in_progress_cycles, force a real check (treat as failure if
no progress).

---

## Bug C — DETACHED auto-suggested via SessionStart hook

### Problem
Stage H added DETACHED state and `cc-autopipe-detach` helper. But Claude
in real session doesn't think to use it — defaults to running training in
foreground, holding orchestrator slot for 60+ minutes.

### Fix

**SessionStart hook injects detection-aware reminder:**

```
=== Long operation guidance ===
If you are about to run an operation expected to take >5 minutes
(model training, large data processing, batch inference):
  1. Launch with nohup in background:
     nohup bash scripts/run_candidate.sh <name> > logs/<name>.log 2>&1 &
  2. Immediately call cc-autopipe-detach with:
     - --reason "training <name>"
     - --check-cmd "test -f data/models/<name>/diagnostics/training_summary.json"
     - --check-every 600
     - --max-wait 14400
  3. End your turn. Engine will resume you when check-cmd succeeds.

Do NOT block the cycle waiting for long operations.
===
```

Detection of "long operation" left to Claude — instructions in prompt.
Optional v1.3: PreToolUse hook detects pattern and warns inline.

### rules.md template addition

Add to template:

```markdown
## Long operation discipline

Operations >5min MUST use cc-autopipe-detach. Pattern:

1. nohup the operation, redirect to log
2. cc-autopipe-detach --reason "..." --check-cmd "..." --check-every 600
3. End your turn (engine takes over)

Never wait synchronously for: model training (>30s in tests, >5min in prod),
large file downloads, multi-period backtests, simulation runs.
```

---

## Bug D — Backlog FIFO discipline (soft enforcement)

### Problem
Claude takes whatever task it interprets as relevant. In AI-trade, Claude
worked on `cand_tcn_trans` while top P0/P1 was `cand_imbloss_v2`.

### Fix

**Soft enforcement via SessionStart hook injection:**

```
=== Backlog directive ===
Top 3 OPEN tasks (DO NOT skip these for others):
  P1 [implement] cand_imbloss_v2 — SwingLoss + class_balance_beta=0.999
  P0 [implement] cand_regimemoe — iTransformer + 3 regime heads
  P1 [implement] cand_mamba — replace iTransformer with Mamba SSM

CURRENT TASK (per state.json): cand_imbloss_v2

If current task is open: continue it. If you need to switch tasks,
write CURRENT_TASK.md with new task and explain why in claude_notes.

Do NOT silently switch tasks — engine tracks current_task and will
treat artifacts not matching current_task as out-of-scope.
===
```

verify.sh checks: do artifact_paths in current_task match files Claude
created? If not (Claude switched task without updating CURRENT_TASK.md),
log warning to failures.jsonl.

**Hard enforcement** is OUT of scope (would require knowing which files
belong to which task — engine doesn't know). Best effort: visibility +
warnings.

### Optional escalation

If Claude switches task without CURRENT_TASK.md update for 2 cycles in
a row → engine writes HUMAN_NEEDED.md + TG alert. Pattern: "Claude
keeps switching tasks. Either fix backlog ordering or adjust PRD."

---

## Bug E — Resolved by A

current_task.id replaces standalone CAND_NAME concept. State persists
between cycles via state.json. session_id resume + current_task field
cover continuity.

---

## Bug F — Multi-cycle stage tracking

### Problem
A single backlog task in R&D requires multiple cycles:
- Cycle 1: Stage A (hypothesis + setup)
- Cycle 2: Stage B (training)
- Cycle 3: Stage C (5 backtests)
- Cycle 4: Stage D (Player 500-seed)
- Cycle 5: Stage E (promotion report)

Each cycle currently starts from "What is the task? Read PRD..." losing
mid-progress context.

### Fix

**stages_completed array in current_task:**

After each successful sub-stage, Claude updates CURRENT_TASK.md:
```
task: cand_imbloss_v2
stage: backtests
stages_completed: [hypothesis, training]
artifact: data/models/exp_cand_imbloss_v2/
notes: Training done, gap=18.2pp, lr=1.45, conf_diff=+0.0234. Starting 5-period backtest now.
```

Stop hook updates state.json.current_task.stages_completed.

SessionStart hook injects:
```
=== Task progress ===
Task: cand_imbloss_v2
Stages completed: hypothesis, training
Current stage: backtests
Notes: Training done, gap=18.2pp, lr=1.45, conf_diff=+0.0234. Starting 5-period backtest now.
===
```

verify.sh can use stages_completed for progressive scoring:
- 1 stage done = score 0.20
- 2 stages = 0.40
- 3 stages = 0.60
- 4 stages = 0.80
- 5 stages = 1.00

This gives engine real progress signal (not binary).

---

## Bug G — Subprocess failure alerting

### Problem
When `_run_claude` returns rc != 0, engine logs to failures.jsonl but
doesn't TG alert. v0.5 alerted only on quota. Real-world: silent rc=1
loops for minutes/hours before someone notices.

### Fix

**Alert on rc != 0 with rate limiting:**

```python
# In orchestrator process_project, after _run_claude returns
if rc != 0:
    # ... existing logging ...
    
    # NEW: TG alert with dedup (max 1 per 10min per project)
    sentinel = user_home / f"alert-rc{rc}-{project_path.name}.last"
    if not sentinel.exists() or (time.time() - sentinel.stat().st_mtime) > 600:
        notify_tg(
            f"[{project_path.name}] cycle_failed rc={rc}\n"
            f"stderr_tail: {stderr[-300:] if stderr else '(empty)'}"
        )
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.touch()
```

Dedup window: 600s (10min). User gets first alert, then silence until
either situation resolves or 10min passes.

---

## Bug H — Smart escalation (only on real subprocess failures)

### Problem
v1.0 Stage L escalates to Opus after 3 consecutive failures regardless
of failure type. Real-world: most "failures" are verify.sh returning 0
because files aren't where it expects (not because Claude is weak).
Escalating to Opus burns quota without solving structural issue.

### Fix

**Categorize failures, escalate only on `claude_subprocess_failed`:**

```python
# In orchestrator, before escalation check
recent_failures = read_recent_failures(project_path, n=3)
crash_failures = [f for f in recent_failures if f["error"] == "claude_subprocess_failed"]
verify_failures = [f for f in recent_failures if f["error"] == "verify_failed"]

if len(crash_failures) >= 3:
    # Escalate — Claude actually crashing, more capability might help
    escalate_to_opus()
elif len(verify_failures) >= 3:
    # Don't escalate — verify likely structural issue
    # Write HUMAN_NEEDED.md instead
    write_human_needed(
        "verify.sh returned passed=false 3 cycles in a row.\n"
        "Likely causes:\n"
        "- verify.sh expectations don't match what Claude is producing\n"
        "- Claude is making real progress but verify can't see it (use in_progress flag)\n"
        "- Real failure: Claude can't make work pass acceptance criteria\n"
        "Last 3 verify outputs:\n" + ...
    )
    notify_tg(f"[{project_path.name}] needs human attention — verify failing 3x")
    s.phase = "failed"
elif len(verify_failures) + len(crash_failures) >= 5:
    # Mixed pattern, give up
    s.phase = "failed"
```

This preserves auto-escalation for real Claude weakness, but stops it
when problem is structural.

---

## Cross-cutting

### Updated state.json schema (v3)

```json
{
  "schema_version": 3,
  "name": "...",
  "phase": "active|paused|done|failed|detached",
  "iteration": 0,
  "current_phase": 1,
  "phases_completed": [],
  "current_task": {
    "id": "string|null",
    "started_at": "ISO8601|null",
    "stage": "string",
    "stages_completed": [],
    "artifact_paths": [],
    "claude_notes": ""
  },
  "session_id": null,
  "last_score": null,
  "last_passed": null,
  "last_in_progress": false,
  "prd_complete": false,
  "consecutive_failures": 0,
  "consecutive_in_progress": 0,
  "last_cycle_started_at": null,
  "last_progress_at": null,
  "threshold": 0.85,
  "paused": null,
  "detached": null,
  "escalated_next_cycle": false,
  "successful_cycles_since_improver": 0,
  "improver_due": false
}
```

### Migration path

state.py read():
- If schema_version == 1: upgrade to 2 (add detached, escalated, etc)
- If schema_version == 2: upgrade to 3 (add current_task, last_in_progress, consecutive_in_progress)
- If schema_version == 3: as-is
- Always set schema_version=3 on write

### Backward compat for projects

v1.2 must work on existing v1.0 projects (hello-fullstack, AI-trade)
without manual migration. State auto-upgrades on first read+write.

### New event types in aggregate.jsonl

- `task_started` — when current_task.id changes from null to non-null
- `stage_completed` — when current_task.stages_completed grows
- `task_switched` — when current_task.id changes from non-null to different non-null
- `cycle_in_progress` — when verify returns in_progress=true
- `subprocess_alerted` — when TG alert fired for rc != 0
- `escalation_skipped` — when verify_failures triggered HUMAN_NEEDED instead of opus

### Footprint estimate

| Bug | Components | Lines added |
|---|---|---|
| A | state schema v3 + CURRENT_TASK.md handling | ~120 |
| A | hooks update (SessionStart, Stop) | ~80 |
| B | verify contract extension | ~60 |
| B | orchestrator in_progress handling | ~60 |
| C | rules.md template + SessionStart guidance | ~40 |
| D | SessionStart top-3 backlog injection | ~50 |
| D | task_switched detection | ~40 |
| E | (covered by A) | 0 |
| F | stages_completed handling | ~50 |
| G | subprocess alert with dedup | ~40 |
| H | failure categorization + smart escalation | ~80 |
| Tests for everything | ~400-500 |
| Smoke validators (3 new) | ~120 |

Total v1.2 addition: ~1140-1240 lines, bringing engine from ~5500 to
~6700.

---

## End of SPEC-v1.2.md
