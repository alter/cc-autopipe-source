# v1.4.1 — PROMOTION.md parser hardening hotfix

**Build complete.** Three groups landed in 7 commits. 891 → 904 tests
passing (+13); 35/43 → 36/44 smokes passing (+1 new, no regressions).

## Groups

### TABLE-CELL-HARDENING

- New `_coerce_table_cell(raw) -> float | None` helper in
  `src/lib/promotion.py`. Handles real Phase 3 PROMOTION.md cell
  shapes that v1.4.0's inline
  `float(raw.rstrip('%').lstrip('+'))` silently swallowed:
  - Bold-cell markers (`**0.78762**`) — Phase 3 NN tables
  - Unicode minus (`−3.32`, U+2212) — Phase 3 LA Δ rows
  - En-dash / em-dash (U+2013 / U+2014) — alternative minus glyphs
  - Em-dash placeholders (`—` / `--` / `-` / `N/A`) → `None`
  - Trailing emoji markers (`0.86003 ✓` → 0.86003)
  - Trailing percent + leading plus (`+692.84%` → 692.84)
- `_parse_table_metrics` now routes every data cell through the
  helper; coercion failures return `None` (skipped) rather than
  silently leaving the metric un-set via a ValueError.
- Dropped the bare `"dd": "max_dd"` entry from
  `_TABLE_COLUMN_ALIASES`. AI-trade documentation tables use `dd`
  for date-format columns (`Date (dd-MM)`, `dd/yyyy`) and unrelated
  abbreviations; the alias claimed those headers as `max_dd` and
  fed date numerals into the leaderboard composite. `max_dd` and
  `max dd` remain.

### QUARANTINE-FILENAME-CONSISTENCY

- `quarantine_invalid` now routes both the marker filename and the
  operator-facing CAND reference text through
  `_promotion_basename(task_id)` (Form 1: only `vec_` stripped,
  canonical engine-emit path). The body heading still shows the
  full task_id for readability.
- Pre-v1.4.1 the marker said
  `data/debug/CAND_vec_p3_meta_anti_winner_bias_PROMOTION.md` while
  the engine actually read from
  `CAND_meta_anti_winner_bias_PROMOTION.md` via the v1.4.0
  MULTI-PREFIX-STRIP candidate-path probe. Operators following the
  breadcrumb found nothing.
- Legacy `UNVALIDATED_PROMOTION_vec_long_*` markers already on
  production disks are left untouched — they're write-once at
  quarantine time and operators own them.

### TIER1-NEGATION-GUARD

- `_parse_verdict_tier1` split into three ordered passes:
  1. REJECTED-class keywords (REJECTED/REJECT/FAILED/FAIL/LONG_LOSES_MONEY) win unconditionally.
  2. CONDITIONAL-class keywords (CONDITIONAL/PARTIAL/NEUTRAL).
  3. PROMOTED-class keywords (PROMOTED/ACCEPTED/ACCEPT/PASSED/PASS/STABLE) with an 8-char negation lookbehind that filters `not pass` / `n't pass` / `fail to pass` / `didn't pass` / `won't pass` etc.
- New regex constants `VERDICT_KEYWORD_REJECT_RE`,
  `VERDICT_KEYWORD_CONDITIONAL_RE`, `VERDICT_KEYWORD_PROMOTE_RE`
  alongside the legacy `VERDICT_KEYWORD_RE` (preserved for external
  callers; `_parse_verdict_tier1` no longer uses it).
- ASCII-only `_NEGATION_PREFIXES`. Unicode negation forms would
  require the third-party `regex` module which the stdlib-only
  constraint forbids; non-English negations are out of scope.
- Mirrors the Tier 4 RESULT-OVER-STATUS two-pass shape from v1.4.0.
- One existing fixture-based test updated: under the new cascade,
  `## Verdict\n\n### PASS — measurement OK\n\n**Status**: FAIL`
  resolves to REJECTED (Pass 1 catches `FAIL`) rather than PROMOTED.
  The Tier-1-beats-Tier-4 ordering invariant the test was written
  for still holds; only the winning tier-1 verdict has flipped.

## Operator action required

Re-run the retroactive validator against AI-trade Phase 3 to
re-score LA-track entries against the hardened table parser:

```bash
python3 tools/retroactive_promotion_validate.py \
    /mnt/c/claude/artifacts/repos/AI-trade \
    --prefix vec_p3_ \
    --reprocess
```

Expected outcomes:
- LA-track entries that previously had `composite=0.3000` (the
  v1.3.13 Phase-3-empty floor) now show `sum_fixed` populated and
  composite computed via the Phase 2 formula. The hardened cell
  coercion lets the table fallback survive Unicode-minus delta
  rows that previously raised ValueError mid-parse.
- New quarantine markers (if any backlog reopens) write to
  `UNVALIDATED_PROMOTION_p3_<rest>.md` matching
  `CAND_p3_<rest>_PROMOTION.md`.
- Phase 1/2 reports closing with `## Verdict\n\nThis task did NOT
  pass.\n\nResult: REJECTED` resolve to REJECTED via Tier 1 Pass 1
  rather than silently capturing `pass` as PROMOTED.

## What's NOT in v1.4.1

- The legacy `VERDICT_KEYWORD_RE` is preserved — external callers
  and tests that import it directly continue to work. It's no
  longer used by `_parse_verdict_tier1`.
- Legacy `UNVALIDATED_PROMOTION_vec_long_*` markers on production
  disks are NOT migrated. Operators delete/edit them manually.
- Table-fallback false-positives historically captured in
  LEADERBOARD.md are NOT auto-archived. Re-running the retroactive
  validator with `--reprocess` is the supported re-score path.
- `_TABLE_COLUMN_ALIASES` retains string-key lookup (no regex-based
  key matching) — explicit keys remain cheaper and audit-friendlier.

## Smokes

One new smoke, green:

- `tests/smoke/run-table-cell-real-shapes-smoke.sh` — two
  PROMOTION.md fixtures mirroring real Phase 3 LA (Unicode-minus Δ
  row) and Phase 3 NN (bold-cell data row) shapes. LA resolves to
  PROMOTED with Phase-2 composite > 0.3 via table-fallback
  sum_fixed; NN resolves to REJECTED via RESULT-OVER-STATUS and is
  correctly absent from LEADERBOARD.md; neither file emits
  `promotion_verdict_unrecognized`.

Registered in `tests/smoke/run-all-smokes.sh`.

## Acceptance

- `pytest tests/ -q` → 904 passed (891 baseline + 13 new test
  cases across 3 new unit-test files; +1 existing fixture-expectation
  update for the Tier 1 cascade in test_promotion.py; +2 integration
  test fixtures updated to the v1.4.1 Form-1 basename convention
  in test_promotion_flow.py and test_post_cycle_delta_scan.py — no
  net change in collected test count from those two updates)
- `bash tests/smoke/run-all-smokes.sh` → 36/44 passed; the 8
  failing stages (a/b/c/d/e/f/k/l) are pre-existing v0.5-era
  stage-smoke failures flagged as baseline noise in STATUS.md
  since v1.3.12 — not v1.4.1 regressions.

## Commits (in order)

1. `promotion: _coerce_table_cell handles Unicode minus, bold cells, em-dash placeholders; drop "dd" alias (v1.4.1)`
2. `tests: cover table-cell coercion + real Phase 3 NN/LA table shapes (v1.4.1)`
3. `promotion: quarantine_invalid uses _promotion_basename for marker + CAND ref consistency (v1.4.1)`
4. `tests: cover quarantine filename basename matching engine read-side (v1.4.1)`
5. `promotion: VERDICT_KEYWORD_RE Tier 1 split into REJECT/CONDITIONAL/PROMOTE passes with negation guard (v1.4.1)`
6. `tests: cover Tier 1 negation handling — "did NOT pass" no longer captured as PROMOTED (v1.4.1)`
7. `smoke: add run-table-cell-real-shapes-smoke.sh covering Unicode minus + bold cells in real Phase 3 table layouts (v1.4.1)`
