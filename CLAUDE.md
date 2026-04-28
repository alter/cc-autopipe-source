@~/.claude/CLAUDE.md

# cc-autopipe — build repository

This is the BUILD repo for cc-autopipe v0.5. We are implementing the engine
described in SPEC.md following the workflow in AGENTS.md.

## Required reading at session start (in order)

1. AGENTS.md — process spec (HOW to build)
2. SPEC.md — product spec (WHAT to build)
3. STATUS.md — current build state
4. OPEN_QUESTIONS.md — unresolved questions

Do not skip step 3 or 4. They tell you where the previous session stopped
and what is blocked.

## Operating mode

Autonomous Claude Code sessions implementing the engine. AGENTS.md §2 defines
Definition of Done per stage. AGENTS.md §13 lists forbidden actions.

## When to ask the human

Only via OPEN_QUESTIONS.md (status: blocked) + Telegram per AGENTS.md §15.
No interactive prompts. No "do you want me to..." questions.

## Critical reminders

- NEVER push to git remote (commit only)
- NEVER tag releases
- NEVER use Anthropic API SDK — only `claude -p` patterns
- NEVER skip a stage's DoD checklist
- ALWAYS update STATUS.md after each commit
- ALWAYS run pre-commit self-review (AGENTS.md §16)
