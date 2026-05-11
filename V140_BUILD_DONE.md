# v1.4.0 — PROMOTION.md format standardization + parser robustness

**Build complete.** Five groups landed in 9 commits. 872 → 891 unit tests
passing (+19); 33/41 → 35/43 smokes passing (+2 new, no regressions).

## Groups

### METRICS-BLOCK-CONVENTION
- `src/orchestrator/prompt.py:_implement_task_prompt_block()` instructs
  Claude to include a `## Metrics for leaderboard` section in every
  PROMOTION.md with labelled fields (`verdict`, `sum_fixed`,
  `regime_parity`, `max_dd`, `dm_p_value`, `dsr`, `auc`, `sharpe`).
- `src/lib/promotion.py` adds `_parse_metrics_block()`,
  `_coerce_metric_value()`, `_parse_verdict_block()`. The labelled
  block is read as Tier 0 in `parse_verdict` and as primary pre-fill
  in `parse_metrics`. Each v1.3.x regex extractor was wrapped with
  `if out[<key>] is None:` so a labelled value is never overwritten.
- Forward-compat: when fully rolled out, the regex code paths become
  dead and can be retired in v1.5.0.

### RESULT-OVER-STATUS
- Tier 4 bold-metadata split into two passes. `BOLD_METADATA_VERDICT_RE`
  removed; replaced by `BOLD_METADATA_VERDICT_PRIMARY_RE` (matches
  `Result | Verdict | Outcome | Decision | Conclusion`) and
  `BOLD_METADATA_VERDICT_STATUS_RE` (matches `Status` only). Primary
  pass fires first; status pass is the fallback. Symmetry: a file with
  only `**Status**: NEUTRAL` still parses correctly.
- Both regexes also accept `**Field:**` (colon inside bold close) in
  addition to the v1.3.x `**Field**:` (colon outside) shape — observed
  on AI-trade Phase 3 NN files (`**Result:** REJECTED — ...`).
- Fixes silent misclassification of REJECTED NN-track tasks (CfC, H3,
  DLinear, Chronos, HyperLSTM, S5) as PROMOTED.

### MULTI-PREFIX-STRIP
- `_promotion_basename` extended via `_TASK_ID_PREFIXES` (covers
  `vec_long_`, `vec_p1_…p4_`, `vec_`). Canonical Form 1 strips only
  `vec_`.
- `promotion_path()` now probes a candidate chain: Form 1
  (canonical) → Form 2 (full phase prefix stripped) → Form 3 (no
  strip). Returns the first existing path; absent that, the
  canonical path so write-side callers get a deterministic target.
- New `promotion_path_candidates()` exposes the full chain for
  callers that need to enumerate explicitly.
- Fixes `promotion_verdict_missing` for Phase 3 meta / nn / lv
  tasks where Claude omits the `p3_` prefix (e.g.
  `CAND_meta_anti_winner_bias_PROMOTION.md` for task
  `vec_p3_meta_anti_winner_bias`).
- All 78 existing promotion-flow tests pass unchanged.

### DAILY-SHARPE
- 2-priority cascade in `parse_metrics`. Priority 1 catches
  `Sharpe(daily)` / `daily Sharpe` / `daily-Sharpe` shapes via a
  daily-form regex. Priority 2 falls back to bare Sharpe (with the
  v1.3.13 `(?:[_\s]ratio)?` + markdown-bold-close tolerance), but
  excludes per-bar contexts via lookbehind (`per-bar `, `per_bar `)
  and lookahead (`(bar)`).
- Fixes Phase 3 LA `sharpe = 90.8` (inflated per-bar) capture; the
  true `daily Sharpe = 18.33` now wins.

### TABLE-METRICS
- `_TABLE_COLUMN_ALIASES` + `_parse_table_metrics()` reads metric
  values from the first markdown table whose header contains a
  recognised alias (`sf`, `Sharpe(daily)`, `AUC`, etc.). Called last
  in `parse_metrics`; only fills `None` slots so the labelled block
  always wins.
- Defense-in-depth for files that follow the v1.4.0 contract loosely
  (e.g. Phase 3 LA reports that include the table but skip the
  labelled block).

## Operator action required

Re-run the retroactive validator on AI-trade Phase 3 to re-score
existing leaderboard entries against the new parsers:

```bash
python3 tools/retroactive_promotion_validate.py \
    /mnt/c/claude/artifacts/repos/AI-trade \
    --prefix vec_p3_ \
    --reprocess
```

Expected outcomes:
- LA-track entries gain non-zero `sum_fixed` columns and composite
  > 0.3 (Phase 2 formula now applies — table-fallback fills sum_fixed).
- NN-track REJECTED entries (CfC, H3, DLinear, etc.) are correctly
  recognised and NOT appended to the leaderboard (Result-over-Status
  primary pass wins over `Status: PASS ✓`).
- Meta-track entries (`vec_p3_meta_*`) finally produce
  `promotion_validated` instead of `promotion_verdict_missing`
  (multi-prefix probe finds `CAND_meta_*` files).

## What's NOT in v1.4.0

- The v1.3.13 regex extractors are preserved as best-effort fallback
  (per PROMPT "Don't").
- LEADERBOARD.md column schema is unchanged (per PROMPT "Don't"; new
  columns are a v1.5.0 concern).
- The metrics block is NOT mandatory at the engine level — Phase 1/2
  reports without the block continue to parse via the legacy regex
  path.
- `_composite()` in `lib/leaderboard.py` is unchanged (the v1.3.13
  Phase 2/3 detection already handles both paths once metrics are
  populated correctly).

## Smokes

Two new smokes, both green:

- `tests/smoke/run-metrics-block-smoke.sh` — labelled block wins
  over per-bar prose + bold-metadata; LEADERBOARD.md composite uses
  Phase 2 formula (sum_fixed populated); composite > 0.3.
- `tests/smoke/run-multi-prefix-filename-smoke.sh` — both
  `CAND_p3_la_*` (Form 1) and `CAND_meta_*` (Form 2) resolve via
  the candidate probe; both emit `promotion_validated`; neither
  emits `promotion_verdict_missing`.

Registered in `tests/smoke/run-all-smokes.sh`.

## Acceptance

- `pytest tests/ -q` → 891 passed (872 baseline + 19 new tests
  across 5 new test files)
- `bash tests/smoke/run-all-smokes.sh` → 35/43 passed; the 8 failing
  stages (a, b, c, d, e, f, k, l) are pre-existing v0.5-era stage
  smokes flagged as baseline noise in STATUS.md since v1.3.12 — not
  v1.4.0 regressions.

## Commits (in order)

1. `prompt: instruct Claude to include "## Metrics for leaderboard" block in PROMOTION.md (v1.4.0)`
2. `promotion: add _parse_metrics_block helper + Tier 0 verdict parser; metrics block wins over regex fallbacks (v1.4.0)`
3. `promotion: split Tier 4 bold-metadata into Result-first then Status fallback (v1.4.0)`
4. `promotion: multi-prefix strip + candidate-path probe for Phase 3 filename variants (v1.4.0)`
5. `promotion: daily-Sharpe priority cascade; skip per-bar / (bar) variants (v1.4.0)`
6. `promotion: markdown-table fallback for column-based metrics; respects labelled-block + regex priority (v1.4.0)`
7. `smoke: add metrics-block + multi-prefix-filename smokes; register in run-all-smokes (v1.4.0)`
8. `docs: v1.4.0 — STATUS.md + V140_BUILD_DONE.md + VERSION bump`
