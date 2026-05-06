# Build Status

**Updated:** 2026-05-06T17:30:00Z
**Current branch:** main
**Current stage:** **v1.3.5 HOTFIX COMPLETE.** Three groups (R:
[research]-task artifact-based completion + Phase 2 verdict-pattern
stages; P: v2.0 PROMOTION.md parser + atomic 5-child ablation
auto-spawn + quarantine; L: persistent LEADERBOARD.md with composite
+ ELO + top-20 + archive + knowledge sentinel arming). Schema
**unchanged at v6** (no new persisted fields, only mtime/flag
adjustments on existing fields). 685 → **743 tests passing** (+58).
20/20 hotfix smokes green (4 v1.3 + 3 v1.3.1 + 3 v1.3.2 + 5 v1.3.3 +
2 v1.3.4 + 3 v1.3.5). Empirical drivers: AI-trade Phase 2 PRD v2.0
needs three engine-side guardrails (research artifact contract,
PROMOTION format enforcement, persistent leaderboard) before
confident 16-week autonomy. Awaiting Roman validation + tag v1.3.5.

**Earlier stage:** v1.3.4 HOTFIX COMPLETE. One group (Group R:
transient classification + retry + network probe gate) landed on
top of v1.3.3. Schema bumped v5 → v6 (additive, backward compatible
— PROMPT_v1.3.4 said 4→5 but v1.3.3 already shipped at 5; coordinated
bump to 6 documented in V134_BUILD_DONE.md). 635 → **685 tests
passing** (+50). 4 v1.3 + 3 v1.3.1 + 3 v1.3.2 + 5 v1.3.3 + 2 v1.3.4
= **17/17 hotfix smokes green**. Empirical drivers: "Server is
temporarily limiting requests" stderr from Anthropic during parallel-
project load, 6:00 MSK router reboot (~5 min outage), and WSL2
networking hiccups all silently took healthy projects to FAILED in
v1.3.3.

**Earlier stage:** v1.3.3 HOTFIX COMPLETE. Three groups (Group L:
liveness check + Group M: cc-autopipe-smoke helper + Group N:
knowledge.md detach gate). Schema bumped v4 → v5. 599 → 635 tests.
See `V133_BUILD_DONE.md`.

**Earlier stage:** v1.3.2 HOTFIX COMPLETE. RECOVERY-SAFE +
STDERR-LOGGING + TRIGGER-SMOKES on top of v1.3.1. 573 → 599 tests.
See `V132_BUILD_DONE.md`.

**Earlier stage:** v1.3.1 HOTFIX COMPLETE. Three groups (B-FIX
regression test + B3-FIX shutdown/lock awareness + DETACH-CONFIG
per-project defaults). 548 → 573. See `V131_BUILD_DONE.md`.

**Earlier stage:** v1.3 BUILD COMPLETE. Full 14-day-autonomy
hardening landed — 9 groups (G refactor + A memory + B recovery +
C infra + D research + E quota + F ops + H META_REFLECT + I
knowledge + K WSL2). Schema bumped v3 → v4. Tag v1.3 awaiting push.

**Earlier stage:** v1.2 BUILD COMPLETE. All 8 bugs (A-H) landed
across 3 batches. Cooldown skipped per Roman 2026-05-03 (interactive
session, mocked claude — no real quota at risk).

## v1.3.5 HOTFIX — final state

**3 groups landed across 10 atomic commits.** See
`V135_BUILD_DONE.md` for the full summary.

| Group | Surface | Tests added |
|---|---|---|
| R/1 | src/lib/research_completion.py — is_research_task / expected_artifact_glob / completion_satisfied / find_top_research_task; backlog.BacklogItem.task_type; backlog.parse_all_tasks | +15 unit + 4 backlog cases |
| R/2 | src/orchestrator/prompt.py — RESEARCH-TASK prompt block branches on top item task_type | +3 integration |
| R/3 | src/orchestrator/cycle.py — pre-cycle [research] snapshot + post-cycle research_task_completed/research_task_pending event with last_passed override on success | (cycle covered by smoke) |
| R/4 | src/lib/knowledge.py — Phase 2 verdict-pattern stages (phase_gate, selection_complete, research_digest, negative_mining, hypo_filed, track_winner) | (covered by knowledge tests) |
| P/1 | src/lib/promotion.py — parse_verdict / validate_v2_sections / parse_metrics / on_promotion_success / quarantine_invalid; 5 ablation children template; atomic backlog mutation | +8 unit + 5 integration |
| P/2 | src/orchestrator/cycle.py — vec_long_* [x] detection + dispatch (PROMOTED-validated, PROMOTED-invalid, REJECTED, missing-verdict) | (cycle covered by smoke) |
| L/1 | src/lib/state.py — touch_knowledge_baseline_mtime helper | (covered by leaderboard test) |
| L/2 | src/lib/leaderboard.py — composite scoring + ELO matchups + top-20 retention + archive + sidecar .leaderboard_elo.json | +15 unit |
| L/3 | tests/smoke/run-{research-task-completion,promotion-validation,leaderboard-elo}-smoke.sh + run-all-smokes wiring | +0 (3 smokes) |

**Test counts (v1.3.5):**
- pytest: 685 (v1.3.4 baseline) → **743 passed** (+58 new tests)
- 20 hotfix smokes all green: 4 v1.3 + 3 v1.3.1 + 3 v1.3.2 +
  5 v1.3.3 + 2 v1.3.4 + 3 v1.3.5
- Three v1.3.5 smokes (heredoc-driven module exercises) under
  `tests/smoke/run-{research-task-completion,promotion-validation,leaderboard-elo}-smoke.sh`,
  each <2s

**Schema:** **unchanged at v6.** No new persisted state fields per
PROMPT_v1.3.5 §"Don't"; only mtime + flag arming on existing v1.3
knowledge sentinel fields.

**New events in aggregate.jsonl:** `research_task_completed`,
`research_task_pending`, `promotion_validated`, `promotion_invalid`,
`promotion_rejected`, `promotion_verdict_missing`,
`ablation_children_spawned`, `leaderboard_updated`,
`leaderboard_append_skipped`, `promotion_children_skipped`.

**New CLI surface:** none.

### Pre-existing baseline observations (NOT v1.3.5)

run-all-smokes.sh had 7 stages red on the `f860073` (v1.3.4 release)
commit BEFORE any v1.3.5 change:

- stages a-f all chain on the same `ruff check tests/` failure: 5
  F401 unused-import warnings in test files (not src). v1.3.4's
  ruff cleanup (`8a7c57c`) ran on `src/` only. Trivially fixable
  in one hygiene commit; deferred per PROMPT_v1.3.5 §"Don't" ("touch
  unrelated v1.3 / v1.3.x features").
- stage-k: orchestrator startup log no longer mentions
  `quota_monitor_interval`. Predates v1.3.5. The 15 quota_monitor
  unit tests still pass; only the smoke's stderr-grep predicate is
  stale.

v1.3.5 introduces no new failures.

### Currently working on

**v1.3.5 build done.** All hotfix smokes green; awaiting Roman
validation + manual smoke against AI-trade after deploying.

### Next

Roman validates + tags `v1.3.5`. See `V135_BUILD_DONE.md` for
the full smoke test plan.

---

## v1.3.4 HOTFIX — final state

**1 group (R) landed across 6 atomic commits.** See
`V134_BUILD_DONE.md` for the full summary.

| Group | Surface | Tests added |
|---|---|---|
| R1 | src/lib/transient.py — classify_failure (transient / structural / unknown) + is_anthropic_reachable + is_internet_reachable | +39 |
| R2 | state.py: SCHEMA_VERSION 5 → 6, State.consecutive_transient_failures + last_transient_at | +4 |
| R3 | cycle.py: _network_gate_ok pre-cycle probe with backoff ladder + CC_AUTOPIPE_NETWORK_PROBE_DISABLED conftest hook | +4 |
| R4 | cycle.py: transient retry loop, CC_AUTOPIPE_TRANSIENT_BACKOFF_OVERRIDE smoke hook | +5 unit + 2 integration |
| R5 | quota.py: fetch_quota retry loop with QUOTA_RETRY_BACKOFF_SEC = (1,3,8) | +5 |
| R6 | daily_report.py: Connectivity section (network_probe_failed + claude_invocation_transient + retry_exhausted counts) | +1 |
| R8+R9 | tests/smoke/v134/ + mock-claude.sh CC_AUTOPIPE_MOCK_TRANSIENT_THEN_OK + run-all-smokes.sh wiring | +0 (smokes) |
| R10 | rules.md.example "Engine retry behavior" section | docs |

**Test counts (v1.3.4):**
- pytest: 635 (v1.3.3 baseline) → **685 passed** (+50 new tests)
- 17 hotfix smokes all green: 4 v1.3 + 3 v1.3.1 + 3 v1.3.2 +
  5 v1.3.3 + 2 v1.3.4
- Both v1.3.4 real-CLI smokes (no Python heredoc per acceptance) under
  `tests/smoke/v134/`: transient retry (real engine + mock-claude
  emitting transient stderr) and network probe (swap-and-restore
  stub of src/lib/transient.py)

**Schema:** **v5 → v6** (additive). Existing v1.3.3 state.json files
read cleanly via dataclass defaults; first write persists v=6. New
counter fields default to 0 / null → behaviour identical to v1.3.3
unless transient pressure triggers retry path.

**New events in aggregate.jsonl:** `network_probe_failed`,
`network_probe_recovered`, `network_probe_giving_up`,
`claude_invocation_transient`, `claude_invocation_retry_exhausted`.

**New CLI surface:** none. New env vars (test-only):
`CC_AUTOPIPE_NETWORK_PROBE_DISABLED`,
`CC_AUTOPIPE_NETWORK_PROBE_BACKOFF_OVERRIDE`,
`CC_AUTOPIPE_TRANSIENT_BACKOFF_OVERRIDE`,
`CC_AUTOPIPE_MOCK_TRANSIENT_THEN_OK`,
`CC_AUTOPIPE_PROBE_COUNTER_FILE`,
`CC_AUTOPIPE_MOCK_COUNTER_FILE`.

### Currently working on

**v1.3.4 build done.** All gates + smokes green; awaiting Roman
validation + manual smoke against AI-trade after deploying.

### Next

Roman validates + tags `v1.3.4`. See `V134_BUILD_DONE.md` for
the full smoke test plan + manual AI-trade scenarios (parallel
project transient pressure, 6:00 MSK router reboot survival).

---

## v1.3.3 HOTFIX — final state

**3 groups landed.** See `V133_BUILD_DONE.md` for the full summary
including the empirical drivers from the AI-trade production run.

| Group | Surface | Tests added |
|---|---|---|
| L | state.Detached pipeline_log_path + stale_after_sec; phase._maybe_resume_on_stale_pipeline; cc-autopipe-detach --pipeline-log / --stale-after-sec; prompt notice block | +6 |
| M | cli/smoke.py + helpers/cc-autopipe-smoke + dispatcher entry; rules.md template "Pipeline script discipline" + "check_cmd composition" | +8 |
| N | state.last_verdict_event_at + last_verdict_task_id; cycle.py verdict-stage stamping (also emits task_verdict event); lib/knowledge_gate.py with exit code 3; cli/init.py seeds knowledge.md header; rules.md template "Knowledge discipline" | +22 (7 unit + 9 integration + 6 helper integration) |

**Test counts (v1.3.3):**
- pytest: 599 (v1.3.2 baseline) → **635 passed** (+36 new tests)
- 15 hotfix smokes all green: 4 v1.3 + 3 v1.3.1 + 3 v1.3.2 + 5 v1.3.3
- All 5 v1.3.3 real-CLI smokes (no Python heredoc per PROMPT) under
  `tests/smoke/v133/`: liveness stale detection, knowledge gate blocks
  detach, smoke helper command, detach with liveness flags (end-to-end
  with worker subprocess), v1.3.2 backward compat (schema migration)

**Schema:** **v4 → v5** (additive). Existing v1.3.2 state.json files
read cleanly via dataclass defaults; first write persists v=5.
Liveness fields default null → behaviour identical to v1.3.2 unless
operator opts in via --pipeline-log.

**New events in aggregate.jsonl:** `detach_pipeline_stale`,
`detach_pipeline_log_missing`, `detach_pipeline_log_clock_skew`,
`task_verdict`.

**New CLI surface:**
- `cc-autopipe smoke <script> [--timeout-sec N] [--min-alive-sec N]
  [--workdir DIR]` — dispatch + `cc-autopipe-smoke` helper
- `cc-autopipe-detach --pipeline-log <path> [--stale-after-sec N]`
- New exit code: `cc-autopipe-detach exit 3` = knowledge gate failure

### Currently working on

**v1.3.3 build done.** All gates + smokes green; awaiting Roman
validation + manual smoke against AI-trade after deploying.

### Next

Roman validates + tags `v1.3.3`. See `V133_BUILD_DONE.md` for
the full smoke test plan + manual AI-trade gate scenarios (verdict
gate, smoke validation, liveness check).

---

## v1.3.2 HOTFIX — final state

**3 groups landed across 4 atomic commits.** See `V132_BUILD_DONE.md`
for the full summary.

| Group | Surface | Tests added |
|---|---|---|
| RECOVERY-SAFE | recovery.py `_should_recover` gate skips meta_reflect / knowledge_update / research_plan pending + non-failed phases | +15 |
| STDERR-LOGGING | main.py `_redirect_streams_for_daemon` + `_rotate_log` (50MB×3) | +10 |
| TRIGGER-SMOKES | 3 new run-*-smoke.sh scripts + run-all-smokes.sh updated to discover them | +0 (smokes) |

**Test counts (v1.3.2):**
- pytest: 573 (v1.3.1 baseline) → **599 passed** (+26 new tests)
- 10 hotfix smokes all green: 4 v1.3 (autonomy, meta-reflect,
  knowledge-enforce, research-plan) + 3 v1.3.1 (stuck-detection,
  recovery-sweep, detach-defaults) + 3 v1.3.2 (meta-reflect-trigger,
  research-mode-trigger, knowledge-mtime)
- KILL-9 acceptance gate: confirmed orchestrator-stderr.log captures
  the startup line after SIGKILL

**Schema:** unchanged (v4).

### Currently working on

**v1.3.2 build done.** All gates + smokes green; awaiting Roman
validation + manual smoke against AI-trade after deploying.

### Next

Roman validates + tags `v1.3.2`. See `V132_BUILD_DONE.md` for
the full smoke test plan.

---

## v1.3.1 HOTFIX — final state

**3 groups landed across 6 atomic commits.** See `V131_BUILD_DONE.md`
for the full summary.

| Group | Surface | Tests added |
|---|---|---|
| B-FIX | regression test for v1.3's already-removed cap-hit trigger | +1 |
| B3-FIX | recovery.py shutdown safety + per-project lock awareness | +3 |
| DETACH-CONFIG | new module + helper rewrite + templates | +21 |

**Test counts (v1.3.1):**
- pytest: 548 (v1.3 baseline) → **573 passed** (+25 new tests)
- 3 new smokes all green: run-stuck-detection-smoke.sh,
  run-recovery-sweep-smoke.sh, run-detach-defaults-smoke.sh
- 4 v1.3 smokes still green

**Schema:** unchanged (v4).

---

## v1.3 BUILD — final state

**9 groups landed across 9 atomic commits. ~5400 new lines src/lib +
src/orchestrator package + tests. Engine grew from ~7.4K (v1.2) to
~12K lines.**

| Group | Surface | Tests added |
|---|---|---|
| G | orchestrator package refactor (10 modules ≤350 lines) | 0 (mechanical) |
| A | src/lib/findings.py + knowledge.py + Stop hook + SessionStart | +32 |
| B | src/lib/activity.py + activity-based stuck + auto-recovery | +18 |
| C | deploy/systemd/* + disk.py + state.json.bak + watchdog | +30 |
| D | research.py: PRD-complete + research mode + RESEARCH_PLAN enforcement | +17 |
| E | session_start_helper quota notice (60/80/95 bands) | +7 |
| F | daily_report.py + health.py + cli/health.py + bypass logging | +13 |
| H | reflection.py: META_REFLECT replaces verify-pattern HUMAN_NEEDED | +16 |
| I | knowledge.md mtime sentinel + mandatory injection | +10 |
| K | doctor.check_wsl_systemd + deploy/WSL2.md (Path A + Path B) | +6 |

**Test counts (v1.3 final):**
- pytest: 397 (v1.2 baseline) → **548 passed** (+151 new tests)
- 4 new smokes all green: run-autonomy-smoke.sh,
  run-meta-reflect-smoke.sh, run-knowledge-enforce-smoke.sh,
  run-research-plan-smoke.sh

**Schema bump:** state.json schema_version 3 → 4. Pre-v4 state files
auto-migrate on first read+write. New fields preserve defaults for
backward compat.

**Module split (G):**

```
src/orchestrator/
  __init__.py                empty
  __main__.py                python3 path/to/orchestrator entry
  _runtime.py                shared logger / clock / shutdown flag
  main.py                    main loop + signal handlers + sweeps
  cycle.py                   process_project (one cycle)
  preflight.py               quota + disk preflights
  prompt.py                  build_prompt + build_claude_cmd
  phase.py                   DETACHED state-machine + PRD phase advance
  recovery.py                smart-escalation + auto-recovery + META_REFLECT trigger
  research.py                D1+D2+D3 (full research mode + anti-dup)
  reflection.py              H META_REFLECT helpers
  daily_report.py            F1 daily summary
  subprocess_runner.py       _run_claude + stash
  alerts.py                  TG fire-and-forget + dedup
```

Bash dispatcher (`src/helpers/cc-autopipe`) unchanged — `python3
$CC_AUTOPIPE_HOME/orchestrator` works on both file (pre-v1.3) and
package (v1.3+) layouts via Python's `__main__.py` auto-detection.

### Currently working on

**v1.3 build done.** All gates + smokes green; awaiting Roman validation.

### Next

Roman validates manually + tags `v1.3` per AGENTS.md §13. See
`V13_BUILD_DONE.md` for the full smoke test plan.

---

**Post-v1.2 patch (2026-05-04):** `src/VERSION` synced from stale
`0.5.0` to `1.2` (matched latest tag); `src/install.sh` now bakes
`git describe --tags --dirty` into `$PREFIX/VERSION` at install time
when source is a git work-tree. Runtime path unchanged — still reads
the file. Stage-a smoke + ad-hoc install dry-run both green.

**Post-v1.2 hotfix — global Claude hooks (2026-05-04):** orchestrator
now disables Roman's `~/.claude/settings.json` hooks (PreToolUse +
UserPromptSubmit) for the duration of an engine run; `cc-autopipe stop`
restores them. Backup preserved at
`~/.claude/settings.json.cc-autopipe-bak`, idempotent across crashes.
New module `src/lib/claude_settings.py`. Tactical deviation from
instruction-hotfix.md: it referenced `src/cli/start.py` which doesn't
exist in this repo (`start` dispatches to `src/orchestrator`); wired
into the orchestrator startup sequence after singleton-lock acquire.
Test counts: 378+1 → **396 passed, 1 skipped** (+18: 14 unit + 4
integration). Awaiting Roman manual smoke test (see
HOTFIX_HOOKS_DONE.md) before tagging.

## v1.2 BUILD — final state

**8 bugs landed across 3 batches. ~30 atomic commits. Engine grew
from ~5.5K (v1.0) to ~7.4K lines (v1.2).**

| Batch | Bugs | Commits | Tests added |
|---|---|---|---|
| Pre-Batch infra | smoke runner + 2 regressions | 3 | n/a (infra) |
| Batch 1 | A + E | 9 | +56 (state v3, current_task, hooks) |
| Batch 2 | B + H | 11 | +41 (in_progress, failures, human_needed, smart escalation) |
| Batch 3 | C + D + F + G | 9 | +57 (backlog, long-op, task_switched, stage_completed, notify) |

**Test counts (v1.2 final):**
- pytest: 243+1 (v1.0 baseline) → **378 passed, 1 skipped**
- test_stop.sh: 34/34 → 60/60
- test_session_start.sh: 17/17 → 41/41
- regression: hello-fullstack-v1 + v12 both green
- gates: batch-1-v12 ✓, batch-2-v12 ✓, batch-3-v12 ✓ (all fast mode)
- smoke runner: **13/13 in 1333s (~22min)** post-Batch 3, post stage-l fix

**v1.2 SPEC↔repo deviations** (per AGENTS-v1.2.md §15 tactical):
1. Hooks stay bash; v1.2 logic in `src/lib/*_helper.py` invoked from
   bash (Q-V12-2 Roman 2026-05-02).
2. Tests under `tests/{unit,integration}/`, not the
   `tests/{lib,orchestrator,hooks}/` paths SPEC-v1.2.md uses
   illustratively (Q-V12-4).
3. `tests/regression/hello-fullstack-{v1,v12}.sh` created from
   scratch as minimal mocked-claude smokes (Q-V12-6).

**v1.0 backward compat:** confirmed via hello-fullstack-v1.sh
(synthetic v1.0-style project; engine pipeline still works) +
state v2 → v3 auto-migration tests + 5 fresh-state schema tests.

### Bug coverage summary

- **Bug A — current_task in state.json (v3 schema):** state.py
  schema_v3 with CurrentTask dataclass; current_task.py parses
  CURRENT_TASK.md; stop_helper syncs file → state; session_start_helper
  injects block.
- **Bug B — verify in_progress flag:** state.update_verify accepts
  `in_progress`; stop.sh parses `.in_progress` from verify; orchestrator
  applies cooldown × multiplier + caps at max_in_progress_cycles
  with verify-stuck HUMAN_NEEDED message.
- **Bug C — DETACHED long-op guidance:** session_start_helper.long_op
  block always injected; rules.md template adds "Long operation
  discipline" section.
- **Bug D — backlog FIFO + task_switched:** backlog.py parses top-N
  by priority; session_start_helper injects top-3 + CURRENT TASK
  highlight; orchestrator emits task_switched / task_started events.
- **Bug E (covered by A):** legacy v1.0 projects without CURRENT_TASK.md
  work via current_task=None default.
- **Bug F — stages_completed progressive scoring:** CurrentTask
  stages_completed field + parser/render; orchestrator emits
  stage_completed event when array grows within same task; verify.sh
  can use for progressive scoring.
- **Bug G — subprocess fail TG alert with dedup:** notify.py
  notify_subprocess_failed_dedup with sentinel-based 600s window
  per-project per-rc; orchestrator wires after rc!=0 log_failure;
  subprocess_alerted event when not deduped.
- **Bug H — smart escalation:** failures.py categorize_recent
  buckets CRASH vs VERIFY; human_needed.py write_verify_pattern /
  write_mixed_pattern; orchestrator routes 3+ verify_failed →
  HUMAN_NEEDED no escalation, 3+ crashes → opus, 5+ mixed → fail,
  fallback preserves v1.0.

### Currently working on

**v1.2 build done.** All gates + smokes green; awaiting Roman validation.

### Next

Roman validates manually + tags `v1.2` per AGENTS-v1.2.md §13.
Roman should:
1. Read this STATUS.md.
2. Run `bash tests/regression/hello-fullstack-v12.sh` (~7s).
3. Optionally run `bash tests/smoke/run-all-smokes.sh` (~20min).
4. Pick a real R&D project (AI-trade) and try a single
   `cc-autopipe run --once` to feel the new SessionStart blocks.
5. `git tag v1.2` when satisfied.

---

## v1.0 BUILD — final state (frozen 2026-05-02 14:50Z)

---

## v1.2 BUILD — in progress

### Understanding check

8 production hardening fixes (A–H) discovered through real-world
test of v1.0 on AI-trade ML R&D project. Grouped into 3 batches per
AGENTS-v1.2.md §3:

- **Batch 1 = A + E:** state schema v3 (`current_task` field with
  id/started_at/stage/stages_completed/artifact_paths/claude_notes;
  also `last_in_progress` + `consecutive_in_progress`); CURRENT_TASK.md
  read by Stop hook → state.json; SessionStart hook reads state →
  injects current task into prompt; v2 → v3 auto-migration on
  first read+write. **E is implicit in A** — `current_task.id`
  replaces standalone CAND_NAME concept.
- **Batch 2 = B + H:** verify.sh contract gains optional
  `in_progress: bool`; engine does NOT count as failure when
  in_progress=true (consecutive_in_progress incremented instead;
  cooldown × multiplier). Smart escalation reads recent failures,
  categorises by error type — `claude_subprocess_failed` (3 in
  a row) → escalate to Opus; `verify_failed` (3 in a row) →
  HUMAN_NEEDED.md + TG, no escalation; mixed/5+ → phase=failed.
- **Batch 3 = C + D + F + G:** SessionStart hook adds long-op
  guidance block (Bug C); reads top-3 OPEN tasks from backlog.md
  by priority and injects (Bug D); detects task switch (Bug D);
  extends current_task with stages_completed array + injects
  progress block (Bug F); orchestrator TG-alerts on rc != 0
  with 600s sentinel-based dedup (Bug G).

After Batch 3 + final integration check + hello-fullstack regression,
build halts for Roman validation. He tags v1.2.

### Tactical SPEC↔repo deviations (per AGENTS-v1.2.md §15)

SPEC-v1.2.md and AGENTS-v1.2.md were drafted against a Python-modular
hook layout that does not match v1.0 reality. Roman approved the
following adaptations as tactical (acknowledged in
[chat 2026-05-02T22:25Z]):

1. **Hook architecture stays bash.** `src/hooks/*.sh` remain thin
   bash dispatchers; v1.2 logic lives in Python helpers under
   `src/lib/` and is invoked from bash via `python3 -c "..."` or
   `python3 src/lib/<helper>.py ...`. Read every SPEC-v1.2.md
   reference like `src/orchestrator/hooks/session_start.py` as
   "the SessionStart logic, implemented in
   `src/lib/session_start_helper.py`".
2. **Test directory is `tests/smoke/` (singular).** The new smoke
   runner lives at `tests/smoke/run-all-smokes.sh`, not
   `tests/smokes/`.
3. **Regression scripts created from scratch as minimal mocked-claude
   smokes.** `tests/regression/hello-fullstack-v1.sh` did not exist
   in v1.0 (was deferred per Stage G shakedown). Built minimally for
   v1.2 to enable programmatic backward-compat verification.

Test-dir mapping under §15 tactical:
- `tests/lib/test_*.py` → `tests/unit/test_*.py`
- `tests/orchestrator/test_*.py` → `tests/integration/test_*.py`
- `tests/hooks/test_*.py` → `tests/unit/test_hooks/test_*.{py,sh}`

Library helpers go flat under `src/lib/`:
`current_task.py`, `session_start_helper.py`, `stop_helper.py`,
`backlog.py`, `failures.py`, `human_needed.py`, plus a Python
`notify.py` adding a `notify_subprocess_failed_dedup` wrapper around
the existing `tg.sh`.

### Currently working on

Pre-Batch 1 infrastructure:
- ✅ `tests/smoke/run-all-smokes.sh` — wrapper around 13 stage smokes
  (validated against stage-a; full 13/13 trusted from v1.0 final
  STATUS.md "individually verified all green" 2026-05-02 afternoon)
- ✅ `tests/regression/hello-fullstack-v1.sh` — mocked-claude regression base
  (131 lines, shellcheck clean, passes on current v1.0 engine; uses
  /usr/bin/true as claude_bin + pre-seeded quota cache)
- ✅ `tests/regression/hello-fullstack-v12.sh` — extends v1 with
  schema_v3 + current_task + in_progress assertions (126 lines,
  shellcheck clean). EXPECTED to fail pre-Batch 1 at schema_v3
  assertion (engine still writes schema_v2); confirmed exit 1
  with clear "expected schema_version=3, got 2" message. Becomes
  green after Batch 1 lands.

**Batch 1 (Bug A + E) — COMPLETE.** 9 atomic commits land schema v3,
current_task helpers, and hook wiring end-to-end. Gate green in fast
mode (lint+pytest+regression+v1.2-specific blocks). Full smoke runner
also green: **13/13 in 1189s (~20min)** — schema_v3 bump did not
regress any v0.5/v1.0 stage smoke.

- ✅ state.py schema_v3 with `current_task` (CurrentTask dataclass) +
  `last_in_progress` + `consecutive_in_progress`. Pre-v3 state files
  auto-migrate on read (defaults supply missing fields).
- ✅ test_state.py: +5 tests covering v2→v3 migration, current_task
  round-trip, partial-dict tolerance, in_progress counters round-trip,
  forward-compat extras pass-through.
- ✅ `src/lib/current_task.py` — parse/write CURRENT_TASK.md
  (line-oriented `key: value`; multi-line notes via continuation;
  artifact: lines accumulate; CLI: `parse` / `write`). +27 unit tests.
  Parser fix exposed by tests: unknown `key:` lines no longer absorbed
  as continuation of previous key.
- ✅ `src/lib/stop_helper.py` + `src/hooks/stop.sh` — Stop hook wires
  CURRENT_TASK.md → state.json.current_task. Empty/missing file is
  a no-op. Helper enforces always-exit-0. +7 unit tests + 13 bash
  assertions (47/47 test_stop.sh).
- ✅ `src/lib/session_start_helper.py` + `src/hooks/session-start.sh` —
  injects `=== Current task ===` block. Two modes (no-task helper /
  populated). +17 unit tests + 9 bash assertions (28/28 test_session_start.sh).
- ✅ `tests/gates/batch-1-v12.sh` — 9-category gate; CC_AUTOPIPE_GATE_FAST=1
  to skip the 25-min smoke-runner block.

**Test counts:**
- pytest: 243+1 (baseline) → 299 passed, 1 skipped
- test_stop.sh: 34/34 → 47/47
- test_session_start.sh: 17/17 → 28/28
- regression: hello-fullstack-v1 green, hello-fullstack-v12 green
- gate (fast mode): 13/13 GATE OK

**Engine size:** state.py 91 lines added; new modules 410 lines; hooks
+15 lines bash. Tests +800 lines. Total ~1300 lines added across
9 commits.

### Tail (last 9 commits — Batch 1)
- fad4371 tests: add batch-1-v12 gate (state schema + current_task)
- 3253c7f tests: cover session_start current_task injection
- 439b1f7 hooks: session_start injects current_task into prompt
- 638e531 tests: cover stop hook current_task integration
- fb3699b hooks: stop reads CURRENT_TASK.md, updates state.json
- 6bd5814 tests: cover current_task module + fix unknown-key absorption
- 1f432ba current_task: parse/write CURRENT_TASK.md helper module
- 165d9fa tests: cover state v2→v3 migration paths
- 7f7b535 state: bump schema to v3 with current_task + in_progress fields

### Currently working on

**Batch 1 + Batch 2 done. Batch 3 starting** (Bug C + D + F + G,
cooldown skipped per .cc-autopipe/SKIP_COOLDOWN, Roman 2026-05-03).

### Batch 2 (Bug B + Bug H) — done

- ✅ state.update_verify accepts `in_progress` kwarg (CLI: --in-progress)
- ✅ src/hooks/stop.sh parses `.in_progress` from verify output, routes
  to cycle_in_progress event when true (no failures.jsonl pollution)
- ✅ orchestrator: in_progress cooldown × multiplier in main loop;
  cap at consecutive_in_progress >= max → phase=failed +
  in_progress-specific HUMAN_NEEDED + TG
- ✅ src/lib/failures.py — `read_recent`, `categorize_recent` with
  CRASH/VERIFY buckets and recommend_{escalation,human_needed,failed}
- ✅ src/lib/human_needed.py — `write`, `write_verify_pattern`,
  `write_mixed_pattern` (atomic, contract-safe)
- ✅ orchestrator: smart escalation routes by category — verify-pattern
  → HUMAN_NEEDED no escalation; crash-pattern → opus (v1.0 path);
  5+ mixed → fail; fallback preserves v1.0 deferred-fail
- ✅ tests/gates/batch-2-v12.sh — 12-category gate (PASSED fast mode)

**Test counts (Batch 2 close):**
- pytest 299+1 → **340 passed, 1 skipped** (+41 new tests)
- test_stop.sh: 47 → **60 PASS**
- regressions: v1 + v12 green
- gate (fast): 12/12 GATE OK

### Batch 3 in progress (Bug C + D + F + G)

- ☐ Bug C: SessionStart long-operation guidance block + rules.md
  template addition
- ☐ Bug D: backlog top-3 injection + task_switched detection
- ☐ Bug F: stages_completed array progressive scoring + injection
- ☐ Bug G: subprocess rc!=0 TG alert with sentinel-based dedup
- ☐ tests/gates/batch-3-v12.sh

### Pre-flight (initial)

| Check | Status |
|---|---|
| 1. `git status` clean | ✅ after `0d09893` (doc switch v0.5/v1.0 → v1.2) |
| 2. pytest 243 + 1 skip | ✅ (130.87s, 2026-05-02T22:00Z) |
| 3. `tests/smoke/run-all-smokes.sh` | ◐ runner being built; individual smokes 13/13 verified per v1.0 final |
| 4. `cc-autopipe doctor` 10/10 | ⏸ deferred to Roman (live oauth/usage call) |
| 5. quota 7d < 90% | ⏸ deferred to Roman (live call burns quota) |

Roman validates 4 + 5 manually before he resumes from any halt.

---

## v1.0 BUILD — final state (frozen 2026-05-02 14:50Z)

v1.0 done. Four batches landed back-to-back over the 2026-05-02
session, with mandatory 60-min inter-batch sleeps between each per
AGENTS-v1.md §1.2:

  Batch a (v0.5.1)        7 commits — rules.md template, verify.sh
                                       template, `cc-autopipe stop`,
                                       gate. GATE PASSED.
  Batch b (Stages H/I/J)  9 commits — schema v2 + Detached, detach
                                       helper, orchestrator DETACHED
                                       branch, pre-tool-use rule 7,
                                       researcher+reporter subagents,
                                       PRD phase parser, orchestrator
                                       phase transitions. GATE PASSED.
  Batch c (Stages K/L)    7 commits — quota_monitor daemon +
                                       orchestrator wiring;
                                       auto_escalation config + state
                                       field + orchestrator branch +
                                       reminder + revert + resume
                                       clear. GATE PASSED.
  Batch d (Stages M/N)    2 commits — systemd + launchd templates +
                                       install/uninstall CLI; improver
                                       subagent + orchestrator
                                       trigger every N successes +
                                       skills dir prep + prompt hint.

Engine grew from ~3.6K lines (v0.5) to ~5.5K lines (v1.0). Test
coverage: 243 pytest pass + 1 macOS skip; 13 stage smokes (a-f, h-n);
4 batch gates + 1 final gate.

Roman should:
  1. `git tag v0.5.1` (Batch a)
  2. `git tag v1.0-batch-b` (Batch b)
  3. `git tag v1.0-batch-c` (Batch c)
  4. `git tag v1.0` (Batch d / final)

Tagging is HUMAN-ONLY per AGENTS-v1.md §6.

## v0.5 legacy stages — final state

All 6 Stage F surfaces shipped in the v0.5.0 build:

  - `helpers/cc-autopipe-checkpoint` — bash, saves
    `.cc-autopipe/checkpoint.md` from arg or stdin (atomic write)
  - `helpers/cc-autopipe-block` — bash, marks state.phase=failed,
    writes HUMAN_NEEDED.md, fires log-event + TG alert
  - `cli/resume.py` — clears PAUSED/FAILED, resets
    consecutive_failures, removes HUMAN_NEEDED.md
  - `cli/tail.py` — tail -f for aggregate.jsonl, ANSI colors,
    --project / --event filters, --no-follow mode, stdlib only
  - `cli/run.py` — single-cycle bypass-singleton wrapper around
    orchestrator.process_project (used by Stage G smoke)
  - `cli/doctor.py` — 10-check prerequisite suite, --offline +
    --json flags, macOS Keychain notice up front

Eight atomic commits land Stage F (helpers + 4 cli + dispatcher +
tests + smoke). 26 new pytest cases in tests/integration/test_cli.py.
tests/smoke/stage-f.sh validates the DoD checklist end-to-end.

All 6 smokes (A–F) pass naked. 147 pytest unit+integration pass
(1 macOS-skip, expected).

Stage G is project-side (hello-fullstack), not engine code. Begins
after May 2 quota reset per Roman's plan.

## Last commit

`docs: OPEN_QUESTIONS Q20 (real-TG leak via tests)` (Q20 fix,
4 commits 2026-05-02 afternoon: tg.sh secrets-resolution + conftest
isolation + test_quota_monitor explicit notify_tg + Q20 docs).

## Batch d gate verification (run 2026-05-02 afternoon)

Full `tests/gates/batch-d.sh` script exceeds the 10-min foreground
window because each of 13 stage smokes internally re-runs the full
pytest suite (~2min × 13 = ~26min minimum). Components verified
individually instead, all green:

- **Hygiene:** working tree clean; no `Status: blocked` in
  OPEN_QUESTIONS.md; no orphan `TODO(v0.5.1|v1.0)` markers.
- **Lint:** `ruff check src tests tools` clean (36 files);
  `ruff format --check` clean; `shellcheck -x` clean (37 files).
- **v0.5 smokes:** stage-a/b/c/d/e/f all OK.
- **v1.0 smokes:** stage-h/i/j/k/l/m/n all OK.
- **Pytest:** 243 passed, 1 macOS-skip (120s).
- **Doctor:** `--offline` reports 8 ok, 0 warn, 0 fail, 2 skip.
- **Batch-d surface:** systemd + launchd templates exist;
  service.py exposes 4 subcommands; dispatcher --help lists
  install-systemd + install-launchd; agents.json carries improver;
  config.yaml carries `improver:` block; state.py exposes
  `successful_cycles_since_improver` and `improver_due`;
  orchestrator carries `_read_config_improver` and
  `improver_trigger_due` event.

Q20 verification: ran each chunk with `CC_AUTOPIPE_TG_TRACE=/tmp/...`
and `unset TG_BOT_TOKEN TG_CHAT_ID`. Trace recorded zero hits on
the real `~/.cc-autopipe/secrets.env`; the 5 logged tg.sh
invocations across all chunks all came from per-test TMP
secrets.env fixtures (stage-a/c/e deliberately seed fake creds to
exercise tg.sh's bad-cred path).

## Stages completion

- [x] Stage A: Foundations (completed 2026-04-29T02:40Z)
- [x] Stage B: Orchestrator skeleton (completed 2026-04-29T03:00Z)
- [x] Stage C: Hooks (completed 2026-04-29T07:05Z)
- [x] Stage D: Locking and recovery (completed 2026-04-29T09:55Z)
- [x] Stage E: Quota awareness (completed 2026-04-29T15:30Z) +
      Q12 hot-fix (2026-04-29T18:30Z, real-endpoint format fix)
- [x] Stage F: Helpers and CLI (completed 2026-04-29T20:00Z) —
      **Engine v0.5.0 complete**
- [x] Stage G: Hello-fullstack smoke test (real-claude verification
      deferred per quota window; engine surfaces validated by 6
      stage smokes + Stage G shakedown bug-fixes 2026-04-30)
- [x] Batch a (v0.5.1 cleanup): 7 commits 2026-05-02 — rules.md
      template, verify.sh template, cc-autopipe stop subcommand,
      gate validator. **v0.5.1 complete** (pending Roman tag v0.5.1).
- [x] Batch b (v1.0 part 1: Stages H/I/J): 9 commits 2026-05-02 —
      schema v2, cc-autopipe-detach + dispatcher, orchestrator
      DETACHED branch, pre-tool-use rule 7, R/R subagents,
      PRD phase parser, orchestrator phase transitions. GATE PASSED;
      pending Roman tag v1.0-batch-b.
- [x] Batch c (v1.0 part 2: Stages K/L): 7 commits 2026-05-02 —
      quota_monitor daemon + orchestrator wiring + smoke;
      auto_escalation config + state field + orchestrator branch +
      reminder injection + revert + resume clear + smoke.
      GATE PASSED; pending Roman tag v1.0-batch-c.
- [x] Batch d (v1.0 part 3: Stages M/N): 2 commits 2026-05-02 —
      systemd + launchd templates + install/uninstall CLI + smoke;
      improver subagent + orchestrator N-success trigger + skills
      dir + prompt hint + smoke. **v1.0 BUILD COMPLETE** pending
      Roman tag v1.0.

## Stage E DoD verification

All items green, validated by `bash tests/smoke/stage-e.sh`:

- [x] quota.py reads OAuth token on Linux from
      `~/.claude/credentials.json` (or `$CC_AUTOPIPE_CREDENTIALS_FILE`)
- [x] quota.py reads OAuth token on macOS from Keychain
      (verified live: nested `claudeAiOauth.accessToken` shape)
- [x] quota.py returns None gracefully when token missing
- [x] quota.py returns None gracefully when endpoint unreachable
- [x] quota.py caches results for 60s (mtime-based TTL)
- [x] ratelimit.py implements 5min/15min/1h ladder
- [x] ratelimit.py resets counter after 6h with no 429
- [x] orchestrator pre-flight check pauses project at >=95% 5h
- [x] orchestrator pre-flight check pauses ALL projects at >=95% 7d
      (Q14 deviation, was >=90%; with 5min TG dedup via
      `7d-tg.last` sentinel). 5h warn raised 0.80→0.85; 7d warn band
      added at 0.90.
- [x] stop-failure.sh uses quota.py first, falls back to ratelimit.py
      (and last-resort 1h if both unavailable)
- [x] tests/integration/test_quota.py passes (12/12, 1 macOS-skip)
- [x] STATUS.md updated

Bonus tests: tests/unit/test_ratelimit.py (14/14),
tests/integration/test_orchestrator_quota.py (12/12, pre-flight +
stop-failure+quota end-to-end).

Test totals: 147 pytest unit+integration across A→F (+30 vs Stage E
post-Q12) plus 1 expected macOS-skip and 103 hook unit cases. Seven
smoke validators (stage-a through stage-f) all green together.

## Stage F DoD verification

All items green, validated by `bash tests/smoke/stage-f.sh`:

- [x] `cc-autopipe-checkpoint` saves checkpoint.md correctly (arg + stdin)
- [x] `cc-autopipe-block` marks project failed and creates HUMAN_NEEDED.md
- [x] `cc-autopipe resume` clears PAUSED/FAILED, resets failures
- [x] `cc-autopipe doctor` checks all prerequisites and reports
      (10 checks, --offline + --json flags)
- [x] `cc-autopipe tail` follows aggregate.jsonl (filters + colors)
- [x] `cc-autopipe run <project> --once` runs single cycle
- [x] All commands have --help (dispatcher + 6 subcommands)
- [x] tests/integration/test_cli.py passes (26/26)
- [x] STATUS.md marked "Engine v0.5.0 complete"

## Process debt

Test-environment escape hatches:
- `CC_AUTOPIPE_QUOTA_DISABLED=1` — short-circuits quota.read_raw
  to None. Used intentionally now in stage-e ladder-fallback test
  and in pytest tests that need a clean "quota unavailable" signal.
  No longer used as a Q12-bug mask (smokes pre-populate cache
  instead).
- `CC_AUTOPIPE_CREDENTIALS_FILE` — overrides
  `~/.claude/credentials.json` for Linux-path tests on macOS.

Stray `test.sh` and `check.sh` in repo root (untracked, not engine
code; Roman's manual exploration scripts). Left alone.

## Stage G shakedown — 2026-04-30 fixes

First real-claude run on hello-fullstack surfaced three engine bugs.
All three landed as atomic commits today.

- **Bug 1 (CRITICAL): `--verbose` missing.** claude 2.1.123 rejects
  `-p` + `--output-format stream-json` without `--verbose`:
      Error: When using --print, --output-format=stream-json requires --verbose
  `_build_claude_cmd` now emits `--verbose` between
  `--dangerously-skip-permissions` and `--max-turns`.
- **Bug 2: subprocess streams not persisted.** Stage F's
  `_stash_stream` early-returned on empty content, so a fast rc!=0
  exit left a stale prior-cycle log. Renamed to
  `claude-last-stdout.log` / `claude-last-stderr.log` and now writes
  on every cycle (even when empty).
- **Bug 2 follow-on: `claude_subprocess_failed` failures entry.** On
  `rc != 0`, orchestrator now appends to `failures.jsonl` with
  `exit_code` and `stderr_tail` (last 500 chars). Without this,
  fast rc=1 exits had no audit trail (no Stop hook fires).
- **Bug 3: lax mock-claude.** `tools/mock-claude.sh` accepted the
  `-p` + stream-json + no-verbose combo — that's why the missing
  `--verbose` shipped to Stage G undetected. Mock now rejects with
  the same diagnostic message real claude emits.

Regression test in `test_orchestrator_claude.py`:
`test_orchestrator_passes_verbose_to_avoid_stream_json_rejection`
asserts no `claude_subprocess_failed` entry under the strict mock.
Verified by deletion: temporarily removed `--verbose` and confirmed
test failed with the expected "requires --verbose" stderr_tail.

All 6 stage smokes (A-F) pass after the fixes. 150 pytest cases
+ 1 macOS skip.

Real-claude verification on hello-fullstack is **deferred**: the
project is paused with `resume_at=2026-05-01T23:59:59Z` from
yesterday's 7d=96% pre-flight pause. Current 7d=94% per Roman's
report, but `_resume_paused_if_due` won't unpause until the
recorded `resume_at` passes. Clearing via `cc-autopipe resume
hello-fullstack` would burn a cycle at 94% 7d — Roman's call.

## Currently blocked

None.

## Recent open questions

- Q1 (resolved by Q12): oauth/usage response format — real-endpoint
  hit revealed integer-vs-float discrepancy.
- Q2 (deferred-to-Stage-G): claude --resume with deleted JSONL.
- Q3 (resolved Stage C): Stop hook session_id reliability.
- Q4 (resolved Stage E): macOS Keychain — no prompt observed,
  ground-truth payload format documented.
- Q5 (deferred-to-Stage-G): --max-turns counter on resume.
- Q6 (open, Stage F/G): backlog.md tag handling.
- Q7 (resolved Stage A): TG --data-urlencode.
- Q8 (resolved Stage D): flock on macOS — used Python fcntl.
- Q9 (resolved Stage A): compat.sh feature-detect.
- Q10 (resolved Stage B): src/cli/ deviation.
- Q11 (resolved Stage D): src/lib/locking.py split.
- Q12 (resolved Stage E hot-fix): oauth/usage emits integer percent.
- Q13 (resolved Stage E hot-fix): additional unused endpoint fields.
- Q14 (resolved-as-deviation, 2026-04-30): pre-flight 7d threshold
  raised 0.90→0.95 + 5h warn raised 0.80→0.85 + 7d warn band added
  at 0.90. SPEC §9.2 deviation, documented for v1 docs review.

Q6 (backlog.md tag handling) carried into Stage G — not exercised
in v0.5 engine code yet (orchestrator's prompt builder reads top-N
[ ] tasks from backlog.md without parsing tags). v0.5 default per
the recommendation in OPEN_QUESTIONS.md Q6: "treat as normal open
task". Stage G will validate against a tagged backlog if hello-
fullstack uses any.

## Tooling notes

- quota.py CLI surface: `read | read-cached | refresh`. Hooks call
  `python3 quota.py read` per SPEC §9.3.
- ratelimit.py CLI: `register-429 | state | reset`.
- mock-quota-server.py works with random ports; tests spawn a fresh
  instance per test via `_free_port()`.

## Notes for next session

Stage G is project-side (hello-fullstack), not engine code.
Engine v0.5.0 is functionally complete per AGENTS.md §12 except
the items below.

### Stage G prep (waits for May 2 quota reset)

- `examples/hello-fullstack/` project skeleton (separate repo per
  AGENTS.md §2 Stage G).
- PRD covering pytest + npm build + docker-compose targets.
- verify.sh wired against those targets.
- Run `cc-autopipe init` in the project, then
  `cc-autopipe run <path> --once` to validate one cycle with real
  claude before kicking off `cc-autopipe start`.
- Goal: full PRD reaches DONE under cc-autopipe in <4h.

### Open items deferred to Stage G or v1

- Q2: claude --resume with deleted JSONL — verify against real
  claude, iterate on `_build_claude_cmd` if it errors.
- Q5: --max-turns counter behaviour on resume — observe under
  hello-fullstack; mitigation already in place via checkpoint-based
  continuity.
- Q6: backlog.md tag handling — defaults to "treat tagged tasks as
  normal open"; revisit if hello-fullstack uses any.

### Stage F things NOT shipped (intentional)

- `cc-autopipe stop` (SPEC §12.3) — left as not_implemented in the
  dispatcher. Stage D singleton lock + SIGTERM handler already
  provide the underlying mechanism; the user-facing wrapper is
  small (read PID file, kill -TERM, kill -KILL after 60s) but
  wasn't in the user's Stage F scope. Add as a v0.5.1 patch or
  early Stage G prep.

### Operating reminders for Stage G

- Roman has Claude MAX 20x. NEVER use Anthropic API SDK.
- Stage G is the ONLY place real claude runs during the build.
- Telegram credentials in ~/.cc-autopipe/secrets.env, not in repo.
- doctor `--offline` keeps tests deterministic; default doctor run
  hits the live oauth/usage + TG endpoints (one-shot, low cost).
- run.py uses SourceFileLoader to import the extensionless
  orchestrator script — keep that pattern if any other CLI needs
  to call into orchestrator internals.
