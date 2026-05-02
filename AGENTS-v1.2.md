# AGENTS-v1.2.md — agentic build workflow for v1.2 fix pack

**Reads alongside:** AGENTS.md (v0.5 baseline), AGENTS-v1.md (v1.0 build patterns)
**For:** Claude Code agent autonomously building v1.2 across 3 batches
**Status:** All v0.5 + v1.0 disciplines apply. This doc adds v1.2-specific patterns.

---

## §1 What this build is

Implement v1.2 fix pack — 8 production hardening fixes (A-H per SPEC-v1.2.md)
discovered through real-world test on AI-trade ML R&D project.

3 batches with automated gates between. Each batch must pass:
- All previous tests (243 → grow to ~290+)
- All previous smokes (13 → grow to 16)
- New batch-specific gate script
- hello-fullstack regression (must remain green)

After Batch 3 + final gate → human (Roman) tags v1.2.

---

## §2 Carry-forward rules from v1.0 build

ALL of these are unchanged and binding:

- Atomic commits per AGENTS.md §4.3
- One concept per commit
- STATUS.md updated on every commit ("Currently working on" + tail)
- Pre-batch quota check: if 7d > 90%, halt with BATCH_HALT.md
- Tests must remain green after every commit
- BATCH_HALT.md on gate failure with TG alert
- 60min sleep between batches (mandatory cooldown)
- No git push (no remote configured by agent)
- No git tag (Roman tags manually after final validation)
- No real Claude API SDK calls (use stub via `claude --print` or fake)
- Backward compat: v1.0 projects must work on v1.2 without manual migration
- Skill files unchanged (no new skills added in v1.2)

If any of these conflict with batch instructions below, STOP and write
`BATCH_HALT.md` with question. Do not improvise.

---

## §3 Batch grouping rationale

**Batch 1 = A + E (state schema v3 + current_task field)**

Foundation for everything else. Without state.json having current_task,
B/D/F have nothing to read. Implement first, validate atomically.

E is "covered by A" — no separate work, but tests verify both legacy
CAND_NAME projects still work.

**Batch 2 = B + H (in_progress flag + smart escalation)**

Both involve failure handling logic. B adds new verify contract field,
H reads failures.jsonl to categorize. Naturally cohesive.

**Batch 3 = C + D + F + G (DETACHED + backlog + stages + alerting)**

All hook/rules/template work + small orchestrator addition (G). Mostly
hook scripts and template strings.

Why this grouping:
- Batch 1 is a hard prerequisite for B (verify needs state to know task)
- Batch 2 builds the failure logic on top of state
- Batch 3 is mostly content (template strings, hook outputs) + one alert path

If Batch 1 fails gate → STOP. If Batch 2 fails → can theoretically continue
to Batch 3, but DON'T (signals deeper issue).

---

## §4 Standard build cycle per batch

```
1. Read SPEC-v1.2.md section for the batch's bugs
2. Plan: list files to create/modify, list tests to add
3. Pre-batch quota check:
   - cc-autopipe-quota-check
   - If 7d > 90% → BATCH_HALT.md, TG alert, exit
4. Implement (atomic commits per concept):
   - Bug X: code change → commit → test → commit
   - Bug Y: code change → commit → test → commit
5. Run all tests: pytest tests/ -x
6. Run all stage smokes: tests/smokes/run-all-smokes.sh
7. Run new batch gate: tests/gates/batch-N-v12.sh
8. Run hello-fullstack regression: tests/regression/hello-fullstack-v12.sh
9. Update STATUS.md + commit summary
10. Sleep 60min (cooldown before next batch)
```

Sleep step uses `sleep 3600` in subprocess. If Roman wants to skip,
he can manually mark `.cc-autopipe/SKIP_COOLDOWN` file — agent checks
this on wake-up.

---

## §5 Batch 1 detail (A + E)

### Bug A — state.json current_task field

**Files to create/modify:**
- `src/lib/state.py` — schema_v3 read/write, migration v2→v3
- `src/lib/current_task.py` (new) — CURRENT_TASK.md parser/writer
- `src/orchestrator/hooks/session_start.py` — inject current_task into prompt
- `src/orchestrator/hooks/stop.py` — read CURRENT_TASK.md, update state.json
- `tests/lib/test_state_v3_migration.py` (new)
- `tests/lib/test_current_task.py` (new)
- `tests/hooks/test_session_start_current_task.py` (new)
- `tests/hooks/test_stop_current_task.py` (new)

**Acceptance criteria:**
- state.json with schema_version=2 auto-upgrades to 3 on read
- state.json with schema_version=3 read/written without modification
- CURRENT_TASK.md missing → current_task.id=None, no error
- CURRENT_TASK.md present with valid YAML/keys → state.json populated
- SessionStart hook injects "Current task: X" when current_task.id != None
- SessionStart hook injects "No current task" when current_task.id == None
- Stop hook reads CURRENT_TASK.md from project_path/.cc-autopipe/
- All 243 v1.0 tests pass

**Atomic commits expected:**
1. `state: add schema_v3 with current_task, auto-migrate v2→v3`
2. `tests: cover state v2→v3 migration paths`
3. `current_task: parse/write CURRENT_TASK.md helper module`
4. `tests: cover current_task module`
5. `hooks: stop reads CURRENT_TASK.md, updates state.json`
6. `tests: cover stop hook current_task integration`
7. `hooks: session_start injects current_task into prompt`
8. `tests: cover session_start current_task injection`

### Bug E

Implicit in A. Just verify legacy projects without CURRENT_TASK.md
still work.

### Gate script for Batch 1

`tests/gates/batch-1-v12.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."

echo "Gate Batch 1 v1.2 (A+E): state schema + current_task"

# 1. All v1.0 tests still pass
pytest tests/ -x --tb=short -q

# 2. Schema migration smoke
TMPDIR=$(mktemp -d)
trap "rm -rf $TMPDIR" EXIT

cat > "$TMPDIR/state-v2.json" <<EOF
{
  "schema_version": 2,
  "name": "test",
  "phase": "active",
  "iteration": 5,
  "session_id": "abc-123",
  "last_score": 0.5,
  "last_passed": false,
  "prd_complete": false,
  "consecutive_failures": 1,
  "threshold": 0.85,
  "paused": null,
  "detached": null,
  "current_phase": 1,
  "phases_completed": [],
  "escalated_next_cycle": false,
  "successful_cycles_since_improver": 0,
  "improver_due": false
}
EOF

python3 -c "
import sys; sys.path.insert(0, 'src/lib')
from state import State
s = State.read('$TMPDIR/state-v2.json')
assert s.schema_version == 3, f'Expected v3 after read, got {s.schema_version}'
assert s.current_task is None, f'Expected current_task=None for v2 migration, got {s.current_task}'
assert s.iteration == 5, 'Iteration must preserve through migration'
s.write('$TMPDIR/state-v3.json')

import json
data = json.load(open('$TMPDIR/state-v3.json'))
assert data['schema_version'] == 3
assert 'current_task' in data
assert data['current_task'] is None
print('Schema migration OK')
"

# 3. CURRENT_TASK.md parsing
mkdir -p "$TMPDIR/project/.cc-autopipe"
cat > "$TMPDIR/project/.cc-autopipe/CURRENT_TASK.md" <<EOF
task: cand_imbloss_v2
stage: training
artifact: data/models/exp_cand_imbloss_v2/
notes: SwingLoss with class_balance_beta=0.999
EOF

python3 -c "
import sys; sys.path.insert(0, 'src/lib')
from current_task import parse_file
ct = parse_file('$TMPDIR/project/.cc-autopipe/CURRENT_TASK.md')
assert ct['id'] == 'cand_imbloss_v2'
assert ct['stage'] == 'training'
assert 'data/models/exp_cand_imbloss_v2/' in ct['artifact_paths']
print('CURRENT_TASK.md parsing OK')
"

echo "Gate Batch 1 v1.2: PASSED"
```

If gate fails, write BATCH_HALT.md (see §6).

---

## §6 Batch 2 detail (B + H)

### Bug B — verify in_progress flag

**Files to create/modify:**
- `src/lib/verify.py` — extend verify result dataclass with `in_progress` field
- `src/orchestrator/process_project.py` — handle in_progress=true case
- `src/lib/config.py` — read `in_progress.max_in_progress_cycles`, `cooldown_multiplier`
- `tests/lib/test_verify_in_progress.py` (new)
- `tests/orchestrator/test_in_progress_no_failure_count.py` (new)

**Acceptance:**
- verify result with `in_progress: true` → consecutive_failures NOT incremented
- consecutive_in_progress incremented instead
- cooldown × multiplier applied to next cycle delay
- After max_in_progress_cycles → log warning, force normal cycle
- Backward compat: verify result without `in_progress` field → v1.0 behavior

### Bug H — Smart escalation

**Files to create/modify:**
- `src/lib/failures.py` — categorize failures by error type
- `src/orchestrator/process_project.py` — replace `if consecutive_failures >= 3` with categorization
- `src/lib/human_needed.py` (new) — write HUMAN_NEEDED.md template
- `tests/lib/test_failure_categorization.py` (new)
- `tests/orchestrator/test_smart_escalation.py` (new)

**Acceptance:**
- 3 consecutive `claude_subprocess_failed` → escalate to Opus (existing behavior)
- 3 consecutive `verify_failed` (score=0) → write HUMAN_NEEDED.md, phase=failed, no escalation
- Mixed pattern (1 crash + 2 verify): no escalation, treat as verify-failed pattern
- 5 consecutive failures of any kind → phase=failed regardless

### Gate script for Batch 2

`tests/gates/batch-2-v12.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."

echo "Gate Batch 2 v1.2 (B+H): in_progress + smart escalation"

# 1. All tests pass
pytest tests/ -x --tb=short -q

# 2. Stage smokes still pass
bash tests/smokes/run-all-smokes.sh

# 3. in_progress simulation
TMPDIR=$(mktemp -d)
trap "rm -rf $TMPDIR" EXIT

# Build a fake verify result with in_progress=true
# Run orchestrator process_project once
# Assert state.json has consecutive_failures=0 + consecutive_in_progress=1

python3 -c "
import sys, json
sys.path.insert(0, 'src/lib')
sys.path.insert(0, 'src/orchestrator')

# Test categorization function
from failures import categorize_recent
recent = [
    {'error': 'verify_failed', 'details': {'score': 0}},
    {'error': 'verify_failed', 'details': {'score': 0}},
    {'error': 'verify_failed', 'details': {'score': 0}},
]
cat = categorize_recent(recent)
assert cat['recommend_escalation'] is False, 'verify_failed pattern should NOT escalate'
assert cat['recommend_human_needed'] is True, 'verify_failed pattern should write HUMAN_NEEDED'

recent_crash = [
    {'error': 'claude_subprocess_failed', 'details': {}},
    {'error': 'claude_subprocess_failed', 'details': {}},
    {'error': 'claude_subprocess_failed', 'details': {}},
]
cat = categorize_recent(recent_crash)
assert cat['recommend_escalation'] is True, 'crash pattern SHOULD escalate'

print('Smart escalation logic OK')
"

echo "Gate Batch 2 v1.2: PASSED"
```

---

## §7 Batch 3 detail (C + D + F + G)

### Bug C — DETACHED guidance via SessionStart

**Files:**
- `src/orchestrator/hooks/session_start.py` — append long-operation guidance
- `templates/rules.md` — add §"Long operation discipline"
- `tests/hooks/test_session_start_long_op_guidance.py` (new)

**Acceptance:**
- SessionStart prompt includes the multi-line long-operation guidance block
- rules.md template generated by `cc-autopipe init` includes the new section
- Existing project rules.md NOT modified (only new init creates new template)

### Bug D — Backlog top-3 + task_switched detection

**Files:**
- `src/orchestrator/hooks/session_start.py` — read backlog.md, inject top 3 OPEN tasks
- `src/lib/backlog.py` — parse backlog.md, return top N OPEN tasks by priority
- `src/orchestrator/process_project.py` — detect task switch (current_task.id changes), log task_switched event
- `tests/lib/test_backlog_parser.py` (new)
- `tests/hooks/test_session_start_backlog_top3.py` (new)
- `tests/orchestrator/test_task_switched_detection.py` (new)

**Acceptance:**
- backlog.md parser returns OPEN tasks in priority order (P0 > P1 > P2)
- Top 3 tasks injected into SessionStart prompt
- Current task highlighted ("CURRENT TASK (per state.json): X")
- task_switched event logged when current_task.id changes between cycles
- 2 task_switched in row without CURRENT_TASK.md → HUMAN_NEEDED.md

### Bug F — stages_completed array

**Files:**
- `src/lib/current_task.py` — extend parser to read stages_completed list
- `src/orchestrator/hooks/stop.py` — append new stage to stages_completed
- `src/orchestrator/hooks/session_start.py` — inject stages_completed into prompt
- `tests/lib/test_current_task_stages.py` (new)
- `tests/hooks/test_stop_stage_append.py` (new)

**Acceptance:**
- stages_completed appends, never overwrites (unless task_switched resets it)
- task_switched → stages_completed reset to []
- SessionStart shows "Stages completed: A, B" + "Current stage: C"

### Bug G — Subprocess alert TG dedup

**Files:**
- `src/orchestrator/process_project.py` — add TG alert path on rc != 0
- `src/lib/notify.py` — add `notify_subprocess_failed` function with sentinel dedup
- `tests/lib/test_notify_dedup.py` (new)
- `tests/orchestrator/test_subprocess_alert.py` (new)

**Acceptance:**
- First rc != 0 → TG alert with stderr_tail (last 300 chars)
- Subsequent rc != 0 within 600s → no alert (dedup via sentinel file mtime)
- After 600s → alert again
- Sentinel path: `~/.cc-autopipe/alert-rc{rc}-{project_name}.last`
- TG alert payload includes project_name, rc, stderr_tail

### Gate script for Batch 3

`tests/gates/batch-3-v12.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."

echo "Gate Batch 3 v1.2 (C+D+F+G): hooks + alerting"

# 1. All tests pass
pytest tests/ -x --tb=short -q

# 2. Stage smokes still pass
bash tests/smokes/run-all-smokes.sh

# 3. SessionStart hook end-to-end
TMPDIR=$(mktemp -d)
trap "rm -rf $TMPDIR" EXIT

mkdir -p "$TMPDIR/proj/.cc-autopipe"
cat > "$TMPDIR/proj/.cc-autopipe/backlog.md" <<EOF
- [ ] [implement] [P0] task_alpha — first thing
- [ ] [implement] [P1] task_beta — second thing
- [ ] [implement] [P1] task_gamma — third thing
- [ ] [implement] [P2] task_delta — later
- [x] [implement] [P0] task_done — already finished
EOF

cat > "$TMPDIR/proj/.cc-autopipe/state.json" <<EOF
{
  "schema_version": 3,
  "name": "test",
  "phase": "active",
  "iteration": 1,
  "current_task": {
    "id": "task_alpha",
    "started_at": "2026-05-02T18:00:00Z",
    "stage": "init",
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
  "current_phase": 1,
  "phases_completed": [],
  "escalated_next_cycle": false,
  "successful_cycles_since_improver": 0,
  "improver_due": false
}
EOF

OUT=$(python3 -c "
import sys
sys.path.insert(0, 'src/orchestrator/hooks')
from session_start import build_context_block
print(build_context_block('$TMPDIR/proj'))
")

echo "$OUT" | grep -q "task_alpha" || { echo "FAIL: top-3 backlog missing task_alpha"; exit 1; }
echo "$OUT" | grep -q "task_beta" || { echo "FAIL: top-3 backlog missing task_beta"; exit 1; }
echo "$OUT" | grep -q "Long operation guidance" || { echo "FAIL: long-op guidance missing"; exit 1; }
echo "$OUT" | grep -q "CURRENT TASK" || { echo "FAIL: current task highlight missing"; exit 1; }

# 4. notify dedup
python3 -c "
import sys, time, tempfile, os
sys.path.insert(0, 'src/lib')
from notify import notify_subprocess_failed_dedup

with tempfile.TemporaryDirectory() as td:
    sentinel_dir = os.path.join(td, 'sentinels')
    os.makedirs(sentinel_dir)
    
    # First call should send
    sent1 = notify_subprocess_failed_dedup('proj1', 1, 'err output', sentinel_dir, dedup_window=600, dry_run=True)
    assert sent1 is True, 'First call should send'
    
    # Second call within window should not send
    sent2 = notify_subprocess_failed_dedup('proj1', 1, 'err output', sentinel_dir, dedup_window=600, dry_run=True)
    assert sent2 is False, 'Second call within window should not send'
    
    print('notify dedup OK')
"

echo "Gate Batch 3 v1.2: PASSED"
```

---

## §8 hello-fullstack regression

After EVERY batch (not just at end), run:

`tests/regression/hello-fullstack-v12.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."

echo "Regression: hello-fullstack must still work end-to-end"

# Use the v1.0 hello-fullstack regression test as base
# But add: state.json should be v3 after first cycle
# But add: current_task should be None at start, populated after first cycle

# (Implementation reuses tests/regression/hello-fullstack-v1.sh structure
#  with v3 schema assertions)

bash tests/regression/hello-fullstack-v1.sh

# Additional v1.2 assertions
TMPDIR_HELLO="${TMPDIR:-/tmp}/hello-fullstack-test"
if [ -f "$TMPDIR_HELLO/.cc-autopipe/state.json" ]; then
    SCHEMA_VER=$(python3 -c "import json; print(json.load(open('$TMPDIR_HELLO/.cc-autopipe/state.json'))['schema_version'])")
    if [ "$SCHEMA_VER" != "3" ]; then
        echo "FAIL: hello-fullstack state should be schema v3, got v$SCHEMA_VER"
        exit 1
    fi
fi

echo "hello-fullstack regression v1.2: PASSED"
```

If regression fails after a batch → STOP, write BATCH_HALT.md, do NOT
proceed to next batch.

---

## §9 BATCH_HALT.md format

Same as v1.0 build (AGENTS-v1.md §X). Reminder of required content:

```markdown
# BATCH_HALT — v1.2 batch <N> failed

**When:** ISO8601 timestamp
**Batch:** N
**Bugs in this batch:** A, E (or B, H, etc)
**Failure point:** which gate / test / smoke

## What I did before halting
- Bullet list of completed atomic commits
- Last successful gate

## What failed
- Exact command output (last 30 lines)
- pytest failure trace (if applicable)
- Smoke output (if applicable)

## Why I am stopping
Per AGENTS-v1.2.md §4, on gate failure I write BATCH_HALT and stop.
Roman must review and either:
- Fix and tell me to continue
- Resolve external issue (quota, env)
- Cancel build

## State of repo
- Last commit hash: ...
- Tests state: N passing, M failing
- Smokes state: ...

## TG alert sent: yes (notify_user output: ...)
```

---

## §10 STATUS.md updates

After every commit:

```markdown
# v1.2 build status

## Currently working on
Batch <N>, Bug <X>, step <Y>: <description>

## Completed in this batch
- ✓ Bug A: state schema v3 (commits 8d11d57..a1b2c3d)
- ◐ Bug E: in progress

## Tests state
243 + N new = X total, all passing

## Quota
5h: X%, 7d: Y%

## Tail (last 5 commits)
- abc1234 hooks: session_start injects current_task
- def5678 tests: cover session_start current_task injection
- ...
```

Roman reads STATUS.md to know where build is. Keep it accurate.

---

## §11 Pre-batch quota check function

Same as v1.0 — `cc-autopipe-quota-check`. If 7d > 90%, halt.

```bash
# Pseudo-code
QUOTA_7D=$(python3 -c "
import sys; sys.path.insert(0, 'src/lib')
import quota
q = quota.read_cached()
print(int(q.seven_day_pct * 100))
")
if [ "$QUOTA_7D" -gt 90 ]; then
    echo "Quota 7d=${QUOTA_7D}% > 90%, halting batch"
    cat > BATCH_HALT.md <<EOF
# BATCH_HALT — quota cap
...
EOF
    notify_tg "v1.2 build halted: quota 7d=${QUOTA_7D}%"
    exit 1
fi
```

---

## §12 Sleep between batches

After each batch's gate passes:

```bash
echo "Batch <N> complete. Cooldown 60min before Batch <N+1>."

if [ -f .cc-autopipe/SKIP_COOLDOWN ]; then
    echo "SKIP_COOLDOWN found, skipping wait"
    rm -f .cc-autopipe/SKIP_COOLDOWN
else
    sleep 3600
fi
```

Mandatory boundary. Don't skip.

---

## §13 Final batch — what triggers v1.2 readiness

After Batch 3 gate passes:

```bash
echo "All 3 batches complete. Running final integration validation."

# 1. Full test suite
pytest tests/ -x

# 2. All smokes (existing 13 + 3 new)
bash tests/smokes/run-all-smokes.sh

# 3. All 3 batch gates re-run
bash tests/gates/batch-1-v12.sh
bash tests/gates/batch-2-v12.sh
bash tests/gates/batch-3-v12.sh

# 4. hello-fullstack regression one more time
bash tests/regression/hello-fullstack-v12.sh

# 5. State.json schema sanity across all test artifacts
find . -name "state.json" -not -path "*/node_modules/*" | while read f; do
    python3 -c "
import json
data = json.load(open('$f'))
assert data['schema_version'] == 3, f'$f has wrong schema_version: {data[\"schema_version\"]}'
assert 'current_task' in data, f'$f missing current_task'
"
done

# 6. Update STATUS.md final
cat > STATUS.md <<EOF
# v1.2 build COMPLETE

All 3 batches green. 8 bugs (A-H) fixed. ~1140-1240 lines added.
Engine size: ~6700 lines.

Tests: NNN passing
Smokes: 16/16 (13 v1.0 + 3 v1.2)
Gates: 3/3 v1.2 + all v1.0
hello-fullstack regression: green
v1.0 backward compat: confirmed

Roman next steps:
1. Run hello-fullstack manually to feel v1.2 behavior
2. Roman tests on AI-trade real R&D scenario
3. After Roman validation: tag v1.2

EOF

git add STATUS.md
git commit -m "v1.2: build complete, all gates green"

notify_tg "cc-autopipe v1.2 build COMPLETE — Roman to validate before tagging"
```

Agent stops here. Tagging is Roman's responsibility per AGENTS.md §13.

---

## §14 Things to NOT do

- Do NOT git push
- Do NOT git tag (Roman's job)
- Do NOT modify SPEC-v1.2.md (it's the contract)
- Do NOT modify AGENTS-v1.2.md (this doc)
- Do NOT skip pre-batch quota check
- Do NOT skip 60min sleep between batches
- Do NOT continue past gate failure
- Do NOT modify hello-fullstack project itself (it's the regression target)
- Do NOT add new MCP servers / external dependencies
- Do NOT touch Python 3.14 venv (it's Roman's, frozen)

---

## §15 If something is unclear

Write BATCH_HALT.md with question. Roman responds with clarification.
Resume from where stopped.

Do NOT improvise SPEC-level decisions. Do improvise tactical (which file
to put helper in, exact variable names, test names).

---

## End of AGENTS-v1.2.md
