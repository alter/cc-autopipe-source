# V1313_BUILD_DONE — cc-autopipe v1.3.13

**Built:** 2026-05-11
**Branch:** main (commits below; no remote push, no tag)
**Driver:** AI-trade Phase 3 retroactive validation surfaced two
silent failures in the v1.3.12 baseline. Twelve DA-track tasks closed
with `**Status**: NEUTRAL` but `parse_verdict()` had no canonical
mapping for `NEUTRAL` and `BOLD_METADATA_VERDICT_RE` did not list it,
so the engine logged `promotion_verdict_unrecognized` and dropped the
outcomes. Four `vec_p3_la_*` ML classification tasks parsed cleanly
but `parse_metrics()` only knew about Phase-2 metrics
(`sum_fixed` / `regime_parity` / `max_DD`), so AUC + Sharpe values
were ignored, `_composite()` collapsed to 0.0000, and all four
entries fell below `TOP_N_RETAINED` into `ARCHIVE` — leaving the
top-20 LEADERBOARD.md empty.

## Group summary

### NEUTRAL-VERDICT — recognise Phase 3 DA-track inconclusive outcome

**`src/lib/promotion.py`** (additive, one file):

- `CANONICAL_MAP["NEUTRAL"] = "CONDITIONAL"`. NEUTRAL signals an
  experiment that found no exploitable edge and no clear bug; keeping
  it in CONDITIONAL preserves the backlog door for re-probes in
  different regimes / features.
- `VERDICT_KEYWORD_RE` (tier 1): `NEUTRAL` added to the alternation.
- `BOLD_METADATA_VERDICT_RE` (tier 4): `NEUTRAL` added to the
  alternation — covers bare `**Status**: NEUTRAL` (the canonical
  DA-track closure).
- `ACCEPTANCE_KEYWORD_RE` group 3 (tier 3): `\bneutral\b` added
  alongside `partial` / `mixed` / `conditional`.
- Module docstring (Tier 1 keyword summary + Tier 4 vocabulary note)
  updated to mention NEUTRAL → CONDITIONAL.

Net effect: Phase 3 DA-track tasks with `**Status**: NEUTRAL` now log
`promotion_conditional` instead of `promotion_verdict_unrecognized`.
PROMOTED / REJECTED keyword sets are untouched, so prior parse paths
are unchanged.

**Tests** — `tests/unit/test_promotion_neutral_verdict.py` (4 cases):

- Bare bold-metadata `**Status**: NEUTRAL` → tier-4 fires → CONDITIONAL.
- `## Verdict \n ### NEUTRAL — ...` → tier-1 fires → CONDITIONAL.
- `## Acceptance \n Result is neutral — ...` → tier-3 fires → CONDITIONAL.
- Defensive `CANONICAL_MAP["NEUTRAL"] == "CONDITIONAL"`.

### PHASE3-METRICS — extract AUC + Sharpe; phase-detecting composite

**`src/lib/promotion.py`** (extend `parse_metrics()`):

- New keys `auc` (ROC AUC, [0,1]) and `sharpe` (annualised Sharpe,
  may be negative) added to the output dict.
- New regex for AUC accepts `AUC`, `ROC AUC`, table-cell `| AUC |`,
  and markdown-bold-wrap `**AUC**`. Post-keyword `\**\s*[|:=]?\s*`
  swallows the trailing `**`, the table-cell `|`, and ordinary
  `:` / `=` separators.
- New regex for Sharpe accepts `Sharpe`, `Sharpe ratio`, and the
  markdown-bold-wrap form. Sign is captured so negative Sharpes
  parse correctly.
- The pre-existing DM p-value regex was tightened in the same commit
  to accept hyphen (`p-value`) and markdown-bold-wrap
  (`**DM p-value**`) — the AI-trade Phase 3 canonical form. The
  underscore (`DM_p_value`) and bare (`DM p value`) forms still match.

**`src/lib/leaderboard.py`** (`_composite()` is phase-detecting):

- When `sum_fixed` is non-None → Phase 2 formula
  `0.5*(sum_fixed/1000) + 0.3*(1-regime_parity) + 0.2*(max_dd/-100)`
  (unchanged behaviour; explicit `sum_fixed=0.0` is still a Phase 2
  report, not a fallback trigger).
- Otherwise → Phase 3 formula
  `0.6*auc_adj + 0.3*sharpe_adj + 0.1*dm_adj` with
  `auc_adj = max(0, (auc-0.5)*2)`, `sharpe_adj = clamp(sharpe/3, 0, 1)`,
  `dm_adj = max(0, 1 - dm_p*10)`.
- LEADERBOARD.md column header unchanged (no schema bump). Phase 3
  rows render empty cells for `sum_fixed` / `regime_parity` / `max_DD`
  / `DSR` and populate the `DM_p` and `composite` columns. Composite
  drives ranking, so Phase 3 tasks now occupy correct positions.

**`tools/retroactive_promotion_validate.py`** (operator helper):

- New `--reprocess` flag. When passed, `_load_already_validated()` is
  bypassed (`already_done = set()`) so a task that previously emitted
  `promotion_validated` is re-scored. Combined with `append_entry`'s
  per-task idempotency, the existing leaderboard row is overwritten
  with the corrected metrics + composite. Header line also gains a
  `Reprocess:` indicator for the operator log.

**Tests** — two new files:

- `tests/unit/test_parse_metrics_phase3.py` (6 cases): inline AUC,
  `ROC AUC`, Sharpe ratio, negative Sharpe, the markdown-bold-wrap
  variant covering AUC + Sharpe + DM together, and a regression that
  confirms Phase 2 reports still set `auc`/`sharpe` to None.
- `tests/unit/test_composite_phase3.py` (5 cases): Phase 2 full set,
  Phase 3 full set, Phase 3 random-chance floor (composite = 0.0),
  empty metrics → 0.0, and the explicit `sum_fixed = 0.0` case that
  must remain on the Phase 2 branch.

### Smoke

**`tests/smoke/run-phase3-promotion-smoke.sh`** (new, registered in
`tests/smoke/run-all-smokes.sh` HOTFIX_SMOKES):

- Test 1 — PROMOTED Phase 3 task with `**AUC**: 0.873 /
  **Sharpe ratio**: 1.45 / **DM p-value**: 0.031`.
  Asserts `promotion_validated` in aggregate.jsonl, a LEADERBOARD.md
  row, and composite ≈ 0.6616 (Phase 3 formula).
- Test 2 — `**Status**: NEUTRAL` task. Asserts `promotion_conditional`
  fires, `promotion_verdict_unrecognized` does NOT fire, and
  LEADERBOARD.md is byte-identical to the post-Test-1 state.
- Test 3 — re-write the PROMOTION.md with weaker AUC/Sharpe/DM and
  re-run with `--reprocess`. Asserts the leaderboard row's composite
  is overwritten with the lower expected value.

## Acceptance gates

- `pytest tests/ -q` — 857 baseline + 14 new tests (4 NEUTRAL +
  6 parse_metrics phase3 + 5 composite phase3) — **871 passing**.
- `bash tests/smoke/run-all-smokes.sh` — all green including the new
  `phase3-promotion` smoke.
- `python3 -c '...parse_verdict(**Status**: NEUTRAL)...'` → CONDITIONAL.
- Operator can run
  `python3 tools/retroactive_promotion_validate.py /path/to/ai-trade
   --prefix vec_p3_ --reprocess` to re-score the 4 existing
  `vec_p3_la_*` entries with composite=0.0000.

## Operator action required

After this drop lands in AI-trade, run:

```
python3 tools/retroactive_promotion_validate.py \
    /path/to/ai-trade \
    --prefix vec_p3_ \
    --reprocess
```

…to rewrite the four `vec_p3_la_*` leaderboard entries with the new
AUC/Sharpe-driven composite. The 12 DA-track NEUTRAL tasks will pick
up `promotion_conditional` events on the next regular cycle (no
operator action needed — the engine logs them automatically once
parse_verdict returns CONDITIONAL).

## Commits (oldest → newest)

1. `promotion: add NEUTRAL to CANONICAL_MAP + 3 verdict regexes (v1.3.13)`
2. `tests: cover NEUTRAL verdict across parse_verdict tiers 1/3/4 (v1.3.13)`
3. `promotion: add auc + sharpe to parse_metrics for Phase 3 PROMOTION.md (v1.3.13)`
4. `leaderboard: Phase 3 composite formula (auc/sharpe/dm) when sum_fixed absent (v1.3.13)`
5. `tools/retroactive: add --reprocess flag to re-score validated tasks (v1.3.13)`
6. `tests: cover parse_metrics Phase 3 fields + leaderboard Phase 3 composite (v1.3.13)`
7. `promotion: handle markdown-bold-wrap and hyphen in Phase 3 metric regexes (v1.3.13)`
8. `smoke: add run-phase3-promotion-smoke.sh covering NEUTRAL + Phase 3 composite (v1.3.13)`
9. `docs: v1.3.13 — STATUS.md + V1313_BUILD_DONE.md + VERSION bump`
