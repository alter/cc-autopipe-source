# cc-autopipe — build repository

This repo contains the source for `cc-autopipe v0.5`, an autonomous pipeline
supervisor for Claude Code on MAX subscription.

**Status:** Implementation in progress.

## For implementing agents

Start here:
1. Read `AGENTS.md` (process spec)
2. Read `SPEC.md` (product spec)
3. Read `STATUS.md` (current build state)
4. Read `OPEN_QUESTIONS.md` (unresolved blockers)

Then begin work per `AGENTS.md` §10 boot procedure.

## For Roman (project owner)

This repo will produce:
- `src/` — engine code (~1100 lines, Python + bash)
- `tests/` — test suite
- `tools/` — dev/build helpers (mock claude, mock quota server)

When v0.5 is complete:
1. Engine installable via `bash src/install.sh`
2. After install: `cc-autopipe init` in any project directory
3. Run: `cc-autopipe start` to start orchestrator

See SPEC.md §17 for full v0.5 acceptance criteria.

## Status

See `STATUS.md` for live build progress.

## Reference docs

- `SPEC.md` — full product specification (1900+ lines)
- `AGENTS.md` — implementation workflow for autonomous agents
- `OPEN_QUESTIONS.md` — tracked open questions
- `STATUS.md` — build progress journal
