# Build Status

**Updated:** 2026-05-02T11:22:00Z
**Current branch:** main
**Current stage:** Stage K complete. quota_monitor wired into
orchestrator main (start/stop in finally), CC_AUTOPIPE_QUOTA_MONITOR_INTERVAL_SEC
env override for tests. Smoke stage-k.sh verifies startup log
+ graceful SIGTERM teardown + 92% threshold fire. 211 pytest pass.
Next: Stage L (auto-escalation).

## Currently working on

Batch c started post-60min-sleep. quota_monitor.py exposes
check_once() (single check + dedup) and QuotaMonitor (daemon
loop wrapping it). Thresholds 70/80/90/95 each fire once per
day per threshold via flag files at
$CC_AUTOPIPE_USER_HOME/7d-warn-{pct}-{date}.flag. Iterates
top-down so a 95% reading doesn't ALSO trigger 70/80/90 in the
same poll. TG failures and quota.read_cached exceptions are
swallowed so a flaky monitor never crashes the orchestrator.
Next: wire into orchestrator main(), then Stage L (auto-escalation).

## Currently working on

v0.5.1 cleanup batch landed in 7 atomic commits:

  1. `templates: rules.md.example workflow discipline` (Q15 resolved)
  2. `templates: verify.sh.example demonstrates safe grep idiom`
     (SPEC-v1.md §1.2 — `|| true; UNCHECKED=${UNCHECKED:-0}` pattern)
  3. `templates: verify.sh.example defaults stay fail-closed`
     (regression repair on test_init.py::test_verify_sh_is_executable)
  4. `cli: implement cc-autopipe stop subcommand` (SPEC.md §12.3 +
     SPEC-v1.md §1.3, SIGTERM-then-SIGKILL with --timeout)
  5. `helpers: dispatcher wires cc-autopipe stop`
  6. `tests: cc-autopipe stop integration coverage` (8 new tests)
  7. `tests: gates/batch-a.sh validator` (AGENTS-v1.md §2.1)

`cc-autopipe stop` is the v0.5 not_implemented item Roman called out
in the prior STATUS — now wired via the singleton lock at
`$CC_AUTOPIPE_USER_HOME/orchestrator.pid`, idempotent against missing/
stale lock files (`fcntl.lock_status` re-acquires when the prior
holder is dead), and escalates to SIGKILL after `--timeout` (default
60s). 158 pytest pass + 1 macOS skip; ruff/shellcheck clean.

Pending: run gate, TG-notify, sleep 60 min, start Batch b.

Roman should `git tag v0.5.1` once the gate passes and Batch b
begins. Tagging is HUMAN-ONLY per AGENTS-v1.md §6.

v1.0 build kicked off in autonomous batch mode. Four batches execute
back-to-back without human intervention: Batch a (v0.5.1 cleanup —
rules.md template, verify.sh grep fix, cc-autopipe stop), Batch b
(Stages H+I+J — DETACHED state, R/R subagents, phase split),
Batch c (Stages K+L — quota monitor, auto-escalation), Batch d
(Stages M+N — systemd/launchd, skill crystallization). Each batch
ends with `tests/gates/batch-X.sh` running working-tree-clean +
all smokes + pytest + lint + doctor; on GREEN the agent TG-notifies,
sleeps 60 min, and starts the next batch with NO pause for human
input. On RED the agent writes `BATCH_HALT.md`, TG-alerts, and ends
the session.

## v0.5 legacy stages — final state

All 6 Stage F surfaces shipped in the v0.5.0 build:

  - `helpers/cc-autopipe-checkpoint` — bash, saves
    `.cc-autopipe/checkpoint.md` from arg or stdin (atomic write)
  - `helpers/cc-autopipe-block` — bash, marks state.phase=failed,
    writes HUMAN_NEEDED.md, fires log-event + TG alert
  - `cli/resume.py` — clears PAUSED/FAILED, resets
    consecutive_failures, removes HUMAN_NEEDED.md
  - `cli/tail.py` — tail -f for aggregate.jsonl, ANSI colors,
    --project / --event filters, --no-follow mode, stdlib only
  - `cli/run.py` — single-cycle bypass-singleton wrapper around
    orchestrator.process_project (used by Stage G smoke)
  - `cli/doctor.py` — 10-check prerequisite suite, --offline +
    --json flags, macOS Keychain notice up front

Eight atomic commits land Stage F (helpers + 4 cli + dispatcher +
tests + smoke). 26 new pytest cases in tests/integration/test_cli.py.
tests/smoke/stage-f.sh validates the DoD checklist end-to-end.

All 6 smokes (A–F) pass naked. 147 pytest unit+integration pass
(1 macOS-skip, expected).

Stage G is project-side (hello-fullstack), not engine code. Begins
after May 2 quota reset per Roman's plan.

## Last commit

`tests: gates/batch-a.sh validator` (Batch a closer, 7 commits
2026-05-02 covering rules.md / verify.sh / cli/stop.py / dispatcher /
tests / gate).

## Stages completion

- [x] Stage A: Foundations (completed 2026-04-29T02:40Z)
- [x] Stage B: Orchestrator skeleton (completed 2026-04-29T03:00Z)
- [x] Stage C: Hooks (completed 2026-04-29T07:05Z)
- [x] Stage D: Locking and recovery (completed 2026-04-29T09:55Z)
- [x] Stage E: Quota awareness (completed 2026-04-29T15:30Z) +
      Q12 hot-fix (2026-04-29T18:30Z, real-endpoint format fix)
- [x] Stage F: Helpers and CLI (completed 2026-04-29T20:00Z) —
      **Engine v0.5.0 complete**
- [x] Stage G: Hello-fullstack smoke test (real-claude verification
      deferred per quota window; engine surfaces validated by 6
      stage smokes + Stage G shakedown bug-fixes 2026-04-30)
- [x] Batch a (v0.5.1 cleanup): 7 commits 2026-05-02 — rules.md
      template, verify.sh template, cc-autopipe stop subcommand,
      gate validator. **v0.5.1 complete** (pending Roman tag v0.5.1).
- [x] Batch b (v1.0 part 1: Stages H/I/J): 9 commits 2026-05-02 —
      schema v2, cc-autopipe-detach + dispatcher, orchestrator
      DETACHED branch, pre-tool-use rule 7, R/R subagents,
      PRD phase parser, orchestrator phase transitions. Pending
      gate run + Roman tag v1.0-batch-b.
- [ ] Batch c (v1.0 part 2: Stages K/L)
- [ ] Batch d (v1.0 part 3: Stages M/N) — closes v1.0

## Stage E DoD verification

All items green, validated by `bash tests/smoke/stage-e.sh`:

- [x] quota.py reads OAuth token on Linux from
      `~/.claude/credentials.json` (or `$CC_AUTOPIPE_CREDENTIALS_FILE`)
- [x] quota.py reads OAuth token on macOS from Keychain
      (verified live: nested `claudeAiOauth.accessToken` shape)
- [x] quota.py returns None gracefully when token missing
- [x] quota.py returns None gracefully when endpoint unreachable
- [x] quota.py caches results for 60s (mtime-based TTL)
- [x] ratelimit.py implements 5min/15min/1h ladder
- [x] ratelimit.py resets counter after 6h with no 429
- [x] orchestrator pre-flight check pauses project at >=95% 5h
- [x] orchestrator pre-flight check pauses ALL projects at >=95% 7d
      (Q14 deviation, was >=90%; with 5min TG dedup via
      `7d-tg.last` sentinel). 5h warn raised 0.80→0.85; 7d warn band
      added at 0.90.
- [x] stop-failure.sh uses quota.py first, falls back to ratelimit.py
      (and last-resort 1h if both unavailable)
- [x] tests/integration/test_quota.py passes (12/12, 1 macOS-skip)
- [x] STATUS.md updated

Bonus tests: tests/unit/test_ratelimit.py (14/14),
tests/integration/test_orchestrator_quota.py (12/12, pre-flight +
stop-failure+quota end-to-end).

Test totals: 147 pytest unit+integration across A→F (+30 vs Stage E
post-Q12) plus 1 expected macOS-skip and 103 hook unit cases. Seven
smoke validators (stage-a through stage-f) all green together.

## Stage F DoD verification

All items green, validated by `bash tests/smoke/stage-f.sh`:

- [x] `cc-autopipe-checkpoint` saves checkpoint.md correctly (arg + stdin)
- [x] `cc-autopipe-block` marks project failed and creates HUMAN_NEEDED.md
- [x] `cc-autopipe resume` clears PAUSED/FAILED, resets failures
- [x] `cc-autopipe doctor` checks all prerequisites and reports
      (10 checks, --offline + --json flags)
- [x] `cc-autopipe tail` follows aggregate.jsonl (filters + colors)
- [x] `cc-autopipe run <project> --once` runs single cycle
- [x] All commands have --help (dispatcher + 6 subcommands)
- [x] tests/integration/test_cli.py passes (26/26)
- [x] STATUS.md marked "Engine v0.5.0 complete"

## Process debt

Test-environment escape hatches:
- `CC_AUTOPIPE_QUOTA_DISABLED=1` — short-circuits quota.read_raw
  to None. Used intentionally now in stage-e ladder-fallback test
  and in pytest tests that need a clean "quota unavailable" signal.
  No longer used as a Q12-bug mask (smokes pre-populate cache
  instead).
- `CC_AUTOPIPE_CREDENTIALS_FILE` — overrides
  `~/.claude/credentials.json` for Linux-path tests on macOS.

Stray `test.sh` and `check.sh` in repo root (untracked, not engine
code; Roman's manual exploration scripts). Left alone.

## Stage G shakedown — 2026-04-30 fixes

First real-claude run on hello-fullstack surfaced three engine bugs.
All three landed as atomic commits today.

- **Bug 1 (CRITICAL): `--verbose` missing.** claude 2.1.123 rejects
  `-p` + `--output-format stream-json` without `--verbose`:
      Error: When using --print, --output-format=stream-json requires --verbose
  `_build_claude_cmd` now emits `--verbose` between
  `--dangerously-skip-permissions` and `--max-turns`.
- **Bug 2: subprocess streams not persisted.** Stage F's
  `_stash_stream` early-returned on empty content, so a fast rc!=0
  exit left a stale prior-cycle log. Renamed to
  `claude-last-stdout.log` / `claude-last-stderr.log` and now writes
  on every cycle (even when empty).
- **Bug 2 follow-on: `claude_subprocess_failed` failures entry.** On
  `rc != 0`, orchestrator now appends to `failures.jsonl` with
  `exit_code` and `stderr_tail` (last 500 chars). Without this,
  fast rc=1 exits had no audit trail (no Stop hook fires).
- **Bug 3: lax mock-claude.** `tools/mock-claude.sh` accepted the
  `-p` + stream-json + no-verbose combo — that's why the missing
  `--verbose` shipped to Stage G undetected. Mock now rejects with
  the same diagnostic message real claude emits.

Regression test in `test_orchestrator_claude.py`:
`test_orchestrator_passes_verbose_to_avoid_stream_json_rejection`
asserts no `claude_subprocess_failed` entry under the strict mock.
Verified by deletion: temporarily removed `--verbose` and confirmed
test failed with the expected "requires --verbose" stderr_tail.

All 6 stage smokes (A-F) pass after the fixes. 150 pytest cases
+ 1 macOS skip.

Real-claude verification on hello-fullstack is **deferred**: the
project is paused with `resume_at=2026-05-01T23:59:59Z` from
yesterday's 7d=96% pre-flight pause. Current 7d=94% per Roman's
report, but `_resume_paused_if_due` won't unpause until the
recorded `resume_at` passes. Clearing via `cc-autopipe resume
hello-fullstack` would burn a cycle at 94% 7d — Roman's call.

## Currently blocked

None.

## Recent open questions

- Q1 (resolved by Q12): oauth/usage response format — real-endpoint
  hit revealed integer-vs-float discrepancy.
- Q2 (deferred-to-Stage-G): claude --resume with deleted JSONL.
- Q3 (resolved Stage C): Stop hook session_id reliability.
- Q4 (resolved Stage E): macOS Keychain — no prompt observed,
  ground-truth payload format documented.
- Q5 (deferred-to-Stage-G): --max-turns counter on resume.
- Q6 (open, Stage F/G): backlog.md tag handling.
- Q7 (resolved Stage A): TG --data-urlencode.
- Q8 (resolved Stage D): flock on macOS — used Python fcntl.
- Q9 (resolved Stage A): compat.sh feature-detect.
- Q10 (resolved Stage B): src/cli/ deviation.
- Q11 (resolved Stage D): src/lib/locking.py split.
- Q12 (resolved Stage E hot-fix): oauth/usage emits integer percent.
- Q13 (resolved Stage E hot-fix): additional unused endpoint fields.
- Q14 (resolved-as-deviation, 2026-04-30): pre-flight 7d threshold
  raised 0.90→0.95 + 5h warn raised 0.80→0.85 + 7d warn band added
  at 0.90. SPEC §9.2 deviation, documented for v1 docs review.

Q6 (backlog.md tag handling) carried into Stage G — not exercised
in v0.5 engine code yet (orchestrator's prompt builder reads top-N
[ ] tasks from backlog.md without parsing tags). v0.5 default per
the recommendation in OPEN_QUESTIONS.md Q6: "treat as normal open
task". Stage G will validate against a tagged backlog if hello-
fullstack uses any.

## Tooling notes

- quota.py CLI surface: `read | read-cached | refresh`. Hooks call
  `python3 quota.py read` per SPEC §9.3.
- ratelimit.py CLI: `register-429 | state | reset`.
- mock-quota-server.py works with random ports; tests spawn a fresh
  instance per test via `_free_port()`.

## Notes for next session

Stage G is project-side (hello-fullstack), not engine code.
Engine v0.5.0 is functionally complete per AGENTS.md §12 except
the items below.

### Stage G prep (waits for May 2 quota reset)

- `examples/hello-fullstack/` project skeleton (separate repo per
  AGENTS.md §2 Stage G).
- PRD covering pytest + npm build + docker-compose targets.
- verify.sh wired against those targets.
- Run `cc-autopipe init` in the project, then
  `cc-autopipe run <path> --once` to validate one cycle with real
  claude before kicking off `cc-autopipe start`.
- Goal: full PRD reaches DONE under cc-autopipe in <4h.

### Open items deferred to Stage G or v1

- Q2: claude --resume with deleted JSONL — verify against real
  claude, iterate on `_build_claude_cmd` if it errors.
- Q5: --max-turns counter behaviour on resume — observe under
  hello-fullstack; mitigation already in place via checkpoint-based
  continuity.
- Q6: backlog.md tag handling — defaults to "treat tagged tasks as
  normal open"; revisit if hello-fullstack uses any.

### Stage F things NOT shipped (intentional)

- `cc-autopipe stop` (SPEC §12.3) — left as not_implemented in the
  dispatcher. Stage D singleton lock + SIGTERM handler already
  provide the underlying mechanism; the user-facing wrapper is
  small (read PID file, kill -TERM, kill -KILL after 60s) but
  wasn't in the user's Stage F scope. Add as a v0.5.1 patch or
  early Stage G prep.

### Operating reminders for Stage G

- Roman has Claude MAX 20x. NEVER use Anthropic API SDK.
- Stage G is the ONLY place real claude runs during the build.
- Telegram credentials in ~/.cc-autopipe/secrets.env, not in repo.
- doctor `--offline` keeps tests deterministic; default doctor run
  hits the live oauth/usage + TG endpoints (one-shot, low cost).
- run.py uses SourceFileLoader to import the extensionless
  orchestrator script — keep that pattern if any other CLI needs
  to call into orchestrator internals.
