# Build Status

**Updated:** 2026-04-29T07:05:00Z
**Current branch:** main
**Current stage:** C complete; D (Locking and recovery) up next

## Currently working on

Stage C is complete. All 11 DoD items in AGENTS.md §2 Stage C are
green, plus the user-added Stage C constraint (orchestrator now
spawns `claude -p` via subprocess.Popen). Total atomic commits this
stage: 14.

Stage D (Locking and recovery) per AGENTS.md §2:
- Singleton orchestrator lock at `~/.cc-autopipe/orchestrator.pid`
  with stale-PID detection (`kill -0`)
- Per-project lock at `<project>/.cc-autopipe/lock` with heartbeat
  timestamp (10s refresh, stale if >120s)
- Crash recovery on `cc-autopipe start` — release dead locks,
  resume from saved state.json
- Test scenarios: kill -9 mid-cycle restart; second `cc-autopipe
  start` exits with "already running"
- Q2 (`claude --resume` behaviour when JSONL deleted), Q5
  (--max-turns counter on resume), Q8 (flock on macOS) are all in
  Stage D's investigation lane.

## Last commit

`tests: integration tests for orchestrator + claude + hooks pipeline`
(deb8af4) → followed by mock fix (`2fc8f86`), pgroup kill fix
(`167474d`), Stage C smoke + STATUS update (this commit pending).

## Stages completion

- [x] Stage A: Foundations (completed 2026-04-29T02:40Z)
- [x] Stage B: Orchestrator skeleton (completed 2026-04-29T03:00Z)
- [x] Stage C: Hooks (completed 2026-04-29T07:05Z)
- [ ] Stage D: Locking and recovery
- [ ] Stage E: Quota awareness
- [ ] Stage F: Helpers and CLI
- [ ] Stage G: Hello-fullstack smoke test

## Stage C DoD verification

All items checked, validated by `bash tests/smoke/stage-c.sh`:

- [x] session-start.sh outputs valid context summary, exits 0
      (test_session_start.sh: 17/17)
- [x] pre-tool-use.sh blocks each rule from §10.2 SPEC.md, one test
      per rule (test_pre_tool_use.sh: 34/34, covers all 6 rules
      with multiple patterns each, plus benigns and the structured
      log format)
- [x] pre-tool-use.sh allows benign actions
- [x] stop.sh runs verify.sh, parses JSON, updates state
      (test_stop.sh: 31/31)
- [x] stop.sh handles malformed verify output (logs to
      failures.jsonl + aggregate, increments failures)
- [x] stop.sh handles verify timeout — implemented via `timeout 60`
      wrapping verify.sh; full 60s timeout exercise deferred to
      Stage G to avoid 60s sleep in unit tests
- [x] stop-failure.sh on rate_limit error transitions to PAUSED
      (test_stop_failure.sh: 20/20)
- [x] stop-failure.sh on other errors increments
      consecutive_failures
- [x] All hooks pass shellcheck (19 bash files clean)
- [x] tests/unit/test_hooks/ passes (uses tools/mock-claude.sh)
- [x] STATUS.md updated

Plus user-added:
- [x] orchestrator now spawns `claude -p` via subprocess.Popen with
      wall-clock timeout, --resume support, agents.json wiring,
      model from config.yaml. Process group killed on
      timeout/shutdown so child processes can't keep pipes open
      (verified by test_wall_clock_timeout_kills_hung_claude).
- [x] tests/integration/test_orchestrator_claude.py covers full
      cycle, DONE/FAILED/PAUSED transitions, session_id round trip,
      timeout enforcement, --resume on second cycle (8/8).

Test totals: 62 pytest unit + integration + 102 hook unit cases
(17+34+31+20). All green.

## Process debt

Q10 (resolved): src/cli/ deviation from SPEC §5.1 documented and
accepted. This stage adds:

- `src/orchestrator` now reads config.yaml's `models.default` line
  via a hand-rolled scanner — no PyYAML dep per AGENTS.md §14
  ("Add new dependencies without OPEN_QUESTIONS.md entry first").
  This is consistent with §14 since stdlib suffices; no Q entry
  needed.

## Currently blocked

None.

## Recent open questions

- Q1 (open, Stage E): oauth/usage endpoint format
- Q2 (open, Stage D): claude --resume behaviour when JSONL deleted
- Q3 (resolved 2026-04-29 Stage C): Stop hook session_id reliability
  — verified via tools/mock-claude.sh DUMP_INPUT facility +
  test_orchestrator_claude.py round-trip case. Real-claude
  verification deferred to Stage G; mitigation in stop.sh tolerates
  null session_id without crashing.
- Q4 (open, Stage E): macOS Keychain prompt
- Q5 (open, Stage D): --max-turns counter reset on resume
- Q6 (open, Stage C/B): backlog.md tag handling — not exercised in
  Stage C; defer to Stage G.
- Q7 (resolved Stage A): TG --data-urlencode
- Q8 (open, Stage D): flock on macOS
- Q9 (resolved Stage A): compat.sh feature-detect

## Tooling notes

- macOS host. brew bash 5.3 + shellcheck 0.11 + .venv with python3.13
  + pytest 9 + ruff 0.15.
- Three smoke validators now: stage-a.sh, stage-b.sh, stage-c.sh.
  Each exits non-zero on first failure with a coloured PASS/FAIL
  summary.
- shellcheck invoked with `-x` so source-tracking through `_lib.sh`
  resolves cleanly.

## Notes for next session

- Stage D scope: singleton + per-project locks. Both per SPEC §8.3.
  Use `flock` from util-linux (Linux) or `shlock` / fcntl (macOS).
  Q8 will resolve the macOS approach during implementation.
- Crash recovery story per SPEC §8.4: detect stale singleton lock
  (`kill -0` against the recorded PID), release per-project locks
  whose heartbeat is >120s old or whose PID is dead.
- Stage D adds a "second `cc-autopipe start` exits with 'already
  running'" code path. Test via two parallel subprocess.Popen calls.
- Q2 (`--resume` with deleted JSONL) is on the Stage D agenda. Test
  by writing a state.json with a fabricated session_id, running one
  cycle through real (or mock) claude, observing the error path.
- Stage D should also seed `~/.cc-autopipe/orchestrator.pid` so
  status.py's "Orchestrator: running (PID NNN)" line goes live —
  it's currently always "not running" because nothing writes it.
- Roman has Claude MAX 20x. NEVER use Anthropic API SDK. Use
  `tools/mock-claude.sh` for hook + integration tests.
- Telegram credentials live in `~/.cc-autopipe/secrets.env`, not in
  the repo.
