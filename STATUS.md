# Build Status

**Updated:** 2026-04-29T03:00:00Z
**Current branch:** main
**Current stage:** B complete; C (Hooks) up next

## Currently working on

Stage B is complete. All 14 DoD items in AGENTS.md §2 Stage B are
green. Next session begins Stage C (Hooks): four bash scripts in
`src/hooks/` (session-start, pre-tool-use, stop, stop-failure) that
Claude Code fires at lifecycle boundaries. The hooks call back into
`src/lib/state.py`'s CLI for state mutations and `src/lib/tg.sh` for
alerts. Stage C also adds `tools/mock-claude.sh`-driven unit tests
under `tests/unit/test_hooks/`.

The orchestrator currently logs `cycle_attempt` events without
spawning `claude -p`. Stage C is when the orchestrator actually starts
invoking `claude -p` via subprocess.Popen — that's wired AFTER the
hooks themselves are in place.

## Last commit

`tests: add integration tests for orchestrator skeleton` (303a33a)

Preceded by: `da9dd1e tests: …status`, `c92ff91 tests: …init`,
`008f3eb orchestrator: …`, `dc8d059 cli: …status`,
`8064a5f cli: …init`, `02e8d08 templates: …`.

## Stages completion

- [x] Stage A: Foundations (completed 2026-04-29T02:40Z)
- [x] Stage B: Orchestrator skeleton (completed 2026-04-29T03:00Z)
- [ ] Stage C: Hooks
- [ ] Stage D: Locking and recovery
- [ ] Stage E: Quota awareness
- [ ] Stage F: Helpers and CLI
- [ ] Stage G: Hello-fullstack smoke test

## Stage B DoD verification

All items green, validated by `bash tests/smoke/stage-b.sh`:

- [x] cc-autopipe init creates .cc-autopipe/ from templates
- [x] cc-autopipe init --force overwrites existing
- [x] cc-autopipe init refuses non-empty .cc-autopipe/ without --force
- [x] cc-autopipe init adds project to ~/.cc-autopipe/projects.list
      (idempotent on repeat)
- [x] cc-autopipe init writes .claude/settings.json with absolute
      paths (4 hooks, all rooted at $CC_AUTOPIPE_HOME/hooks/)
- [x] cc-autopipe init adds gitignore entries (idempotent, preserves
      existing user content)
- [x] orchestrator main loop reads projects.list, iterates FIFO
- [x] orchestrator does NOT spawn claude yet (just logs cycle_attempt;
      verified by running with PATH stripped of `claude`)
- [x] cc-autopipe status displays project phases from state.json
- [x] cc-autopipe status --json produces valid JSON
- [x] orchestrator exits cleanly on SIGTERM (verified mid-sleep,
      <1s observed exit time)
- [x] tests/integration/test_init.py passes (13/13)
- [x] tests/integration/test_status.py passes (11/11)
- [x] STATUS.md updated

Plus: tests/integration/test_orchestrator.py (11/11), not in the
literal DoD list but added to cover the orchestrator side of Stage B
the same way init/status are covered.

Test totals so far: 55 unit + integration tests, all green.

## Currently blocked

None.

## Recent open questions

- Q1, Q2, Q3, Q4, Q5, Q6, Q8 — open, none blocking yet.
- Q7 (resolved 2026-04-29 Stage A): TG --data-urlencode.
- Q9 (resolved 2026-04-29 Stage A): compat.sh feature-detect.

## Tooling notes

- macOS host. brew bash 5.3 + shellcheck 0.11 + .venv with python3.13
  + pytest 9 + ruff 0.15.
- Smoke scripts auto-detect `.venv/bin/pytest` and fall back to PATH.
- Dev workflow: from repo root,
  `export CC_AUTOPIPE_HOME="$(pwd)/src"` and
  `export PATH="$CC_AUTOPIPE_HOME/helpers:$PATH"`.

## Notes for next session

- Stage C scope: 4 bash hooks in `src/hooks/`. SPEC.md §10.1-§10.4
  has full pseudocode. They invoke `state.py` (CLI subcommands) and
  `tg.sh` (already implemented). PreToolUse §10.2 has the 6 block
  rules — each one needs its own unit test per AGENTS.md §2 Stage C.
- The orchestrator's `process_project` in src/orchestrator currently
  just logs cycle_attempt. Stage C is when it grows
  `subprocess.Popen([\"claude\", ...])` per SPEC.md §6.1
  `build_claude_cmd`. Use tools/mock-claude.sh for tests; do NOT
  burn real MAX quota.
- Hook tests should exercise via stdin JSON the way Claude Code
  invokes hooks per SPEC.md §10.1. Spec assumes Stop hook receives
  `session_id` reliably (Q3 — verify or document deviation).
- Stage C also touches `tools/mock-claude.sh` more heavily — the
  bootstrap version supports session-start / stop / rate-limit /
  block-secret / long-bash scenarios; review whether those cover
  the actual Stage C tests we need.
- Roman has Claude MAX 20x. NEVER use Anthropic API SDK. Use
  `tools/mock-claude.sh` for all hook integration tests.
- Telegram credentials live in `~/.cc-autopipe/secrets.env`, not in
  the repo.
