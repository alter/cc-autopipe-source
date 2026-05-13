# v1.5.8 — gate freshness + active-phase coverage + May-13 recovery

**Build complete.** Three groups, 4 logical commits (+ docs), +6 new
tests across three files (3 unit + 3 integration). Closes the two
v1.5.7 architectural gaps surfaced by AI-trade production within 3
hours of the v1.5.7 restart, plus ships the operator-driven rollback
script for the ~351 fake closures already on disk.

## Motivation

v1.5.7 BACKLOG-WRITE-GATE deployed 2026-05-13. Production result:

- 363 closed tasks in the first 3 hours.
- Only 12 of them produced a new `CAND_*_PROMOTION.md`.
- Spot-check of 5 sampled closures: 5/5 had no PROMOTION file at all.
- Closure descriptions copied the task body verbatim or said
  `SKIP (data unavailable)` / `analytical rejection`.

Two architectural bugs let those 351 closures through the v1.5.7 gate:

1. **GATE-PHASE-COVERAGE**: `audit_and_revert` was wired into
   `recovery._should_resume_done`, which early-returns when
   `state.phase != "done"`. AI-trade sat in `phase=active` while the
   agent did its work; the gate never ran while the agent was the one
   producing fabrications.

2. **STALE-PROMOTION-AMNESTY**: `has_promotion` used
   `pathlib.Path.exists()`. AI-trade had ~520 PROMOTION files from
   earlier fabrication runs in `data/debug/`; cleanup re-opened the
   corresponding rows, the agent re-closed them, and the stale stubs
   from prior runs satisfied the gate.

v1.5.8 closes both gaps and ships a one-shot operator recovery script
for the ~351 closures that already made it to disk.

## GROUP GATE-ALWAYS-RUNS — per-tick sweep

`src/orchestrator/main.py` gains `_gate_sweep_all_projects(user_home,
projects)`, invoked from the top of the outer main loop on every tick
(line ~410 in the post-edit file). It walks `projects.list` and calls
`audit_and_revert` for each project regardless of phase. Per-project
errors are logged and swallowed so a corrupt backlog on one project
never kills the sweep.

The corresponding `audit_and_revert` call inside
`recovery._should_resume_done` is removed — keeping both would split
snapshot ownership between two call sites that don't coordinate, and
the per-tick sweep already covers the done-path. The startup sweep
in `main.py` is kept untouched: it's a single-shot first-fire
guarantee independent of the main loop's first iteration.

Result: the gate now audits closures during `phase=active` (where
agents do most of their work), not only on done→active transitions.

## GROUP STALE-PROMOTION-REJECTED — mtime-based freshness

`backlog_gate.audit_and_revert` now reads the snapshot file's mtime
and compares it against each candidate PROMOTION file's mtime.
PROMOTION is accepted as evidence only when
`pfile.st_mtime >= snap.st_mtime - PROMOTION_FRESHNESS_GRACE_SEC`
(60 seconds).

The 60s grace covers the legitimate close-race where the agent writes
PROMOTION ~immediately before flipping the row to `[x]` in the same
cycle — within that window the two events can land microseconds apart
or in reverse order depending on filesystem stat granularity. Larger
than 60s would re-open a loophole big enough to resurrect long-stale
PROMOTIONs as "fresh evidence".

On first deploy (no snapshot exists), `snap_mtime` falls back to 0,
which makes every PROMOTION trivially fresh — the legacy-amnesty
branch in `audit_and_revert` catches those rows separately (they have
`prev_char == "x"` from the snapshot's first write, so they never
reach the freshness check at all).

The reverted-row event gains three new fields:
- `pfile_mtime` — the file's mtime, or null if no PROMOTION file
- `snap_mtime` — the snapshot's mtime
- `stale` — True iff a PROMOTION file existed but was rejected as old

Distinct `unverified_close_blocked` reason strings distinguish the
"agent wrote nothing" case from the "agent re-closed against a stale
stub" case, so operators / analytics can split the two.

Helper `_has_promotion_file(debug_dir, task_id)` is renamed to
`_find_promotion_file` and returns `Path | None` instead of `bool`,
so the freshness check can read the same path's mtime without a
second filesystem walk.

## GROUP MAY-13-RECOVERY-SCRIPT — operator rollback CLI

New module `src/lib/recovery_revert_fake_closures.py` exposes
`revert_fake_closures(project_path, since_iso, apply=False)` and an
argparse-driven `main()`. It walks `backlog.md`, finds every `[x]`
row, and reverts those whose `CAND_<task>_PROMOTION.md` file is
missing OR has `mtime < since_ts`. Dry-run by default; `--apply`
rewrites the backlog and emits a `revert_fake_closures_applied`
event into the per-project + aggregate logs.

Wired into the existing `state.py` CLI dispatcher as a new
`revert-fake-closures` subcommand. Operator usage:

```bash
sudo systemctl stop cc-autopipe.service
python3 src/lib/state.py revert-fake-closures \
    /mnt/c/claude/artifacts/repos/AI-trade \
    2026-05-13T00:00:00Z                 # dry-run

# review the candidate list (~351 expected for AI-trade)

python3 src/lib/state.py revert-fake-closures \
    /mnt/c/claude/artifacts/repos/AI-trade \
    2026-05-13T00:00:00Z --apply         # actually revert

sudo systemctl start cc-autopipe.service
```

The script is operator-driven on purpose — auto-running on first
startup would risk discarding any May-13 closure that was actually
legitimate. The operator picks the gate-gap window (`since_iso`),
reviews the candidate list, then commits.

## Commits (in order)

1. `orchestrator: invoke audit_and_revert on every sweep tick
   regardless of phase (v1.5.8)` — `_gate_sweep_all_projects` in
   main.py + the matching removal from `recovery._should_resume_done`.
2. `backlog_gate: PROMOTION must be fresh (mtime > snapshot mtime -
   60s grace) (v1.5.8)` — `_is_fresh_promotion` helper, refactored
   `_find_promotion_file`, freshness check + event fields in
   `audit_and_revert`.
3. `state-cli: add revert-fake-closures subcommand for v1.5.7 gate-gap
   recovery (v1.5.8)` — new module + state.py wiring.
4. `tests: cover v1.5.8 gate freshness + phase coverage +
   revert-fake-closures (v1.5.8)` — 6 new tests across three files.

PROMPT estimated ~5 commits; ship is 4 (tests bundled into one
commit instead of three because the three test files share no
production code and were authored together).

## Tests

- `tests/unit/test_gate_freshness.py` — 3 cases (newer ok, older
  reverted with stale=True, within-grace ok).
- `tests/integration/test_gate_phase_coverage.py` — 2 cases (gate
  runs for phase=active, regression check for phase=done).
- `tests/integration/test_recovery_revert_fake_closures.py` — 1 case
  with three input shapes (fresh promo kept, stale promo reverted,
  no promo reverted) + dry-run-then-apply state machine.

Full suite: **+6 passing tests vs v1.5.7 HEAD**, 0 regressions.
Pre-existing 17 baseline failures (uncommitted preflight WIP +
AI-trade real-fixture test_promotion.py noise per V155/V156/V157
BUILD_DONE) reproduce identically on v1.5.7 HEAD with the v1.5.8
changes stashed, confirming they are not v1.5.8 work.

## What's NOT in v1.5.8 (intentional)

- No env-var for `PROMOTION_FRESHNESS_GRACE_SEC` — 60s hardcoded per
  PROMPT's "DON'T set grace_seconds > 5 min" guideline.
- No automatic May-13 revert on first startup — operator-driven only.
  Auto-revert risks data loss if any May-13 closure was actually
  legitimate (PROMPT explicitly bans this).
- No change to snapshot mtime semantics — the snapshot still
  represents "engine's view of backlog at start of cycle". PROMOTION
  freshness is compared against that.
- No retroactive gate run on past `aggregate.jsonl` to surface old
  unblocked closures — `revert-fake-closures` is the rollback path.

## Operator action on AI-trade

```bash
# 1. Stop the engine so no concurrent writes to backlog.md
sudo systemctl stop cc-autopipe.service

# 2. Dry-run the rollback — should report ~350 candidates
python3 /mnt/c/.../cc-autopipe-source/src/lib/state.py \
    revert-fake-closures \
    /mnt/c/claude/artifacts/repos/AI-trade \
    2026-05-13T00:00:00Z

# 3. Review the printed candidate list, then apply
python3 /mnt/c/.../cc-autopipe-source/src/lib/state.py \
    revert-fake-closures \
    /mnt/c/claude/artifacts/repos/AI-trade \
    2026-05-13T00:00:00Z --apply

# 4. Restart
sudo systemctl start cc-autopipe.service
```

Within 30 min of restart, expect:
- `gate sweep` log messages every tick (per-tick coverage active).
- Any new fake closure attempt by the agent triggers
  `unverified_close_blocked stale=True` (if a stub PROMOTION exists
  from prior runs) or `unverified_close_blocked stale=False` (if no
  PROMOTION at all).

The reverted ~350 tasks reopen as actionable backlog items and the
agent must produce verify proof or fresh PROMOTION before any closure
is honoured.
