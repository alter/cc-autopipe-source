# Build Status

**Updated:** 2026-05-09T20:30:00Z
**Current branch:** main
**Current stage:** **v1.3.10 HOTFIX COMPLETE.** One group
(POST-CYCLE-DELTA-SCAN: new `_post_cycle_delta_scan(project_path,
pre_open_vec_long)` helper in `src/orchestrator/cycle.py` runs after
the existing v1.3.5 pre-cycle validation loop and validates
`vec_long_* + [implement] + [x]` tasks that were NOT in
`pre_open_vec_long` — i.e. tasks added & closed in the SAME cycle
(Claude meta-task pattern observed in AI-trade Phase 2 v2.0). Same
pipeline as the pre-cycle path: `parse_verdict → validate_v2_sections
(with task_id) → on_promotion_success / quarantine_invalid / log`,
but every emitted event carries `origin="post_cycle_delta"` so
operators distinguish pre-cycle path matches from delta-scan matches
in aggregate.jsonl. Idempotency: `pre_ids` exclusion ensures no
double-emit when an id appears in both the pre_open snapshot and the
post-cycle `[x]` set). Schema **unchanged at v6** — no new persisted
fields per PROMPT_v1.3.10 §"Don't". Pre-cycle validation path
unchanged (purely additive). 833 → **840 tests passing** (+7: empty
pre_open delta-promote, pre+post overlap exclusion, strategy-prefix
quarantine, non-strategy relax → spawn, delta-path REJECTED, missing
PROMOTION.md → unrecognized+missing, idempotency exclusion). 26/26
hotfix smokes green (25 v1.3.9 + new mid-cycle-add-close + 5 v1.3.3
+ 2 v1.3.4). Empirical driver: AI-trade Phase 2 v2.0 production
aggregate.jsonl showed **~30 `knowledge_sentinel_armed_via_promotion`
events without parallel `promotion_validated_attempt` events** post-
v1.3.9 deploy. Sentinel arming fires from every PROMOTION.md verdict,
but the v1.3.5 validation loop only iterates `pre_open_vec_long` — so
mid-cycle-added meta-tasks (`vec_long_phase8_summary`,
`vec_long_production_script`, etc.) closed within the same cycle were
silently skipped: 0 ablation children, no LEADERBOARD.md append.
v1.3.10 closes the loop. Awaiting Roman validation + tag v1.3.10.

**Earlier stage:** **v1.3.9 HOTFIX COMPLETE.** One group
(BOLD-METADATA-VERDICT: tier-4 inline `**Field**: KEYWORD` fallback
in `parse_verdict` via new `BOLD_METADATA_VERDICT_RE` +
`_parse_verdict_tier4_bold_metadata` — fires only when tiers 1-3
returned None; restricted to closure-synonym field names
(Status / Result / Outcome / Verdict / Decision / Conclusion) so
unrelated bold metadata like `**Note**: ...` or `**Pareto points**: 7`
does NOT trigger; keyword vocabulary mirrors tiers 1+3 so canonical
mapping stays consistent). Schema **unchanged at v6** — no new
persisted fields per PROMPT_v1.3.9 §"Don't". Tiers 1-3 unchanged
(purely additive). 820 → 833 tests passing (+13). 25/25 hotfix
smokes green. End-to-end re-validation on real AI-trade Phase 2
fixtures: `CAND_elo_rating`, `CAND_tournament_round_robin`,
`CAND_tournament_swiss`, `CAND_optuna_mo` all resolve PROMOTED via
tier 4. Empirical driver: AI-trade Phase 2 v2.1 production
aggregate.jsonl showed 31 `promotion_verdict_unrecognized` events in
12 hours post-v1.3.8 deploy — all from compact bold-metadata
measurement reports.

**Earlier stage:** **v1.3.8 HOTFIX COMPLETE.** Three groups
(SENTINEL-RACE-FIX: idempotent sentinel arming in both
`_maybe_arm_sentinel_via_promotion` and the stage_completed
verdict-stage path + new `_safe_baseline_mtime` helper that snapshots
pre-cycle mtime so a same-cycle Claude knowledge.md append still
clears pending; RECOVERY-SWEEP-SENTINEL-TIMEOUT: 4h escape hatch via
`_is_sentinel_genuinely_stuck` + `sentinel_stuck_force_clear` reason
in `_should_recover` + sentinel teardown in `maybe_auto_recover`;
PROMOTION-HOOK-DIAGNOSTICS: 9-prefix `STRATEGY_PROMOTION_PREFIXES`
gate via `requires_full_v2_validation` + relaxed `validate_v2_sections(path,
task_id=...)` for measurement / infra tasks + per-stage event trail
in `on_promotion_success` (`_entered`, `ablation_children_spawned` /
`promotion_children_skipped`, `_failed stage=...`, `_completed`)).
Schema **unchanged at v6** with NO new persisted fields per
PROMPT_v1.3.8 §"Don't". 798 → **820 tests passing** (+22). 24/24
hotfix smokes green (16 v1.3+ + new sentinel-race + 5 v1.3.3 + 2
v1.3.4 = 17 hotfix-style + 7 stage-letter). 9/9 real AI-trade
Phase 2 PROMOTION reference files parse to expected verdicts;
`vec_long_quantile` and `vec_long_risk_adj_target` (the production
measurement tasks v1.3.7 silently quarantined for "missing v2.0
sections") now `validate_v2_sections(task_id=...) → ok=True` and
will fire `on_promotion_success` end-to-end. Empirical drivers:
AI-trade Phase 2 v2.0 ~10h autonomous run 2026-05-09 — sentinel-arm
race left engine permanently stuck at `phase=failed,
knowledge_update_pending=True` for 4+ hours with infinite recovery
skip loop, AND 5 measurement-task PROMOTIONs spawned 0 ablation
children + LEADERBOARD.md never created.

**Earlier stage:** **POST-v1.3.7 OPERATOR TOOLING.** Ships
`cc-autopipe snapshot` (src/helpers/cc-autopipe-snapshot, wired into
the dispatcher) — universal 12-section one-shot project health view
with optional 13th section via `<project>/.cc-autopipe/snapshot-extra.sh`
hook. Auto-detects backlog at project root vs `.cc-autopipe/`,
falls back through journalctl → orchestrator-stderr.log →
aggregate.jsonl so the timeline works under systemd, daemon mode,
or foreground equally. README rewritten as an operator guide with
new §6 "Monitoring — is it working?" (snapshot table + snapshot-extra
hook docs + raw log-surface reference + 6-step working-or-not
checklist). README §9 filesystem layout corrected:
orchestrator-stderr.log + orchestrator-stdout.log (was incorrectly
named orchestrator.log) + health.jsonl + per-project
snapshot-extra.sh. Empirical driver: Roman ran the AI-trade-specific
`tmp/one-shot-snapshot.sh` and asked for a universal version that
ships by default — install.sh already copies all of helpers/
recursively, so nothing else to wire. Smoke-tested against AI-trade
v1.3.7 live (iteration 62, phase=active): all 12 universal sections
render with real engine state; ps lines truncate cleanly. No engine
state mutated, no schema change.

**Earlier stage:** **v1.3.7 HOTFIX COMPLETE.** Three groups
(ACCEPTANCE-FALLBACK: 3-tier verdict parser with Acceptance/Conclusion/
Result/Outcome/Status section + ✅/❌ marker support; STUCK-WITH-PROGRESS:
filesystem-evidence stuck gate via `_check_in_cycle_progress` —
PROMOTION.md mtime + backlog `[x]` delta + CURRENT_TASK stages_completed
mtime; ACTIVITY-MTIME-BASED: unconditional `last_activity_at` refresh
at cycle_end on filesystem evidence, closes pause+resume staleness).
Schema **unchanged at v6** with one additive field (`cycle_backlog_x_count_at_start`).
770 → **798 tests passing** (+28). 23/23 hotfix smokes green (16 v1.3+
hotfix style including new acceptance-fallback + 5 v1.3.3 + 2 v1.3.4).
9/9 real AI-trade Phase 2 PROMOTION reference files parse to expected
verdicts (the two Acceptance-only files dm_test and seed_var that
v1.3.6 dropped to None now resolve PROMOTED). Empirical drivers:
AI-trade Phase 2 v2.0 stress test 2026-05-09T00:06:14 cycle showed
v1.3.6 verdict regex missed ~50% of measurement/infra reports +
spurious stuck_failed transitions blocked multi-month autonomy.
Awaiting Roman validation + tag v1.3.7.

**Earlier stage:** **v1.3.6 HOTFIX COMPLETE.** Three groups
(VERDICT-LENIENT: heading-style PROMOTION.md parser + new CONDITIONAL
canonical state; PHASE-DONE-RECOVERY: sweep_done_projects auto-resume
when backlog reopens; SENTINEL-PATTERNS: broader knowledge.md arming
vocabulary + PROMOTION-mtime fallback). Schema **unchanged at v6** (no
new persisted fields). 743 → **770 tests passing** (+27). 22/22 hotfix
smokes green (15 v1.3+ hotfix style + 5 v1.3.3 + 2 v1.3.4) including 2
new v1.3.6 smokes (lenient-verdict, phase-done-reopen). 5/5 real
AI-trade Phase 1/2 PROMOTION reference files parse to expected
verdicts. Empirical drivers: AI-trade Phase 2 v2.1 surfaced three
operational gaps blocking confident multi-month autonomy on the v2.0
220-task / 3-4 month roadmap.

**Earlier stage:** v1.3.5 HOTFIX COMPLETE. Three groups (R:
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
confident 16-week autonomy.

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

## v1.3.8 HOTFIX — final state

**3 groups landed across 8 atomic commits (3 SENTINEL-RACE-FIX +
2 RECOVERY-SWEEP-SENTINEL-TIMEOUT + 3 PROMOTION-HOOK-DIAGNOSTICS) +
1 smoke commit + 1 STATUS commit.** See `V138_BUILD_DONE.md` for the
full summary.

| Group | Surface | Tests added |
|---|---|---|
| SENTINEL-RACE-FIX/1 | src/orchestrator/cycle.py — new `_safe_baseline_mtime(s, project_path)` helper computes `min(current_mtime, cycle_start_unix)` (or `current_mtime - 1` fallback); `_maybe_arm_sentinel_via_promotion` rewritten as idempotent (early-return + `knowledge_sentinel_arm_skipped_already_armed` event when pending=True with all other gates passed); arm event enriched with `baseline_mtime` + `current_mtime`. | (covered in test_sentinel_race.py) |
| SENTINEL-RACE-FIX/2 | src/orchestrator/cycle.py — stage-based arming branch under `stage_completed` mirrors the same idempotency: `was_already_armed = s.knowledge_update_pending` checked, only flip + baseline-snapshot when False. Skip event emitted with `reason=stage_based`. `task_verdict` + `knowledge_update_required` events still fire on every verdict stage (telemetry preserved). | (covered in test_sentinel_race.py) |
| SENTINEL-RACE-FIX/3 | src/lib/stop_helper.py — `maybe_clear_knowledge_update_flag` enriched with `baseline_was` + `current_mtime` payload on the `knowledge_updated_detected` event so race traces are visible in aggregate.jsonl. The v1.3 baseline-reset-on-clear behaviour is preserved (no functional change there). | (covered in test_sentinel_race.py) |
| SENTINEL-RACE-FIX/4 | tests/integration/test_sentinel_race.py NEW file: 5 cases — pre-cycle baseline at arm; idempotent skip on already-armed; clear+reset on mtime advance; v1.3.6 bug-state direct simulation (pending=True + baseline=current_mtime → must NOT re-arm); cleared+new PROMOTION arms again with fresh baseline. | +5 |
| RECOVERY-SWEEP-SENTINEL-TIMEOUT/1 | src/orchestrator/recovery.py — `SENTINEL_STUCK_THRESHOLD_SEC = 4 * 3600`; `_is_sentinel_genuinely_stuck(s, project_path)` (all-True gate: pending=True + last_activity > 4h + mtime ≤ baseline); `_should_recover(s, project_path=None)` extended (project_path optional for unit-test back-compat); `maybe_auto_recover` clears sentinel state before phase reset when reason is `sentinel_stuck_force_clear`, emits `sentinel_force_cleared` and tags `auto_recovery_attempted` with `recover_reason`. | (covered in test_recovery_sentinel_timeout.py) |
| RECOVERY-SWEEP-SENTINEL-TIMEOUT/2 | tests/integration/test_recovery_sentinel_timeout.py NEW file: 5 cases — 2h-stuck still skipped; 5h-stuck force-cleared + recovered; 5h-but-mtime-advanced falls through to standard skip; pending=False uses standard recovery; direct unit test of `_is_sentinel_genuinely_stuck` per-branch. | +5 |
| PROMOTION-HOOK-DIAGNOSTICS/1 | src/lib/promotion.py — `STRATEGY_PROMOTION_PREFIXES` (9 prefixes: synth/dr_synth/pack/moe/cascade/ensemble/committee/stacking/hybrid); `requires_full_v2_validation(task_id)`; `validate_v2_sections(path, task_id=None)` — task_id supplied + non-strategy → `(True, [])`; task_id=None preserves v1.3.5 strict 5-section check (back-compat); `on_promotion_success(...)` instrumented with `_entered` / `_completed` / `_failed` events tagged with `stage=ablation_spawn` or `stage=leaderboard` on raise. | (covered in test_promotion.py + test_promotion_diagnostics.py) |
| PROMOTION-HOOK-DIAGNOSTICS/2 | src/orchestrator/cycle.py — call site updated: `validate_v2_sections(p_path, task_id=pre_item.id)`. Emits `promotion_validated_attempt` at entry and `promotion_v2_sections_check` (with `all_present`, `missing` csv, `strict` flag) for every PROMOTED-verdict task. | (covered in test_promotion_diagnostics.py) |
| PROMOTION-HOOK-DIAGNOSTICS/3 | tests/unit/test_promotion.py +7 cases (4 prefix-gate + 3 validate-with-task_id branch); tests/integration/test_promotion_diagnostics.py NEW file: 5 cases — strategy happy path (entered+spawned+completed); no-backlog skip; ablation-stage failure event; leaderboard-stage failure event; full happy path with real leaderboard module + LEADERBOARD.md side-effect. | +12 |
| smokes | tests/smoke/run-sentinel-race-smoke.sh + run-all-smokes.sh wiring (`HOTFIX_SMOKES` += sentinel-race) | +0 (1 smoke) |

**Test counts (v1.3.8):**
- pytest: 798 (v1.3.7 baseline) → **820 passed** (+22 new tests)
- 24 hotfix-style smokes all green: 17 v1.3+ (incl. new
  sentinel-race) + 5 v1.3.3 + 2 v1.3.4
- AI-trade reference verification: 9/9 real Phase 2 PROMOTION files
  parse to expected verdicts; `vec_long_quantile` and
  `vec_long_risk_adj_target` now also pass `validate_v2_sections(task_id=...)`
  with `strict=False, ok=True` (the production measurement tasks
  v1.3.7 silently dropped before).

**Schema:** **unchanged at v6.** No new persisted fields per
PROMPT_v1.3.8 §"Don't"; only event-payload enrichment on existing
events + new event names.

**New events in aggregate.jsonl:**
- `knowledge_sentinel_arm_skipped_already_armed` (Group A)
- `sentinel_force_cleared` (Group B)
- `on_promotion_success_entered` / `_completed` / `_failed stage=...`
  (Group C)
- `promotion_validated_attempt` / `promotion_v2_sections_check`
  (Group C)

**Enriched events:** `knowledge_updated_detected` (+`baseline_was`,
`current_mtime`); `knowledge_sentinel_armed_via_promotion`
(+`baseline_mtime`, `current_mtime`); `auto_recovery_attempted`
(+`recover_reason`).

**New CLI surface:** none.

### Tactical deviations from PROMPT_v1.3.8-hotfix.md

1. **`maybe_clear_knowledge_pending` lives in `stop_helper.py`, not
   `knowledge.py`.** Real function name `maybe_clear_knowledge_update_flag`
   (per v1.3 I4); behaviour matches the prompt spec — only the event
   payload was extended.
2. **Stage-based arming patched too.** Prompt targets the v1.3.6
   `_maybe_arm_sentinel_via_promotion` only, but the same race exists
   in the `stage_completed` verdict-stage branch. Both arming paths
   get the idempotency check + `_safe_baseline_mtime` snapshot.
3. **`_should_recover` keeps backward-compatible signature.** Prompt
   changes the signature to require project_path; implementation
   makes it optional with default None so existing
   `tests/unit/test_recovery_safe.py` continues to call
   `_should_recover(s)` without churn.
4. **`leaderboard_append_skipped` retained for backward compat.** New
   `on_promotion_success_failed stage=leaderboard` event added per
   the prompt, AND the v1.3.5 event preserved so existing tooling
   filtering on the legacy name keeps working.

### Currently working on

**v1.3.8 build done.** All gates + smokes green; awaiting Roman
validation + manual smoke against AI-trade after deploying.

### Next

Roman validates + tags `v1.3.8`. See `V138_BUILD_DONE.md` for
the full smoke test plan + acceptance gate trace.

---

## v1.3.7 HOTFIX — final state

**3 groups landed across 7 atomic commits (2 ACCEPTANCE-FALLBACK + 3
STUCK-WITH-PROGRESS + 2 ACTIVITY-MTIME-BASED) + 1 smoke commit.** See
`V137_BUILD_DONE.md` for the full summary.

| Group | Surface | Tests added |
|---|---|---|
| ACCEPTANCE-FALLBACK/1 | src/lib/promotion.py — ACCEPTANCE_HEADING_RE + ACCEPTANCE_KEYWORD_RE + _ACCEPTANCE_NEXT_HEADING_RE; tier-1 logic extracted to `_parse_verdict_tier1`; new `_parse_verdict_acceptance` helper. parse_verdict now runs 3 tiers (verdict heading → legacy strict → acceptance/conclusion section); each tier returns first match. ✅/❌ markers symmetric in the keyword vocabulary. | (covered by unit tests) |
| ACCEPTANCE-FALLBACK/2 | tests/unit/test_promotion.py +10 cases (8 synthetic + 2 fixture-based against real AI-trade Acceptance-only files; fixture tests skip cleanly when AI-trade repo isn't present). | +10 |
| STUCK-WITH-PROGRESS/1 | src/lib/state.py — `cycle_backlog_x_count_at_start: Optional[int]` (single additive field per PROMPT_v1.3.7 §"Don't"); to_dict serialises the new key. SCHEMA_VERSION unchanged at 6. | +4 (test_state_v137.py NEW file) |
| STUCK-WITH-PROGRESS/2 | src/orchestrator/cycle.py — `_count_backlog_x` + `_check_in_cycle_progress` helpers; cycle_start snapshot of backlog `[x]`; stuck-detection branch consults fs_progress before honouring the legacy stuck_failed path (emits `stuck_check_skipped_progress_detected` with all evidence fields populated when progress detected). | (covered by integration tests) |
| STUCK-WITH-PROGRESS/3 | tests/integration/test_stuck_with_progress.py NEW file: 6 helper-level cases for `_check_in_cycle_progress` + 5 process_project end-to-end scenarios. Stubs `activity_lib.detect_activity` to is_active=False to reproduce the AI-trade scenario where the 5000-file walk budget skipped fresh artefacts. | +11 |
| ACTIVITY-MTIME-BASED/1 | src/orchestrator/cycle.py — unconditional `last_activity_at = _now_iso()` at cycle_end when `_check_in_cycle_progress.any_progress` AND `phase != "failed"`. Idempotent with the stuck-skip path; no-op without progress. | (covered by integration tests) |
| ACTIVITY-MTIME-BASED/2 | tests/integration/test_activity_refresh.py NEW file: rc=0 + new PROMOTION → activity advances; rc=0 + 0 progress → unchanged; pause/resume + active cycle with progress → activity advances past pre-pause snapshot (the literal AI-trade bug). | +3 |
| smokes | tests/smoke/run-acceptance-fallback-smoke.sh + run-all-smokes.sh wiring | +0 (1 smoke) |

**Test counts (v1.3.7):**
- pytest: 770 (v1.3.6 baseline) → **798 passed** (+28 new tests)
- 23 hotfix-style smokes all green: 16 v1.3+ (incl. new
  acceptance-fallback) + 5 v1.3.3 + 2 v1.3.4
- AI-trade reference verification: 9/9 real Phase 2 PROMOTION files
  parse to expected verdicts (two Acceptance-only files dm_test +
  seed_var that v1.3.6 returned None on now resolve to PROMOTED;
  the other 7 keep their v1.3.6 verdicts unchanged)

**Schema:** **unchanged at v6** with ONE additive field
(`cycle_backlog_x_count_at_start`, default null). Pre-v1.3.7 v6 state
files migrate via the same dataclass-defaults path used everywhere
else.

**New events in aggregate.jsonl:** `stuck_check_skipped_progress_detected`
(emitted with `new_promotions`, `backlog_x_delta`, `current_task_grew`
fields).

**New CLI surface:** none.

### Tactical deviations from PROMPT_v1.3.7-hotfix.md

1. **Tier-3 keyword vocabulary symmetry.** PROMPT_v1.3.7 §
   ACCEPTANCE-FALLBACK gave a regex with bare `❌` for REJECTED but no
   bare `✅` for PROMOTED. Real AI-trade documentation-style
   Acceptance sections (e.g. `seed_var`) confirm work with bare ✅
   alone — no `met` / `pass` / `criteria met` prose. Implementation
   adds bare `✅` to the PROMOTED group symmetrically with `❌` in
   REJECTED. Without this, the seed_var fixture would have remained
   None and the prompt's manual smoke would have failed on it.
2. **`_parse_verdict_tier1` extracted.** PROMPT_v1.3.7 reused the
   v1.3.6 inline parse logic; the implementation pulls it into a
   helper so each tier reads independently and the level-aware
   `_next_heading_re` lives next to its caller.
3. **Activity probe stub in tests.** Both new test files monkeypatch
   `activity_lib.detect_activity` to is_active=False. Without the
   stub the legacy probe (which scans data/{models,backtest,debug}
   for any fresh file) catches the test artefacts BEFORE the v1.3.7
   gate runs — but the AI-trade bug condition is precisely "activity
   probe missed because of the 5000-file walk cap", so reproducing
   that miss is what makes the gate testable.

### Currently working on

**v1.3.7 build done.** All gates + smokes green; awaiting Roman
validation + manual smoke against AI-trade after deploying.

### Next

Roman validates + tags `v1.3.7`. See `V137_BUILD_DONE.md` for
the full smoke test plan.

---

## v1.3.6 HOTFIX — final state

**3 groups landed across 8 atomic commits (+1 baseline hygiene fix +
1 follow-up bugfix).** See `V136_BUILD_DONE.md` for the full summary.

| Group | Surface | Tests added |
|---|---|---|
| VERDICT-LENIENT/1 | src/lib/promotion.py — VERDICT_HEADING_RE + VERDICT_KEYWORD_RE + _next_heading_re(level) + CANONICAL_MAP. Two-pass discovery: locate Verdict heading at any level (with optional `Stage X:` prefix and optional `**` bold), then scan up to 20 lines or until same-or-higher-level heading for keyword. CONDITIONAL/PARTIAL canonicalise to a new third state. Legacy `**Verdict: PROMOTED**` preserved as fallback. | (covered by unit + integration tests) |
| VERDICT-LENIENT/2 | src/orchestrator/cycle.py — CONDITIONAL emits promotion_conditional event, no on_promotion_success, no children, no leaderboard. Unrecognized verdict path emits promotion_verdict_unrecognized AND legacy promotion_verdict_missing for backwards compat. | (covered by integration tests) |
| VERDICT-LENIENT/3 | tests/unit/test_promotion.py +8 cases; tests/integration/test_promotion_flow.py +3 cases | +11 |
| PHASE-DONE-RECOVERY | src/orchestrator/recovery.py — _should_resume_done gate with same enforcement-state guards as _should_recover; _count_open_backlog helper; maybe_resume_done per-project flip with per-project lock; sweep_done_projects iterable wrapper. Wired into src/orchestrator/main.py periodic sweep alongside auto_recover_failed_projects. | +7 (tests/integration/test_recovery_sweep.py NEW file) |
| SENTINEL-PATTERNS/1 | src/lib/knowledge.py — _VERDICT_PATTERNS extended with complete/completed/done/closed/finished/reject/pass/fail and analysis_complete/reporting_complete/implementation_complete. Substring match preserved. NB: prompt placed this in current_task.py — actual location is knowledge.py with helper is_verdict_stage. | +2 |
| SENTINEL-PATTERNS/2 | src/orchestrator/cycle.py — _maybe_arm_sentinel_via_promotion(project, post_task_id, s) helper. Arms sentinel when fresh PROMOTION.md (mtime <5 min) parses to a verdict, even if stages_completed lacks a verdict pattern. Idempotent w/ stage-based path. Emits knowledge_sentinel_armed_via_promotion. | +7 (tests/integration/test_sentinel_promotion_fallback.py NEW file) |
| smokes | tests/smoke/run-lenient-verdict-smoke.sh + tests/smoke/run-phase-done-reopen-smoke.sh + run-all-smokes.sh wiring | +0 (2 smokes) |

**Test counts (v1.3.6):**
- pytest: 743 (v1.3.5 baseline) → **770 passed** (+27 new tests)
- 22 hotfix-style smokes all green: 15 v1.3+ (incl. 2 v1.3.6 new) + 5
  v1.3.3 + 2 v1.3.4
- AI-trade reference verification: 5/5 real Phase 1/2 PROMOTION files
  parse to expected verdicts (long_only_baseline=REJECTED,
  dr_synth_v1=CONDITIONAL, q_compressed_partial_filter=PROMOTED,
  dr_regime_classifier_check=PROMOTED, rl=REJECTED)

**Schema:** **unchanged at v6.** No new persisted fields per
PROMPT_v1.3.6 §"Don't"; only new event records in aggregate.jsonl.

**New events in aggregate.jsonl:** `promotion_conditional`,
`promotion_verdict_unrecognized` (legacy `promotion_verdict_missing`
also still emitted alongside), `phase_done_to_active`,
`phase_done_resume_skipped`, `knowledge_sentinel_armed_via_promotion`.

**New CLI surface:** none.

### Tactical deviations from PROMPT_v1.3.6-hotfix.md

1. **Verdict-pattern location.** Prompt §SENTINEL-PATTERNS placed
   `_VERDICT_PATTERNS` and the helper `matches_verdict_pattern` in
   `src/lib/current_task.py`. Actual location is `src/lib/knowledge.py`,
   helper is `is_verdict_stage`. No behavior change vs prompt intent —
   broaden the vocabulary in the place it actually lives.
2. **`_next_heading_re` is heading-level-aware.** Prompt's reference
   impl used a single `^#{1,4}\s+` boundary that would treat the
   `### STABLE` sub-heading under `## Verdict` as the next section,
   missing the keyword. Real AI-trade fixtures all use sub-heading
   verdicts. Implementation respects level: `## Verdict` is bounded
   only by `##` or `#`, not by `###`/`####`. All 5 reference files
   parse correctly as a result.
3. **`_should_resume_done` uses `_count_open_backlog`, not
   `detect_prd_complete`.** Initial implementation followed the
   prompt's reference impl (which uses `detect_prd_complete`), but
   that returns False on missing backlog and erroneously flipped a
   long-done project with no backlog file into active
   (regression caught by `test_skips_done_and_failed_projects`).
   Switched to direct `^[ \t]*-[ \t]*\[ \]` count: 0 lines (or
   missing backlog) → skip with `prd_still_complete`.

### Currently working on

**v1.3.6 build done.** All gates + smokes green; awaiting Roman
validation + manual smoke against AI-trade after deploying.

### Next

Roman validates + tags `v1.3.6`. See `V136_BUILD_DONE.md` for
the full smoke test plan.

---

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
