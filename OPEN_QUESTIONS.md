# Open Questions

Tracked questions per AGENTS.md §8. Each entry has status: open | resolved | blocked.

When resolving, leave the entry but mark `Status: resolved` with resolution
commit reference. This builds an audit trail.

---

## Q1. [STAGE-E] [open] Exact format of oauth/usage response in Apr 2026

**Discovered:** 2026-04-28 (during SPEC drafting)
**Stage:** E (Quota awareness)
**Blocking:** lib/quota.py implementation

**Question:**
The `oauth/usage` endpoint format documented in codelynx.dev (Oct 2025) may have
changed. Need to verify actual response structure against current Claude Code 2.1+
behavior.

**Investigation plan:**
1. Once OAuth token reading works (early Stage E), make one curl call manually
2. Compare structure to assumed `{five_hour: {utilization, resets_at}, seven_day: {...}}`
3. If different, update quota.py parser accordingly

**Mitigation if cannot resolve:**
quota.py returns None on parse failure → orchestrator falls back to ratelimit.py
ladder. Pipeline still works, just less efficient.

---

## Q2. [STAGE-D] [open] Behavior of `claude --resume` when JSONL deleted

**Discovered:** 2026-04-28
**Stage:** D (Locking and recovery)
**Blocking:** orphaned-session recovery logic

**Question:**
If `~/.claude/projects/*/{session_id}.jsonl` is deleted but state.json still has
the session_id, does `claude --resume <id>` error out cleanly, or does it produce
unexpected behavior (silent fresh session, hang, etc)?

**Investigation plan:**
1. During Stage D, manually delete a session JSONL
2. Run `claude --resume <id>` against it
3. Document exit code and stderr behavior

**Mitigation:**
Catch any error from `claude --resume`, fall back to fresh `claude -p`. Log the
event to failures.jsonl with reason="orphan_session".

---

## Q3. [STAGE-C] [resolved] Hook Stop event session_id reliability

**Discovered:** 2026-04-28
**Stage:** C (Hooks)
**Blocking:** stop.sh saving session_id for next resume

**Question:**
SPEC.md §10.3 assumes Stop hook receives `session_id` field reliably in stdin
JSON. Need to verify in current Claude Code 2.1.115+.

**Investigation plan:**
1. During Stage C, instrument stop.sh with raw input dump
2. Run via mock-claude.sh first, then real claude on a tiny test
3. Confirm session_id always present, document any edge cases

**Mitigation:**
If session_id is unreliable, parse it from `~/.claude/projects/` directory by
finding latest JSONL mtime instead.

**Resolution (2026-04-29, Stage C):**
Two-pronged verification:

1. **Mock path (fully under test):** tools/mock-claude.sh gained a
   `CC_AUTOPIPE_MOCK_DUMP_INPUT` facility that dumps the Stop hook's
   exact stdin to a file. tests/integration/test_orchestrator_claude.py
   `test_session_id_round_trip_from_mock_into_state` reads that file
   and asserts `session_id` is present, non-empty, and matches what
   landed in state.json after stop.sh ran.
   tests/unit/test_hooks/test_stop.sh additionally exercises the
   "no session_id" case (state.session_id remains null without
   crashing).

2. **Real-claude path:** deferred to Stage G smoke test. Mitigation
   already in place: stop.sh's `[ -n "$SESSION" ]` guard means a
   missing session_id is silently tolerated (state.session_id stays
   null) — the Stage E session-JSONL fallback (find latest mtime under
   ~/.claude/projects/) is therefore a v1 enhancement, not a v0.5
   blocker. If real claude turns out NOT to populate session_id, the
   pipeline still functions; cycles just don't `--resume` each other.

Reference commits:
- `21a0851 hooks: add stop.sh with verify.sh runner …`
- `631b55e tests: add unit tests for stop hook` (Q3 round-trip case)
- `deb8af4 tests: integration tests for orchestrator + claude + hooks
   pipeline` (mock-dump verification)
- `0812494 tools: extend mock-claude.sh with popen-style invocation`
   (DUMP_INPUT facility)

---

## Q4. [STAGE-E] [open] macOS Keychain permission prompt on first access

**Discovered:** 2026-04-28
**Stage:** E (Quota awareness)
**Blocking:** Smooth install on macOS

**Question:**
First call to `security find-generic-password -s "Claude Code-credentials" -w`
may prompt user with Keychain dialog. Behavior on subsequent calls? Headless
context (no GUI)?

**Investigation plan:**
1. Test on macOS during Stage E
2. If interactive prompt happens, document workaround in install.sh
3. Possible workaround: instruct user to run a one-time `security` command
   manually before first cc-autopipe start

**Mitigation:**
quota.py returns None on Keychain access failure → ladder fallback works.

---

## Q5. [STAGE-D] [open] --max-turns counter reset on resume

**Discovered:** 2026-04-28
**Stage:** D (Locking and recovery)
**Blocking:** Long-task continuation strategy

**Question:**
When `claude --resume <id> -p "..." --max-turns 35` is invoked, is the turns
counter reset to 0 for this invocation, or accumulated across the original
session?

If accumulated: long sessions hit the cap quickly across multiple resumes.
If per-invocation: our checkpoint-based continuation works as designed.

**Investigation plan:**
1. Stage D testing: run a session, exit at turn 30, resume, check counter
2. If accumulated, change strategy: don't resume, start fresh sessions with
   checkpoint.md as guide

**Mitigation:**
Either behavior is workable. Document and adapt.

---

## Q6. [STAGE-C] [open] backlog.md tag handling for v1+ tags in v0.5

**Discovered:** 2026-04-28
**Stage:** C (Hooks) or B
**Blocking:** None (clarification only)

**Question:**
SPEC.md §7.6 says v0.5 ignores `[architect]` and `[parallel-impl]` tags. Confirm:
should these tagged tasks be treated as normal `[ ]` open tasks, or skipped?

Recommendation: treat as normal open. Tag is metadata for later, not a skip
signal in v0.5.

**Mitigation:**
Default to "treat as normal open task". Document choice.

---

## Q7. [STAGE-A] [resolved] Telegram multiline message rendering

**Discovered:** 2026-04-28
**Stage:** A (Foundations)
**Blocking:** None (cosmetic)

**Question:**
Telegram API requires escaped newlines in some cases. tg.sh sends `-d
"text=$MSG"` which may handle multiline poorly.

**Investigation plan:**
Stage A testing: send a 3-line message, verify rendering in TG client.
If broken, add MarkdownV2 mode + escape sequences.

**Mitigation:**
For v0.5, restrict TG messages to single-line. Use semicolons instead of
newlines.

**Resolution (2026-04-29, Stage A):**
Switched tg.sh from `-d "text=$MSG"` to `--data-urlencode "text=$MSG"`.
curl handles encoding correctly including newlines, special chars, and
unicode without further escaping. End-to-end TG round-trip verification
deferred to a session where Roman supplies real `secrets.env` (DoD
validates the no-secrets/exit-0 contract, not the wire format).

---

## Q9. [STAGE-A] [resolved] compat.sh: GNU coreutils on macOS host

**Discovered:** 2026-04-29 during Stage A smoke testing.
**Stage:** A (Foundations)
**Blocking:** Compat shim correctness on hybrid hosts.

**Question:**
SPEC.md §6.6 dispatches date/stat syntax on `uname -s` (Darwin → BSD,
Linux → GNU). On Roman's macOS host, `/opt/homebrew/opt/coreutils/
libexec/gnubin` is ahead of `/usr/bin` on PATH, so `date` and `stat`
are GNU even though uname says Darwin. The spec's BSD branch fails.

**Resolution:**
compat.sh now feature-detects the flavour:
  - `date --version` succeeds → GNU; fails → BSD
  - `stat --version` succeeds → GNU; fails → BSD
This is a strict superset of the spec's behaviour and is more robust.
SPEC.md §6.6 sample code is illustrative — keeping the function
contract (`date_from_epoch`, `file_mtime`) untouched; only the dispatch
mechanism is harder. No spec edit needed; documenting the choice here.

---

## Q8. [STAGE-D] [open] flock behavior on macOS

**Discovered:** 2026-04-28
**Stage:** D (Locking and recovery)
**Blocking:** Cross-platform locking

**Question:**
`flock` from coreutils on macOS may not exist (default `flock` syntax differs
from Linux util-linux flock). util-linux flock from brew may not be in PATH.

**Investigation plan:**
1. Stage D: test flock invocation pattern on macOS
2. If flock unavailable: use fcntl-based Python locking from state.py instead
3. Document install.sh dependency: `brew install util-linux` or use Python lock

**Mitigation:**
Fall back to Python-based fcntl locking if bash flock fails. Cross-platform
solution costs minor complexity but works everywhere.

---

## Q10. [STAGE-B] [resolved] src/cli/ subdirectory not in SPEC §5.1

**Discovered:** 2026-04-29 during Stage B implementation.
**Stage:** B (Orchestrator skeleton)
**Blocking:** None. Behavioural deviation only.

**Question:**
SPEC.md §5.1 lists `helpers/cc-autopipe-checkpoint`, `helpers/cc-autopipe-block`
under `helpers/`, plus `lib/{state,quota,ratelimit}.py` under `lib/`. There
is no top-level slot for "command implementations". Stage B introduced
`src/cli/init.py` and `src/cli/status.py` as Python modules invoked from
the bash dispatcher.

Why not bash:
- `cc-autopipe init` does template substitution, JSON validation
  (settings.json), and idempotent line-edits to projects.list and
  .gitignore. Doing this in pure bash is error-prone (no atomic
  multi-line edits, fragile JSON manipulation, gnarly heredoc escaping).
- `cc-autopipe status` produces a human table AND `--json` output. JSON
  serialization across nested project state, quota cache, and event
  history is trivial in Python and ugly in bash.

Why not in `lib/`:
- `lib/` is shared library code (state, quota, ratelimit, tg). CLI
  command implementations are distinct — they have argparse surfaces,
  user-facing output, and side effects on the user environment. Putting
  them in `lib/` would conflate two concerns.

**Resolution:**
Implementation accepted. The bash dispatcher (`src/helpers/cc-autopipe`)
delegates `init` and `status` to `python3 $CC_AUTOPIPE_HOME/cli/init.py`
and `.../cli/status.py` respectively. This pattern will likely also be
used for `cc-autopipe doctor` and `cc-autopipe tail` in Stage F.

Reference commits:
- `8064a5f cli: implement cc-autopipe init`
- `dc8d059 cli: implement cc-autopipe status`

**SPEC.md update note for v1 docs review:**
SPEC.md §5.1 should be amended to add `cli/` between `lib/` and
`templates/`, with command-implementation modules listed there. Current
v0.5 spec under-specifies the boundary between the bash dispatcher and
its command implementations.

---

## Resolved questions

(None yet. As questions are resolved, move them here with resolution commit refs.)
