# AGENTS.md — Workflow for cc-autopipe v0.5 implementation

This document tells Claude Code (running interactively in the cc-autopipe-source repo) HOW to implement cc-autopipe v0.5 from SPEC.md. It is the operational manual for the build process.

**SPEC.md** — what to build (product spec).  
**AGENTS.md** — how to build it (process spec).

Read both before starting any work.

---

## 0. Critical reading on session start

Every new Claude Code session in this repo MUST start by reading, in order:

1. `AGENTS.md` (this file)
2. `SPEC.md` (product specification)
3. `STATUS.md` (current build progress, see §11)
4. `CLAUDE.md` if present (project-specific overrides of universal CLAUDE.md)

Do not skip step 3. STATUS.md tells you where the previous session stopped, what's done, what's in progress, what's blocked.

---

## 1. Project layout

```
cc-autopipe-source/                      Build repo
├── SPEC.md                              Product spec (read-only during build)
├── AGENTS.md                            This file (read-only during build)
├── STATUS.md                            Build progress journal (you write this)
├── CLAUDE.md                            Project CLAUDE.md, references baseline
├── README.md                            User-facing docs
│
├── src/                                 Engine code (this is what we ship)
│   ├── orchestrator                     Python entry point (executable)
│   ├── helpers/
│   │   ├── cc-autopipe                  Bash dispatcher
│   │   ├── cc-autopipe-checkpoint
│   │   └── cc-autopipe-block
│   ├── hooks/
│   │   ├── session-start.sh
│   │   ├── pre-tool-use.sh
│   │   ├── stop.sh
│   │   └── stop-failure.sh
│   ├── lib/
│   │   ├── state.py
│   │   ├── quota.py
│   │   ├── ratelimit.py
│   │   ├── tg.sh
│   │   └── compat.sh
│   ├── templates/
│   │   └── .cc-autopipe/                Project skeleton
│   ├── install.sh
│   ├── VERSION
│   └── CLAUDE_CODE_MIN_VERSION
│
├── tests/                               Test code
│   ├── unit/                            Per-module tests
│   ├── integration/                     Multi-component tests
│   ├── fixtures/                        Sample state.json, mock projects
│   └── smoke/                           End-to-end scripts
│
├── tools/                               Build/dev tools, not shipped
│   ├── mock-claude.sh                   Fake `claude` binary for testing
│   ├── mock-quota-server.py             Fake oauth/usage endpoint
│   ├── inject-429.sh                    Force a 429 simulation
│   └── seed-fixtures.sh                 Generate test fixtures
│
└── .gitignore
```

The repo is itself initialized with cc-autopipe v0.5 once stage F is complete — meta. Until then, no `.cc-autopipe/` directory in this repo.

---

## 2. Build stages and definition of done

Stages from SPEC.md §16, with explicit DoD for each.

### Stage A — Foundations
**Components:** `lib/compat.sh`, `lib/state.py`, `lib/tg.sh`, `install.sh` (minimal), `helpers/cc-autopipe`

**DoD checklist:**
- [ ] `python3 -m pytest tests/unit/test_state.py` passes
- [ ] state.py atomic write verified by concurrent-write test
- [ ] state.py recovers from corrupted JSON (test injects garbage)
- [ ] tg.sh sends to test chat without errors when secrets present
- [ ] tg.sh exits 0 silently when secrets absent
- [ ] compat.sh `date_from_epoch` and `file_mtime` produce identical output on Linux and macOS (test in CI or manual)
- [ ] `cc-autopipe --help` lists all subcommands (even if unimplemented)
- [ ] All bash files pass `shellcheck`
- [ ] All Python files pass `ruff check`
- [ ] Commit message follows §4 conventions
- [ ] STATUS.md updated with Stage A completion timestamp

**Validation command:**
```bash
bash tests/smoke/stage-a.sh && echo "Stage A: OK"
```

### Stage B — Orchestrator skeleton
**Components:** `orchestrator` (main loop), `cc-autopipe init`, `cc-autopipe status`

**DoD:**
- [ ] `cc-autopipe init` creates `.cc-autopipe/` from templates in current dir
- [ ] `cc-autopipe init --force` overwrites existing
- [ ] `cc-autopipe init` refuses non-empty `.cc-autopipe/` without --force
- [ ] `cc-autopipe init` adds project to `~/.cc-autopipe/projects.list`
- [ ] `cc-autopipe init` writes `.claude/settings.json` with absolute paths
- [ ] `cc-autopipe init` adds gitignore entries
- [ ] orchestrator main loop reads projects.list, iterates FIFO
- [ ] orchestrator does NOT spawn claude yet (just logs cycle attempts)
- [ ] `cc-autopipe status` displays project phases from state.json
- [ ] `cc-autopipe status --json` produces valid JSON
- [ ] orchestrator exits cleanly on SIGTERM
- [ ] tests/integration/test_init.py passes
- [ ] tests/integration/test_status.py passes
- [ ] STATUS.md updated

### Stage C — Hooks
**Components:** all 4 hooks in `hooks/`

**DoD:**
- [ ] session-start.sh outputs valid context summary, exits 0
- [ ] pre-tool-use.sh blocks each rule from §10.2 SPEC.md (one test per rule)
- [ ] pre-tool-use.sh allows benign actions
- [ ] stop.sh runs verify.sh, parses JSON, updates state.json
- [ ] stop.sh handles malformed verify output (logs to failures.jsonl, increments failures)
- [ ] stop.sh handles verify timeout (60s)
- [ ] stop-failure.sh on rate_limit error transitions to PAUSED
- [ ] stop-failure.sh on other errors increments consecutive_failures
- [ ] All hooks pass shellcheck
- [ ] tests/unit/test_hooks/ passes (uses tools/mock-claude.sh)
- [ ] STATUS.md updated

### Stage D — Locking and recovery
**Components:** singleton lock, per-project lock, crash recovery

**DoD:**
- [ ] Two `cc-autopipe start` invocations: second exits with "already running"
- [ ] `kill -9 $(pgrep -f orchestrator)`, restart, no stale lock issue
- [ ] Per-project lock with heartbeat, stale detection works
- [ ] Test scenario: orchestrator crashes mid-cycle, restart resumes correctly
- [ ] tests/integration/test_recovery.py passes (simulates kill -9)
- [ ] STATUS.md updated

### Stage E — Quota awareness
**Components:** `lib/quota.py`, `lib/ratelimit.py`, pre-flight integration

**DoD:**
- [ ] quota.py reads OAuth token on Linux from `~/.claude/credentials.json`
- [ ] quota.py reads OAuth token on macOS from Keychain
- [ ] quota.py returns None gracefully when token missing
- [ ] quota.py returns None gracefully when endpoint unreachable
- [ ] quota.py caches results for 60s
- [ ] ratelimit.py implements 5min/15min/1h ladder
- [ ] ratelimit.py resets counter after 6h with no 429
- [ ] orchestrator pre-flight check pauses project at >95% 5h
- [ ] orchestrator pre-flight check pauses ALL projects at >90% 7d
- [ ] stop-failure.sh uses quota.py first, falls back to ratelimit.py
- [ ] tests/integration/test_quota.py passes (uses tools/mock-quota-server.py)
- [ ] STATUS.md updated

### Stage F — Helpers and CLI
**Components:** all remaining `cc-autopipe *` subcommands

**DoD:**
- [ ] `cc-autopipe-checkpoint` saves checkpoint.md correctly
- [ ] `cc-autopipe-block` marks project failed and creates HUMAN_NEEDED.md
- [ ] `cc-autopipe resume` clears PAUSED/FAILED, resets failures
- [ ] `cc-autopipe doctor` checks all prerequisites and reports
- [ ] `cc-autopipe tail` follows aggregate.jsonl
- [ ] `cc-autopipe run <project> --once` runs single cycle
- [ ] All commands have --help
- [ ] tests/integration/test_cli.py passes
- [ ] STATUS.md marked "Engine v0.5.0 complete"

### Stage G — Hello-fullstack smoke test
This is project setup, not engine code. Lives in separate repo.

**DoD:**
- [ ] hello-fullstack project created in `examples/hello-fullstack/`
- [ ] PRD with full acceptance criteria from SPEC.md §05 (previous version doc)
- [ ] verify.sh covers pytest + npm build + docker compose check
- [ ] `cc-autopipe init` works in this project
- [ ] Single cycle (`cc-autopipe run examples/hello-fullstack --once`) executes
- [ ] Full PRD reaches DONE under cc-autopipe in <4h (this is the actual smoke test)

---

## 3. Local development workflow

Until install.sh works (Stage A) and the engine is installed system-wide, develop in the build repo directly.

### 3.1 Running engine code without install

```bash
# From cc-autopipe-source/ root
export CC_AUTOPIPE_HOME="$(pwd)/src"
export PATH="$CC_AUTOPIPE_HOME/helpers:$PATH"

# Now `cc-autopipe ...` resolves to src/helpers/cc-autopipe
cc-autopipe --help
```

### 3.2 Mock Claude binary

Use `tools/mock-claude.sh` for hook testing without real `claude -p`.

```bash
# Make mock-claude available
export PATH="$(pwd)/tools:$PATH"
alias claude='mock-claude.sh'

# Now claude -p invokes the mock, which fires hooks with synthetic input
mock-claude.sh -p "test prompt" --max-turns 3
```

The mock:
- Reads `tools/mock-claude.config.json` for canned responses
- Fires SessionStart, PreToolUse (synthetic Bash), Stop hooks in sequence
- Returns deterministic JSON outputs
- Does NOT call Anthropic API

### 3.3 Mock quota server

For Stage E development:

```bash
# Run on localhost:8765
python3 tools/mock-quota-server.py &

# Override quota.py endpoint via env
export CC_AUTOPIPE_QUOTA_ENDPOINT="http://localhost:8765/api/oauth/usage"

# Now quota.py hits the mock
python3 src/lib/quota.py read
```

Mock server has endpoints to set fake utilization:
```bash
curl -X POST localhost:8765/admin/set -d '{"five_hour":0.97,"seven_day":0.5}'
```

### 3.4 Simulating 429

```bash
# Force StopFailure with 429
echo '{"error":"rate_limit","error_details":"429"}' | bash src/hooks/stop-failure.sh

# Verify state.json transitioned to paused
cat .cc-autopipe/state.json | jq .
```

### 3.5 Test fixtures

`tests/fixtures/` contains:
- `sample-state.json` — valid state with various phases
- `corrupted-state.json` — for recovery tests
- `mock-project/` — minimal project with .cc-autopipe/, backlog.md, verify.sh
- `verify-passing.sh`, `verify-failing.sh`, `verify-malformed.sh`

Run `bash tools/seed-fixtures.sh` to regenerate after schema changes.

---

## 4. Branch and commit conventions

### 4.1 Branch strategy

- `main` — only stable, all DoD checklists for current stage passed
- `stage-X-<component>` — feature branch per stage
- After stage DoD: PR to main, squash-merge

In v0.5 with Claude Code as the implementer, you (Claude) work directly on feature branches. Push to main is HUMAN-ONLY. NEVER `git push` from agent — only `git commit`.

### 4.2 Commit format

```
<scope>: <imperative summary> (max 60 chars)

<body explaining why, not what (the diff shows what)>

Refs: SPEC.md §X.Y
Stage: A | B | C | D | E | F | G
DoD: <checklist item just completed>
```

Scope is one of: `state`, `quota`, `ratelimit`, `hooks`, `orchestrator`, `cli`, `tests`, `docs`, `tools`.

Examples:
```
state: implement atomic write with tmpfile + rename

Atomic via O_TMPFILE + linkat is overkill for our use case. The simpler 
write-tmp-then-rename pattern is POSIX-portable and sufficient given our 
single-writer model (one orchestrator at a time, project lock per project).

Refs: SPEC.md §6.2
Stage: A
DoD: state.py atomic write verified by concurrent-write test
```

```
hooks: add pre-tool-use.sh secret-detection blocks

Patterns from SPEC.md §10.2 plus a check for ANTHROPIC_API_KEY in 
plaintext. Tested against fixtures/secret-leak-cases.json.

Refs: SPEC.md §10.2, §13.2
Stage: C
DoD: pre-tool-use.sh blocks each rule from §10.2 SPEC.md
```

### 4.3 Atomic commits

One commit = one logical change. NOT:
- Mixing fixes for unrelated components
- Mixing tests with implementation (separate commits OK if test was written first)
- Multi-line "and also" descriptions

DO:
- One module's implementation = one commit
- Tests for that module = separate commit (can be same PR)
- Refactor before adding feature = separate commits

### 4.4 What NEVER to commit

- `secrets.env` (engine's own secrets during dev)
- Live `.cc-autopipe/state.json` from your dev runs
- `~/.claude/credentials.json` (DO NOT EVER copy this into repo)
- `.DS_Store`, `*.pyc`, `__pycache__`, `.idea/`, `.vscode/` (in .gitignore)
- TODO comments without GitHub issue links (see §6.5)

---

## 5. Code standards

### 5.1 Python (used in: orchestrator, lib/state.py, lib/quota.py, lib/ratelimit.py)

- **Version:** 3.11+
- **Style:** ruff with default rules + `RUF`, `B`, `SIM`
- **Type hints:** mandatory on public functions, optional internal
- **Docstrings:** mandatory on public functions, format = brief one-liner + optional details
- **Error handling:** explicit `try/except`, never bare `except`
- **Logging:** `print(..., file=sys.stderr)` for v0.5. Structured logging via `logging` module deferred to v1.

Pre-commit check:
```bash
ruff check src/ tests/
ruff format --check src/ tests/
```

If you don't have ruff: `pip install ruff` (no other Python deps in this repo for v0.5).

### 5.2 Bash (used in: helpers, hooks, lib/tg.sh, lib/compat.sh, install.sh)

- **Version:** 5.0+ (macOS users use `/opt/homebrew/bin/bash`, NOT `/bin/bash` which is 3.2)
- **Shebang:** always `#!/bin/bash` (NOT `#!/bin/sh`)
- **Strict mode:** `set -euo pipefail` at top of every script
- **Style:** shellcheck-clean
- **Variables:** `"$var"` quoted always, `"${var:-default}"` for defaults
- **Functions:** `lower_snake_case`, return via stdout or exit code, NOT global vars
- **No `eval`** — ever. If you think you need eval, ask in OPEN_QUESTIONS.md instead.

Pre-commit check:
```bash
find src/ tests/ -name '*.sh' -exec shellcheck {} +
```

### 5.3 Common to both

- **2-space indent** for YAML/JSON, **4-space** for Python and bash
- **No trailing whitespace**
- **Newline at EOF**
- **UTF-8 only**, LF line endings
- **No tabs** anywhere
- **English** for all code, comments, commits, docs (per Roman's userPreferences)

---

## 6. Test strategy

### 6.1 Required tests in v0.5

For each component, MINIMUM:
- One unit test for happy path
- One unit test for one error case
- One integration test if component crosses module boundaries

This is not 100% coverage. It's "is the contract honored, does recovery work".

### 6.2 Layout

```
tests/
├── unit/
│   ├── test_state.py
│   ├── test_quota.py
│   ├── test_ratelimit.py
│   └── test_hooks/
│       ├── test_session_start.sh
│       ├── test_pre_tool_use.sh
│       ├── test_stop.sh
│       └── test_stop_failure.sh
├── integration/
│   ├── test_init.py
│   ├── test_status.py
│   ├── test_recovery.py
│   ├── test_quota.py
│   └── test_cli.py
├── smoke/
│   ├── stage-a.sh
│   ├── stage-b.sh
│   ├── stage-c.sh
│   ├── stage-d.sh
│   ├── stage-e.sh
│   └── stage-f.sh
└── fixtures/
    └── ...
```

### 6.3 Test runner

For Python: `pytest`. No fixtures framework, just plain functions and `tmp_path`.

For bash: `bats-core` if available, otherwise plain bash with manual assertions.

Run all tests:
```bash
make test                # if Makefile exists
# or:
pytest tests/unit tests/integration && bash tests/smoke/stage-*.sh
```

### 6.4 What NOT to test in v0.5

Skip:
- Unicode edge cases in file paths (out of threat model)
- Concurrent orchestrator instances on same project (singleton lock makes this impossible)
- Network conditions other than "down" (timeout suffices)
- macOS-specific tests if you're on Linux (mark as `pytest.mark.skipif(sys.platform != 'darwin')`)
- Performance / benchmarks

### 6.5 TODO discipline

If you must leave a TODO:
```python
# TODO(v1): handle DETACHED state for long ops. See OPEN_QUESTIONS.md#detach
```

Format: `TODO(<target-version>): <description>. See <reference>.`

NEVER commit a TODO without:
- A target version (v1, v2, never)
- A reference to either an issue or OPEN_QUESTIONS.md entry

---

## 7. Logging during development

The engine has its own log system (`aggregate.jsonl`) but we can't use it during build (chicken-and-egg).

**During implementation:**
- All Python: `print(..., file=sys.stderr)` for diagnostic output
- All bash: `echo "..." >&2`
- Don't add structured logging to engine code in v0.5 — defer to v1
- For build/test debugging: `set -x` in bash, `print(repr(x))` in Python, remove before commit

**For tests:**
- Pytest captures stdout/stderr automatically
- Use `print` for debug, run with `pytest -s` to see output
- Failed assertions should include actual vs expected

**For STATUS.md updates (your build journal):**
See §11.

---

## 8. Resolving open questions

SPEC.md §19 lists 8 questions that need verification during implementation. Plus you'll discover more.

### 8.1 OPEN_QUESTIONS.md format

Maintain `OPEN_QUESTIONS.md` in repo root:

```markdown
# Open Questions

## Q1. [STAGE-X] [STATUS] Title

**Discovered:** YYYY-MM-DD during stage X
**Status:** open | resolved | blocked
**Blocking:** which DoD items can't proceed without answer

**Question:**
<full question>

**Investigation:**
<what you tried>

**Resolution:** (only if status=resolved)
<answer + commit reference>
```

### 8.2 When to add a question

Add to OPEN_QUESTIONS.md when:
- SPEC.md §19 question becomes relevant in current stage
- You discover a behavior contradicting SPEC.md
- You find an edge case not covered by SPEC.md
- You want a design decision confirmed before committing

### 8.3 When NOT to add a question

Don't add when:
- It's a typo / minor issue (just fix and commit)
- It's an opinion ("this could be cleaner") — defer to v1
- It's blocked on user input that won't come — work around or skip

### 8.4 Resolving questions

Three paths:

**Path A: Self-resolve.** If you can answer with research (web search, code reading, manual test), do so. Document in OPEN_QUESTIONS.md and reference resolution commit.

**Path B: Defer.** If question doesn't block current DoD, mark as deferred to v1 with TODO.

**Path C: Block on human.** If you cannot proceed without answer, write entry, mark `Status: blocked`, send TG notification (`bash src/lib/tg.sh "[cc-autopipe-build] BLOCKED: <Q-ID>"`), then move to next non-dependent task.

Never silently work around an open question. Either resolve, defer, or block visibly.

---

## 9. When stuck

Three failure modes during build, three responses:

### 9.1 Test fails after 3 attempts to fix

If you've changed code 3 times and the test still fails:

1. Stop attempting fixes
2. Read SPEC.md section for the component again
3. Read your own diff against the spec word by word
4. If still unclear → OPEN_QUESTIONS.md entry, mark blocked
5. Move to next component (don't sit there)

### 9.2 Implementation diverges from spec

If you find SPEC.md describes something that won't work as written:

1. Do NOT silently deviate
2. Add OPEN_QUESTIONS.md entry with proposed deviation
3. Mark stage DoD as blocked on this question
4. Either wait for user to confirm deviation OR work on parallel components

### 9.3 Tooling problem

If `pytest` won't install, `shellcheck` is missing, etc:

1. Try once to install
2. If fails, document in STATUS.md "Tooling issue: <X>" and skip that validation
3. Continue with other DoD items
4. Add cleanup task to OPEN_QUESTIONS.md

Don't burn 30 minutes on tooling. Engine code matters more than perfect lint.

---

## 10. What to do FIRST in a new session

When you (Claude Code) start a new session in this repo:

1. **Read these in order:**
   - This file (AGENTS.md)
   - SPEC.md
   - STATUS.md
   - OPEN_QUESTIONS.md
   - CLAUDE.md (project)
2. **Run `git status` and `git log --oneline -10`** to see what's been committed
3. **Check current branch:** `git branch --show-current`
4. **Read STATUS.md "Currently working on" section** (see §11)
5. **Verify last commit** matches STATUS.md description
6. **Look for `WIP` markers** in code (`grep -r 'WIP\|XXX' src/`)
7. **Run smoke tests for completed stages** to verify nothing regressed
8. **Then start work** on the next DoD item

Skip step 7 only if it would take >5 minutes — but always run if last session ended mid-stage.

---

## 11. STATUS.md — build progress journal

Maintain `STATUS.md` in repo root. Update after EVERY meaningful event (commit, blocker, stage completion).

### 11.1 Format

```markdown
# Build Status

**Updated:** 2026-04-28T15:30:00Z
**Current branch:** stage-c-hooks
**Current stage:** C (hooks)
**Stage progress:** 3/4 hooks done

## Currently working on
pre-tool-use.sh secret-detection rules. 4 of 6 patterns done. 
Next: long-op heuristic, .claude/settings.json block.

## Last commit
hooks: add pre-tool-use.sh state.json write block (SHA abc123)

## Stages completion
- [x] Stage A: Foundations (completed 2026-04-27)
- [x] Stage B: Orchestrator skeleton (completed 2026-04-28T10:00)
- [ ] Stage C: Hooks (in progress, 60%)
- [ ] Stage D: Locking and recovery
- [ ] Stage E: Quota awareness
- [ ] Stage F: Helpers and CLI
- [ ] Stage G: Hello-fullstack smoke test

## Currently blocked
None.

## Recent open questions
- Q3 (resolved 2026-04-28): Stop hook receives session_id reliably in 2.1.121
- Q5 (open): Behavior of --max-turns when checkpoint exists. Investigating.
```

### 11.2 When to update

After every:
- Commit
- DoD checklist item complete
- Open question opened or resolved
- Stage transition
- Blocker encountered or cleared

If session ends mid-task, last update should describe **exactly where you stopped** so next session continues.

### 11.3 What NOT to put in STATUS.md

- Long technical discussion (that goes in commit messages)
- Failed approaches (unless they inform current direction)
- Code snippets (use commit history)

Keep STATUS.md under 300 lines. Archive old completed-stage details to `STATUS-archive.md` if you must.

---

## 12. Definition of Done — entire v0.5

v0.5 is complete when:

- [ ] All Stages A-F DoD checklists checked
- [ ] All tests pass: `pytest tests/unit tests/integration && bash tests/smoke/*.sh`
- [ ] `cc-autopipe doctor` passes on Ubuntu 22.04+ and macOS 14+
- [ ] hello-fullstack smoke test (Stage G) reaches DONE in <4 hours
- [ ] OPEN_QUESTIONS.md has zero `Status: blocked` entries
- [ ] All `TODO(v0.5)` markers resolved or downgraded to `TODO(v1)`
- [ ] README.md exists with install + first-run instructions
- [ ] Tagged commit: `git tag v0.5.0`

User (Roman) verifies the last item. Agent never tags.

---

## 13. Forbidden actions

You (Claude Code) MUST NOT:

- Push to git remote (only commit locally)
- Tag releases
- Modify `~/.claude/credentials.json`
- Run `install.sh` system-wide during build (use local `CC_AUTOPIPE_HOME`)
- Send TG messages with project-internal info except via `src/lib/tg.sh`
- Skip a stage's DoD checklist
- Mark stage complete without all DoD items checked
- Implement v1 features in v0.5 (out-of-scope features listed in SPEC.md §18)
- Use Claude API SDK (HARD RULE per project policy)
- Add new dependencies without OPEN_QUESTIONS.md entry first
- Edit AGENTS.md or SPEC.md (these are inputs, not outputs)
  - Exception: if SPEC.md has a typo, fix in commit with `docs:` scope and reference

---

## 14. Approved tools

You can freely use:
- `git` (commit only, NEVER push)
- `pytest`, `ruff`, `shellcheck`
- Standard POSIX tools: `grep`, `find`, `sed`, `awk`, `jq`, `flock`
- Python stdlib (`json`, `urllib`, `subprocess`, `dataclasses`, `pathlib`, `os`, `sys`, `time`)
- Bash 5+ builtins
- `curl` for tg.sh and tools/mock-quota-server.py testing
- `tools/mock-claude.sh`, `tools/mock-quota-server.py`, `tools/inject-429.sh`

You can use after explicit OPEN_QUESTIONS.md approval:
- New Python packages (must justify, prefer stdlib)
- `expect` / `pexpect` for TUI testing (avoid if possible)

You can NEVER use:
- `claude` Python SDK (`anthropic` package)
- Any LLM call from engine code
- Network calls except: `oauth/usage` endpoint (quota.py), Telegram (tg.sh)

---

## 15. Communication contract with Roman

You operate autonomously in this repo. Communication channel = TG (via tg.sh) + STATUS.md + OPEN_QUESTIONS.md.

### 15.1 When to TG-notify Roman

- Stage X DoD complete
- Blocker that prevents progress (after attempting workaround)
- Critical decision needed (architectural deviation from SPEC.md)
- v0.5 complete

Format:
```
[cc-autopipe-build] Stage A complete. 280 lines, all tests passing. Next: Stage B.
[cc-autopipe-build] BLOCKED: Q5 needs answer. Switching to Stage E pre-flight check work.
[cc-autopipe-build] v0.5 ready. Run smoke test: bash tests/smoke/full-v0.5.sh
```

### 15.2 When NOT to TG-notify

- Routine commits
- Test passes
- Open questions you can self-resolve
- Style fixes

Aim: 3-10 TG messages total during entire v0.5 build.

---

## 16. Self-review checklist before each commit

Before `git commit`:

1. [ ] Code follows §5 standards
2. [ ] Tests for this change exist or are explicitly skipped per §6.4
3. [ ] No secrets in code or commits
4. [ ] No TODOs without target version + reference
5. [ ] Commit message follows §4.2 format
6. [ ] Changed files are intentional (run `git status`, `git diff --stat`)
7. [ ] Linters pass on changed files: `ruff check <files>` or `shellcheck <files>`
8. [ ] STATUS.md updated to reflect this commit
9. [ ] If you opened/resolved an open question, OPEN_QUESTIONS.md updated

If any unchecked: don't commit yet.

---

## 17. Final note

Two failure modes from past Claude Code autonomous projects, in our community:

**Failure A:** agent rushes through DoD, marks things complete without verifying, breaks production. Mitigation: §17.1 pre-commit checklist, §2 explicit DoD per stage.

**Failure B:** agent gets stuck on one component, burns hours, loses context. Mitigation: §9 stuck protocol, §10 fresh-session boot procedure.

If you find yourself in either pattern, STOP and re-read this section.

---

End of AGENTS.md.
