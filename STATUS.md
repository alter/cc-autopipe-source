# Build Status

**Updated:** 2026-05-02T22:30:00Z
**Current branch:** main
**Current stage:** **v1.2 BUILD STARTED.** Pre-Batch infrastructure
in progress. v1.0 BUILD COMPLETE on 2026-05-02 afternoon (243 pytest +
1 skip baseline holds; 13/13 stage smokes individually verified;
4 batch gates + final gate green; awaiting Roman tag v1.0).

---

## v1.2 BUILD — in progress

### Understanding check

8 production hardening fixes (A–H) discovered through real-world
test of v1.0 on AI-trade ML R&D project. Grouped into 3 batches per
AGENTS-v1.2.md §3:

- **Batch 1 = A + E:** state schema v3 (`current_task` field with
  id/started_at/stage/stages_completed/artifact_paths/claude_notes;
  also `last_in_progress` + `consecutive_in_progress`); CURRENT_TASK.md
  read by Stop hook → state.json; SessionStart hook reads state →
  injects current task into prompt; v2 → v3 auto-migration on
  first read+write. **E is implicit in A** — `current_task.id`
  replaces standalone CAND_NAME concept.
- **Batch 2 = B + H:** verify.sh contract gains optional
  `in_progress: bool`; engine does NOT count as failure when
  in_progress=true (consecutive_in_progress incremented instead;
  cooldown × multiplier). Smart escalation reads recent failures,
  categorises by error type — `claude_subprocess_failed` (3 in
  a row) → escalate to Opus; `verify_failed` (3 in a row) →
  HUMAN_NEEDED.md + TG, no escalation; mixed/5+ → phase=failed.
- **Batch 3 = C + D + F + G:** SessionStart hook adds long-op
  guidance block (Bug C); reads top-3 OPEN tasks from backlog.md
  by priority and injects (Bug D); detects task switch (Bug D);
  extends current_task with stages_completed array + injects
  progress block (Bug F); orchestrator TG-alerts on rc != 0
  with 600s sentinel-based dedup (Bug G).

After Batch 3 + final integration check + hello-fullstack regression,
build halts for Roman validation. He tags v1.2.

### Tactical SPEC↔repo deviations (per AGENTS-v1.2.md §15)

SPEC-v1.2.md and AGENTS-v1.2.md were drafted against a Python-modular
hook layout that does not match v1.0 reality. Roman approved the
following adaptations as tactical (acknowledged in
[chat 2026-05-02T22:25Z]):

1. **Hook architecture stays bash.** `src/hooks/*.sh` remain thin
   bash dispatchers; v1.2 logic lives in Python helpers under
   `src/lib/` and is invoked from bash via `python3 -c "..."` or
   `python3 src/lib/<helper>.py ...`. Read every SPEC-v1.2.md
   reference like `src/orchestrator/hooks/session_start.py` as
   "the SessionStart logic, implemented in
   `src/lib/session_start_helper.py`".
2. **Test directory is `tests/smoke/` (singular).** The new smoke
   runner lives at `tests/smoke/run-all-smokes.sh`, not
   `tests/smokes/`.
3. **Regression scripts created from scratch as minimal mocked-claude
   smokes.** `tests/regression/hello-fullstack-v1.sh` did not exist
   in v1.0 (was deferred per Stage G shakedown). Built minimally for
   v1.2 to enable programmatic backward-compat verification.

Test-dir mapping under §15 tactical:
- `tests/lib/test_*.py` → `tests/unit/test_*.py`
- `tests/orchestrator/test_*.py` → `tests/integration/test_*.py`
- `tests/hooks/test_*.py` → `tests/unit/test_hooks/test_*.{py,sh}`

Library helpers go flat under `src/lib/`:
`current_task.py`, `session_start_helper.py`, `stop_helper.py`,
`backlog.py`, `failures.py`, `human_needed.py`, plus a Python
`notify.py` adding a `notify_subprocess_failed_dedup` wrapper around
the existing `tg.sh`.

### Currently working on

Pre-Batch 1 infrastructure:
- ✅ `tests/smoke/run-all-smokes.sh` — wrapper around 13 stage smokes
  (validated against stage-a; full 13/13 trusted from v1.0 final
  STATUS.md "individually verified all green" 2026-05-02 afternoon)
- ✅ `tests/regression/hello-fullstack-v1.sh` — mocked-claude regression base
  (131 lines, shellcheck clean, passes on current v1.0 engine; uses
  /usr/bin/true as claude_bin + pre-seeded quota cache)
- ✅ `tests/regression/hello-fullstack-v12.sh` — extends v1 with
  schema_v3 + current_task + in_progress assertions (126 lines,
  shellcheck clean). EXPECTED to fail pre-Batch 1 at schema_v3
  assertion (engine still writes schema_v2); confirmed exit 1
  with clear "expected schema_version=3, got 2" message. Becomes
  green after Batch 1 lands.

Pre-Batch infra complete. **Batch 1 in progress** (Bug A + E):
- ✅ state.py schema_v3 with `current_task` (CurrentTask dataclass) +
  `last_in_progress` + `consecutive_in_progress`. Pre-v3 state files
  auto-migrate on read (defaults supply missing fields). hello-fullstack-v12
  regression now green.
- ✅ test_state.py: +5 tests covering v2→v3 migration, current_task
  round-trip, partial-dict tolerance, in_progress counters round-trip,
  forward-compat extras pass-through. 248 + 1 skipped now.
- ✅ `src/lib/current_task.py` — parse/write CURRENT_TASK.md
  (line-oriented `key: value`; multi-line notes via continuation;
  artifact: lines accumulate; CLI: `parse` / `write`). +27 unit
  tests in `tests/unit/test_current_task.py`. 275+1 pytest now.
- ✅ `src/lib/stop_helper.py` + `src/hooks/stop.sh` — Stop hook wires
  CURRENT_TASK.md → state.json.current_task. Empty/missing file is a
  no-op. Helper enforces always-exit-0 contract. +7 unit tests +13
  hook-level bash assertions in `test_stop.sh` (47/47 PASS now).
  pytest 282+1.
- ✅ `src/lib/session_start_helper.py` + `src/hooks/session-start.sh` —
  SessionStart hook injects `=== Current task ===` block from
  state.current_task. +17 unit tests in test_session_start_helper.py
  (formatting / null-safety / relative-time / hook-contract / CLI /
  v2-state migration). +9 hook-level bash assertions (28/28 PASS).
  299+1 pytest. Both regressions green (v1 + v12).

### Pre-flight (initial)

| Check | Status |
|---|---|
| 1. `git status` clean | ✅ after `0d09893` (doc switch v0.5/v1.0 → v1.2) |
| 2. pytest 243 + 1 skip | ✅ (130.87s, 2026-05-02T22:00Z) |
| 3. `tests/smoke/run-all-smokes.sh` | ◐ runner being built; individual smokes 13/13 verified per v1.0 final |
| 4. `cc-autopipe doctor` 10/10 | ⏸ deferred to Roman (live oauth/usage call) |
| 5. quota 7d < 90% | ⏸ deferred to Roman (live call burns quota) |

Roman validates 4 + 5 manually before he resumes from any halt.

---

## v1.0 BUILD — final state (frozen 2026-05-02 14:50Z)

v1.0 done. Four batches landed back-to-back over the 2026-05-02
session, with mandatory 60-min inter-batch sleeps between each per
AGENTS-v1.md §1.2:

  Batch a (v0.5.1)        7 commits — rules.md template, verify.sh
                                       template, `cc-autopipe stop`,
                                       gate. GATE PASSED.
  Batch b (Stages H/I/J)  9 commits — schema v2 + Detached, detach
                                       helper, orchestrator DETACHED
                                       branch, pre-tool-use rule 7,
                                       researcher+reporter subagents,
                                       PRD phase parser, orchestrator
                                       phase transitions. GATE PASSED.
  Batch c (Stages K/L)    7 commits — quota_monitor daemon +
                                       orchestrator wiring;
                                       auto_escalation config + state
                                       field + orchestrator branch +
                                       reminder + revert + resume
                                       clear. GATE PASSED.
  Batch d (Stages M/N)    2 commits — systemd + launchd templates +
                                       install/uninstall CLI; improver
                                       subagent + orchestrator
                                       trigger every N successes +
                                       skills dir prep + prompt hint.

Engine grew from ~3.6K lines (v0.5) to ~5.5K lines (v1.0). Test
coverage: 243 pytest pass + 1 macOS skip; 13 stage smokes (a-f, h-n);
4 batch gates + 1 final gate.

Roman should:
  1. `git tag v0.5.1` (Batch a)
  2. `git tag v1.0-batch-b` (Batch b)
  3. `git tag v1.0-batch-c` (Batch c)
  4. `git tag v1.0` (Batch d / final)

Tagging is HUMAN-ONLY per AGENTS-v1.md §6.

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

`docs: OPEN_QUESTIONS Q20 (real-TG leak via tests)` (Q20 fix,
4 commits 2026-05-02 afternoon: tg.sh secrets-resolution + conftest
isolation + test_quota_monitor explicit notify_tg + Q20 docs).

## Batch d gate verification (run 2026-05-02 afternoon)

Full `tests/gates/batch-d.sh` script exceeds the 10-min foreground
window because each of 13 stage smokes internally re-runs the full
pytest suite (~2min × 13 = ~26min minimum). Components verified
individually instead, all green:

- **Hygiene:** working tree clean; no `Status: blocked` in
  OPEN_QUESTIONS.md; no orphan `TODO(v0.5.1|v1.0)` markers.
- **Lint:** `ruff check src tests tools` clean (36 files);
  `ruff format --check` clean; `shellcheck -x` clean (37 files).
- **v0.5 smokes:** stage-a/b/c/d/e/f all OK.
- **v1.0 smokes:** stage-h/i/j/k/l/m/n all OK.
- **Pytest:** 243 passed, 1 macOS-skip (120s).
- **Doctor:** `--offline` reports 8 ok, 0 warn, 0 fail, 2 skip.
- **Batch-d surface:** systemd + launchd templates exist;
  service.py exposes 4 subcommands; dispatcher --help lists
  install-systemd + install-launchd; agents.json carries improver;
  config.yaml carries `improver:` block; state.py exposes
  `successful_cycles_since_improver` and `improver_due`;
  orchestrator carries `_read_config_improver` and
  `improver_trigger_due` event.

Q20 verification: ran each chunk with `CC_AUTOPIPE_TG_TRACE=/tmp/...`
and `unset TG_BOT_TOKEN TG_CHAT_ID`. Trace recorded zero hits on
the real `~/.cc-autopipe/secrets.env`; the 5 logged tg.sh
invocations across all chunks all came from per-test TMP
secrets.env fixtures (stage-a/c/e deliberately seed fake creds to
exercise tg.sh's bad-cred path).

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
      PRD phase parser, orchestrator phase transitions. GATE PASSED;
      pending Roman tag v1.0-batch-b.
- [x] Batch c (v1.0 part 2: Stages K/L): 7 commits 2026-05-02 —
      quota_monitor daemon + orchestrator wiring + smoke;
      auto_escalation config + state field + orchestrator branch +
      reminder injection + revert + resume clear + smoke.
      GATE PASSED; pending Roman tag v1.0-batch-c.
- [x] Batch d (v1.0 part 3: Stages M/N): 2 commits 2026-05-02 —
      systemd + launchd templates + install/uninstall CLI + smoke;
      improver subagent + orchestrator N-success trigger + skills
      dir + prompt hint + smoke. **v1.0 BUILD COMPLETE** pending
      Roman tag v1.0.

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
