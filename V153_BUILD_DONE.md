# v1.5.3 — orphan PROMOTION rescan + leaderboard timestamp + rc<0 visibility

**Build complete.** Three groups, 5 commits, +14 tests (8 unit + 6 integration).

## Groups

### ORPHAN-PROMOTION-RESCAN

- `state.State` gains `last_cycle_ended_at: Optional[str]`. Set by
  `_emit_cycle_end` on every successful close path (normal cycle end
  + transient-retry close); deliberately NOT set by the SIGTERM-flush
  path in `main.py`, so an interrupted cycle's PROMOTION files stay
  mtime-newer than the cutoff.
- New `recovery.rescan_orphan_promotions(project_path)`: iterates
  `data/debug/CAND_*_PROMOTION.md` whose mtime is newer than
  `state.last_cycle_ended_at`, skips files already present in
  `LEADERBOARD.md` (idempotent), then runs the same
  `parse_verdict → validate_v2_sections → on_promotion_success`
  pipeline that `_post_cycle_delta_scan` runs. NEUTRAL / CONDITIONAL /
  REJECTED verdicts log an `orphan_promotion_skipped` event and move
  on (per v1.5.1 ablation gate — they were never destined for the
  leaderboard).
- Wired into `orchestrator.main`: at startup, before the main loop,
  every project in `projects.list` gets one rescan. Per-project errors
  are caught and logged so a single corrupt project never aborts boot.
- Wired into `recovery.maybe_resume_done`: the existing 30-min
  done-resume sweep now opportunistically rescans first, so projects
  parked in `phase=done` still benefit from late-arriving rescues.
- Closes the AI-trade 2026-05-12 gap where
  `vec_p5_la_champion_full_backtest`'s PROMOTION file (written during
  the SIGTERM-interrupted iter 174) never landed in LEADERBOARD.md.

### LEADERBOARD-TIMESTAMP-FIX

- `_write_leaderboard_md` now accepts an optional
  `last_updated: datetime | None` and falls back to
  `datetime.now(timezone.utc)` when omitted.
- `append_entry` captures one wall-clock `now` and threads it through
  both `_write_leaderboard_md` calls (live table + rollover archive),
  guaranteeing the live header, archive header, and subsequent
  `leaderboard_updated` event share a single timestamp.
- The header was already rewritten on every append in v1.5.2 — the
  observed staleness in AI-trade was a symptom of orphan PROMOTIONs
  (the events fired but `on_promotion_success → append_entry` was
  never called for those task_ids). ORPHAN-PROMOTION-RESCAN is the
  load-bearing fix; this change makes the timestamp deterministic
  for tests and consistent across the live/archive pair.

### CYCLE-RC-NEGATIVE-VISIBILITY

- New `cycle._emit_cycle_end` helper consolidates both `cycle_end`
  emit sites in `process_project` (normal close + transient-retry
  close). When `isinstance(rc, int) and rc < 0`, the event payload
  gains `killed_by_signal=<NAME>` derived from
  `signal.Signals(-rc).name`, with a `signal_<N>` fallback for
  unknown numbers.
- String rc values (e.g. `"interrupted"` from the SIGTERM flush in
  `main.py`) are deliberately untouched — that path does not call
  `_emit_cycle_end`.
- Closes the AI-trade iter 182/183 post-mortem gap where
  `rc=-1 score=null` events lacked the `SIGHUP` context.

## What's IN v1.5.3 (vs the original PROMPT)

- PROMPT estimated ~7 tests; the build ships 14. Defense-in-depth:
  the orphan-rescan path got 6 cases (PROMOTED rescue, pre-cutoff
  skip, idempotent re-scan, NEUTRAL skip-with-event, missing cutoff,
  missing data/debug), the signal-annotation path got 6 cases
  (named signal, no annotation for rc>=0, unknown-signal fallback,
  string-rc passthrough, state persist on/off), and the timestamp
  path got 2.
- `_emit_cycle_end` is a real helper, not just an inline tweak.
  PROMPT illustrated the change inline at each emit site; the
  helper unifies both call sites + their state-persist semantics.

## What's NOT in v1.5.3 (intentional)

- No retroactive rescan for PROMOTION files older than the cutoff at
  initial v1.5.3 deploy. On first restart after upgrade,
  `state.last_cycle_ended_at` is None for every project (the field
  is new) → `rescan_orphan_promotions` returns 0 across the board.
  After one clean cycle per project, the field populates and any
  subsequent orphan gets rescued. Operator can manually `touch`
  an orphan PROMOTION file to set its mtime > the freshly-set
  cutoff if they want the v1.5.3 rescue to pick it up on the next
  sweep.
- No CLI force-rescan command. Per the PROMPT YAGNI directive, the
  per-sweep + per-startup invocations cover all real recovery cases.
- No SPEC.md update — no SPEC.md exists in this build repo.

## Acceptance

- `pytest tests/ -q` — 947 passed (was 933 baseline). +14 new tests:
  - 2 in `tests/unit/test_leaderboard_timestamp.py`
  - 6 in `tests/unit/test_cycle_end_signal_annotation.py`
  - 6 in `tests/integration/test_orphan_promotion_rescan.py`
  The 6 pre-existing real-AI-trade-fixture failures in
  `test_promotion.py` remain baseline noise unrelated to v1.5.3
  scope.
- `bash tests/smoke/run-all-smokes.sh` — 37/46 passed. The
  v1.5.2 baseline was 38/46; the additional failing stage
  `phase3-promotion` is independent of v1.5.3 — it is caused by an
  uncommitted `src/lib/leaderboard.py` DM-significance-gate WIP in
  Roman's working tree at session start, which applies a `× 0.5`
  composite penalty when `dm_p_value ≥ 0.05` and breaks the smoke's
  `composite=0.291` expectation (becomes 0.1455). v1.5.3 commits do
  not include or affect that WIP.

## Commits (in order)

1. `cycle: emit_cycle_end helper -- signal annotation + last_cycle_ended_at persist (v1.5.3)`
2. `recovery: add rescan_orphan_promotions for SIGTERM-survived PROMOTION files (v1.5.3)`
3. `orchestrator: invoke orphan-promotion rescan on startup (v1.5.3)`
4. `leaderboard: explicit last_updated param so timestamp refresh is testable (v1.5.3)`
5. `tests: cover v1.5.3 -- orphan rescan, leaderboard timestamp, cycle_end signal annotation`

## Operator action on next deploy

- Restart `cc-autopipe.service`. The startup banner will now emit
  one `[orchestrator <ts>] <project>: rescued N orphan PROMOTION(s) on
  startup` per project where N>0. On first boot after upgrade N is
  always 0 (no cutoff established yet); subsequent boots may surface
  the actual rescue count.
- To force-rescue an existing orphan that pre-dates the new cutoff,
  `touch` the PROMOTION file so its mtime advances past
  `state.last_cycle_ended_at`, then wait one sweep cycle (≤30 min)
  or restart.
