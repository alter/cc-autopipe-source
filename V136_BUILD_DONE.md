# v1.3.6 HOTFIX — Build Done

**Status:** complete, acceptance gates green for all v1.3.6 surfaces;
awaiting Roman validation + tag.
**Date:** 2026-05-07
**Branch:** main
**Tag pending:** v1.3.6 (HUMAN-ONLY per CLAUDE.md)

## Why this release exists

v1.3.5 baseline established. AI-trade Phase 2 v2.1 ran for 10 hours
autonomously, closed all 23 tasks. The run surfaced 3 operational gaps
that block confident multi-month autonomy on the upcoming v2.0 stress
test (~220-task / 3-4 month roadmap):

1. **Verdict regex too strict — 100% promotions silently un-validated.**
   v1.3.5's `VERDICT_RE` matched only the `**Verdict: PROMOTED**` /
   `**Verdict: REJECTED**` exact form. Real Phase 1/2 PROMOTION.md files
   use heading-style verdicts (`## Verdict: LONG_LOSES_MONEY`,
   `## Stage D: Verdict\n### REJECT — vec_rl`,
   `## Verdict\n### STABLE — ...`, `## Verdict\n### CONDITIONAL — ...`).
   v1.3.5 logged `promotion_verdict_missing` for every closed Phase 2
   task — zero ablation children spawned, zero leaderboard updates.
2. **`phase=done` sticky — no auto-recovery when backlog reopens.**
   v1.3.5 has `recovery.maybe_auto_recover` for failed projects but no
   equivalent for done. When operator adds new tasks to a done project's
   backlog, engine remained stuck until manual state.json edit.
3. **Sentinel verdict-pattern stages too narrow — knowledge.md not
   arming.** v1.3.5 `_VERDICT_PATTERNS` set was small. Real Claude
   task-sessions use stages like "complete", "implementation",
   "reporting", "analysis" — none matched. The mtime sentinel never
   armed and v2.1 finished with `knowledge_baseline_mtime: None`
   despite 23 verdicts.

## Three groups landed (8 atomic commits, +1 baseline hygiene + 1 fix)

| # | Commit (head) | Group | Summary |
|---|---|---|---|
| 0 | `smoke: align promotion-validation paths …` | hygiene | unblock baseline (db6683c prefix-strip aftermath) |
| 1 | `promotion: lenient verdict parser …` | VERDICT-LENIENT/1 | two-pass discovery, CONDITIONAL canonical state |
| 2 | `cycle: handle CONDITIONAL verdict state …` | VERDICT-LENIENT/2 | dispatch + backwards-compat events |
| 3 | `tests: cover heading-style verdict …` | VERDICT-LENIENT/3 | 8 unit + 3 integration cases |
| 4 | `recovery: add sweep_done_projects …` | PHASE-DONE-RECOVERY | sweep + main loop wiring + 7 integration tests |
| 5 | `knowledge: broaden verdict patterns …` | SENTINEL-PATTERNS/1 | 12 new vocabulary entries + 3 unit tests |
| 6 | `cycle: arm knowledge sentinel via fresh PROMOTION.md …` | SENTINEL-PATTERNS/2 | `_maybe_arm_sentinel_via_promotion` helper + 7 integration tests |
| 7 | `smoke: add run-lenient-verdict-smoke.sh …` | smokes | 2 new hotfix smokes |
| 8 | `recovery: gate sweep_done resume on actual open tasks …` | bugfix | `_count_open_backlog` instead of `detect_prd_complete` for missing-backlog handling |

### Group VERDICT-LENIENT

`src/lib/promotion.py`:
- `VERDICT_HEADING_RE`: matches `^(?:\*\*)?#{0,4}\s*(?:Stage X: )?Verdict\b…`,
  case-insensitive, multi-line. Captures heading level via `(?P<hashes>…)`
  named group so the body-scan boundary respects it.
- `VERDICT_KEYWORD_RE`: matches PROMOTED, REJECTED, ACCEPT(ED),
  REJECT, PASS(ED), FAIL(ED), STABLE, CONDITIONAL, PARTIAL,
  LONG_LOSES_MONEY.
- `_next_heading_re(level)`: factory returning `^#{1,level}\s+`. Critical
  fix vs prompt's reference impl — sub-headings under `## Verdict`
  (`### STABLE — …`) must NOT terminate the body scan; only same-level
  or higher (`##` or `#`) headings do. AI-trade fixtures all use
  sub-heading verdicts.
- `CANONICAL_MAP`: folds keyword variants into PROMOTED / REJECTED /
  CONDITIONAL.
- Legacy `**Verdict: PROMOTED**` exact pattern preserved as fallback so
  v1.3.5 PROMOTION fixtures still parse.
- `parse_verdict(path)` returns `'PROMOTED' | 'REJECTED' | 'CONDITIONAL'
  | None`.

`src/orchestrator/cycle.py`:
- CONDITIONAL emits `promotion_conditional` event, does NOT call
  `on_promotion_success`, does NOT spawn ablation children, does NOT
  fire leaderboard append. Operator reviews and manually escalates.
- Unrecognized verdict path emits `promotion_verdict_unrecognized`
  AND legacy `promotion_verdict_missing` for tooling compatibility.

Verified against 5 real AI-trade Phase 1/2 PROMOTION files — all
resolve to expected verdicts.

### Group PHASE-DONE-RECOVERY

`src/orchestrator/recovery.py`:
- `_should_resume_done(s, project_path) -> (bool, reason)` — gate
  mirrors `_should_recover` with the same enforcement-state guards
  (meta_reflect_pending, knowledge_update_pending,
  research_plan_required) so a done project in any in-flight loop is
  left alone.
- Resume condition: `_count_open_backlog(project_path) > 0` — counts
  `^[ \t]*-[ \t]*\[ \]` lines directly. Missing backlog or 0 open lines
  → `prd_still_complete` skip. (Initial implementation used
  `detect_prd_complete` which returns False on missing backlog and
  caused regression in `test_skips_done_and_failed_projects`; switched
  to direct count.)
- `maybe_resume_done(project)` — single-project flip with per-project
  lock + atomic state.write. On resume: `phase=active`, `prd_complete=
  False`, `prd_complete_detected=False`, `current_task=None`,
  `last_score=None`, `last_passed=None`. Logs `phase_done_to_active`
  with `reason=backlog_reopened, open_tasks=N`.
- `sweep_done_projects(projects)` — iterable wrapper for the periodic
  sweep, returns count revived. Aborts on shutdown flag.

`src/orchestrator/main.py`:
- Wired alongside `auto_recover_failed_projects` in the existing
  `RECOVERY_INTERVAL_SEC` (30 min) sweep cadence.

Schema unchanged.

### Group SENTINEL-PATTERNS

`src/lib/knowledge.py`:
- `_VERDICT_PATTERNS` extended with: `complete`, `completed`, `done`,
  `closed`, `finished`, `reject`, `pass`, `fail`, `analysis_complete`,
  `reporting_complete`, `implementation_complete`. Substring match
  via `is_verdict_stage` so `stage_e_verdict`, `analysis_complete`,
  `implementation_complete` all arm; bare `implementation` does not.

NB: `PROMPT_v1.3.6-hotfix.md` placed `_VERDICT_PATTERNS` in
`src/lib/current_task.py` and called the helper
`matches_verdict_pattern`. Actual location is `src/lib/knowledge.py`,
helper is `is_verdict_stage`. Tactical adjustment, no behavior change
vs prompt intent.

`src/orchestrator/cycle.py`:
- New helper `_maybe_arm_sentinel_via_promotion(project, post_task_id, s)`
  arms the sentinel when:
  - task id starts with `vec_` or `phase_gate_`
  - `PROMOTION.md` exists at `promotion_path()`
  - mtime within `PROMOTION_MTIME_FRESH_WINDOW_SEC` (5 min)
  - `parse_verdict()` returns a verdict
  - sentinel is NOT already armed (idempotent w/ stage-based path)
- Emits `knowledge_sentinel_armed_via_promotion` with
  `task_id, promotion_mtime_age_sec`.

## Smokes — 2 new, all <3s, all green

- `tests/smoke/run-lenient-verdict-smoke.sh` — heading-style PROMOTED,
  CONDITIONAL (no children), inline LONG_LOSES_MONEY rejected.
- `tests/smoke/run-phase-done-reopen-smoke.sh` — still-complete done
  skipped, reopen → flip to active, second sweep silent, enforcement
  outranks reopen.

Wired into `HOTFIX_SMOKES` in `run-all-smokes.sh`.

## New events in aggregate.jsonl

- `promotion_conditional` (task_id) — partial pass, no side effects
- `promotion_verdict_unrecognized` (task_id) — parser couldn't match
  any keyword (legacy `promotion_verdict_missing` still emitted alongside)
- `phase_done_to_active` (project, reason=backlog_reopened, open_tasks)
- `phase_done_resume_skipped` (project, reason)
- `knowledge_sentinel_armed_via_promotion` (task_id,
  promotion_mtime_age_sec)

## New CLI surface

None. No new env vars; no schema bump.

## Acceptance gates — green for v1.3.6 surfaces

- pytest: 743 (v1.3.5 baseline) → **770 passed** (+27 new tests)
  - `tests/unit/test_promotion.py`: +8 cases (heading-style + CONDITIONAL)
  - `tests/integration/test_promotion_flow.py`: +3 cases (cycle dispatch)
  - `tests/integration/test_recovery_sweep.py`: +7 cases (NEW file)
  - `tests/unit/test_knowledge_enforce.py`: +2 cases (broader vocabulary)
  - `tests/integration/test_sentinel_promotion_fallback.py`: +7 cases (NEW file)
- run-all-smokes hotfix-style: **15/15 v1.3+ hotfix smokes green**
  (was 13/13 in v1.3.5; +2 new for v1.3.6: lenient-verdict +
  phase-done-reopen). v133 + v134 real-CLI smokes (7/7) also green.
- AI-trade reference verification: 5/5 real Phase 1/2 PROMOTION files
  parse to expected verdicts.

### Pre-existing baseline observations (NOT introduced by v1.3.6)

`run-all-smokes.sh` baseline before v1.3.6 changes had seven red
stages (a-f, k) — same as v1.3.5. All chained on either the
test-file F401 ruff failure or the stale stage-k stderr-grep.
v1.3.6 introduces no new failures.

## Schema

**Unchanged at v6.** No new persisted fields. v1.3.6 adds new event
records but no state.json shape changes, per PROMPT_v1.3.6 §"Don't"
("don't introduce new state.json schema fields").

## Manual smoke (Roman, after agent done)

```bash
pytest tests/ -q
bash tests/smoke/run-all-smokes.sh lenient-verdict phase-done-reopen
bash tests/smoke/run-promotion-validation-smoke.sh
bash tests/smoke/run-leaderboard-elo-smoke.sh

# Verify on real AI-trade PROMOTION files (read-only, no engine state mutation)
python3 <<'EOF'
import sys
sys.path.insert(0, 'src/lib')
from promotion import parse_verdict
from pathlib import Path

for f in [
    '/mnt/c/claude/artifacts/repos/AI-trade/data/debug/CAND_long_only_baseline_PROMOTION.md',
    '/mnt/c/claude/artifacts/repos/AI-trade/data/debug/CAND_dr_synth_v1_PROMOTION.md',
    '/mnt/c/claude/artifacts/repos/AI-trade/data/debug/CAND_q_compressed_partial_filter_PROMOTION.md',
    '/mnt/c/claude/artifacts/repos/AI-trade/data/debug/CAND_dr_regime_classifier_check_PROMOTION.md',
    '/mnt/c/claude/artifacts/repos/AI-trade/data/debug/CAND_rl_PROMOTION.md',
]:
    print(f'{Path(f).name:60s} → {parse_verdict(Path(f))}')
EOF
# Expected:
#   CAND_long_only_baseline_PROMOTION.md      → REJECTED
#   CAND_dr_synth_v1_PROMOTION.md             → CONDITIONAL
#   CAND_q_compressed_partial_filter_PROMOTION.md → PROMOTED
#   CAND_dr_regime_classifier_check_PROMOTION.md  → PROMOTED
#   CAND_rl_PROMOTION.md                      → REJECTED

# Tag
git tag v1.3.6
```

## Next

Roman validates + tags `v1.3.6`. Multi-month autonomy v2.0 stress test
then has the three engine-side guardrails the v1.3.5 baseline lacked:
heading-style PROMOTION verdict recognition, automatic done→active
resume on backlog reopen, and resilient knowledge.md sentinel arming
across both stage-based and PROMOTION-mtime fallback paths.
