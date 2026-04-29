# Build Status

**Updated:** 2026-04-29T02:40:00Z
**Current branch:** main
**Current stage:** A complete; B (Orchestrator skeleton) up next

## Currently working on

Stage A is complete. All 11 DoD items in AGENTS.md §2 Stage A are
green. Next session begins Stage B (Orchestrator skeleton): write
`src/orchestrator` (Python main loop), `cc-autopipe init` (template
copy + projects.list registration + .claude/settings.json absolute-
path wiring + gitignore entries), and `cc-autopipe status` (one-screen
overview from state.json across registered projects). Also need to
populate `src/templates/.cc-autopipe/` with the project skeleton. The
orchestrator must NOT spawn `claude` yet — Stage B only logs cycle
attempts; real `claude -p` invocation lands in Stage C alongside the
hooks.

## Last commit

`tests: add Stage A unit suite and smoke validator` (23e7def)

Preceded by: `bf15518 state: …`, `eea1bbe compat,tg,cli: …`,
`43763d0 tools: fix shellcheck violations in mock-claude.sh`.

## Stages completion

- [x] Stage A: Foundations (completed 2026-04-29T02:40Z)
- [ ] Stage B: Orchestrator skeleton
- [ ] Stage C: Hooks
- [ ] Stage D: Locking and recovery
- [ ] Stage E: Quota awareness
- [ ] Stage F: Helpers and CLI
- [ ] Stage G: Hello-fullstack smoke test

## Stage A DoD verification

All items checked, validated by `bash tests/smoke/stage-a.sh`:

- [x] `pytest tests/unit/test_state.py` passes (20/20)
- [x] state.py atomic write verified by concurrent-write test
      (8 spawn-mode workers × 50 writes; reader-during-writer storm)
- [x] state.py recovers from corrupted JSON
      (garbage / truncated / empty cases)
- [x] tg.sh sends without errors when secrets present
      (exit 0 verified with fake creds)
- [x] tg.sh exits 0 silently when secrets absent
- [x] compat.sh `date_from_epoch` and `file_mtime` correct
      (feature-detected GNU/BSD; ISO 8601 sample matches)
- [x] `cc-autopipe --help` lists every subcommand
- [x] All bash files pass shellcheck (8 files)
- [x] All Python files pass `ruff check`
      and `ruff format --check`
- [x] Commit messages follow §4 conventions
- [x] STATUS.md updated with completion timestamp

End-to-end TG round-trip verification deferred to a session where
Roman's `secrets.env` is available; the DoD validates the no-secrets/
exit-0 contract, not the wire format.

## Currently blocked

None.

## Recent open questions

- Q7 (resolved 2026-04-29): TG multiline rendering — switched tg.sh
  to `--data-urlencode`, no escape-table needed.
- Q9 (resolved 2026-04-29, new): compat.sh GNU coreutils on macOS —
  feature-detect rather than uname-dispatch.
- Q1, Q2, Q3, Q4, Q5, Q6, Q8 — all open, none blocking yet.

## Tooling notes

- macOS host. brew-installed shellcheck 0.11 and bash 5.3 at session
  start (system bash is 3.2).
- System pytest under python3.11 was broken (TypeError: lineno on AST
  alias). Created `.venv/` with python3.13 + fresh pytest 9 + ruff 0.15.
  Smoke script auto-detects `.venv/bin/pytest` and falls back to PATH.
- Dev workflow per AGENTS.md §3.1: from repo root,
  `export CC_AUTOPIPE_HOME="$(pwd)/src"` and
  `export PATH="$CC_AUTOPIPE_HOME/helpers:$PATH"` to use cc-autopipe
  without running install.sh.

## Notes for next session

- Stage B starts with `src/orchestrator` Python main loop. Use
  `subprocess.Popen` for `claude -p` later — DO NOT use the
  `anthropic` SDK.
- `cc-autopipe init` template lives at `src/templates/.cc-autopipe/`
  per SPEC.md §5.1; Stage B is its first user.
- Roman has Claude MAX 20x. Use `tools/mock-claude.sh` for local hook
  testing. Do not burn real quota during build.
- Telegram credentials live in `~/.cc-autopipe/secrets.env`, not in
  the repo.
