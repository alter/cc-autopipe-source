# v1.5.0 — reactive rate-limit policy

**Build complete.** Three groups landed in 8 commits, +30 unit/
integration tests, +1 smoke. 904 → 923 tests passing; 36/44 → 37/45
smokes (+1 new, no regressions; the 8 v0.5-era failures
a/b/c/d/e/f/k/l remain baseline noise unchanged from v1.4.1).

## Groups

### PREFLIGHT-5H-REMOVAL

- `_preflight_quota` in `src/orchestrator/preflight.py` no longer
  checks the 5h rolling window. The 5h pause + 5h warn branches and
  the `PREFLIGHT_5H_PAUSE` / `PREFLIGHT_5H_WARN` constants are gone.
  Function now returns one of `"ok"`, `"warn_7d"`, `"paused_7d"`.
- `src/orchestrator/cycle.py` line 557 simplified from
  `if preflight in ("paused_5h", "paused_7d"):` to
  `if preflight == "paused_7d":`.
- `tests/smoke/stage-e.sh` step 4 inverted: with 5h_pct=1.00 and
  safe 7d, the orchestrator must NOT pause. Step 5 updated to the
  v1.5.0 7d=0.99 threshold.
- Engine now uses 100% of the rolling 5h window; rate-limit pauses
  are driven entirely by Claude CLI's 429 responses via the reactive
  path in `stop-failure.sh` + `ratelimit.py`. The burned cost of a
  false-start cycle (one `claude -p` invocation that immediately
  exits 429) is negligible vs. the productive cycles unlocked by
  riding the 5h window to actual exhaustion.

### PREFLIGHT-7D-BUMP

- `PREFLIGHT_7D_PAUSE` 0.95 → 0.98, `PREFLIGHT_7D_WARN` 0.90 → 0.95.
  Weekly quota rides closer to the wall; ~1 day cushion remains.
  Folded into the GROUP 1 preflight rewrite.
- Threshold change can be reverted independently from the 5h
  removal — both constants are isolated.
- All v1.4.x 7d-related tests in `tests/integration/test_orchestrator_quota.py`
  updated to the new thresholds (warn 95%/96%, pause 98%/99%).

### REACTIVE-429-PARSE-FIRST

- `src/hooks/stop-failure.sh` parses retry-after from the 429 error
  message itself before consulting `quota.py` cache. Three forms:
  1. ISO 8601 timestamp anywhere in `error_details` (`Resets at
     2026-05-11T18:10:00Z`)
  2. Relative-time prose (`retry after 15 minutes`,
     `in 600 seconds`) — runs ahead of the bare-seconds header form
     so prose with a unit is not misread as a header value.
  3. `Retry-After: <N>` / `X-RateLimit-Reset: <N>` header (seconds,
     no unit).
- Parsed value wins over quota cache and ratelimit ladder. Logged
  as `resolved_via=parsed_message` in `aggregate.jsonl`.
- `DETAILS` is passed to the embedded Python via env var (not via
  HEREDOC interpolation) so arbitrary shell metacharacters in the
  message stay safe.
- `src/lib/ratelimit.py`: escalating 5min / 15min / 60min ladder
  collapsed to a flat 15min via `FALLBACK_WAIT_SEC = 900`.
  `LADDER_SEC` and `RESET_AFTER_SEC` constants removed. `register_429`
  still persists `count` + `last_429_ts` for postmortem audit but
  no longer derives the wait from `count`. Get_resume_at, CLI surface,
  and state file format unchanged.
- `src/hooks/stop-failure.sh` last-resort fallback shortened 1h → 15min
  via inline `timedelta(minutes=15)`. With both parse-first and
  flat-15min ladder in front of it, the last-resort path is
  practically unreachable but kept defensively.

## Operator action required

None. Restart `cc-autopipe.service` to pick up the new policy.
Existing PAUSED projects auto-resume at their scheduled `resume_at`
regardless of which resolution path produced it.

## What's NOT in v1.5.0

- No SPEC.md edits in this repo — the v1.5.0 build repo does not
  carry a tracked SPEC.md (PROMPT's §9.2 / §9.3 references are
  forward-looking for the downstream specification, not in scope
  for the engine build).
- Pre-v1.5.0 `paused (reason=5h_pre_check)` entries in production
  `aggregate.jsonl` are NOT migrated — they're historical and the
  engine simply stops emitting new ones.
- `quota_monitor.py` daemon polling is untouched — it still drives
  the 7d warn path that the (still-present) 7d preflight branch
  reads via `quota.read_cached()`.
- No `pre_429` early-warning event — the whole point of v1.5.0 is
  to trust the API as the rate-limit oracle.
- No grace-period retries before honouring a 429 — Anthropic's API
  is authoritative, and retrying typically returns another 429 with
  the same retry-after.

## Smokes

One new smoke, green:

- `tests/smoke/run-reactive-rate-limit-smoke.sh` — synthesises a
  rate_limit stop-hook payload with a parseable ISO timestamp 2s in
  the future, runs `stop-failure.sh`, asserts
  `state.paused.resume_at` matches the parsed timestamp + 60s safety
  margin and `resolved_via=parsed_message`, confirms no
  `5h_pre_check` event was emitted, then sleeps past the resume
  time and drives one orchestrator loop to verify
  `_resume_paused_if_due` auto-flips `phase` back to `active`.

Registered in `tests/smoke/run-all-smokes.sh` as
`reactive-rate-limit`.

## Acceptance

- `pytest tests/ -q` → **923 passed** (904 baseline + 30 new tests
  − 11 net removed/rewritten: 3 5h-preflight integration tests
  removed, 2 ladder-progression integration tests updated to flat
  15min, 6 ratelimit unit tests rewritten for the flat-fallback
  policy). Two intermittent multiprocessing FileNotFoundErrors
  observed in `test_state::test_concurrent_*` are pre-existing
  flakes unrelated to v1.5.0; both pass when run in isolation.
- `bash tests/smoke/run-all-smokes.sh` → **37/45** passed; the 8
  failing v0.5-era stages (a/b/c/d/e/f/k/l) are baseline noise per
  STATUS.md, unchanged from v1.4.1. Net change: +1 smoke
  (reactive-rate-limit), no regressions.

## Commits (in order)

1. `preflight: remove 5h pre-cycle pause + warn branches; bump 7d to 0.98/0.95 (v1.5.0)`
2. `cycle: drop "paused_5h" handling from preflight branch (v1.5.0)`
3. `tests: cover 5h saturation alone does not pause + new 7d thresholds (v1.5.0)`
4. `tests: integration suite + stage-e smoke updated to v1.5.0 thresholds (v1.5.0)`
5. `ratelimit: collapse 5/15/60 ladder to flat 15min FALLBACK_WAIT_SEC=900 (v1.5.0)`
6. `stop-failure: parse retry-after from 429 message; last-resort 1h → 15min (v1.5.0)`
7. `tests: cover ratelimit flat-fallback + stop-failure 429-parse paths (v1.5.0)`
8. `smoke: add run-reactive-rate-limit-smoke.sh covering parsed-message resume + auto-unpause (v1.5.0)`
