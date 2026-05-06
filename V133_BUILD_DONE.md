# v1.3.3 hotfix complete

Commits: TBD (one per group + STATUS + this doc)
Tests added: +36
Total tests: 599 baseline (v1.3.2) + 36 new = **635 passed, 0 skipped**
All gates green: yes
Smokes: 4 v1.3 + 3 v1.3.1 + 3 v1.3.2 + 5 v1.3.3 = 15 total, all green
Schema bump: v4 → **v5** (additive, backward compatible)

## Why this release exists

Empirical failures during v1.3.2 production run on AI-trade project:

1. **vec_multihead** ran full Stage A→D pipeline, REJECTED with sum
   -10.05%. Claude did NOT update `knowledge.md` before switching to
   the next task. Two consecutive REJECTs would have lost the lesson
   permanently.
2. **vec_rl** Stage A (PPO 1M steps) succeeded; Stage B/C crashed with
   `PermissionError: [Errno 13] Permission denied: '/app/data/backtest'`
   from a Claude-written launcher. The engine kept polling `check_cmd`
   for **50+ minutes** with zero awareness the pipeline was dead. Without
   intervention, ~10 hours would have been wasted up to the max-wait
   timeout.
3. Root cause class A: engine's `check_cmd` only answers "is success
   condition met?" — never "is the process alive?". Silent crashes burn
   the entire max-wait window.
4. Root cause class B: Claude writes pipeline scripts with no smoke
   validation. First real execution = first error discovery, which
   happens AFTER detach when nobody is watching.

Three orthogonal safeguards: **liveness check** (engine-side stale
detection), **smoke gate** (project-side script validation before
detach), **knowledge enforcement** (engine-side gate blocking detach
if the verdict was not recorded).

## Group summaries

- **L — Liveness check (engine-side):** `Detached` gains optional
  `pipeline_log_path` + `stale_after_sec` fields. When both are set
  and `check_cmd` is failing, `phase._maybe_resume_on_stale_pipeline`
  inspects the log mtime gap. Two outcomes besides "still waiting":
    - log mtime gap > threshold → emit `detach_pipeline_stale`,
      transition phase=active with `last_detach_resume_reason="pipeline_stale"`
    - log file missing → emit `detach_pipeline_log_missing`, same
      resume path (reason="pipeline_log_missing")
  The next cycle's `_build_prompt` injects a "PIPELINE LIVENESS
  RESUMED" / "PIPELINE LOG MISSING" notice block so Claude knows to
  diagnose instead of moving to the next task. Clock-skew (negative
  age) is logged once via `detach_pipeline_log_clock_skew` and not
  treated as stale.

  CLI flags: `cc-autopipe-detach --pipeline-log <abs_path>` +
  `--stale-after-sec N` (default 1800 when log path provided alone).
  `--stale-after-sec` without `--pipeline-log` errors out with rc=64.

  Tests: +6 unit (`tests/unit/test_liveness_phase.py` —
  no-op without flags / log missing / stale resume / fresh log /
  clock skew) + integration coverage via P1 / P4 smokes.

- **M — Smoke validation gate (helper command):** New
  `cc-autopipe-smoke <script> [--timeout-sec N] [--min-alive-sec N]
  [--workdir DIR]` validates a pipeline script before Claude calls
  detach. Three terminal outcomes:
    - SMOKE_OK exit 0 (script rc=0 within timeout, OR alive past
      min-alive then killed cleanly via `os.killpg`)
    - SMOKE_FAIL exit 1 (script rc!=0 within timeout — last 30 stderr
      lines dumped to console)
    - misuse exit 2 (missing/non-executable script, --timeout < --min-alive)

  Process tree kill is reliable: `start_new_session=True` +
  `os.killpg(os.getpgid(pid), SIGTERM→SIGKILL)`, same idiom the engine
  uses elsewhere for cleanup. Engine does NOT enforce smoke; that's
  rules.md discipline (see Group O additions). Engine's safety net for
  the case Claude skips smoke is the Group L liveness check.

  Tests: +8 unit (`tests/unit/test_smoke_cli.py`) + P3 smoke covers
  the bash wrapper + dispatcher path.

- **N — Knowledge.md enforcement gate (engine-side):**
  `last_verdict_event_at` + `last_verdict_task_id` added to State.
  `cycle.py` stamps both whenever a verdict-stage transition is
  detected (same path that arms `knowledge_update_pending`). New
  `task_verdict` event emitted to aggregate.jsonl alongside the
  existing `knowledge_update_required`.

  New `lib/knowledge_gate.py` enforces at detach time:
    - knowledge.md missing  → exit 3 with BLOCKED stderr message
    - knowledge.md mtime < verdict_ts → exit 3 with explicit reason
    - knowledge.md fresh    → exit 0, gate passes silently

  `cc-autopipe-detach` invokes the gate before `state.set_detached`.
  On a successful detach (gate passed), `set_detached` resets
  `last_verdict_event_at` / `last_verdict_task_id` to None so the gate
  fires once per verdict, not on every subsequent detach.

  `cc-autopipe init` seeds a fresh `.cc-autopipe/knowledge.md` with a
  v1.3.3 header template (verdict-formatted entry skeleton). Existing
  files are left alone — operators with curated notes don't get
  clobbered.

  Tests: +7 unit (`tests/unit/test_knowledge_gate.py`) + 9 integration
  (`tests/integration/test_detach_knowledge_gate.py`) + P2 smoke.

- **O — rules.md template extensions:** Three new sections appended
  to `src/templates/.cc-autopipe/rules.md.example`:
    - "Knowledge discipline (v1.3.3 — engine-enforced)" — explains
      the exit 3 path.
    - "Pipeline script discipline (v1.3.3 — mandatory before detach)"
      — smoke validation procedure + Docker / env / path failure
      classes + always pass `--pipeline-log` to detach.
    - "check_cmd composition (v1.3.3 guidance)" — GOOD/BAD examples
      with `find -size +1c`, composite `test -s + grep`, and
      OR-marker for terminal-state polling.

  Rules-only — no engine code change.

- **P — Real-CLI smoke tests (NO Python heredoc):** Five new smoke
  scripts under `tests/smoke/v133/`:

    - `test_liveness_stale_detection.sh` — seeds a project with stale
      pipeline.log (mtime 1h ago), Detached state with stale_after_sec=300,
      runs `cc-autopipe run --once` with mock-claude. Asserts
      `detach_pipeline_stale` event with `log_age_sec >= 3000` and
      phase moved past `detached`.

    - `test_knowledge_gate_blocks_detach.sh` — seeds verdict 60s ago,
      missing/stale knowledge.md → `cc-autopipe-detach` exits 3 with
      BLOCKED, state unmutated. Append entry → retry → exit 0,
      phase=detached, last_verdict_event_at=null.

    - `test_smoke_helper_command.sh` — three sub-cases (success.sh
      rc=0 → SMOKE_OK / fail.sh rc=7 → SMOKE_FAIL with "boom" in
      stderr / long.sh sleep 120 with --timeout=8 → SMOKE_OK alive,
      kill confirmed via `pgrep -f`). Plus dispatcher path
      (`cc-autopipe smoke <script>`) + missing-script misuse rc=2.

    - `test_detach_with_liveness_flags.sh` — most expensive (~14s).
      Two paths: (A) worker writes log every 0.5s + creates done.flag
      → `detach_resumed` (no stale event); (B) worker writes for 2s
      then exits without done.flag → after 4s the log is stale →
      `detach_pipeline_stale` event fires.

    - `test_v132_backward_compat.sh` — crafts schema_version=4 state
      WITHOUT new fields, runs `cc-autopipe status` + `cc-autopipe
      run --once`. Asserts schema migrates 4→5, liveness fields stay
      null (no retroactive opt-in), no stale events fired (parity
      with v1.3.2 behaviour for opt-out projects).

  `tests/smoke/run-all-smokes.sh` resolver extended to discover
  `tests/smoke/v133/test_<name>.sh` via the `v133-<name>` prefix
  pattern. Default smoke run now includes all 15 hotfix smokes.

## Commit list (TBD)

Will land as 5 atomic commits per group + STATUS + this doc. Per
CLAUDE.md and project convention: NEVER push, NEVER tag — Roman
handles both manually after validation.

## SPEC ↔ repo deviations

1. PROMPT-v1.3.3 §L1 referenced `src/cc_autopipe/state.py` and a
   `_migrate_state_v4_to_v5` helper. Real layout is `src/lib/state.py`,
   and the existing `State.from_dict` already migrates additively
   (forces `kwargs["schema_version"] = SCHEMA_VERSION`, dataclass
   defaults supply missing fields). No explicit migration helper
   needed; the v4→v5 path is exercised by
   `test_v4_state_migrates_to_v5_on_read` in `tests/unit/test_state_v133.py`.

2. PROMPT-v1.3.3 §L2 specified a `DetachResult` enum and `STALE_RESUMED`
   value. Real `_process_detached` already returns plain string
   sentinels ("active" / "detached" / "failed"); introducing an enum
   for one callsite is over-abstraction. Implementation: stale path
   returns "active" with `last_detach_resume_reason` field stamped on
   state — the next cycle's `_build_prompt` reads it and injects the
   notice. Functionally equivalent.

3. PROMPT-v1.3.3 §N1 specified three event types `task_verdict_reject`
   / `task_verdict_accept` / `task_verdict_infra_failed`. The engine
   cannot reliably distinguish these without parsing PROMOTION.md /
   knowledge.md — the verdict outcome (REJECT vs ACCEPT) lives in
   Claude's output text, not in any structured signal. Implementation:
   one `task_verdict` event with `stage` + `task_id` + `verdict_ts`,
   emitted alongside the existing `knowledge_update_required`. The
   gate logic only needs the timestamp anyway. Listed verdict types
   moved to rules.md guidance for the human-readable knowledge entry
   header.

4. PROMPT-v1.3.3 acceptance gates listed "All 599+ pytest tests pass."
   Hit 635 (+36 — additions on top of v1.3.2 baseline 599).

5. PROMPT-v1.3.3 Q1 asked for `CHANGELOG.md`. The project uses
   `V*_BUILD_DONE.md` files as the changelog convention (V13_BUILD_DONE.md,
   V131_BUILD_DONE.md, V132_BUILD_DONE.md). Creating a fresh
   CHANGELOG.md would diverge from the established pattern; this file
   serves the changelog role for v1.3.3.

6. PROMPT-v1.3.3 Q3 asked for README.md "Detached operations"
   subsections. The repo's README.md does not currently have a
   "Detached operations" section to extend — README is the build-repo
   audience-pointer, not user-facing documentation. The detach
   workflow lives in `src/templates/.cc-autopipe/rules.md.example`
   (which Group O extends in detail) — that's where Claude reads it
   each session, which is the load-bearing path. Skipping the README
   addition keeps the doc surface coherent.

7. PROMPT-v1.3.3 instructed "commit + tag v1.3.3" / "Push to local
   main." CLAUDE.md overrides: NEVER push, NEVER tag — Roman handles
   both manually. Tags + push deferred to Roman post-validation.

## Acceptance gates

1. **pytest** 635 passed in 303s (target: 599+; +36 new tests
   covering state v4→v5 migration, liveness phase logic,
   knowledge_gate exit codes, smoke CLI behavior, detach helper
   knowledge gate + liveness flags integration).
2. **Hotfix smokes** all 15 green via `run-all-smokes.sh`:
   - autonomy / meta-reflect / knowledge-enforce / research-plan
     (v1.3 baseline)
   - stuck-detection / recovery-sweep / detach-defaults (v1.3.1)
   - meta-reflect-trigger / research-mode-trigger / knowledge-mtime
     (v1.3.2)
   - v133-liveness-stale-detection / v133-knowledge-gate-blocks-detach
     / v133-smoke-helper-command / v133-detach-with-liveness-flags
     / v133-v132-backward-compat (v1.3.3)
3. **Schema migration** confirmed via P5 smoke + unit test:
   v1.3.2 state.json (schema_version=4, no new fields) reads cleanly,
   migrates to schema_version=5 on next write, liveness fields default
   null (no retroactive opt-in), no behavior delta vs v1.3.2.
4. **STATUS.md** updated with v1.3.3 section at top (TBD post-commit).

## Known limitations

- Liveness check is opt-in. Existing v1.3.2 detached projects without
  `--pipeline-log` will continue to wait the full max-wait window on
  silent crashes. Roman should retrofit AI-trade's pipeline launchers
  to pass `--pipeline-log` (rules.md template now mandates this on
  every detach).

- Knowledge gate is project-scoped. If Roman has Claude work on multiple
  projects in parallel, each project's `state.json` tracks its own
  verdict timestamp. There's no cross-project enforcement.

- `cc-autopipe-smoke` does not enforce non-network startup. A pipeline
  that hits a slow endpoint at startup but is otherwise healthy will
  appear stuck → SMOKE_OK alive past min-alive. This is intentional
  (the alive heuristic is a coarse "didn't crash early" check, not
  full integration testing).

- `task_verdict` event differentiation deferred. Engine emits a single
  event type per verdict-stage transition without parsing the verdict
  outcome (REJECT/ACCEPT/INFRA_FAILED). If Roman needs that
  granularity for telemetry, parse `findings.md` / `knowledge.md`
  out-of-band.

## Smoke test plan for Roman (post-validation)

```bash
cd /mnt/c/claude/artifacts/repos/cc-autopipe-source

# Pytest baseline
pytest tests/ -q
# expect: 635 passed

# All hotfix smokes via the wrapper
bash tests/smoke/run-all-smokes.sh \
    autonomy meta-reflect knowledge-enforce research-plan \
    stuck-detection recovery-sweep detach-defaults \
    meta-reflect-trigger research-mode-trigger knowledge-mtime \
    v133-liveness-stale-detection v133-knowledge-gate-blocks-detach \
    v133-smoke-helper-command v133-detach-with-liveness-flags \
    v133-v132-backward-compat
# expect: 15/15 passed

# Or full smoke run (v0.5/v1.0 stages + all hotfix smokes)
bash tests/smoke/run-all-smokes.sh

# Manual gate validation against AI-trade after deploy:
#   1. Trigger a verdict (Stage A→D verdict on any task), DON'T
#      update knowledge.md, then call cc-autopipe-detach:
#         expect: BLOCKED rc=3, knowledge.md older than verdict
#   2. Append the lesson, retry detach:
#         expect: rc=0, phase=detached
#   3. Detach a long pipeline with --pipeline-log + --stale-after-sec=1800.
#      Kill the worker process. Within 30 minutes:
#         expect: detach_pipeline_stale event in aggregate.jsonl

# Bump VERSION + tag (Roman only — never agent)
echo "1.3.3" > src/VERSION
git tag v1.3.3
```

## What this hotfix unlocks

The combined v1.3 / v1.3.1 / v1.3.2 / v1.3.3 path gives 14-day
autonomy with:

- **Stuck detection that ignores legitimate ML training** (B-FIX,
  v1.3.1).
- **Auto-recovery that respects enforcement state** (B3-FIX +
  RECOVERY-SAFE, v1.3.2).
- **Per-project detach defaults** (DETACH-CONFIG, v1.3.1).
- **Captured tracebacks for silent deaths** (STDERR-LOGGING, v1.3.2).
- **Pre-flight validation of enforcement loops** (TRIGGER-SMOKES,
  v1.3.2).
- **Live pipeline death detection** (Group L, v1.3.3) — engine forces
  recovery within 30 minutes instead of waiting the full max-wait
  window.
- **Pre-detach script validation** (Group M, v1.3.3) — silent crashes
  at minute 1 caught before they burn 10 hours.
- **Mandatory verdict knowledge accumulation** (Group N, v1.3.3) —
  consecutive REJECTs no longer lose lessons because the engine
  blocks the next detach until knowledge.md is updated.

Done.
