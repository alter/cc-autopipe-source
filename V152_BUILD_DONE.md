# v1.5.2 — state CLI unpause + SIGTERM cycle_end flush

**Build complete.** Two groups, 4 commits, +8 tests (3 unit + 5 integration).

## Groups

### STATE-CLI-CLEAR-PAUSED

- `src/lib/state.py` now exposes `clear_paused(project_path)` and a
  matching `python3 state.py clear-paused <project>` CLI subcommand.
  Symmetric inverse of `set-paused`.
- Phase routes:
  - `phase=paused` + `prd_complete=False` → `phase=active, paused=None`
  - `phase=paused` + `prd_complete=True`  → `phase=done,   paused=None`
- Idempotent: clearing a non-paused project prints
  `already not paused (phase=<current>)` and writes nothing (no state
  rewrite, no `paused_cleared` event).
- Emits a `paused_cleared` event with `new_phase=<active|done>` when
  state actually changes.
- `set-paused` signature and behaviour unchanged.

### CYCLE-END-ON-SIGTERM

- `src/orchestrator/main.py` SIGTERM/SIGINT handler now calls
  `_flush_in_flight_cycles(user_home)` BEFORE `set_shutdown(True)`.
- For each project in `projects.list`, the flush scans
  `.cc-autopipe/memory/progress.jsonl` for an unmatched `cycle_start`
  (i.e. the last `cycle_start` event index is greater than the last
  `cycle_end` event index, or there is no `cycle_end` at all). If found,
  appends one synthetic
  `cycle_end iteration=<state.iteration> phase=<state.phase>
  rc=interrupted score=null interrupted_by=sigterm` event via the
  standard `state.log_event` path (writes both per-project
  `progress.jsonl` and `~/.cc-autopipe/log/aggregate.jsonl`).
- `rc="interrupted"` (string) is deliberately distinct from any
  subprocess exit code (0/1/137/124/...). Downstream analytics can
  filter shutdown artifacts cleanly.
- Best-effort throughout. Three layers of guard:
  - `_has_in_flight_cycle` returns `False` on missing or unreadable
    progress.jsonl and tolerates per-line `JSONDecodeError`.
  - `_flush_in_flight_cycles` skips per-project on any exception
    (logs and continues), so one corrupt project never blocks the
    others.
  - The handler itself wraps the call in a final try/except — a
    signal-handler raise would terminate the orchestrator without
    `set_shutdown(True)` and break the graceful-shutdown path.
- Does NOT block waiting for the claude subprocess to exit (systemd
  reaps the process tree). Does NOT attempt to RESUME the interrupted
  cycle on next startup. Claude session-id resume continues to handle
  work continuity; the synthetic cycle_end only closes the telemetry
  record.

## Background

v1.5.0 production observed: orchestrator received SIGTERM mid-iteration
168 on `AI-trade`, logged `shutting down at next safe point`, the
safe-point wait exceeded systemd's `TimeoutStopSec=60s`, SIGKILL forced.
Iteration 168 work survived (claude session resume picked it up next
startup as iter 169), but iter 168 had no `cycle_end` event written:
the per-cycle leaderboard / activity / aggregate analytics had a
dangling `cycle_start`. On regular restarts this becomes a habitual
telemetry gap. Reactive policy work in v1.5.0 made restart cadence
higher (paused/resumed projects), amplifying the impact.

The `clear-paused` ergonomics issue is older: pre-v1.5.2 the only way
to undo a manual pause (or one written by
`manual_phase_done_loop_workaround`) was hand-editing `state.json`
with python or jq. `set-paused` shipped a long time ago without its
inverse.

## Operator action required

None. Restart `cc-autopipe.service` to pick up the new handler. The
next time systemd sends SIGTERM, `cycle_end rc=interrupted` will land
before SIGKILL (assuming the handler runs at all — Python signal
handlers run between opcodes, so a CPU-bound C extension could still
beat the flush; in practice the orchestrator is I/O-bound on
subprocess.Popen.wait).

## What's NOT in v1.5.2

- No new state.json field. We deliberately did not add `last_cycle_ended_at`
  — the progress.jsonl tail-scan is already a more reliable signal
  (state.last_cycle_started_at is set but never cleared on a clean
  cycle_end, so it can't be used as a sentinel directly).
- No mid-cycle telemetry — the synthetic `cycle_end` carries
  `score=null`, not whatever Claude was doing. Treat it as "iteration
  N ended without a verdict."
- No retroactive flush for pre-v1.5.2 dangling cycle_starts. Operator
  may delete or annotate them with a one-off script if downstream
  analytics complain.
- No SPEC.md update — no `SPEC.md` exists in this build repo (PROMPT
  reference is stale; earlier v1.5.x builds did not update SPEC.md
  either).
- No config flag for the flush — it's always on. The cost is one
  state.read + one log_event per project per shutdown; negligible.

## Acceptance

- `pytest tests/ -q` — 933 passed (was 925 baseline). +8 new tests:
  3 `tests/unit/test_state_cli_clear_paused.py` + 5
  `tests/integration/test_sigterm_cycle_end_flush.py`. The 6
  pre-existing real-AI-trade-fixture failures in `test_promotion.py`
  remain baseline noise unrelated to v1.5.2 scope (documented in
  V151_BUILD_DONE.md).
- `bash tests/smoke/run-all-smokes.sh` — 38/46 passed; identical to
  v1.5.1 baseline. The 8 failing stages a/b/c/d/e/f/k/l are v0.5-era
  baseline noise unchanged across the v1.x line.

## Commits (in order)

1. `state: add clear-paused CLI subcommand (v1.5.2)`
2. `tests: cover clear-paused — active/done branches + idempotent no-op (v1.5.2)`
3. `orchestrator: SIGTERM/SIGINT handler flushes interrupted cycle_end (v1.5.2)`
4. `tests: cover sigterm flush — in-flight emits, idle skips, corrupt state safe (v1.5.2)`
