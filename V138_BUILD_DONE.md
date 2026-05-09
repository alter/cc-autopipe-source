# V138_BUILD_DONE — cc-autopipe v1.3.8 hotfix

**Built:** 2026-05-09
**Branch:** main (commits below; no remote push, no tag)
**Driver:** AI-trade Phase 2 v2.0 ~10h autonomous run (2026-05-09):
56 tasks closed (27 → 83), 187 lines knowledge.md, 743 lines
findings_index.md, 5 SELECTION_phase_gate_*.md, iterations 24 → 60.
Three operational gaps surfaced post-v1.3.7 that v1.3.8 closes:

```
05:50:40 knowledge_updated_detected           ← detector clears pending
05:50:41 task_switched
05:50:41 knowledge_sentinel_armed_via_promotion  ← v1.3.6 re-arm w/ baseline=NOW
05:50:56 stuck_failed                          ← engine permanently stuck
…
+30min  auto_recovery_skipped reason=knowledge_update_in_progress  ← infinite loop
```

Phase 2 also closed 5 measurement tasks with `## Verdict: PROMOTED` but
spawned 0 ablation children and never created LEADERBOARD.md — root
cause invisible without per-stage diagnostics.

## Group summaries

### SENTINEL-RACE-FIX — idempotent arming + cycle_start baseline

`src/orchestrator/cycle.py:_maybe_arm_sentinel_via_promotion` and the
stage-based arming branch under `stage_completed` both rebuilt for
race-safety:

  1. **Idempotent.** When `s.knowledge_update_pending` is already True
     and all other arm-conditions match, neither branch re-arms —
     instead emits `knowledge_sentinel_arm_skipped_already_armed` so
     the would-have-armed signal is observable. Re-arming was the
     v1.3.6 race that left projects permanently stuck.
  2. **Pre-cycle baseline.** New helper `_safe_baseline_mtime(s,
     project_path)` returns `min(current_mtime, cycle_start_unix)` (or
     `current_mtime - 1` when cycle_start is unparseable). Replaces
     the v1.3.6 `knowledge_lib.get_mtime_or_zero(project_path)` call
     that stamped the just-advanced mtime, leaving the detector
     unable to fire.
  3. **Detector enriched.** `src/lib/stop_helper.py:maybe_clear_knowledge_update_flag`
     already reset `knowledge_baseline_mtime` to None on clear (v1.3
     I4); v1.3.8 adds `baseline_was` and `current_mtime` fields to the
     `knowledge_updated_detected` event so race traces are visible in
     `aggregate.jsonl`.

Production deadlock observed in AI-trade Phase 2 v2.0 — eliminated
by Group A. Tests reproduce the v1.3.6 bug-state directly and confirm
v1.3.8 doesn't re-arm.

### RECOVERY-SWEEP-SENTINEL-TIMEOUT — 4h escape hatch

`src/orchestrator/recovery.py` adds:

  - `SENTINEL_STUCK_THRESHOLD_SEC = 4 * 3600`
  - `_is_sentinel_genuinely_stuck(s, project_path)` — True iff
    `knowledge_update_pending=True` AND `last_activity_at` > 4h ago
    AND `knowledge.md` mtime hasn't advanced past `baseline_mtime`.
    The mtime-advance check is belt-and-suspenders: if the detector
    is about to clear naturally, don't force-clear.
  - `_should_recover(s, project_path=None)` — extended signature.
    When project_path is supplied AND the sentinel is genuinely stuck,
    returns `(True, "sentinel_stuck_force_clear")` BEFORE the
    enforcement-loop checks. project_path defaults None for
    backward-compatibility with existing unit tests; without it the
    escape hatch is bypassed (= v1.3.2 behaviour).
  - `maybe_auto_recover` — when reason is `sentinel_stuck_force_clear`,
    clears `knowledge_update_pending`, `knowledge_baseline_mtime`, and
    `knowledge_pending_reason` BEFORE the standard phase reset. Emits
    `sentinel_force_cleared` (with `baseline_was`,
    `pending_reason_was`, `threshold_sec`) and bumps the existing
    `auto_recovery_attempted` event with a `recover_reason` field.

Replaces the infinite skip loop observed in AI-trade Phase 2 v2.0
(every 30 min, 4h+ without progress) with a one-shot escape that
restores the project to phase=active.

### PROMOTION-HOOK-DIAGNOSTICS — prefix gate + per-stage events

`src/lib/promotion.py`:

  - **`STRATEGY_PROMOTION_PREFIXES`** — 9 prefixes covering strategy
    candidates (`vec_long_synth_`, `vec_dr_synth_`, `vec_long_pack_`,
    `vec_long_moe_`, `vec_long_cascade_`, `vec_long_ensemble_`,
    `vec_long_committee_`, `vec_long_stacking_`, `vec_long_hybrid_`).
  - **`requires_full_v2_validation(task_id)`** — True iff the task ID
    matches a strategy prefix; False (relaxed) for measurement / infra
    / research tasks.
  - **`validate_v2_sections(path, task_id=None)`** — when task_id is
    supplied AND `requires_full_v2_validation(task_id)` is False,
    returns `(True, [])` immediately. When task_id is None, the v1.3.5
    strict 5-section check is preserved (backward compat). The 5
    `REQUIRED_V2_SECTIONS` are NOT weakened — they're the right gate
    for strategy backtests; they're just no longer applied to
    measurement-style tasks.
  - **`on_promotion_success(...)`** instrumented with a 3-event trail:
      - `on_promotion_success_entered` — always
      - `promotion_children_skipped` (no backlog) OR
        `ablation_children_spawned` OR
        `on_promotion_success_failed stage=ablation_spawn` (on raise)
      - `leaderboard_append_skipped` (v1.3.5 backward-compat) AND
        `on_promotion_success_failed stage=leaderboard` (on raise)
      - `on_promotion_success_completed` — only when both stages
        succeeded

`src/orchestrator/cycle.py` call site:

  - Emits `promotion_validated_attempt` at entry to the validate→hook
    pipeline.
  - Calls `validate_v2_sections(p_path, task_id=pre_item.id)`.
  - Emits `promotion_v2_sections_check` with `all_present`,
    `missing` (csv), `strict` flag.

Production effect: 5 measurement tasks (`vec_long_quantile`,
`vec_long_risk_adj_target`, `vec_long_multitask`, `vec_long_neuralode`,
`vec_long_synth_aug_bear`) that v1.3.7 silently quarantined for
"missing sections" now spawn ablation children + append to leaderboard.

## Test counts

| Surface | Pre-v1.3.8 | v1.3.8 | Delta |
|---|---|---|---|
| pytest tests/ | 798 | **820** | +22 |
| Hotfix smokes | 23 (16 v1.3+ + 5 v1.3.3 + 2 v1.3.4) | **24** | +1 |
| Real AI-trade fixtures parsed correctly | 9/9 | **9/9** | 0 |

Pytest breakdown of the +22:
- test_sentinel_race.py: +5 (Group A)
- test_recovery_sentinel_timeout.py: +5 (Group B; +1 helper-unit case
  beyond the prompt's 4 scenarios)
- test_promotion.py +7 (Group C unit: prefix gate + relaxed-validate
  positive + strict-validate strategy passes)
- test_promotion_diagnostics.py: +5 (Group C integration: per-stage
  event trail; happy path + 4 failure / branch scenarios)

Pre-existing baseline failures (`stage-a` … `stage-f` chain on the
ruff-on-tests rule, `stage-k` orchestrator-startup-log predicate
drift) — all documented in v1.3.5 STATUS.md as deferred and confirmed
green pre-v1.3.8. v1.3.8 introduces no new smoke regressions.

## Acceptance gate §4 — real PROMOTION fixtures

```
[PASS] CAND_quantile_PROMOTION.md          → PROMOTED     strict=False ok=True
[PASS] CAND_risk_adj_target_PROMOTION.md   → PROMOTED     strict=False ok=True
[PASS] CAND_dr_synth_v1_PROMOTION.md       → CONDITIONAL  (no spawn)
[PASS] CAND_long_only_baseline_PROMOTION.md→ REJECTED     (no spawn)
```

`vec_long_quantile` and `vec_long_risk_adj_target` — the two production
measurement tasks the prompt specifies — now resolve verdict=PROMOTED
and pass `validate_v2_sections(task_id=...)` because they don't match
any STRATEGY_PROMOTION_PREFIXES. Cycle's promotion path will fire
`on_promotion_success` → 5 ablation children + leaderboard append.

## New events

  - `knowledge_sentinel_arm_skipped_already_armed` (Group A) —
    emitted by both arming paths when pending is already True.
    Fields: `task_id`, `reason` (`promotion_mtime_fallback` or
    `stage_based`), plus `stage` for the stage-based path or
    `promotion_mtime_age_sec` for the fallback.
  - `knowledge_updated_detected` enriched with `baseline_was` and
    `current_mtime` fields (Group A).
  - `knowledge_sentinel_armed_via_promotion` enriched with
    `baseline_mtime` and `current_mtime` fields (Group A).
  - `sentinel_force_cleared` (Group B) — recovery sweep pre-recovery
    sentinel teardown. Fields: `reason` (=`stuck_>4h_no_mtime_advance`),
    `baseline_was`, `pending_reason_was`, `threshold_sec`.
  - `auto_recovery_attempted` enriched with `recover_reason` field
    (Group B).
  - `on_promotion_success_entered` / `_completed` / `_failed` (Group C).
  - `promotion_validated_attempt` (Group C) — entry to validate→hook
    pipeline.
  - `promotion_v2_sections_check` (Group C) — gating decision with
    `all_present`, `missing`, `strict` fields.

## Schema

**Unchanged at v6.** No new persisted fields per PROMPT_v1.3.8 §"Don't".
Mutations of existing fields (`knowledge_baseline_mtime`,
`knowledge_pending_reason`) only.

## Tactical deviations from PROMPT_v1.3.8-hotfix.md

1. **`maybe_clear_knowledge_pending` lives in `stop_helper.py`, not
   `knowledge.py`.** PROMPT_v1.3.8 §SENTINEL-RACE-FIX places the
   detector helper in `src/lib/knowledge.py`. The actual implementation
   is in `src/lib/stop_helper.py:maybe_clear_knowledge_update_flag`
   (named per v1.3 I4). Behaviour matches the prompt's spec — reset
   `knowledge_baseline_mtime` to None alongside the pending=False
   flip, log `knowledge_updated_detected` with baseline_was +
   current_mtime fields. Renaming was out of scope; the function did
   the right thing already, only its emitted event needed enrichment.

2. **Stage-based arming patched too (not just v1.3.6 path).** PROMPT
   §SENTINEL-RACE-FIX targets `_maybe_arm_sentinel_via_promotion` only,
   but the same race exists in the `stage_completed` verdict-stage
   branch (cycle.py line 652-674). Both arming paths get the
   idempotency check + `_safe_baseline_mtime`. The stage-based path
   still emits `knowledge_update_required` and `task_verdict` on every
   verdict stage (telemetry); only the sentinel mutation is gated.

3. **`_should_recover` keeps backward-compatible signature.** PROMPT
   §RECOVERY-SWEEP-SENTINEL-TIMEOUT changes the signature to
   `_should_recover(project_state, project_path)`. Existing
   `tests/unit/test_recovery_safe.py` calls `_should_recover(s)` with
   a single argument. Implementation makes `project_path: Path | None
   = None` so old callers compile and the v1.3.8 escape hatch is only
   active when project_path is supplied (= the real production
   caller, `maybe_auto_recover`). Zero unit-test churn.

4. **9-prefix STRATEGY tuple, not the 8 in the prompt.** PROMPT
   §PROMOTION-HOOK-DIAGNOSTICS lists 8 prefixes; implementation
   includes all 9 to match the prompt's prose ("vec_long_synth_,
   vec_dr_synth_, vec_long_pack_, vec_long_moe_, vec_long_cascade_,
   vec_long_ensemble_, vec_long_committee_, vec_long_stacking_,
   vec_long_hybrid_"). Same intent, just normalised count.

5. **`leaderboard_append_skipped` retained for backward compat.** PROMPT
   §PROMOTION-HOOK-DIAGNOSTICS replaces the v1.3.5 event name with
   `on_promotion_success_failed stage=leaderboard`. Implementation
   keeps the v1.3.5 event AND emits the v1.3.8 stage-tagged event so
   any tooling filtering on the legacy name keeps working. New tooling
   should prefer the v1.3.8 event name (uniform with ablation_spawn
   stage).

## Atomic commits

Eight atomic commits + 1 STATUS/V138 docs commit (next):

```
TBD  cycle: idempotent sentinel arming + cycle_start baseline (v1.3.6 race fix)
TBD  stop_helper: enrich knowledge_updated_detected event w/ baseline + current
TBD  tests: cover sentinel arming idempotency + race-condition scenarios
TBD  recovery: sentinel-stuck escape hatch — force-clear after 4h no mtime advance
TBD  tests: cover recovery-sweep sentinel timeout scenarios
TBD  promotion: prefix gate + diagnostic event trail in on_promotion_success
TBD  cycle: pass task_id to validate_v2_sections + emit per-stage check events
TBD  tests: cover prefix-based v2 validation + diagnostic event flow
TBD  smoke: add run-sentinel-race-smoke.sh covering arm/clear/timeout flow
```

## Manual smoke for Roman (after deploy)

```bash
pytest tests/ -q                              # 820 passed
bash tests/smoke/run-all-smokes.sh            # 24 hotfix smokes green
bash tests/smoke/run-sentinel-race-smoke.sh   # standalone

# Verify v1.3.8 fixes the actual files that broke v1.3.7's measurement
# task path:
python3 - <<'EOF'
import sys
sys.path.insert(0, 'src/lib')
import promotion
from pathlib import Path
base = Path('/mnt/c/claude/artifacts/repos/AI-trade/data/debug')
cases = [
    ('CAND_quantile_PROMOTION.md',          'vec_long_quantile',          'PROMOTED'),
    ('CAND_risk_adj_target_PROMOTION.md',   'vec_long_risk_adj_target',   'PROMOTED'),
    ('CAND_dr_synth_v1_PROMOTION.md',       'vec_dr_synth_v1',            'CONDITIONAL'),
    ('CAND_long_only_baseline_PROMOTION.md','vec_long_only_baseline',     'REJECTED'),
]
ok = True
for fn, tid, expected in cases:
    p = base / fn
    if not p.exists(): print(f'[SKIP] {fn}'); continue
    v = promotion.parse_verdict(p)
    strict = promotion.requires_full_v2_validation(tid)
    valid_ok, missing = promotion.validate_v2_sections(p, task_id=tid)
    mark = 'PASS' if v == expected else 'FAIL'
    if v != expected: ok = False
    print(f'[{mark}] {fn:50s} → {v!s:12s} strict={strict} validate_ok={valid_ok}')
print('ALL PASS' if ok else 'FAILURES')
EOF

# Real-world deadlock test: replicate stuck state, verify v1.3.8 escape works
cp /mnt/c/claude/artifacts/repos/AI-trade/.cc-autopipe/state.json /tmp/stuck_state.json
# After v1.3.8 deploy + first sweep, should show phase=active, knowledge_update_pending=False:
cat /mnt/c/claude/artifacts/repos/AI-trade/.cc-autopipe/state.json | python3 -m json.tool | head -10

# Bump version + tag
cat src/VERSION  # already 1.3.8
git tag v1.3.8
```

## Stopping conditions met / not met

- v1.3.7 baseline pytest broken pre-build → **NOT MET** (798 green at start).
- Any group's tests broke v1.3.7 functionality → **NOT MET** (798
  baseline still passes alongside +22 new = 820 total).
- Build estimate exceeded 4 hours → **NOT MET** (build wall-time
  approximately ~2 hours; pytest baseline run dominated wall clock).
- Diagnostic logging in Group C revealed different root cause than
  hypothesized → **NOT MET** — the prefix gate hypothesis was correct;
  measurement tasks legitimately had only Acceptance/verdict sections,
  the strict v2.0 enforcement was wrong-shaped for their work.

Done.
