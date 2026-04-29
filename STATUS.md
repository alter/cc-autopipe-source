# Build Status

**Updated:** 2026-04-29T09:55:00Z
**Current branch:** main
**Current stage:** D complete; E (Quota awareness) up next

## Currently working on

Stage D is complete. All 6 DoD items in AGENTS.md §2 Stage D are
green plus the user-flagged orchestrator-split process debt (Q11 →
factored into `src/lib/locking.py`). Atomic commits this stage: 9.

Stage E (Quota awareness) per AGENTS.md §2:
- `src/lib/quota.py` — reads `oauth/usage` endpoint with macOS
  Keychain support; 60s cache; returns None on failure
- `src/lib/ratelimit.py` — 5min/15min/1h ladder; resets after 6h
- Pre-flight check in orchestrator: pause project at >95% 5h or
  pause-all at >90% 7d
- `stop-failure.sh` rewires to use quota.py first, falls back to
  ladder
- `tools/mock-quota-server.py` already exists as a stub — Stage E
  will exercise it
- Q1 (oauth/usage response format) and Q4 (Keychain prompt) are
  Stage E investigation lanes.

## Last commit

`docs: Q2/Q5/Q8 outcomes after Stage D investigation` (0d1624d) →
followed by Stage D smoke + STATUS update (this commit pending).

## Stages completion

- [x] Stage A: Foundations (completed 2026-04-29T02:40Z)
- [x] Stage B: Orchestrator skeleton (completed 2026-04-29T03:00Z)
- [x] Stage C: Hooks (completed 2026-04-29T07:05Z)
- [x] Stage D: Locking and recovery (completed 2026-04-29T09:55Z)
- [ ] Stage E: Quota awareness
- [ ] Stage F: Helpers and CLI
- [ ] Stage G: Hello-fullstack smoke test

## Stage D DoD verification

All items green, validated by `bash tests/smoke/stage-d.sh`:

- [x] Two `cc-autopipe start` invocations: second exits with
      "already running" (rc=1)
- [x] `kill -9 $(pgrep -f orchestrator)`, restart, no stale lock
      issue (recovery observed in 0s — well under SPEC's 60s budget)
- [x] Per-project lock with heartbeat — HeartbeatThread refreshes
      timestamp every 10s; stale-detection logs but doesn't
      force-release (v0.5 contract)
- [x] Test scenario: orchestrator crashes mid-cycle, restart resumes
      correctly — state.json iteration is "off by one. Acceptable."
      per SPEC §8.4
- [x] tests/integration/test_recovery.py passes (6/6)
- [x] STATUS.md updated

Plus user-flagged process debt:
- [x] Q11: src/orchestrator stays under 500 lines after Stage D
      (485 lines vs 600+ if monolithic)
- [x] src/lib/locking.py (349 lines) handles all locking concerns
- [x] tests/integration/test_locking.py (12/12) covers primitives
      + orchestrator integration

Test totals: 80 pytest unit+integration cases (added 18 — 12
locking + 6 recovery), plus 102 hook unit cases. 5 smoke validators
(A+B+C+D + the 4 hook test scripts).

## Process debt

Resolved this stage:
- Q11: orchestrator split → lib/locking.py (precedent: Q10's cli/
  split for command implementations)

## Currently blocked

None.

## Recent open questions

- Q1 (open, Stage E): oauth/usage endpoint format
- Q2 (deferred-to-Stage-G): claude --resume with deleted JSONL —
  mock can't model real claude's JSONL check. Mitigations in code.
- Q3 (resolved Stage C): Stop hook session_id reliability
- Q4 (open, Stage E): macOS Keychain prompt
- Q5 (deferred-to-Stage-G): --max-turns counter on resume — mock
  ignores the flag. Checkpoint-based continuity already works.
- Q6 (open, Stage F/G): backlog.md tag handling
- Q7 (resolved Stage A): TG --data-urlencode
- Q8 (resolved Stage D): flock on macOS — used Python fcntl instead
- Q9 (resolved Stage A): compat.sh feature-detect
- Q10 (resolved Stage B): src/cli/ deviation
- Q11 (resolved Stage D): src/lib/locking.py split

## Tooling notes

- Stage D added `import locking` to orchestrator and status.py via
  `sys.path.insert(0, lib/)` — same pattern as `import state`.
- `_format_orchestrator` in status.py renders uptime per SPEC §12.4
  ("PID 12345, uptime 2h 34m"); edge cases for <60s/<1h/<1d/>=1d.
- mock-claude.sh's `CC_AUTOPIPE_MOCK_SLEEP_SEC` env is the engine for
  testing per-project lock holding and SIGKILL recovery.

## Notes for next session

- Stage E scope: lib/quota.py + lib/ratelimit.py + orchestrator
  pre-flight + stop-failure.sh quota integration. SPEC §6.3, §6.4,
  §9. Tools/mock-quota-server.py already exists in the bootstrap.
- quota.py reads OAuth token: Linux from ~/.claude/credentials.json,
  macOS from Keychain. Q4 investigation is whether the Keychain call
  prompts on first use.
- `oauth/usage` endpoint format (Q1) is a sample from codelynx.dev
  (~Oct 2025). quota.py must be defensive — return None on parse
  failure → orchestrator falls through to ladder.
- ratelimit.py: 5min/15min/1h ladder; reset count after 6h with no
  429. State persisted at ~/.cc-autopipe/ratelimit.json.
- After Stage E, stop-failure.sh's TODO(v0.5-stage-E) for the 1h
  fallback gets replaced by quota.py + ladder per SPEC §9.3.
- Roman has Claude MAX 20x. NEVER use Anthropic API SDK. Use
  tools/mock-quota-server.py for endpoint testing.
- Telegram credentials in ~/.cc-autopipe/secrets.env, not in repo.
