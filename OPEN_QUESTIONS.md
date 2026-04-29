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

## Q2. [STAGE-D] [deferred-to-stage-G] `claude --resume` with deleted JSONL

**Discovered:** 2026-04-28
**Stage:** D (Locking and recovery)
**Blocking:** None — mitigation is in place.

**Question:**
If `~/.claude/projects/*/{session_id}.jsonl` is deleted but state.json
still has the session_id, does `claude --resume <id>` error out cleanly?

**Investigation outcome (Stage D, 2026-04-29):**
mock-claude.sh accepts --resume IDs unconditionally and runs the
selected scenario; it does NOT model real claude's JSONL-existence
check. Verifying behaviour against real claude requires actually
spending MAX quota, which AGENTS.md §13 forbids during build.

**Resolution (deferred to Stage G):**
Verification against real `claude` will happen during Stage G's
hello-fullstack smoke run. Expected outcomes and the mitigation that
applies regardless:
- If real claude errors with non-zero rc: orchestrator already logs
  cycle_end with the rc; consecutive_failures will accumulate, and on
  the next cycle the build_claude_cmd unconditionally tries --resume
  again. If this turns out to be the actual behaviour, we'll add
  detection (parse stderr for "session not found", clear session_id,
  retry fresh) in Stage G or v1.
- If real claude silently starts a fresh session: state.session_id
  already gets overwritten by the next Stop hook, so the system
  self-corrects within one cycle. No code change needed.

**Mitigation already in code:**
src/orchestrator's `_build_claude_cmd` notes: "claude itself errors
cleanly if the JSONL is missing." If that turns out to be wrong
during Stage G, we'll iterate then.

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

## Q5. [STAGE-D] [deferred-to-stage-G] --max-turns counter reset on resume

**Discovered:** 2026-04-28
**Stage:** D (Locking and recovery)
**Blocking:** None — both behaviours are workable.

**Question:**
When `claude --resume <id> -p "..." --max-turns 35` is invoked, is the
turns counter reset to 0 for this invocation, or accumulated across
the original session?

**Investigation outcome (Stage D, 2026-04-29):**
mock-claude.sh ignores --max-turns entirely (it just runs hooks and
exits). Real-claude verification needs MAX quota, deferred per
AGENTS.md §13.

**Resolution (deferred to Stage G):**
Stage G will observe whether long-running hello-fullstack hits the
35-turn cap rapidly under resume. The orchestrator's current strategy
already accommodates either outcome:
- If per-invocation reset (the SPEC's assumption): nothing to do.
- If accumulated across resume: add `--resume` only when state's
  `last_progress_at` is recent (e.g. <30min). Otherwise start fresh
  with checkpoint.md as the continuity guide. This is a small change
  to `_build_claude_cmd`.

**Mitigation already in code:**
Checkpoint-based continuity is already supported — `_build_prompt`
emits "RESUME FROM CHECKPOINT" when `.cc-autopipe/checkpoint.md`
exists, regardless of session_id. So even if we drop --resume
entirely under v1, the pipeline keeps working.

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

## Q8. [STAGE-D] [resolved] flock behavior on macOS

**Discovered:** 2026-04-28
**Stage:** D (Locking and recovery)
**Blocking:** Cross-platform locking

**Question:**
`flock` from coreutils on macOS may not exist (default `flock` syntax
differs from Linux util-linux flock). util-linux flock from brew may
not be in PATH.

**Resolution (Stage D, 2026-04-29):**
Skipped shell `flock(1)` entirely — used Python `fcntl.flock` from
the stdlib. Identical syscall semantics on Linux + macOS, no brew
dependency, and POSIX advisory locks auto-release when the holder
process dies (which makes the SPEC §8.4 kill -9 recovery automatic).

This is a strict superset of the SPEC's "Both use flock (Linux/macOS
via brew)" statement and avoids the install.sh `brew install
util-linux` step the SPEC implied.

Reference commit: `992683b lib: add fcntl-based locking with
singleton + per-project + heartbeat`.

**SPEC.md update note for v1 docs review:**
SPEC §8.3 "Both use flock (Linux/macOS via brew)" should be amended
to "Both use fcntl.flock from Python stdlib — works on Linux + macOS
without brew dependency."

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

## Q11. [STAGE-D] [resolved] Factor locking out of src/orchestrator

**Discovered:** 2026-04-29 at start of Stage D.
**Stage:** D (Locking and recovery)
**Blocking:** None. Pre-implementation architectural choice.

**Question:**
After Stage C, src/orchestrator is 447 lines. SPEC.md §6.1 budgets it
at ~400 lines for the FINAL form. Stage D adds singleton lock,
per-project lock, heartbeat thread, stale detection, and crash
recovery — naive estimate ~150-200 lines, pushing the file to 600+.

Two options:
1. Keep the orchestrator monolithic and accept the budget overrun.
2. Factor locking into `src/lib/locking.py`, keep orchestrator as
   "main loop only".

**Resolution:**
Option 2. Factor into `src/lib/locking.py`.

Reasoning:
- Locking has a clean library API (acquire / release / heartbeat) with
  no need for orchestrator state. Easy to extract.
- Q10 already established the same pattern for command implementations
  (`src/cli/init.py`, `src/cli/status.py`). This is consistent precedent.
- `lib/locking.py` is testable in isolation. A monolithic orchestrator
  forces locking tests to spin up the whole main loop.
- SPEC §5.1 listed `lib/state.py`, `lib/quota.py`, `lib/ratelimit.py`
  under `lib/`. Adding `lib/locking.py` is a natural extension with the
  same character (atomic file operations, no main-loop coupling). The
  spec under-specifies but doesn't contradict.

**SPEC.md update note for v1 docs review:**
SPEC.md §5.1 should add `lib/locking.py` to the lib/ inventory (just
like Q10's note about adding `cli/`). Also §6.1's "~400 lines"
orchestrator budget should be split between orchestrator (main loop +
prompt + claude spawn) and locking.

Reference commit (forward, will be filled in by the lib/locking.py
commit when it lands).

---

## Resolved questions

(None yet. As questions are resolved, move them here with resolution commit refs.)
