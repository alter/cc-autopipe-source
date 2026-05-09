# cc-autopipe

Autonomous pipeline supervisor for **Claude Code** (MAX subscription, headless
`claude -p` mode). Runs Claude over your project repos in a loop, around the
clock, with quota-aware pacing, recovery, hooks, and Telegram alerts.

- **Engine version:** see `src/VERSION` (current: `1.3.7`)
- **Min Claude Code:** see `src/CLAUDE_CODE_MIN_VERSION` (current: `2.1.115`)
- **Min Python:** 3.11 · **Min bash:** 5
- **Tested on:** WSL2 / Ubuntu, native Linux, macOS

---

## 1. Requirements

Make sure these are on your `PATH` before installing:

| Tool             | Min       | Why                                            |
|------------------|-----------|------------------------------------------------|
| `claude`         | 2.1.115   | The engine drives `claude -p` headless         |
| `python3`        | 3.11      | Engine code uses 3.11 stdlib                   |
| `bash`           | 5.0       | Hooks rely on bash 5 features                  |
| `jq`             | any       | JSON parsing in hooks/helpers                  |
| `curl`           | any       | OAuth quota probe + Telegram                   |
| `git`            | any       | State tracking and version detection           |
| `ruff`, `shellcheck` | optional | Build-only; install if you plan to hack on engine |

`cc-autopipe doctor` (after install) checks all of these and prints
remediation hints.

---

## 2. One-shot install

From a checkout of this repo:

```bash
bash src/install.sh
```

Defaults:

- engine prefix: `~/cc-autopipe`
- per-user state: `~/.cc-autopipe`

Override with `--prefix DIR` / `--user-home DIR` if you need to.
`--no-copy` keeps the engine in place and is only useful if you set
`CC_AUTOPIPE_HOME` to your checkout (dev mode).

The installer:

1. Creates `~/.cc-autopipe/{log,projects.list,secrets.env}` (secrets file is
   `chmod 600`).
2. Copies `src/` into the prefix and freezes `VERSION` from the latest
   git tag if available.
3. Marks `helpers/`, `hooks/`, and `lib/` shell scripts executable.
4. Prints the `PATH` snippet for next steps.

---

## 3. First-time host setup

After `install.sh`:

```bash
# 3.1 — put helpers on PATH (add to your shell rc).
export PATH="$HOME/cc-autopipe/helpers:$PATH"

# 3.2 — fill in Telegram secrets (optional, but strongly recommended for alerts).
$EDITOR ~/.cc-autopipe/secrets.env
#   TG_BOT_TOKEN=...
#   TG_CHAT_ID=...

# 3.3 — verify the install.
cc-autopipe doctor
```

`cc-autopipe doctor` runs 11 checks: `claude` binary + version, python,
jq, ruff, shellcheck, secrets perms, hooks executable, OAuth token
readable, Telegram send-test, `oauth/usage` reachability, and (on WSL)
WSL systemd. Use `--offline` to skip the two network checks, `--json`
for machine-readable output. Exit code is non-zero iff any check is
`fail`.

If `claude login` hasn't been done yet on this host, do it now — the
engine reads the OAuth token from `~/.claude/credentials.json` (Linux)
or the macOS Keychain.

---

## 4. Per-project setup

Inside any git repo you want the engine to drive:

```bash
cd /path/to/your-project
cc-autopipe init
```

This creates a `.cc-autopipe/` directory with templates, registers the
project in `~/.cc-autopipe/projects.list`, writes
`.claude/settings.json` (with absolute hook paths), and appends
engine-managed entries to `.gitignore`.

You then **must** edit four files before the project is ready:

| File                      | Purpose                                            |
|---------------------------|----------------------------------------------------|
| `.cc-autopipe/prd.md`     | What to build. Acceptance criteria as `- [ ]` items. ~2 KB max — only the first 2048 bytes ship to each cycle. |
| `.cc-autopipe/context.md` | Stack, conventions, constraints. ~1 KB max.       |
| `.cc-autopipe/verify.sh`  | Returns one JSON line: `{passed, score, prd_complete, details}`. The orchestrator decides DONE from this. **Replace the stub block before running `start`** — the default fails closed. |
| `.cc-autopipe/rules.md`   | Project-specific rules Claude must follow.        |

`config.yaml` controls iteration caps, model defaults, auto-escalation,
the improver subagent, and `cc-autopipe-detach` defaults — review it
once, the defaults are fine for most projects. ML / long-training
workloads should bump `detach_defaults.max_wait_sec` (default 4h → 8–
48h depending on workload; see comments in the file).

Validate the wiring with a single cycle before going live:

```bash
cc-autopipe run . --once
```

Exit code: `0` cycle completed · `1` project missing/uninitialized · `2`
phase ended in `failed`.

---

## 5. Running the orchestrator

```bash
cc-autopipe start              # daemonize: stderr/stdout → ~/.cc-autopipe/log/orchestrator-{stderr,stdout}.log (50 MB rotation, keeps .1/.2/.3)
cc-autopipe start --foreground # stay attached (use this under systemd)
cc-autopipe stop               # graceful shutdown via the singleton lock
cc-autopipe status             # one-screen overview (add --json for machine output)
cc-autopipe snapshot           # 13-section universal health snapshot (see §6)
cc-autopipe health             # recent metrics (add --24h for window, --json for machine)
cc-autopipe tail               # follow ~/.cc-autopipe/log/aggregate.jsonl
cc-autopipe resume <project>   # clear PAUSED/FAILED and reset failure counters
cc-autopipe run <project>      # one-off cycle, bypasses singleton (--once for a single iteration)
cc-autopipe doctor             # re-verify environment any time
cc-autopipe --version
```

Only one orchestrator runs at a time per `~/.cc-autopipe/` (singleton
lock at `orchestrator.pid`). It walks `projects.list` FIFO, spawns
`claude -p` per active project, and logs every cycle event to
`aggregate.jsonl`.

---

## 6. Monitoring — is it working?

### 6.1 Quick check (one command)

```bash
cd /path/to/your-project
cc-autopipe snapshot
```

Renders 12 universal sections + an optional 13th project hook:

| § | What it shows                          | Source                                                   |
|---|----------------------------------------|----------------------------------------------------------|
| 1 | Orchestrator running, recent events    | `cc-autopipe status`                                     |
| 2 | What Claude is working on now          | `<project>/.cc-autopipe/CURRENT_TASK.md`                |
| 3 | Phase + per-phase detail (active/detached/paused/failed/done) | `<project>/.cc-autopipe/state.json` |
| 4 | All engine state flags (sanity dump)   | `state.json`                                             |
| 5 | Cycle timeline (last events)           | `journalctl` → `orchestrator-stderr.log` → `aggregate.jsonl` |
| 6 | Errors / tracebacks                    | same fallback chain, grepped                            |
| 7 | Last N events globally                 | `~/.cc-autopipe/log/aggregate.jsonl`                    |
| 8 | Backlog stats (open / in-progress / done) | `<project>/backlog.md` or `<project>/.cc-autopipe/backlog.md` |
| 9 | Detached operations + live runners (claude, docker, etc.) | `<project>/.cc-autopipe/detached/` + `ps` |
| 10| `knowledge.md` + `findings_index.md`   | `<project>/.cc-autopipe/`                                |
| 11| Quota burn (5h / 7d) + disk free       | `cc-autopipe health --24h`                              |
| 12| Engine stderr log size + recent errors | `~/.cc-autopipe/log/orchestrator-stderr.log`            |
| 13| Project-specific extras (optional)     | `<project>/.cc-autopipe/snapshot-extra.sh`              |

Flags:

```bash
cc-autopipe snapshot --since '24 hours ago'    # default: 6 hours ago
cc-autopipe snapshot --events 30               # default: 15 events in §7
cc-autopipe snapshot --project /path/to/proj   # default: cwd
```

### 6.2 Project-specific extensions

Universal snapshot only covers engine surface. Anything domain-specific
(ML candidate reports, leaderboards, custom artefact directories) goes
in **`<project>/.cc-autopipe/snapshot-extra.sh`** — `cc-autopipe
snapshot` runs it as the final section if it exists and is executable.

Available env vars in the extra script:

| Var          | Value                                            |
|--------------|--------------------------------------------------|
| `PROJECT`    | absolute project root                            |
| `CCA`        | `$PROJECT/.cc-autopipe`                          |
| `STATE`      | `$CCA/state.json`                                |
| `USER_HOME`  | `~/.cc-autopipe` (or `$CC_AUTOPIPE_USER_HOME`)   |
| `AGG`        | `$USER_HOME/log/aggregate.jsonl`                 |
| `STDERR_LOG` | `$USER_HOME/log/orchestrator-stderr.log`         |
| `SINCE`      | the `--since` arg (e.g. `'6 hours ago'`)         |

Example skeleton (research-style project with PROMOTION artefacts):

```bash
#!/bin/bash
# .cc-autopipe/snapshot-extra.sh — project-local snapshot additions
set -u

echo
echo "  PROMOTION artefacts (last 5):"
ls -lat "$PROJECT"/data/debug/CAND_*_PROMOTION.md 2>/dev/null \
    | head -5 | sed 's/^/    /'

echo
echo "  LEADERBOARD top entries:"
LB="$PROJECT/data/debug/LEADERBOARD.md"
[ -f "$LB" ] && head -25 "$LB" | sed 's/^/    /' || echo "    (no LEADERBOARD.md yet)"

echo
echo "  Recent data/ writes (last 2h):"
find "$PROJECT/data/" -mmin -120 -type f 2>/dev/null \
    -printf '%T@ %p\n' | sort -rn | head -5 | awk '{print "    " $2}'
```

`chmod +x` it once. Add to project `.gitignore` if it contains paths
you don't want shared.

### 6.3 Raw log surfaces

If you need the raw stream rather than a snapshot:

```bash
# Live-follow the cycle event stream (every project, every event):
cc-autopipe tail
cc-autopipe tail --project AI-trade --event cycle_start,cycle_end
cc-autopipe tail -n 50 --no-follow              # last 50, no follow

# Engine stderr/stdout (daemon mode only — empty under --foreground):
tail -f ~/.cc-autopipe/log/orchestrator-stderr.log
tail -f ~/.cc-autopipe/log/orchestrator-stdout.log

# Under systemd:
journalctl -u cc-autopipe -f                    # follow
journalctl -u cc-autopipe --since '6 hours ago' --no-pager
journalctl -u cc-autopipe-watchdog -f           # watchdog separately

# Aggregate event log (machine-readable; everything cc-autopipe knows):
tail -f ~/.cc-autopipe/log/aggregate.jsonl | jq -c '{ts, project, event}'

# Per-project state (one cycle's worth of facts):
jq . /path/to/project/.cc-autopipe/state.json
cat /path/to/project/.cc-autopipe/CURRENT_TASK.md
cat /path/to/project/.cc-autopipe/HUMAN_NEEDED.md   # only if blocked
```

### 6.4 "Is it actually working?" checklist

Run through these in order whenever you're unsure:

1. `cc-autopipe status` → orchestrator PID present, uptime sane.
2. `cc-autopipe snapshot` §3 → phase is `active` or `detached` (not
   `failed` / `paused` / `done` unexpectedly).
3. §5 timeline shows a `cycle_end` within the last hour or so.
4. §6 errors are clean (or you understand the ones that are there).
5. §11 quota under 90% on both 5h and 7d.
6. §12 stderr log mtime is recent (orchestrator hasn't deadlocked
   silently).

If §3 says `failed`, read `<project>/.cc-autopipe/HUMAN_NEEDED.md`,
fix the root cause, then `cc-autopipe resume <project>`. If §6 has
fresh tracebacks, the same file usually points at the symptom.

---

## 7. Run as a service

### Linux (systemd) — system unit

```bash
sudo cp deploy/systemd/cc-autopipe.service          /etc/systemd/system/
sudo cp deploy/systemd/cc-autopipe-watchdog.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cc-autopipe cc-autopipe-watchdog
journalctl -u cc-autopipe -f
```

If your install path or username differs, edit `ExecStart` and `User=`
in both unit files first. Full notes: `deploy/systemd/INSTALL.md`.

### WSL2

WSL2 systemd is opt-in. Two paths:

- **Path A (preferred):** enable WSL systemd and use the units above.
- **Path B (fallback):** drive the orchestrator from Windows Task
  Scheduler with `wsl.exe -d Ubuntu -e bash -c "..."`.

Step-by-step for both: `deploy/WSL2.md`. `cc-autopipe doctor` reports
`wsl-systemd: ok` on Path A and `fail` (with remediation hint) on
Path B.

### macOS (launchd)

```bash
cc-autopipe install-launchd        # writes/loads ~/Library/LaunchAgents/com.cc-autopipe.plist
cc-autopipe uninstall-launchd      # the inverse
```

---

## 8. Helpers used from inside Claude sessions

These run **inside** a Claude session (the engine exposes them on
`PATH` for hooks):

| Command                      | Purpose                                                           |
|------------------------------|-------------------------------------------------------------------|
| `cc-autopipe checkpoint <text>` | Save `.cc-autopipe/checkpoint.md` mid-cycle.                  |
| `cc-autopipe block <reason>`    | Mark project blocked, write `HUMAN_NEEDED.md`, alert via TG. |
| `cc-autopipe detach --reason ... --check-cmd ...` | Release the orchestrator slot for a long background task; engine polls `--check-cmd` until 0 (→ active) or `--max-wait` (→ failed). Reads defaults from `config.yaml#detach_defaults` or env. |
| `cc-autopipe smoke <script>`    | Validate a pipeline script before detaching (v1.3.3+).        |

`detach` defaults are 600 s poll / 14400 s (4 h) max-wait. **Bump
`max_wait_sec` for ML training** — 4 h is short for typical
multi-period or ensemble runs; see `config.yaml` comments.

---

## 9. Filesystem layout

Per host:

```
~/cc-autopipe/                  # engine (set by --prefix)
├── VERSION
├── helpers/                    # cc-autopipe + cc-autopipe-* dispatchers (incl. snapshot, detach, smoke)
├── hooks/                      # session-start, pre-tool-use, stop, stop-failure
├── cli/                        # init, start, stop, status, resume, run, tail, doctor, health
├── orchestrator/               # main loop + cycle/recovery/quota
├── lib/                        # state, locking, quota, notify, …
└── templates/.cc-autopipe/     # seed for `cc-autopipe init`

~/.cc-autopipe/                 # per-user state (set by --user-home)
├── projects.list               # absolute paths of registered projects
├── secrets.env                 # chmod 600 — TG_BOT_TOKEN / TG_CHAT_ID
├── orchestrator.pid            # singleton lock + payload
├── quota-cache.json            # last quota snapshot
└── log/
    ├── aggregate.jsonl                    # cycle event stream (always written)
    ├── health.jsonl                       # per-cycle quota/disk records (cc-autopipe health reads this)
    ├── orchestrator-stderr.log[.1…3]      # rotated stderr — daemon mode only
    └── orchestrator-stdout.log[.1…3]      # rotated stdout — daemon mode only
```

Per project:

```
<project>/.cc-autopipe/
├── prd.md                      # PRD with `- [ ]` acceptance criteria
├── context.md                  # stack/conventions
├── verify.sh                   # returns §7.7 JSON line
├── rules.md                    # project rules
├── knowledge.md                # append-only journal Claude writes
├── config.yaml                 # iteration caps, models, detach defaults
├── agents.json                 # subagent roster
├── state.json                  # engine-managed (gitignored)
├── checkpoint.md               # engine-managed (gitignored)
├── HUMAN_NEEDED.md             # appears when project is blocked (gitignored)
├── snapshot-extra.sh           # optional, executable: project hook for `cc-autopipe snapshot`
└── memory/                     # engine-managed scratch (gitignored)

<project>/.claude/settings.json # hook wiring (gitignored)
```

---

## 10. Troubleshooting

- **Orchestrator won't start:** another orchestrator is running. Check
  `cc-autopipe status` — it reports the live PID + start time. `cc-autopipe
  stop` to shut it down cleanly.
- **`doctor` says `claude binary: < 2.1.115`:** `claude self-update` or
  reinstall.
- **`OAuth token: no credentials file`:** run `claude login` once on
  this host.
- **`secrets.env: perms 0644 (must be 0600)`:** `chmod 600
  ~/.cc-autopipe/secrets.env`.
- **Project stuck in `failed`:** read `<project>/.cc-autopipe/HUMAN_NEEDED.md`
  for context, fix the root cause, then `cc-autopipe resume <project>`.
- **WSL boot has no orchestrator:** either Path A systemd isn't enabled
  yet (`cc-autopipe doctor` will say so), or Path B Task Scheduler
  isn't registered. See `deploy/WSL2.md`.
- **Long ML cycles end in `failed` from "stuck":** v1.3.7 closed the
  pause+resume staleness bug — make sure you're on `1.3.7+`. If still
  failing, bump `detach_defaults.max_wait_sec` in `config.yaml`.

---

## 11. Version notes

`STATUS.md` is the live build journal — current state, deviations, test
counts. Per-version build summaries live in `V13_BUILD_DONE.md` …
`V137_BUILD_DONE.md`.

Headlines for the latest releases:

- **v1.3.7** — 3-tier verdict parser (Acceptance/Conclusion fallback +
  ✅/❌ markers); filesystem-evidence stuck gate that respects
  in-cycle progress; unconditional `last_activity_at` refresh on
  cycle_end with progress (closes pause+resume staleness bug). Schema
  unchanged at v6 with one additive field.
- **v1.3.6** — heading-style `Verdict:` parser + new `CONDITIONAL`
  state; phase-done auto-resume when backlog reopens; broader
  knowledge.md sentinel vocabulary.
- **v1.3.5** — `[research]` artifact contract; PROMOTION.md format
  parser + 5-child ablation auto-spawn; persistent `LEADERBOARD.md`.
- **v1.3.4** — transient classification + retry + network probe gate.
- **v1.3.3** — liveness check, `cc-autopipe smoke`, knowledge.md detach
  gate.
- **v1.3** — full 14-day-autonomy hardening pass.

Schema: persisted state is at v6 (unchanged across v1.3.5 → v1.3.7).
Migrations are dataclass-defaults — older state files load
transparently.

---

## For implementing agents (build process)

This repo is the **build** repo. If you're an agent picking up the
build, read in order:

1. `AGENTS.md` — process spec (HOW to build).
2. `SPEC.md` — product spec (WHAT to build).
3. `STATUS.md` — current build state.
4. `OPEN_QUESTIONS.md` — unresolved blockers.

Then begin work per `AGENTS.md` §10 boot procedure. Rules in `CLAUDE.md`
(no remote pushes, no tags, no Anthropic API SDK, always update
`STATUS.md` after a commit) are non-negotiable.
