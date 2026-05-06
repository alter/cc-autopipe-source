# v1.3.5 HOTFIX — Build Done

**Status:** complete, acceptance gates green for all v1.3.5 surfaces;
awaiting Roman validation + tag.
**Date:** 2026-05-06
**Branch:** main
**Tag pending:** v1.3.5 (HUMAN-ONLY per CLAUDE.md)

## Why this release exists

AI-trade Phase 2 (PRD v2.0, 16-week autonomous run) targets long-only
mastery, regime parity, and mixed-model integration. Three engine
gaps blocked confident autonomy:

1. `[research]` tasks (`phase_gate_*`, `vec_long_meta_negative_*`,
   `vec_long_meta_research_*`) produce analysis artifacts (SELECTION_*,
   NEGATIVE_MINING_*, HYPO_*, RESEARCH_DIGEST_*), not code. v1.3.4
   expected `verify.sh passed=true` AND a git commit AND TYPICAL_BUGS
   check. Research tasks fail all three legitimately; without engine
   support every Phase Gate (5 over 16 weeks) needed manual rescue.
2. PROMOTION reports lacked v2.0 validation. Every PROMOTED `vec_long_*`
   task should have regime-stratified PnL + statistical significance +
   walk-forward stability sections. With ~150 promotions expected over
   4 months, drift was inevitable; engine never read PROMOTION.md.
3. No persistent ranking across promotions. PRD calls for LEADERBOARD.md
   with ELO + top-20 + quarterly re-tournament. v1.3.4 had nothing —
   each promotion was isolated.

## Three groups landed (10 atomic commits)

| # | Commit (head) | Group | Summary |
|---|---|---|---|
| 1 | lib: add research_completion module | R/1 | new module + BacklogItem.task_type |
| 2 | prompt: distinct prompt block for [research] | R/2 | prompt branches on top task_type |
| 3 | cycle: branch on task_type before verify.sh | R/3 | post-cycle synthesises last_passed for [research] |
| 4 | knowledge: register Phase 2 verdict-pattern stages | R/4 | 6 new substring patterns |
| 5 | lib: add promotion module | P/1 | parse_verdict / validate_v2_sections / parse_metrics / on_promotion_success / quarantine_invalid |
| 6 | cycle: validate vec_long_* PROMOTION on [x] mark | P/2 | engine-side enforcement |
| 7 | state: touch_knowledge_baseline_mtime helper | L/1 | knowledge sentinel arm helper |
| 8 | lib: add leaderboard module | L/2 | composite + ELO + top-20 + archive |
| 9 | smoke: v1.3.5 R/P/L smokes + run-all-smokes wiring | L/3 | 3 new smokes (each <2s) |
| 10 | (this commit) docs: V135_BUILD_DONE + STATUS + VERSION | docs | release prep |

### Group RESEARCH-COMPLETION (R)

`[research]` tasks now complete on (artifact path + verdict-stage),
not verify.sh.

- `src/lib/research_completion.py`: `is_research_task`,
  `expected_artifact_glob`, `completion_satisfied`,
  `find_top_research_task`.
- `src/lib/backlog.py`: new `BacklogItem.task_type` property —
  returns the first non-priority bracketed tag (lowercased), defaults
  to `"implement"`. Also exposes `parse_all_tasks`.
- `src/lib/knowledge.py`: `_VERDICT_PATTERNS` extended with `phase_gate`,
  `selection_complete`, `research_digest`, `negative_mining`,
  `hypo_filed`, `track_winner`. Existing `promoted` / `rejected`
  cover `synth_promoted` / `synth_rejected`.
- `src/orchestrator/prompt.py`: when topmost open backlog item is
  `[research]`, prompt builder injects a RESEARCH-TASK instruction
  block listing the required artifact glob and verdict-stage names —
  no verify.sh, no commit. Otherwise unchanged.
- `src/orchestrator/cycle.py`: snapshots top `[research]` task pre-
  cycle. Post-cycle, when `rc==0` and `completion_satisfied` returns
  True, synthesises a passed-verify state (`last_passed=True`,
  `last_score=1.0`, `consecutive_failures=0`) and emits
  `research_task_completed`. Otherwise emits `research_task_pending`
  without bumping any failure counter.

### Group PROMOTION-PARSER (P)

Engine validates v2.0 sections + spawns 5 ablation children atomically
on validated PROMOTED.

- `src/lib/promotion.py`:
  - `promotion_path(project, task_id)` → `data/debug/CAND_<id>_PROMOTION.md`
  - `parse_verdict(path)` → `'PROMOTED'` | `'REJECTED'` | `None`
  - `validate_v2_sections(path)` → `(bool, [missing])`. Required:
    Long-only verification, Regime-stratified PnL, Statistical
    significance, Walk-forward stability, No-lookahead audit
  - `parse_metrics(path)` → dict of sum_fixed / regime_parity /
    max_dd / dm_p_value / dsr; missing fields default `None`,
    not zero
  - `on_promotion_success(project, item, metrics)`: atomic
    backlog mutation (tmp+os.replace) appending 5 ablation children
    BEFORE any `## Done` section if present; calls
    `leaderboard.append_entry` (best-effort)
  - `quarantine_invalid(project, item, missing)`: writes
    `data/debug/UNVALIDATED_PROMOTION_<id>.md`; reverts backlog `[x]`
    → `[~]` for the matching task; logs `promotion_invalid`
- Ablation children (5 fixed templates): `*_ab_drop_top`, `*_ab_loss`,
  `*_ab_seq`, `*_ab_seed`, `*_ab_eth`. Priority = parent + 1, capped
  at P3.
- `src/orchestrator/cycle.py`: pre-cycle snapshots open `vec_long_*`
  `[implement]` tasks. Post-cycle, for any that transitioned to `[x]`,
  parses CAND_*_PROMOTION.md and dispatches:
  - PROMOTED + valid → `on_promotion_success` + `promotion_validated`
  - PROMOTED + invalid → `quarantine_invalid` + `promotion_invalid`
  - REJECTED → `promotion_rejected`
  - missing verdict line → `promotion_verdict_missing`

### Group LEADERBOARD-WRITER (L)

Persistent ranking with ELO across promotions.

- `src/lib/leaderboard.py`:
  - Composite scoring: `0.5*sum_fixed/1000 + 0.3*(1-regime_parity) +
    0.2*max_dd/-100`. Missing metrics → 0 contribution (incomplete-
    report penalty).
  - ELO: K=32, initial=1500, matchups vs current top-3 on each
    append.
  - Top-20 retained inline in `data/debug/LEADERBOARD.md`; entries
    beyond rank 20 archived to
    `data/debug/ARCHIVE/LEADERBOARD_<YYYY-MM-DD>.md`.
  - Sidecar `data/debug/.leaderboard_elo.json` holds raw ratings +
    history (machine-readable).
  - `LEADERBOARD.md` round-trippable via `_read_existing_entries` →
    `append_entry`.
  - Idempotent on task_id: re-promotion replaces the prior entry.
- `src/lib/state.py`: new `touch_knowledge_baseline_mtime(project)`
  helper. Sets `knowledge_baseline_mtime` to `knowledge.md` mtime
  and `knowledge_update_pending=True`. Called by
  `leaderboard.append_entry` so every validated promotion arms the
  v1.3 mtime sentinel — the SessionStart hook then enforces a
  lessons append next cycle (defense-in-depth).
- Schema: **unchanged at v6**. Per PROMPT_v1.3.5 §"Don't",
  no schema bump for this hotfix.

## Smokes — three new, all <2s, all green

- `tests/smoke/run-research-task-completion-smoke.sh` — RESEARCH-TASK
  prompt block routing + `completion_satisfied` decision matrix +
  Phase 2 verdict-stage detection
- `tests/smoke/run-promotion-validation-smoke.sh` — PROMOTED+full,
  PROMOTED+missing-section, REJECTED scenarios; atomic-write check
- `tests/smoke/run-leaderboard-elo-smoke.sh` — 5 promotions ranked
  descending by composite; ELO drift; 21st-promotion archive;
  knowledge sentinel armed; round-trip parse stable

Wired into `tests/smoke/run-all-smokes.sh::HOTFIX_SMOKES` so a full
smoke run covers them.

## New events in aggregate.jsonl

- `research_task_completed` (task_id, artifact)
- `research_task_pending` (task_id, reason)
- `promotion_validated` (task_id, sum_fixed?, regime_parity?, …)
- `promotion_invalid` (task_id, missing_sections)
- `promotion_rejected` (task_id)
- `promotion_verdict_missing` (task_id)
- `ablation_children_spawned` (parent, count)
- `leaderboard_updated` (task_id, rank)
- `leaderboard_append_skipped` (task_id, reason) — best-effort hook fail
- `promotion_children_skipped` (task_id, reason=backlog_missing)

## New CLI surface

None. Three new env-var-free smoke scripts (real CLI not required —
heredoc-driven module exercises).

## Acceptance gates — green for v1.3.5 surfaces

- pytest: 685 (v1.3.4 baseline) → **743 passed** (+58 new tests)
  - `tests/unit/test_research_completion.py`: 15 cases
  - `tests/unit/test_backlog.py`: +4 task_type cases (was 14, now 18)
  - `tests/unit/test_promotion.py`: 8 cases
  - `tests/unit/test_leaderboard.py`: 15 cases
  - `tests/integration/test_research_prompt.py`: 3 cases
  - `tests/integration/test_promotion_flow.py`: 5 cases
- run-all-smokes — three new v1.3.5 smokes pass in ~3s combined; all
  v1.3 / v1.3.1 / v1.3.2 / v1.3.3 / v1.3.4 hotfix smokes still green
  (13/13 of the hotfix-style smokes including the three new ones).

### Pre-existing baseline observations (NOT introduced by v1.3.5)

`run-all-smokes.sh` baseline before any v1.3.5 change observed seven
red stages:

- **stages a-f (6 stages)** — all chained on the same `ruff check
  src tests tools` failure. Five F401 unused-import warnings remain
  in test files (`test_detach_helper.py`, `test_main_logging.py`,
  `test_daily_report.py`, `test_health.py`). v1.3.4's ruff cleanup
  commit (`8a7c57c`) appears to have covered `src/` only, not
  `tests/`. These predate v1.3.5 and are not exercised by any of the
  hotfix-style smokes that gate Phase 2 work. Trivially fixable in
  one hygiene commit; deferred per PROMPT §"Don't" ("touch unrelated
  v1.3 / v1.3.x features").
- **stage-k (1 stage)** — startup log no longer mentions
  `quota_monitor_interval` per the test's expectation. Predates
  v1.3.5. The quota_monitor unit tests themselves (15/15) still
  pass; only the smoke's process-stderr-grep predicate is stale.

These were red on `main` at the v1.3.4 release commit (`f860073
v1.3.4: bump src/VERSION`). v1.3.5 introduces no new failures.

## Schema

**Unchanged at v6.** No new persisted fields beyond
`knowledge_baseline_mtime` / `knowledge_update_pending` adjustments
on existing v1.3 fields, per PROMPT_v1.3.5 §"Don't".

## Manual smoke (Roman, after agent done)

```bash
pytest tests/ -q
bash tests/smoke/run-all-smokes.sh research-task-completion promotion-validation leaderboard-elo

# End-to-end on real AI-trade clone (use a copy, not the live project)
cp -R /mnt/c/claude/artifacts/repos/AI-trade /tmp/AI-trade-smoke
cc-autopipe stop 2>/dev/null
cc-autopipe start  # daemonized

# Manually mark first vec_long_only_baseline as [x] with synthetic
# PROMOTION.md containing all 5 v2.0 sections and a `**Verdict:
# PROMOTED**` line. Verify after next cycle:
#   - 5 ablation children appear in backlog before `## Done`
#   - data/debug/LEADERBOARD.md created with rank 1
#   - data/debug/.leaderboard_elo.json holds rating 1500
#   - knowledge_update_pending=True in state.json

# Bump version
echo "1.3.5" > src/VERSION

# Tag
git tag v1.3.5
```

## Next

Roman validates + tags `v1.3.5`. AI-trade Phase 2 PRD v2.0 then has
the engine-side guardrails listed in PRD §"Engine integration
requirements" — defense in depth alongside Claude-in-task-session
honesty.
