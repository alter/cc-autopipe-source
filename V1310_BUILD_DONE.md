# V1310_BUILD_DONE — cc-autopipe v1.3.10 hotfix

**Built:** 2026-05-09
**Branch:** main (commits below; no remote push, no tag)
**Driver:** AI-trade Phase 2 v2.0 production aggregate.jsonl post-v1.3.9
deploy: ~30 `knowledge_sentinel_armed_via_promotion` events fired
WITHOUT the corresponding `promotion_validated_attempt` events that the
v1.3.5 PROMOTION-PARSER + v1.3.8 PROMOTION-HOOK-DIAGNOSTICS pipeline
should emit through the same code path. Investigation pinned the cause
to a scope bug in `src/orchestrator/cycle.py`: the v1.3.5 validation
loop iterates **only** over `pre_open_vec_long` — tasks that were open
at the START of the cycle. Production runs observed Claude regularly:

1. Closing a pre-existing task (e.g. `vec_long_production_script` —
   in pre_open ✓)
2. **Adding** a new meta-task to the backlog mid-cycle (e.g.
   `vec_long_phase8_summary`)
3. **Closing** the new task with a fresh `## Verdict` PROMOTION report
   in the same cycle

Step 3's task is NOT in the pre-cycle snapshot → the validation loop
skips it → no `promotion_validated_attempt` event → no
`validate_v2_sections` → no `on_promotion_success` → no ablation
children, no leaderboard append. The task is silently dropped from the
v1.3.5 enforcement path, even though `parse_verdict` correctly
classifies it (sentinel arming via `_maybe_arm_sentinel_via_promotion`
proves the verdict path itself was working).

In the AI-trade stress test this affected ~30 measurement and meta-tasks:
ablation children remained at 0 across the entire run, LEADERBOARD.md
was only created when Claude wrote it manually inside a task, never via
the engine hook.

## Group summary

### POST-CYCLE-DELTA-SCAN — validate vec_long_* tasks closed mid-cycle, not just pre-open ones

`src/orchestrator/cycle.py`:

  - **`_post_cycle_delta_scan(project_path, pre_open_vec_long)`** — new
    module-level helper that runs after the existing pre-cycle
    `pre_open_vec_long` loop. Parses the post-cycle backlog, selects
    `[x] + vec_long_* + task_type=="implement"` items NOT in
    `pre_ids = {pi.id for pi in pre_open_vec_long}`, and routes each
    through the same `parse_verdict → validate_v2_sections (with
    task_id) → on_promotion_success / quarantine_invalid / log`
    pipeline as the pre-cycle path.
  - **Idempotency:** the `pre_ids` exclusion is the load-bearing
    invariant — a task that appears in BOTH `pre_open_vec_long` AND
    the post-cycle `[x]` set goes only through the pre-cycle path,
    never through the delta scan. No double-emit of
    `promotion_validated_attempt`.
  - **Audit field:** every event emitted by the delta scan carries
    `origin="post_cycle_delta"` so operators grep'ping aggregate.jsonl
    can distinguish pre-cycle path matches from delta-scan matches
    without running a join.
  - **Telemetry-only failure mode:** any exception inside the helper
    is logged via `_log` and swallowed. A delta-scan crash never takes
    the cycle down (matches the pre-cycle path's exception contract).

The call site in `process_project` is a single line —
`_post_cycle_delta_scan(project_path, pre_open_vec_long)` — placed
immediately after the existing pre-cycle validation try/except. Helper
extraction (vs. inline block) was made for direct unit-testability:
the integration tests call the helper directly with a synthetic
`pre_open` list rather than spinning up the full `process_project`
mock-claude pipeline.

## Test counts

| Surface | Pre-v1.3.10 | v1.3.10 | Delta |
|---|---|---|---|
| pytest tests/ | 833 | **840** | +7 |
| Hotfix smokes | 25 | **26** | +1 |

Pytest breakdown of the +7:

- `tests/integration/test_post_cycle_delta_scan.py` — 7 cases:
  - `test_pre_existing_excluded_only_new_via_delta_path` — pre_open
    contains task A; post-cycle backlog has both A and B closed; only
    B emits delta-path events.
  - `test_empty_precycle_with_mid_cycle_added_promoted` — empty
    pre_open + mid-cycle PROMOTED non-strategy task; ablation children
    spawn, `promotion_validated origin=post_cycle_delta`.
  - `test_strategy_prefix_missing_sections_quarantines` — strategy
    prefix (`vec_long_synth_meta_v1`) PROMOTED but missing v2
    sections; `quarantine_invalid` runs, NO `promotion_validated`.
  - `test_non_strategy_prefix_missing_sections_relaxes_to_ok` —
    non-strategy prefix (`vec_long_q_compressed_x`) PROMOTED missing
    v2 sections; relaxed gate returns ok=True; `on_promotion_success`
    fires, ablation children spawn.
  - `test_rejected_verdict_via_delta_path` — REJECTED verdict in
    delta-path emits only `promotion_rejected origin=post_cycle_delta`.
  - `test_missing_promotion_md_emits_unrecognized_and_missing` — no
    PROMOTION.md → `promotion_verdict_unrecognized` AND legacy
    `promotion_verdict_missing`, both `origin=post_cycle_delta`.
  - `test_idempotency_pre_and_post_no_double_emit` — task in both
    pre_open and post-x; delta scan emits zero
    `origin=post_cycle_delta` events for that id.

Smoke breakdown of the +1:

- `tests/smoke/run-mid-cycle-add-close-smoke.sh` — 2 tests:
  - Test 1: pre-existing closed via PROMOTION + new task added &
    closed mid-cycle. Asserts both have `promotion_validated_attempt`
    (pre-existing without `origin`, mid-cycle-added with
    `origin=post_cycle_delta`); both spawn 5 ablation children (10
    child lines total in backlog); LEADERBOARD.md contains both ids.
  - Test 2 variant: empty pre_open + unparseable PROMOTION verdict
    emits `promotion_verdict_unrecognized` + `promotion_verdict_missing`,
    both `origin=post_cycle_delta`. No ablation children.

## New events

None as event names. The audit field `origin="post_cycle_delta"` is
added to existing events when emitted by the new helper:

- `promotion_validated_attempt`
- `promotion_v2_sections_check`
- `promotion_validated`
- `promotion_rejected`
- `promotion_conditional`
- `promotion_verdict_unrecognized`
- `promotion_verdict_missing`

All seven events keep their previous payload schema; the `origin` key
is purely additive. Pre-cycle path events do NOT carry an `origin`
field — its absence is the marker for "pre-cycle path".

## Schema

**Unchanged at v6.** No new persisted fields per PROMPT_v1.3.10
§"Don't" (rule 3: no new state.json schema fields). Helper is
stateless — relies on backlog.md + aggregate.jsonl + PROMOTION.md
filesystem state only.

## Atomic commits

Three atomic commits + 1 STATUS/V1310 docs commit (next):

```
0c20c8f cycle: post-cycle delta scan for vec_long_* tasks closed mid-cycle (v1.3.10)
0375453 tests: cover same-cycle-add+close validation via post-cycle delta path
c14f9d0 smoke: add run-mid-cycle-add-close-smoke.sh covering post-cycle delta-scan flow
TBD     docs: v1.3.10 — STATUS.md + V1310_BUILD_DONE.md + VERSION bump
```

## Manual smoke for Roman (after deploy)

```bash
pytest tests/ -q                                   # 840 passed
bash tests/smoke/run-all-smokes.sh                 # 26 hotfix smokes green
bash tests/smoke/run-mid-cycle-add-close-smoke.sh  # standalone

# Diff sentinel_armed vs validated on the live AI-trade aggregate.jsonl —
# v1.3.10 should drive new-cycle mismatches to ~0.
python3 - <<'EOF'
import json
agg = open('/home/$USER/.cc-autopipe/log/aggregate.jsonl').readlines()
sentinel_armed = set()
validated = set()
for line in agg:
    try:
        evt = json.loads(line)
    except json.JSONDecodeError:
        continue
    if evt.get('event') == 'knowledge_sentinel_armed_via_promotion':
        sentinel_armed.add(evt.get('task_id'))
    elif evt.get('event') == 'promotion_validated_attempt':
        validated.add(evt.get('task_id'))
gap = sentinel_armed - validated - {None}
print(f'Total sentinel-armed: {len(sentinel_armed)}')
print(f'Total validated: {len(validated)}')
print(f'Mismatches (armed but not validated): {len(gap)}')
print(f'Sample: {list(gap)[:10]}')
EOF
# Pre-v1.3.10 historical mismatches stay (no retroactive validation).
# Future cycles should show parity for new task IDs.

echo "1.3.10" > src/VERSION
git tag v1.3.10
```

## Stopping conditions met / not met

- v1.3.9 baseline pytest broken pre-build → **NOT MET** (833 green at
  start, 840 green after — clean superset, no regressions).
- Build estimate exceeded 2 hours → **NOT MET** (~25 minutes wall time
  net of two pytest baseline runs that dominated).
- Real AI-trade backlog reveals tasks closed mid-cycle that NEITHER
  path validates → **NOT MET** — pre-cycle owns pre_open ids; delta
  scan owns the rest. Together they cover every `vec_long_*` task that
  transitions to `[x]` within a cycle.

Done.
