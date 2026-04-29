# Build Status

**Updated:** 2026-04-29T15:30:00Z
**Current branch:** main
**Current stage:** E complete; F (Helpers and CLI) up next

## Currently working on

Stage E is complete. All 11 DoD items in AGENTS.md §2 Stage E are
green. Atomic commits this stage: 12.

Stage F (Helpers and CLI) per AGENTS.md §2:
- `cc-autopipe-checkpoint` (helpers/) — saves checkpoint.md from
  inside a Claude session
- `cc-autopipe-block` (helpers/) — marks project failed + creates
  HUMAN_NEEDED.md
- `cc-autopipe resume` — clears PAUSED/FAILED, resets failures
- `cc-autopipe doctor` — verifies all prerequisites
- `cc-autopipe tail` — follows aggregate.jsonl
- `cc-autopipe run <project> --once` — single-cycle test mode
- All commands `--help`-discoverable
- tests/integration/test_cli.py covers each subcommand

Q6 (backlog.md tag handling) lands during Stage F or carries into G.

## Last commit

`tests: status renders quota line when cache is present` (3dd3a5d)
→ followed by Q1/Q4 docs (3207239) and Stage E smoke + STATUS update
(this commit pending).

## Stages completion

- [x] Stage A: Foundations (completed 2026-04-29T02:40Z)
- [x] Stage B: Orchestrator skeleton (completed 2026-04-29T03:00Z)
- [x] Stage C: Hooks (completed 2026-04-29T07:05Z)
- [x] Stage D: Locking and recovery (completed 2026-04-29T09:55Z)
- [x] Stage E: Quota awareness (completed 2026-04-29T15:30Z)
- [ ] Stage F: Helpers and CLI
- [ ] Stage G: Hello-fullstack smoke test

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
- [x] orchestrator pre-flight check pauses project at >95% 5h
- [x] orchestrator pre-flight check pauses ALL projects at >90% 7d
      (with 5min TG dedup via `7d-tg.last` sentinel)
- [x] stop-failure.sh uses quota.py first, falls back to ratelimit.py
      (and last-resort 1h if both unavailable)
- [x] tests/integration/test_quota.py passes (12/12, 1 macOS-skip)
- [x] STATUS.md updated

Bonus tests: tests/unit/test_ratelimit.py (14/14),
tests/integration/test_orchestrator_quota.py (12/12, pre-flight +
stop-failure+quota end-to-end).

Test totals: 117 pytest unit+integration (62→117 across A→E) plus
1 expected macOS-skip and 102+1=103 hook unit cases. Six smoke
validators (stage-a through stage-e) all green together.

## Process debt

Two test-environment escape hatches added this stage (both safety,
not features):
- `CC_AUTOPIPE_QUOTA_DISABLED=1` — short-circuits quota.read_raw
  to None so framework tests don't accidentally hit
  api.anthropic.com when Roman's macOS Keychain has live creds.
- `CC_AUTOPIPE_CREDENTIALS_FILE` — overrides
  `~/.claude/credentials.json` for Linux-path tests on macOS.

Stray `test.sh` in repo root (untracked, not engine code; Roman's
manual exploration script). Left alone.

## Currently blocked

None.

## Recent open questions

- Q1 (deferred-to-Stage-G): oauth/usage response format — verified
  against the mock; real-endpoint check waits for Stage G smoke.
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

## Tooling notes

- quota.py CLI surface: `read | read-cached | refresh`. Hooks call
  `python3 quota.py read` per SPEC §9.3.
- ratelimit.py CLI: `register-429 | state | reset`.
- mock-quota-server.py works with random ports; tests spawn a fresh
  instance per test via `_free_port()`.

## Notes for next session

- Stage F scope: 6 subcommands + 2 helpers, all per SPEC §12.
  - `helpers/cc-autopipe-checkpoint`: short bash, writes
    `.cc-autopipe/checkpoint.md` from stdin (the body) or arg.
  - `helpers/cc-autopipe-block`: mark FAILED + HUMAN_NEEDED.md +
    TG alert.
  - `cc-autopipe resume <project>`: state.phase = active, reset
    consecutive_failures, remove HUMAN_NEEDED.md.
  - `cc-autopipe doctor`: SPEC §12.9 checklist — claude binary,
    jq, flock (n/a since fcntl), Python 3.11+, secrets.env perms,
    hooks executable, credentials/Keychain, TG send-test, oauth
    endpoint reachable. Could add `--quick` to skip network checks
    for offline runs.
  - `cc-autopipe tail`: `tail -f` aggregate.jsonl with
    human-readable formatting.
  - `cc-autopipe run <project> --once`: single cycle, bypasses
    singleton lock per SPEC §12.6.
  - `cc-autopipe stop`: SIGTERM the singleton, wait up to 60s, then
    SIGKILL. Already half-implemented via the Stage D singleton.
- All commands need `--help`. Dispatcher already routes; just wire
  the implementations.
- After Stage F, the only remaining stage is G (hello-fullstack
  smoke test) — that's project-side, not engine code.
- Q6 (backlog.md tags): touch when implementing prompt building or
  status; v0.5 should ignore unknown tags rather than crash.
- Roman has Claude MAX 20x. NEVER use Anthropic API SDK. Stage G is
  the ONLY place real claude runs.
- Telegram credentials in ~/.cc-autopipe/secrets.env, not in repo.
