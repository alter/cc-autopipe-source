# Bootstrap manifest

This directory contains the minimal scaffolding to start cc-autopipe v0.5
implementation in a fresh git repo. Place these files in your build repo
ALONGSIDE SPEC.md and AGENTS.md.

## Files

| File | Purpose |
|---|---|
| CLAUDE.md | Project-level CLAUDE.md, references universal baseline + AGENTS.md |
| STATUS.md | Build journal template (agent updates this every commit) |
| OPEN_QUESTIONS.md | Pre-populated with all 8 questions from SPEC.md §19 |
| README.md | User-facing project description |
| .gitignore | Standard ignores + cc-autopipe state files |
| tools/mock-claude.sh | Fake `claude` for testing hooks without real quota |
| tools/mock-quota-server.py | Fake oauth/usage endpoint |
| tools/inject-429.sh | Helper to test 429 handling |
| tools/seed-fixtures.sh | Generate test fixtures from canonical templates |

## Setup steps

```bash
# 1. Create build repo
mkdir ~/projects/cc-autopipe-source
cd ~/projects/cc-autopipe-source
git init

# 2. Copy bootstrap files
cp /path/to/bootstrap/* .
cp /path/to/bootstrap/.gitignore .

# 3. Copy SPEC.md and AGENTS.md (from previous artifacts)
cp /path/to/SPEC.md .
cp /path/to/AGENTS.md .

# 4. Make tools executable
chmod +x tools/*.sh tools/*.py

# 5. Initial commit
git add -A
git commit -m "bootstrap: initial scaffolding for cc-autopipe v0.5 build

Refs: SPEC.md, AGENTS.md
Stage: pre-build"

# 6. Start Claude Code
claude --dangerously-skip-permissions
```

## First Claude Code session

Use this prompt verbatim:

```
Read AGENTS.md fully, then SPEC.md sections 1-6, then STATUS.md and 
OPEN_QUESTIONS.md. Confirm understanding by summarizing the build plan 
in STATUS.md "Currently working on" section. Then begin Stage A per 
AGENTS.md §2 Definition of Done checklist.

Do not push to git remote. Commit locally only.
```

The agent self-organizes from there.

## What the agent does NOT need from you

- Coding instructions (in AGENTS.md)
- DoD criteria (in AGENTS.md §2)
- File layout (in AGENTS.md §1, SPEC.md §5)
- Architecture decisions (in SPEC.md)

## What you DO during the build

- Review commits on the feature branch
- Periodically check STATUS.md
- Answer Q-entries in OPEN_QUESTIONS.md when status=blocked
- Push to remote (your hands only) after stage completion
- Tag `v0.5.0` after final acceptance
