# AGENTS-v1.md — Workflow extension for cc-autopipe v0.5.1 + v1.0

**Reads alongside:** AGENTS.md (v0.5 build process), SPEC-v1.md (v1 product spec)
**Status:** Implementation-ready
**Audience:** Claude Code agent autonomously building v0.5.1 + v1.0

This document EXTENDS AGENTS.md. v0.5 disciplines (commit format, code
standards, OPEN_QUESTIONS protocol, STATUS.md updates) carry forward
unchanged. New sections below define batch-mode work with **automated gates**.

---

## 1. Build mode: autonomous batches

v1.0 work is organized into 4 batches, executed sequentially without
human intervention between them. Agent advances batch-to-batch
**automatically** when gate criteria are satisfied.

### 1.1 Batch ordering (immutable for this build)

| Batch | Stages | Estimated lines | Quota estimate |
|---|---|---|---|
| **a** (v0.5.1) | 1.1 + 1.2 + 1.3 cleanup | ~180 | 5-10% of 7d |
| **b** (v1.0 part 1) | H + I + J | ~600 | 30-40% of 7d |
| **c** (v1.0 part 2) | K + L | ~350 | 20-25% of 7d |
| **d** (v1.0 part 3) | M + N | ~330 | 20-25% of 7d |

Cumulative: ~75-100% of 7d quota. **This is the entire weekly budget.**
Be efficient.

### 1.2 Inter-batch behavior

Between batches:
1. Agent runs **automated gate** (see §2)
2. If gate green: TG notify "Batch X complete, starting Batch Y", sleep 60min, start next
3. If gate red: STOP, write `BATCH_HALT.md` with diagnosis, TG alert, end session
4. **Sleep 60min between batches** is mandatory — gives quota time to settle, gives Roman a chance to abort if needed

The 60-minute sleep is NOT optional. Implement via `time.sleep(3600)` or
similar, even if it feels redundant. It is a safety boundary.

---

## 2. Automated gate criteria

After completing each batch, agent runs ALL of the following before
advancing. ANY failure halts progression.

### 2.1 Gate checklist (in order)

```
[ ] Working tree clean (git status shows nothing)
[ ] All 6 v0.5 stage smokes still pass
[ ] All new v1.0 stage smokes pass
[ ] pytest tests/ pass with no new SKIP entries (without OPEN_QUESTIONS justification)
[ ] ruff check src/ — clean
[ ] ruff format --check src/ — clean
[ ] shellcheck on all bash files — clean
[ ] cc-autopipe doctor returns 10/10 OK on real environment
   (Note: this hits real OAuth/usage endpoint — counts toward quota)
[ ] STATUS.md updated with batch completion timestamp
[ ] OPEN_QUESTIONS.md has zero status:blocked entries
[ ] No TODO(v0.5.1) or TODO(v1.0) markers without OPEN_QUESTIONS reference
[ ] git log shows atomic commits per AGENTS.md §4.3 (no fat commits >5 files unless template)
```

### 2.2 Gate execution script

Each batch creates `tests/gates/batch-X.sh` that runs all checks:

```bash
#!/bin/bash
# tests/gates/batch-a.sh
set +e

FAIL=0

check() {
    local desc="$1"; shift
    "$@" >/dev/null 2>&1
    if [ $? -ne 0 ]; then
        echo "GATE FAIL: $desc" >&2
        FAIL=$((FAIL + 1))
    else
        echo "GATE OK: $desc"
    fi
}

cd "$(dirname "$0")/../.."

check "working tree clean" sh -c '[ -z "$(git status --porcelain)" ]'
check "v0.5 stage-a smoke" bash tests/smoke/stage-a.sh
check "v0.5 stage-b smoke" bash tests/smoke/stage-b.sh
check "v0.5 stage-c smoke" bash tests/smoke/stage-c.sh
check "v0.5 stage-d smoke" bash tests/smoke/stage-d.sh
check "v0.5 stage-e smoke" bash tests/smoke/stage-e.sh
check "v0.5 stage-f smoke" bash tests/smoke/stage-f.sh

# v0.5.1 specific checks (extend per batch)
check "cc-autopipe stop --help" sh -c "cc-autopipe stop --help | grep -q stop"
# ... add more per batch ...

check "pytest" .venv/bin/pytest tests/ -q --tb=no
check "ruff check" ruff check src/
check "ruff format" ruff format --check src/
check "shellcheck" sh -c 'find src/ tests/ tools/ -name "*.sh" -exec shellcheck {} +'
check "doctor" cc-autopipe doctor --offline
check "no blocked Q" sh -c '! grep -q "Status: blocked" OPEN_QUESTIONS.md'

if [ $FAIL -gt 0 ]; then
    echo "BATCH GATE FAILED ($FAIL checks). See above." >&2
    exit 1
fi

echo "BATCH GATE PASSED."
exit 0
```

### 2.3 Gate failure handling

When gate fails, agent MUST:

1. Stop further work immediately (do NOT try to fix and re-gate)
2. Write `BATCH_HALT.md` in repo root:
   ```markdown
   # Batch X Halted
   
   **Timestamp:** YYYY-MM-DDTHH:MM:SSZ
   **Failed gate items:** [list from script output]
   **Last successful commit:** SHA
   **Last action:** [what you were doing when gate ran]
   
   ## Diagnosis
   [What probably caused this — your best guess]
   
   ## To resume
   1. Roman should investigate gate failures
   2. Either fix manually OR delete BATCH_HALT.md and let next session retry
   ```
3. TG notify: `[cc-autopipe-build] Batch X HALTED: <gate items>`
4. Update STATUS.md "Currently blocked" section
5. End session (do not start next batch)

A halted batch can be resumed by next Claude Code session — fresh
session reads BATCH_HALT.md, decides to retry or hand to Roman.

---

## 3. Per-batch implementation order

### 3.1 Batch a — v0.5.1 cleanup

Order of work (each = atomic commit):

1. `templates: update rules.md.example with workflow discipline`
2. `templates: fix verify.sh.example grep || echo 0 bug`
3. `cli: implement cc-autopipe stop subcommand`
4. `tests: cc-autopipe stop unit + integration`
5. `helpers: dispatcher wires stop subcommand`
6. `tests: gates/batch-a.sh validator`
7. `docs: STATUS marks v0.5.1 complete`

After commit 7: run `bash tests/gates/batch-a.sh`. If GATE PASSED:
- TG: "Batch a (v0.5.1) complete. Sleeping 60min before Batch b."
- `git tag v0.5.1`
- `time.sleep(3600)`
- Start Batch b

### 3.2 Batch b — Stage H + I + J

Order of work:

**Stage H (DETACHED):**
1. `state: add detached field + set_detached method`
2. `state: CLI subcommand set-detached`
3. `helpers: cc-autopipe-detach script`
4. `orchestrator: handle DETACHED phase in process_project`
5. `hooks: pre-tool-use allows nohup + cc-autopipe-detach pattern`
6. `tests: detached state transitions (12+ cases)`
7. `tests: pre-tool-use nohup+detach allowance`
8. `tests/smoke: stage-h.sh end-to-end`

**Stage I (R/R subagents):**
9. `templates: add researcher subagent to agents.json`
10. `templates: add reporter subagent to agents.json`
11. `tests: agents.json structure validation includes new agents`
12. `tests/smoke: stage-i.sh agents discoverable`

**Stage J (Phase split):**
13. `state: add current_phase + phases_completed`
14. `state: schema migration v1 → v2`
15. `prd: phase parser (recognize ### Phase N: headers)`
16. `orchestrator: phase transition logic + session reset`
17. `tests: phase parser + transition + backward compat`
18. `tests/smoke: stage-j.sh 3-phase mock PRD`

**Batch b gate:**
19. `tests: gates/batch-b.sh validator (extends batch-a checks)`
20. `docs: STATUS marks Batch b complete`

After commit 20: gate, sleep, start Batch c.

### 3.3 Batch c — Stage K + L

**Stage K (Quota monitor):**
1. `lib: quota_monitor module with daemon-thread loop`
2. `orchestrator: start quota_monitor thread on main()`
3. `orchestrator: graceful shutdown of monitor on SIGTERM`
4. `tests: quota_monitor warning thresholds + dedup`
5. `tests/smoke: stage-k.sh monitor lifecycle`

**Stage L (Auto-escalation):**
6. `config: schema includes auto_escalation section`
7. `orchestrator: check consecutive_failures, escalate model`
8. `orchestrator: inject reminder text in escalation prompt`
9. `orchestrator: revert to default model after success`
10. `tests: escalation + reminder + revert (8+ cases)`
11. `tests/smoke: stage-l.sh 3 fails → opus model`

**Batch c gate:**
12. `tests: gates/batch-c.sh validator`
13. `docs: STATUS marks Batch c complete`

After commit 13: gate, sleep, start Batch d.

### 3.4 Batch d — Stage M + N

**Stage M (systemd / launchd):**
1. `init: cc-autopipe.service.template (Linux systemd)`
2. `init: com.cc-autopipe.plist.template (macOS launchd)`
3. `cli: install-systemd subcommand`
4. `cli: install-launchd subcommand`
5. `cli: uninstall-systemd / uninstall-launchd`
6. `tests: install creates correct files in correct locations`
7. `tests/smoke: stage-m.sh install + uninstall on host`

**Stage N (Skill crystallization):**
8. `templates: add improver subagent to agents.json`
9. `orchestrator: trigger improver every N successful cycles`
10. `orchestrator: ensure .claude/skills/ exists on first improver run`
11. `tests: improver creates SKILL.md after mock cycle history`
12. `tests/smoke: stage-n.sh skill discovered post-cycle`

**Batch d gate (also v1.0 final gate):**
13. `tests: gates/batch-d.sh validator (extends previous)`
14. `tests: gates/v1.0-final.sh — comprehensive cross-batch validator`
15. `docs: STATUS marks v1.0 complete`

After commit 15: gate, do NOT sleep, do NOT start anything else.
Final TG: "v1.0 BUILD COMPLETE — pending Roman tag v1.0".

---

## 4. New disciplines for batch mode

### 4.1 No SPEC deviations without OPEN_QUESTIONS

In v0.5 build, deviations were caught and documented (Q10, Q11, Q12).
In batch mode, agent works autonomously — same discipline mandatory.

If agent finds SPEC-v1.md describes something that won't work:
1. Add OPEN_QUESTIONS.md entry IMMEDIATELY (before workaround)
2. Choose: defer to v1.1 OR implement workaround with documentation
3. Do NOT silently deviate

### 4.2 No new dependencies without flag

v0.5 used Python stdlib + pytest + ruff + shellcheck. Stick to these.
If a new dependency is genuinely needed:
1. Add OPEN_QUESTIONS entry justifying it
2. Status: blocked
3. Halt batch, TG Roman

### 4.3 Test budget per batch

Each batch should add **at least N tests** where N = estimated stages × 8.

| Batch | Min new tests |
|---|---|
| a | 8 |
| b | 24 (3 stages × 8) |
| c | 16 (2 × 8) |
| d | 16 (2 × 8) |

Skipping below this number requires OPEN_QUESTIONS justification.

### 4.4 Smoke validator pattern

Each new stage gets `tests/smoke/stage-<letter>.sh` following v0.5 pattern:
- ruff + shellcheck checks
- Full pytest run
- Stage-specific functional checks
- Ends with `Stage <letter>: OK` on success

### 4.5 Quota awareness during build

Before each batch, agent calls `quota.read_cached()` and decides:
- If 7d > 80%: TG "Approaching weekly quota cap, batch X may be last"
- If 7d > 90%: HALT batch, write BATCH_HALT.md, TG "Quota too high to proceed"
- If 7d > 95%: definitely halt — pre-flight check would pause anyway

Roman has the option to set `CC_AUTOPIPE_QUOTA_OVERRIDE=1` env var to
bypass this check (for explicit "I know what I'm doing" cases). Without
it, agent self-protects.

---

## 5. State persistence between batches

Between batches, the following are checked into git:
- All source code
- All tests (including gates)
- Updated STATUS.md
- Updated OPEN_QUESTIONS.md
- Updated SPEC-v1.md (if SPEC deviations resolved)

NOT in git:
- BATCH_HALT.md (only created on failure)
- .venv/, .pytest_cache/, etc (already gitignored)
- Local lock files

When new session starts (after sleep or after halt):
1. Read AGENTS-v1.md (this file)
2. Read SPEC-v1.md
3. Read STATUS.md
4. Check for BATCH_HALT.md — if exists, do NOT continue, halt mode
5. Otherwise: identify last completed batch, start next

---

## 6. Branch strategy for v1.0

Decision: stay on `main` branch (consistent with v0.5 pattern).

Each batch produces a tag:
- `v0.5.1` after batch a
- `v1.0-batch-b` after batch b
- `v1.0-batch-c` after batch c
- `v1.0` after batch d

Tagging is HUMAN ACTION. Agent must NOT tag. Agent commits, then writes
in STATUS.md "Roman should run: `git tag <tagname>`" if applicable.

---

## 7. v1.0 acceptance criteria

v1.0 is complete when:

- [ ] All 4 batches gated GREEN
- [ ] All 11 stages (3 cleanup + H/I/J/K/L/M/N) per SPEC-v1.md acceptance
- [ ] tests/gates/v1.0-final.sh passes
- [ ] hello-fullstack still works end-to-end (regression)
- [ ] OPEN_QUESTIONS.md has zero status:blocked
- [ ] All TODO(v1.0) markers resolved
- [ ] STATUS.md says "v1.0 BUILD COMPLETE"
- [ ] Roman tags v1.0

After v1.0: engine can run real production projects (Legal Parser,
trading bot) with full autonomous loop including long operations,
phased PRDs, quota safety, auto-escalation, and self-improvement
via skill crystallization.

---

## 8. Failure modes specific to batch mode

### 8.1 Agent loses context mid-batch

Mitigation: every commit updates STATUS.md "Currently working on" with
exact next step. If session restarts, new agent reads and continues.

### 8.2 Quota exhausted mid-batch

Mitigation: pre-batch quota check (§4.5). If exhausted during batch:
gate will fail at next checkpoint, BATCH_HALT.md created, work resumes
after Roman intervention or quota reset.

### 8.3 Git tree corruption

Mitigation: each commit is atomic, tree should always be clean. If git
status shows mess: agent must `git stash` and investigate before
continuing.

### 8.4 Agent disagrees with SPEC

Mitigation: §4.1. OPEN_QUESTIONS first, deviation second.

### 8.5 60min sleep interrupted

If session is killed during inter-batch sleep: next session reads STATUS.md,
sees last batch completed, runs gate, advances. Effectively resumes from
the sleep point.

---

## End of AGENTS-v1.md

To start the build, see PROMPT-v1.md (separate document).
