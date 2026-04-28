# Build Status

**Updated:** TBD on first session
**Current branch:** main
**Current stage:** Not started

## Currently working on

Nothing yet. Awaiting first build session.

First action for next session: read AGENTS.md fully, then SPEC.md §1-§6,
then begin Stage A (Foundations) per AGENTS.md §2.

## Last commit

Initial bootstrap commit by human.

## Stages completion

- [ ] Stage A: Foundations (lib/state.py, lib/compat.sh, lib/tg.sh, install.sh skeleton, helpers/cc-autopipe dispatcher)
- [ ] Stage B: Orchestrator skeleton (orchestrator entry, cc-autopipe init, cc-autopipe status)
- [ ] Stage C: Hooks (4 hooks in src/hooks/)
- [ ] Stage D: Locking and recovery
- [ ] Stage E: Quota awareness (lib/quota.py, lib/ratelimit.py)
- [ ] Stage F: Helpers and CLI (remaining commands)
- [ ] Stage G: Hello-fullstack smoke test

## Currently blocked

None.

## Recent open questions

None yet. See OPEN_QUESTIONS.md for tracked questions.

## Notes for next session

- Roman has Claude MAX 20x subscription. Do NOT use Anthropic API SDK.
- Project policy bans claude API calls except via `claude -p` CLI patterns.
- Build environment: WSL2/Ubuntu primarily, macOS secondary.
- Telegram credentials live in `~/.cc-autopipe/secrets.env` (NOT in this repo).
