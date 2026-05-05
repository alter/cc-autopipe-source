# v1.3.2 hotfix complete

Commits: 4 (3 fix groups + STATUS + this doc)
Tests added: +26
Total tests: 573 baseline (v1.3.1) + 26 new = **599 passed, 0 skipped**
All gates green: yes
Smokes: 4 v1.3 + 3 v1.3.1 + 3 v1.3.2 = 10 total, all green
Schema bump: none (v4 unchanged)

## Group summaries

- **RECOVERY-SAFE (sweep respects enforcement state):** v1.3.1
  wired auto-recovery into the main loop, but the sweep was blind to
  v1.3's enforcement loops. A FAILED project sitting in
  `meta_reflect_pending=True` waiting for Claude to write
  `META_DECISION.md` would get reset on the next 30-min sweep,
  clearing the flag and leaving the engine confused about why it
  triggered. Same risk for `knowledge_update_pending` and
  `research_plan_required`.

  New `_should_recover(s) -> (bool, reason)` gate centralises the
  decision. A FAILED project is now skipped if any of:
    - `meta_reflect_pending=True`            → reason=meta_reflect_in_progress
    - `knowledge_update_pending=True`        → reason=knowledge_update_in_progress
    - `research_plan_required=True`          → reason=research_plan_pending
    - phase != "failed"                      → reason=phase=<X>_not_failed
    - `last_activity_at < 1h ago`            → reason=recent_activity
    - `last_activity_at` missing             → reason=no_activity_history

  When skipped on a FAILED project, an `auto_recovery_skipped` event
  with the reason is appended to aggregate.jsonl so Roman can grep
  after autonomy and see which enforcement loops were live. Healthy
  `phase=active` projects don't log skips (would flood the aggregate
  log every 30 min).

  +15 unit tests in `tests/unit/test_recovery_safe.py` cover every
  skip reason (incl. enforcement-outranks-activity-age) plus the
  happy path that confirms clean failed projects still revive.

- **STDERR-LOGGING (daemon traceback capture):** The 4-5 May
  AI-trade autonomy run saw the orchestrator die twice silently.
  Cause: when `cc-autopipe start` runs without `--foreground`,
  Python's stderr is attached to whatever the parent shell handed
  down — once that terminal closes, stderr is gone. Any unhandled
  exception from the long-lived loop disappears with it.

  Fix: when neither `--foreground` nor `CC_AUTOPIPE_NO_REDIRECT` is
  set, redirect Python-level `sys.stderr` / `sys.stdout` AND
  OS-level fds 1/2 to append-mode files inside `user_home/log/`:
    - `orchestrator-stderr.log`
    - `orchestrator-stdout.log`

  `os.dup2` of the OS-level fds means subprocess children (claude
  binary, hook scripts) inherit the redirected streams too — no
  output is lost across spawn boundaries. Append mode preserves
  history across orchestrator restarts. When an existing log
  exceeds `LOG_ROTATE_BYTES` (50 MB) at startup, it shifts to `.1`
  (oldest of `.1` / `.2` / `.3` is dropped) so 14-day autonomy
  doesn't accumulate gigabytes.

  Test-harness escape hatch: `CC_AUTOPIPE_NO_REDIRECT=1` disables
  the redirect for tests that capture subprocess output via
  `subprocess.run(capture_output=True)`. Set session-wide in
  `tests/conftest.py` so existing tests are unaffected; cleared
  explicitly in `test_main_logging.py` for the daemon path tests.

  +10 tests: 4 unit tests for `_rotate_log` (basic shift, full
  chain shift, partial chain, missing-file no-op), 6 subprocess-
  based integration tests (foreground vs daemon, log dir auto-
  create, append across restarts, pre-rotate on oversized seed).
  KILL-9 acceptance gate run manually — orchestrator-stderr.log
  captured the startup line after SIGKILL.

- **TRIGGER-SMOKES (enforcement lifecycle validation):**
  META_REFLECT, research_mode, and knowledge_update enforcement all
  rely on multi-cycle state machines. Unit tests cover individual
  functions; the v1.3 functional smokes cover discrete arms +
  clears. Neither system has actually fired in production — the
  4-5 May AI-trade run never hit 3 same-task verify failures, never
  exhausted the backlog, never completed a verdict stage. First
  activation will happen DURING 14-day autonomy with Roman offline.

  Three new synthetic trigger smokes pin the full lifecycle:

    - `tests/smoke/run-meta-reflect-trigger-smoke.sh` — seed
      CURRENT_TASK + 3 verify_failed entries, trigger META_REFLECT,
      verify SessionStart MANDATORY block injection, simulate
      Claude writing META_DECISION skip, confirm backlog mutated
      to `[~won't-fix]` and state cleared.

    - `tests/smoke/run-research-mode-trigger-smoke.sh` — closed-
      only backlog, `detect_prd_complete` + `maybe_activate_after_cycle`,
      plan target written under `data/debug/RESEARCH_PLAN_*.md`,
      MANDATORY block, backlog mutation without plan quarantines
      to `UNVALIDATED_BACKLOG_*.md`, plan filing clears flag and
      logs `research_plan_filed`.

    - `tests/smoke/run-knowledge-mtime-smoke.sh` — verdict stage
      arms flag, MANDATORY KNOWLEDGE UPDATE block emitted while
      pending, mtime not advancing keeps flag, mtime advance via
      `stop_helper.maybe_clear_knowledge_update_flag` clears flag
      and logs `knowledge_updated_detected`.

  `tests/smoke/run-all-smokes.sh` now resolves both
  `stage-<name>.sh` and `run-<name>-smoke.sh`, and the default run
  includes all v1.3+ hotfix smokes (autonomy, meta-reflect,
  knowledge-enforce, research-plan, stuck-detection,
  recovery-sweep, detach-defaults, plus the 3 new trigger smokes).

## Commit list

```
59ca3a2 tests/smoke: 3 v1.3.2 trigger smokes pin enforcement-loop lifecycles
20aaedd main: redirect stderr/stdout to rotating log when daemonized
d5cc74f recovery: skip projects with active enforcement state
```

## SPEC ↔ repo deviations

1. PROMPT-v1.3.2 §STDERR-LOGGING said "When `cc-autopipe start` is
   invoked without `--foreground`, the orchestrator daemonizes". The
   repo's main.py comments contradict that — `--background` is
   "Reserved" and the orchestrator does not currently self-daemonize.
   Implementation matches the prompt's INTENT (capture stderr in
   non-foreground mode) but doesn't add forking; redirection happens
   in-process via `os.dup2` regardless of whether the parent
   daemonized via `nohup` or systemd.

2. PROMPT-v1.3.2 §STDERR-LOGGING test plan listed `subprocess.Popen`
   with SIGTERM. Implementation uses `subprocess.run` with
   `CC_AUTOPIPE_MAX_LOOPS=1` instead (deterministic, no signal
   timing required). KILL-9 acceptance gate run separately as a
   one-shot script (see acceptance gates below) since pytest's
   process management makes signal testing fragile.

3. PROMPT-v1.3.2 §TRIGGER-SMOKES suggested running
   `cc-autopipe run <project> --once` with mocked claude. Followed
   the existing v1.3 / v1.3.1 smoke pattern instead (Python module
   calls with seeded states + assertions on state.json + aggregate
   events). Faster (~3s per smoke vs. ~30s for full mock-claude
   round-trip) and exercises the same trigger paths. Mock-claude
   integration was deemed redundant since the existing v1.2
   `test_orchestrator_claude.py` already covers the
   prompt-injection round-trip.

4. PROMPT-v1.3.2 acceptance gates listed `pytest 584 passed`. Hit
   599 (more granular RECOVERY-SAFE coverage than the prompt
   estimated +15 for that group rather than +6).

## Acceptance gates

1. **pytest** 599 passed in 308s (target: ~584; over-shot due to
   richer RECOVERY-SAFE coverage).
2. **Hotfix smokes** all 10 green:
   - autonomy / meta-reflect / knowledge-enforce / research-plan
     (v1.3 baseline)
   - stuck-detection / recovery-sweep / detach-defaults (v1.3.1)
   - meta-reflect-trigger / research-mode-trigger / knowledge-mtime
     (v1.3.2)
3. **KILL-9 stderr capture** confirmed via standalone harness:
   spawn orchestrator without `--foreground`, wait for the redirected
   stderr file to appear with the startup line, send SIGKILL, verify
   the file persists with the startup log readable. Result:
   `orchestrator-stderr.log` carried `[orchestrator ...] started; ...`
   plus `claude_settings: stale bypass backup detected ...` (the
   pre-startup warning that caused the 4-5 May silent deaths to
   leave no diagnostic — now captured).
4. **STATUS.md** updated with v1.3.2 section at top.

## Known limitations

- Real-claude smoke not run during the hotfix build (mocked claude
  only). Roman should manually run `cc-autopipe run <project>` once
  against AI-trade after deploying v1.3.2 on host to confirm the new
  enforcement-skip behavior (no false `auto_recovery_attempted`
  during a real META_REFLECT in flight).

- `_redirect_streams_for_daemon` runs once at startup and ignores
  log-file rotation needs DURING a long-running engine. Real-world
  expectation: 50 MB per file × 3 rotations = up to 150 MB per
  stream; if a single autonomy run produces more than 50 MB of
  stderr, the live file just keeps growing. Roman can manually
  truncate / rotate via `cc-autopipe stop && cc-autopipe start`
  if needed (the next startup pre-rotates oversized files). For
  the 14-day window this is unlikely to matter — typical orchestrator
  stderr is dozens of KB per day.

- `CC_AUTOPIPE_NO_REDIRECT` opt-out preserves test compatibility
  but is not documented in `cc-autopipe --help`. Documented inline
  in `main.py` module docstring; tests reference the env var
  directly. Operators should not need to know it exists.

## Smoke test plan for Roman (post-push)

```bash
cd /mnt/c/claude/artifacts/repos/cc-autopipe-source

# Pytest baseline
pytest tests/ -q
# expect: 599 passed

# All hotfix smokes via the wrapper
bash tests/smoke/run-all-smokes.sh \
    autonomy meta-reflect knowledge-enforce research-plan \
    stuck-detection recovery-sweep detach-defaults \
    meta-reflect-trigger research-mode-trigger knowledge-mtime
# expect: 10/10 passed

# Or full smoke run (v0.5/v1.0 stages + all hotfix smokes)
bash tests/smoke/run-all-smokes.sh

# Daemonized logging acceptance gate (manual)
cc-autopipe stop 2>/dev/null
cc-autopipe start  # daemonized, NOT --foreground
sleep 5
ls -la ~/.cc-autopipe/log/orchestrator-stderr.log
tail ~/.cc-autopipe/log/orchestrator-stderr.log
# expect: contains "[orchestrator ...] started; user_home=..." and
#         possibly "disabled global Claude hooks ..."

# Bump VERSION + tag
echo "1.3.2" > src/VERSION
git tag v1.3.2
```

## What this hotfix unlocks

The combined v1.3 / v1.3.1 / v1.3.2 path gives 14-day autonomy with:

- **Stuck detection that ignores legitimate ML training** (B-FIX,
  v1.3.1 regression test).
- **Auto-recovery that respects enforcement state** (B3-FIX +
  RECOVERY-SAFE) — won't clobber meta_reflect / knowledge_update /
  research_plan enforcement loops; won't race against in-flight
  cycles (per-project lock); won't keep mutating after SIGTERM
  (shutdown awareness).
- **Per-project detach defaults** (DETACH-CONFIG) — AI-trade can
  set 12h `max_wait_sec` in its `.cc-autopipe/config.yaml`.
- **Captured tracebacks for silent deaths** (STDERR-LOGGING) — when
  the engine inevitably dies during autonomy, Roman finds a stack
  trace in `~/.cc-autopipe/log/orchestrator-stderr.log` instead of
  "the process is gone".
- **Pre-flight validation of the enforcement loops** (TRIGGER-SMOKES)
  — META_REFLECT / research_mode / knowledge_update first-activation
  bugs caught BEFORE Roman is offline.

Done.
