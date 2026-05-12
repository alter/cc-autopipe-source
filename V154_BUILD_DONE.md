# v1.5.4 — orphan rescan migration guard

**Build complete.** One group, 2 commits, +3 integration tests
(`tests/integration/test_rescan_migration.py`), 1 existing v1.5.3 test
re-scoped.

## Group

### RESCAN-MIGRATION-GUARD

- `recovery.rescan_orphan_promotions` cutoff resolution is now a
  three-tier ladder:
    1. `state.last_cycle_ended_at` (v1.5.3+ healthy path) — preferred.
    2. Missing field → backfill from `aggregate.jsonl`'s most recent
       `cycle_end` event matching this project's name. The backfill
       value is **NOT** persisted to state.json (per PROMPT directive);
       once a real cycle closes, v1.5.3's `_emit_cycle_end` persistence
       takes over naturally.
    3. Aggregate also unavailable / no matching event → `cutoff=0`
       (scan every CAND_*_PROMOTION). The `_is_in_leaderboard`
       membership grep keeps that path idempotent across re-runs.
- New helper `recovery._backfill_cutoff_from_aggregate(project_path)`:
  forward-streams `aggregate.jsonl`, fast-filters lines by literal
  substring (`"event":"cycle_end"` and `"project":"<name>"`) before
  paying the JSON-decode cost, returns the latest `ts` as UNIX seconds
  or None. Honors `CC_AUTOPIPE_USER_HOME` via `state._user_home()` so
  test isolation works without further plumbing.
- Backfill emits a single `orphan_rescan_cutoff_backfilled` event
  (`cutoff_ts=<ISO>` + `source="aggregate.jsonl"`) so an operator
  inspecting startup logs after upgrade can see the migration fire
  exactly once per project. No event when backfill returns None (the
  cutoff=0 path is the silent-but-safe fallback).
- Closes the AI-trade 2026-05-12 gap: with v1.5.3 deployed,
  `vec_p5_la_champion_full_backtest` from SIGTERM-interrupted iter 174
  remained unvalidated because the pre-v1.5.3 `state.json` had no
  `last_cycle_ended_at` and the v1.5.3 rescan silently bailed at
  `if not cutoff_str: return 0`. v1.5.4 picks up the cutoff from
  aggregate.jsonl's iter-173 `cycle_end` and rescues the orphan on
  next restart with no operator surgery.

## What's IN v1.5.4 (vs the original PROMPT)

- PROMPT estimated ~3 tests; build ships 3 new tests + 1 re-scoped
  v1.5.3 test (`test_missing_cutoff_skips_rescan` → 
  `test_missing_cutoff_and_no_promotion_files_returns_zero`, since
  the "no cutoff → return 0" assertion no longer holds; the new
  trivially-safe edge it pins is "no cutoff + no aggregate + no
  PROMOTION files → still returns 0 without crashing").
- Helper uses the existing `_parse_iso_utc` for ISO parsing rather
  than the PROMPT's `datetime.fromisoformat(...replace("Z", "+00:00"))`
  pattern — consistent with the other 5 callsites in the file and
  picks up the same tz-aware semantics.

## What's NOT in v1.5.4 (intentional, per PROMPT "Don't")

- No state.json schema migration / mutate-in-place — backfill happens
  per-call at zero filesystem cost.
- No backfilled-cutoff cache in state.json — once a real cycle_end
  fires, v1.5.3 persistence kicks in naturally.
- No reverse-scan optimisation of aggregate.jsonl — append-only and
  bounded by project lifetime; forward scan is fine.

## Acceptance

- `pytest tests/integration/test_rescan_migration.py tests/integration/test_orphan_promotion_rescan.py -v`
   → 9 passed (3 new migration tests + 6 v1.5.3 rescan tests, including
   the re-scoped one).
- Full `pytest tests/ -q` — baseline 947 passing + new tests, with the
  same 6 pre-existing `test_promotion.py` real-AI-trade-fixture
  failures noted in V153_BUILD_DONE.md as out-of-scope baseline noise.

## Commits (in order)

1. `recovery: backfill cutoff from aggregate.jsonl when last_cycle_ended_at missing (v1.5.4)`
2. `tests: cover migration guard — backfill / fresh / pre-set states (v1.5.4)`

## Operator action on AI-trade

1. `systemctl restart cc-autopipe.service` (no state.json hand-edit
   required — revert any manual `last_cycle_ended_at` backfill if it
   was applied as the v1.5.3 workaround).
2. Startup log should show one
   `orphan_rescan_cutoff_backfilled cutoff_ts=<iter-173-ts>
   source=aggregate.jsonl` event for AI-trade, immediately followed by
   the `promotion_validated origin=orphan_rescan
   task_id=vec_p5_la_champion_full_backtest` event.
3. `LEADERBOARD.md` should gain the missing
   `vec_p5_la_champion_full_backtest` row.

Subsequent restarts (state.last_cycle_ended_at now populated) will not
re-fire the backfill event — v1.5.3's healthy path takes over.
