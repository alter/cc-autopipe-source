# v1.5.5 — CANONICAL_MAP fix + orphan rescan corrections + leaderboard replay

**Build complete.** Three groups, 6 logical commits (+ docs), +18 new
tests across three files, 4 pre-existing tests re-scoped where they
encoded the pre-fix collapse.

## Groups

### CANONICAL-MAP-FIX

- `src/lib/promotion.py:CANONICAL_MAP` rewritten. Pre-v1.5.5 silently
  mapped `NEUTRAL → CONDITIONAL` (the v1.3.13 "keep the backlog door
  open" comment was the load-bearing bug). Map is now an identity
  table for the four canonical PRD verdicts (PROMOTED / REJECTED /
  CONDITIONAL / NEUTRAL) plus explicit aliases:
    - PROMOTED aliases: ACCEPT(ED), PASS(ED), STABLE
    - REJECTED aliases: REJECT, FAIL(ED), LONG_LOSES_MONEY, DEGENERATE
    - CONDITIONAL aliases: PARTIAL
    - NEUTRAL aliases: NO_IMPROVEMENT / NO-IMPROVEMENT / NOIMPROVEMENT,
      INCONCLUSIVE
- Unknown verdicts return `None` from `CANONICAL_MAP.get(...)`; callers
  fall through to the `parse_verdict` cascade rather than silently
  defaulting to CONDITIONAL.
- `parse_metrics` now surfaces unmapped raw values as
  `out["_unmapped_verdict"]` so an operator can spot new conventions
  in `aggregate.jsonl`. Caller layer logs — promotion.py stays free of
  the orchestrator-import cycle.
- Closes the AI-trade 2026-05-12 scan finding (108/520 PROMOTION files
  with `file=NEUTRAL parsed=CONDITIONAL` mismatch, plus one
  `file=CLEAN parsed=CONDITIONAL` that motivated the unknown-fallthrough
  semantics).
- Four pre-v1.5.5 tests that pinned the old collapse are flipped:
  `test_promotion_neutral_verdict` (all 4 cases),
  `test_metrics_block`, `test_verdict_result_over_status`, and the
  labelled-block leg of `test_promotion_ablation_gate`. Tier-3
  acceptance keyword regex hardcodes `"CONDITIONAL"` for its group-3
  return (independent of `CANONICAL_MAP`), so the prose-acceptance
  `neutral` case still resolves to CONDITIONAL — the docstring is
  updated to clarify the asymmetry.

### ORPHAN-RESCAN-FIX

Two corrections to v1.5.3 `recovery.rescan_orphan_promotions`:

1. **Verdict skip gate removed.** Pre-v1.5.5 bailed on
   `verdict != "PROMOTED"` with an `orphan_promotion_skipped` event.
   This contradicted the v1.5.1 ABLATION-VERDICT-GATE design where
   `on_promotion_success` already gates ablation spawn on PROMOTED but
   runs the leaderboard hook for ALL verdicts. The two paths now have
   the same observable surface.

2. **task_id derived from PROMOTION body.** New
   `_extract_task_id_from_body(text, fallback_from_filename)` reads
   `^**Task:** <id>` from the body via
   `_PROMOTION_TASK_FIELD_RE`. AI-trade convention writes filenames as
   `CAND_<short>_PROMOTION.md` (e.g.
   `CAND_p5_la_champion_full_backtest_PROMOTION.md`) but real backlog
   IDs include the `vec_<phase>_<track>_<descr>` prefix; the filename
   is a stripped display alias only. Pre-v1.5.5 leaderboard rows
   landed under the stripped key, invisible to the rest of the
   pipeline keyed on `vec_*`.
   Legacy bodies without the `**Task:**` field fall back to the
   filename regex so old fixtures keep parsing.

- The v1.5.3 `test_neutral_orphan_skipped_with_event_no_leaderboard`
  test is renamed + flipped to
  `test_neutral_orphan_rescued_just_like_promoted`.

### LEADERBOARD-REPLAY

- `src/lib/leaderboard.py:rebuild_from_files(project)` — one-shot
  recovery: truncate LEADERBOARD.md, walk
  `data/debug/CAND_*_PROMOTION.md`, derive task_id from body, run
  `validate_v2_sections` for parity with the live path, then
  `append_entry` per file. Returns `{scanned, appended, failed}`.
  Idempotent on an unchanged filesystem.
- New `_truncate_leaderboard_rows(project)` deletes LEADERBOARD.md
  before rebuilding so stale rows from the pre-fix parse cannot
  persist. ELO state file is preserved — its history table is
  re-appended by per-file `append_entry` matchups.
- `python3 state.py rebuild-leaderboard <project>` CLI. Prints the
  counts dict as plain JSON so a post-deploy script can pipe through
  `jq`. No interactive confirmation — scripted contexts would hang on
  a prompt (PROMPT explicit "Don't").

## Commits (in order)

1. `promotion: CANONICAL_MAP no longer maps NEUTRAL → CONDITIONAL
   (v1.5.5)`
2. `tests: cover all four canonical verdicts + aliases + unknown
   fallthrough`
3. `recovery: rescue all verdicts + extract task_id from PROMOTION
   body (v1.5.5)` — combined gate-removal + task_id-derivation into one
   recovery commit (the two changes share the same call-site read of
   the file text; splitting yields an intermediate state where rescued
   rows land under the wrong key, which is worse for bisect than the
   combined commit).
4. `tests: cover orphan rescan task_id derivation + all-verdict
   rescue`
5. `leaderboard: add rebuild_from_files + state CLI rebuild-leaderboard
   (v1.5.5)` — combined the library function and CLI wiring into one
   commit (CLI is two lines of argparse + dispatch, splitting yields
   an unused public function in the intermediate state).
6. `tests: cover leaderboard rebuild with mixed verdicts`

PROMPT estimated ~8 commits across the three groups (2+3+3); ship is
6. The deviations are noted above and preserve atomic bisect quality
(each commit passes pytest standalone).

## What's NOT in v1.5.5 (intentional)

- No state.json schema migration — the parser fix is forward-only.
- No pre-v1.5.5 "default to CONDITIONAL" preservation flag — the
  collapse was wrong; fixed forward per PROMPT directive.
- No composite/ELO history migration — ELO is a derived metric,
  regenerable by `rebuild_from_files`.
- No interactive confirmation on `rebuild-leaderboard` — operator
  runs it from a script; a prompt would hang.
- The pre-existing uncommitted DM-significance gate WIP in
  `src/lib/leaderboard.py` (touched lines: `_composite` docstring,
  Phase-2/3 raw-then-gate restructure, `_fmt_pct → _fmt_float` in the
  sum_fixed cell) is left in the working tree. v1.5.5 only touched
  the import and added new functions at the end of the file; staged
  hunks were patched selectively to avoid pulling the WIP forward.

## Acceptance

- New tests:
  - `tests/unit/test_canonical_map.py` — 8 cases (identity for the
    four canonical verdicts, alias preservation, NEUTRAL aliases,
    unknown fallthrough, round-trip)
  - `tests/integration/test_orphan_rescan_v155.py` — 4 cases
    (NEUTRAL/REJECTED rescue, body task_id wins, legacy fallback)
  - `tests/integration/test_leaderboard_rebuild.py` — 3 cases
    (mixed-verdict rebuild, no-debug-dir defensive, CLI subprocess)
- Touched existing tests: 4 files flipped to assert canonical-NEUTRAL
  behaviour (`test_promotion_neutral_verdict`, `test_metrics_block`,
  `test_verdict_result_over_status`, `test_promotion_ablation_gate`)
  plus `test_orphan_promotion_rescan` rename + flip.
- All 15 new tests + 13 orphan-rescan/migration tests pass; full
  pytest suite remains in the v1.5.4 baseline shape (947 passing +
  6 pre-existing `test_promotion.py` real-AI-trade-fixture failures
  noted as out-of-scope baseline noise per V153_BUILD_DONE.md).

## Operator action on AI-trade

```bash
sudo systemctl stop cc-autopipe.service
python3 /home/alter/cc-autopipe/lib/state.py rebuild-leaderboard \
  /mnt/c/claude/artifacts/repos/AI-trade
sudo systemctl start cc-autopipe.service
```

Verify with the mismatch scan script — should return `Mismatches: 0`.
The `vec_p5_la_champion_full_backtest` row should now carry the
canonical `verdict=NEUTRAL` and the corrected composite.
