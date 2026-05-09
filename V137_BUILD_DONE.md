# V137_BUILD_DONE — cc-autopipe v1.3.7 hotfix

**Built:** 2026-05-09
**Branch:** main (commits below; no remote push, no tag)
**Driver:** AI-trade Phase 2 v2.0 stress test 2026-05-09T00:06:14
surfaced 3 operational gaps that blocked confident multi-month
autonomy:

```
00:05:57 promotion_verdict_unrecognized × 4 (all 4 closed tasks)
00:05:58 subprocess_alerted iteration=24 rc=1
00:06:14 stuck_failed last_activity_at=2026-05-07T01:30:16Z (2 days stale)
00:06:14 cycle_end iteration=24 phase=failed rc=1
```

The cycle did real work (4 PROMOTION files written, 4 backlog `[x]`
added), but v1.3.6's verdict regex didn't recognise Acceptance-only
PROMOTION reports and the engine-internal `last_activity_at` was
frozen at the pre-pause snapshot. v1.3.7 ships a 3-tier verdict
parser, a filesystem-evidence stuck gate, and an unconditional
activity-mtime refresh.

## Group summaries

### ACCEPTANCE-FALLBACK — 3-tier verdict parser

`src/lib/promotion.py:parse_verdict` now walks three tiers; each
returns the first match and the next tier fires only on None:

  1. **Verdict heading + body keyword** (v1.3.6, unchanged) —
     extracted into `_parse_verdict_tier1`.
  2. **Legacy strict `**Verdict: PROMOTED**`** (v1.3.5, unchanged).
  3. **Acceptance / Conclusion / Result / Outcome / Status heading +
     verdict-equivalent keyword or ✅/❌ marker** (NEW). Scoped to
     the section under the matched heading capped at 30 lines or until
     the next ##/### boundary.

Tier-3 keyword vocabulary (case-insensitive):

```
PROMOTED:    'criteria met', 'all met', 'fully met', whole-word 'met',
             'met ✅', '✅ met', 'pass', 'passed', bare ✅
REJECTED:    'criteria not met', 'not met', 'fail', 'failed', bare ❌
CONDITIONAL: 'partial', 'partially met', 'mixed', 'conditional'
```

Two real AI-trade Phase 2 measurement PROMOTION files that v1.3.6
returned None on now resolve to PROMOTED:

  - `CAND_long_stat_dm_test_PROMOTION.md` — closes with
    `## Acceptance` + `✅ **PASS** — Pipeline-ready implementation`.
  - `CAND_long_baseline_seed_var_PROMOTION.md` — closes with
    `## Acceptance` + `✅ \`std/mean\` documented as noise floor`
    (caught by the bare-✅ symmetry — see deviation #1 below).

The other 7 v1.3.6 fixture cases keep their existing verdicts.

### STUCK-WITH-PROGRESS — filesystem-evidence stuck gate

Adds `_check_in_cycle_progress(project_path, cycle_start_at, s)` to
`src/orchestrator/cycle.py`. Three filesystem signals:

```
new_promotion_files     CAND_*_PROMOTION.md mtime ≥ cycle_start
backlog_x_delta          `- [x]` count grew vs cycle-start snapshot
current_task_stages_grew CURRENT_TASK.md mtime ≥ cycle_start AND
                         post-cycle stages_completed non-empty
```

State carries one new field — `cycle_backlog_x_count_at_start: int |
None`, snapshotted at cycle_start so the post-cycle delta is
computable. `SCHEMA_VERSION` stays at 6 (additive only); old v6
state files migrate via the dataclass-defaults path used everywhere
else.

When `evaluate_stuck` returns "fail", the engine consults
`_check_in_cycle_progress` first. On any progress evidence: refresh
`last_activity_at` to now and emit a single combined
`stuck_check_skipped_progress_detected` event with all three evidence
fields populated. Without progress: legacy `stuck_failed` path runs
unchanged (phase=failed, HUMAN_NEEDED.md, TG alert).

The legacy `activity_lib.detect_activity` probe still runs at its
existing site — purpose is unchanged (process scan, last-stage
tracking, generic file walk). v1.3.7 just adds a more targeted check
that doesn't depend on the 5000-file walk budget the legacy probe
sometimes truncates with.

### ACTIVITY-MTIME-BASED — unconditional activity refresh

Closes the pause+resume staleness bug. After the stuck-detection
block, the engine refreshes `last_activity_at` to now whenever
`_check_in_cycle_progress.any_progress` is true (and phase isn't
"failed"). Idempotent with the stuck-skip refresh above; a no-op when
nothing changed on the filesystem.

Real driver: AI-trade paused 2026-05-07T11:51:07Z and resumed
2026-05-09T00:00:56Z. After the resume cycle wrote 4 PROMOTION files,
`last_activity_at` was still 2026-05-07T01:30:16Z (46h stale, set
during the cycle PRECEDING the pause), feeding the spurious
`stuck_failed` decision logged at 00:06:14. With v1.3.7, the resume
cycle's filesystem evidence drives `last_activity_at` forward and the
next sweep sees a healthy timestamp.

## Test counts

| Surface | Pre-v1.3.7 | v1.3.7 | Delta |
|---|---|---|---|
| pytest tests/ | 770 | **798** | +28 |
| Hotfix smokes | 22 (15 v1.3+ + 5 v1.3.3 + 2 v1.3.4) | **23** | +1 |
| Real AI-trade fixtures resolved correctly | 7/9 (2 None) | **9/9** | +2 |

Pytest breakdown of the +28:
- promotion: +10 (8 synthetic + 2 real-fixture cases)
- state v137: +4
- stuck-with-progress: +11 (6 helper + 5 process_project scenarios)
- activity-refresh: +3

Pre-existing baseline failures (`stage-a` … `stage-f` chain on the
ruff-on-tests rule, `stage-k` orchestrator-startup-log predicate
drift) — all documented in v1.3.5 STATUS.md as deferred and confirmed
green pre-v1.3.7. v1.3.7 introduces no new smoke regressions.

## Real AI-trade fixture verification

```
[PASS] CAND_long_stat_dm_test_PROMOTION.md         -> PROMOTED
[PASS] CAND_long_baseline_seed_var_PROMOTION.md    -> PROMOTED
[PASS] CAND_long_only_baseline_PROMOTION.md        -> REJECTED
[PASS] CAND_dr_synth_v1_PROMOTION.md               -> CONDITIONAL
[PASS] CAND_q_compressed_partial_filter_PROMOTION.md -> PROMOTED
[PASS] CAND_dr_regime_classifier_check_PROMOTION.md  -> PROMOTED
[PASS] CAND_rl_PROMOTION.md                        -> REJECTED
[PASS] CAND_meta_PROMOTION.md                      -> REJECTED
[PASS] CAND_tbm_PROMOTION.md                       -> REJECTED
ALL PASS
```

## New events

`stuck_check_skipped_progress_detected` — emitted by `cycle.py` when
`evaluate_stuck` would have failed but `_check_in_cycle_progress`
reports `any_progress`. Fields: `iteration`, `new_promotions`,
`backlog_x_delta`, `current_task_grew`. One event per cycle, not one
per evidence type.

## Schema

**Unchanged at v6.** One additive field —
`cycle_backlog_x_count_at_start: int | None` defaulting to None.
Per-PROMPT_v1.3.7 §"Don't" this is the only state.json schema field
introduced in v1.3.7.

## Tactical deviations from PROMPT_v1.3.7-hotfix.md

1. **Tier-3 keyword vocabulary symmetry — bare ✅ added to PROMOTED
   group.** PROMPT_v1.3.7's reference regex listed `❌` for REJECTED
   but no `✅` for PROMOTED, breaking symmetry. Real AI-trade
   documentation-style Acceptance sections (e.g. `seed_var`) confirm
   work with bare ✅ alone — no `met` / `pass` / `criteria met`
   prose anywhere. Implementation adds `✅` to group 1 (PROMOTED) so
   `seed_var` resolves to PROMOTED, matching the prompt's
   manual-smoke expectation. Documented in `promotion.py` module
   docstring + ACCEPTANCE_KEYWORD_RE inline comment.

2. **`_parse_verdict_tier1` extracted into a helper.** PROMPT_v1.3.7
   inlined the v1.3.6 verdict-tier logic; the implementation pulls it
   into a helper so each tier reads cleanly and the level-aware
   `_next_heading_re` (v1.3.6 deviation #2) stays adjacent to the
   tier that needs it. No behavioural change on the v1.3.6 cases.

3. **Activity-probe stub in tests.** Both
   `test_stuck_with_progress.py` and `test_activity_refresh.py`
   monkeypatch `activity_lib.detect_activity` to is_active=False.
   Without the stub the legacy probe catches fresh test artefacts
   BEFORE the v1.3.7 gate runs and the gate event never fires — but
   the AI-trade bug condition is precisely "activity probe missed
   because of the 5000-file walk cap", so reproducing that miss is
   what makes the gate testable.

4. **Section bound for tier-3 is level-agnostic** (`^#{1,4}\s+`).
   Tier-1's `_next_heading_re` is heading-level-aware (v1.3.6 fix);
   tier-3 doesn't need to be because Acceptance / Conclusion sections
   in the AI-trade fixtures don't nest sub-headings before the
   verdict keyword. Documented inline at
   `_ACCEPTANCE_NEXT_HEADING_RE`.

## Atomic commits

```
fbdd980  smoke: add run-acceptance-fallback-smoke.sh covering 3 v1.3.7 scenarios
dff257d  tests: cover last_activity_at refresh through pause+resume + active cycle
d45e017  cycle: refresh last_activity_at from filesystem mtime on every cycle_end with progress
488b6bf  tests: cover stuck-with-progress detection — respects filesystem evidence
86963eb  cycle: snapshot backlog [x] at cycle_start; check progress before stuck_failed
95f1291  state: add cycle_backlog_x_count_at_start field for in-cycle progress detection
16f51c3  tests: cover acceptance-fallback parsing with real Phase 2 PROMOTION fixtures
2e88e0b  promotion: v1.3.7 acceptance/conclusion fallback for measurement-task verdicts
```

8 atomic commits + 1 STATUS/V137 docs commit (next).

## Manual smoke for Roman (after deploy)

```bash
pytest tests/ -q                              # 798 passed
bash tests/smoke/run-all-smokes.sh            # 23 hotfix smokes green
bash tests/smoke/run-acceptance-fallback-smoke.sh  # standalone

# Verify v1.3.7 fixes the actual files that broke v1.3.6:
python3 - <<'EOF'
import sys
sys.path.insert(0, 'src/lib')
from promotion import parse_verdict
from pathlib import Path
cases = [
    ('CAND_long_stat_dm_test_PROMOTION.md',          'PROMOTED'),
    ('CAND_long_baseline_seed_var_PROMOTION.md',     'PROMOTED'),
    ('CAND_long_only_baseline_PROMOTION.md',         'REJECTED'),
    ('CAND_dr_synth_v1_PROMOTION.md',                'CONDITIONAL'),
    ('CAND_q_compressed_partial_filter_PROMOTION.md','PROMOTED'),
    ('CAND_dr_regime_classifier_check_PROMOTION.md', 'PROMOTED'),
    ('CAND_rl_PROMOTION.md',                         'REJECTED'),
    ('CAND_meta_PROMOTION.md',                       'REJECTED'),
    ('CAND_tbm_PROMOTION.md',                        'REJECTED'),
]
base = Path('/mnt/c/claude/artifacts/repos/AI-trade/data/debug')
ok = True
for fn, expected in cases:
    actual = parse_verdict(base / fn)
    mark = 'PASS' if actual == expected else 'FAIL'
    if actual != expected:
        ok = False
    print(f'[{mark}] {fn:55s} -> {actual!s:12s} (expected {expected})')
print('ALL PASS' if ok else 'FAILURES')
EOF

# Bump version + tag
echo "1.3.7" > src/VERSION
git tag v1.3.7
```

## Stopping conditions met / not met

- v1.3.6 baseline pytest broken pre-build → **NOT MET** (770 green).
- Any group's tests broke v1.3.6 functionality → **NOT MET** (770
  baseline tests still pass alongside +28 new).
- Build estimate exceeded 4 hours → **NOT MET** (build wall-time
  approximately 1h 45min).
- Real AI-trade Acceptance-only PROMOTION files don't parse to
  PROMOTED → **NOT MET** (both dm_test and seed_var resolve PROMOTED
  with the bare-✅ symmetry deviation; documented).

Done.
