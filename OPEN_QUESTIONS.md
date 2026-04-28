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

## Q3. [STAGE-C] [open] Hook Stop event session_id reliability

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

## Q7. [STAGE-A] [open] Telegram multiline message rendering

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

## Resolved questions

(None yet. As questions are resolved, move them here with resolution commit refs.)
