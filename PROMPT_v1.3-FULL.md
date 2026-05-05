# PROMPT-v1.3-full.md — cc-autopipe v1.3 build (true 14-day autonomy)

## Mission

cc-autopipe v1.2 works in supervised mode but cannot survive 2 weeks of fully autonomous operation without human intervention. Roman is going offline for 2 weeks. Build v1.3 to deliver true autonomous operation.

This is one comprehensive build on a clean v1.2 baseline. ~10 groups, ~30 atomic commits, ~120 tests. Estimate ~30 hours of work spread across multiple sessions per AGENTS.md.

The build is structured as: **first refactor, then base infrastructure, then hardening.** Refactor is non-negotiable — without it, base infrastructure inflates `process_project` past readability and hardening becomes impossible to land cleanly.

Hardening replaces naive failure modes (auto-skip, hope-based knowledge updates, blind candidate generation) with enforced helicopter-view loops. Roman is offline; the engine cannot rely on Telegram alerts, HUMAN_NEEDED.md files, or operator intervention to keep moving.

## Required reading (in order)

1. `/mnt/c/claude/artifacts/repos/cc-autopipe-source/SPEC.md`
2. `/mnt/c/claude/artifacts/repos/cc-autopipe-source/AGENTS.md`
3. `/mnt/c/claude/artifacts/repos/cc-autopipe-source/STATUS.md` (confirm v1.2 final state)
4. `/mnt/c/claude/artifacts/repos/cc-autopipe-source/src/orchestrator` (current monolith — read it whole, you're about to split it)
5. `/mnt/c/claude/artifacts/repos/cc-autopipe-source/src/lib/state.py` (State + CurrentTask)
6. `/mnt/c/claude/artifacts/repos/cc-autopipe-source/src/lib/failures.py` + `human_needed.py` (current verify-pattern path that GROUP H replaces)
7. `/mnt/c/claude/artifacts/repos/cc-autopipe-source/src/lib/quota.py` + `quota_monitor.py`
8. `/mnt/c/claude/artifacts/repos/cc-autopipe-source/src/lib/claude_settings.py` (the hooks hotfix)
9. `/mnt/c/claude/artifacts/repos/cc-autopipe-source/src/lib/session_start_helper.py` (where injection blocks live)
10. `/mnt/c/claude/artifacts/repos/cc-autopipe-source/src/hooks/` (all hook scripts)
11. `/mnt/c/claude/artifacts/repos/cc-autopipe-source/src/cli/start.py` (if absent — note: dispatcher invokes orchestrator directly), `stop.py`, `doctor.py`
12. `/mnt/c/claude/artifacts/repos/cc-autopipe-source/tests/` (current passing baseline)

Run `pytest tests/ -x --tb=short -q` BEFORE any change. Confirm 396 passed + 1 skipped (v1.2 final per STATUS.md). Confirm `bash tests/smoke/run-all-smokes.sh` is green. If either is red — STOP, write BLOCKED.md.

---

## Build order and dependency rationale

```
GROUP G (refactor)              MUST be first; everything else depends on clean module structure
  ↓
GROUP A (memory persistence)    findings.py + knowledge.md baseline; H and I depend on these
  ↓
GROUP B (recovery)              activity detection + stuck detection + auto-recovery
  ↓
GROUP C (infra resilience)      systemd + disk + atomic state + watchdog
  ↓
GROUP D (PRD lifecycle)         complete detection + research mode WITH anti-duplication
  ↓
GROUP E (quota injection)       text-only; depends on session_start_helper
  ↓
GROUP F (observability)         daily report + health metrics + bypass cleanup
  ↓
GROUP H (META_REFLECT)          replaces verify-pattern HUMAN_NEEDED for autonomy
  ↓
GROUP I (enforced knowledge)    sentinel-based knowledge.md update enforcement
  ↓
GROUP K (WSL2 doctor)           independent; could land anywhere after G but goes here for cleanliness
```

After each GROUP: full pytest + all smokes must stay green. If any test breaks — fix before next group.

---

## GROUP G — Orchestrator package refactor (DO FIRST)

This is a pure mechanical refactor. No behavior change. Every existing test must pass without changes to test logic (only imports).

### G1. Convert `src/orchestrator` from script to package

Current: `src/orchestrator` is an extensionless executable Python script. Bash dispatcher invokes via `python3 "$CC_AUTOPIPE_HOME/orchestrator"`. `src/cli/run.py` imports it via `SourceFileLoader`.

Target: `src/orchestrator/` is a directory with `__main__.py`. `python3 path/to/dir` automatically executes `dir/__main__.py` — bash dispatcher unchanged.

Steps:
1. Create `src/orchestrator/__init__.py` (empty).
2. Create `src/orchestrator/__main__.py` containing `from orchestrator.main import main; sys.exit(main(sys.argv[1:]))`.
3. Move existing orchestrator content into the modules listed in G2.
4. Update `src/cli/run.py` to use `importlib.import_module('orchestrator.cycle')` instead of `SourceFileLoader`.
5. Verify `python3 src/orchestrator` still runs the loop (smoke check).

### G2. Module split (no module exceeds 350 lines)

```
src/orchestrator/
  __init__.py           empty
  __main__.py           entry (imports orchestrator.main, calls run)
  main.py               main loop, signal handlers, projects.list walk, singleton lock
  cycle.py              process_project (was 400 lines; must shrink via delegation)
  preflight.py          _preflight_quota + disk check (placeholder, populated by C2) + activity (B1)
  prompt.py             _build_prompt + _build_claude_cmd + model/escalation config readers
  recovery.py           auto-recovery from failed (B3) + meta-reflection coordination (H)
  research.py           PRD-complete detection (D1) + research mode injection (D2)
  reflection.py         (placeholder — populated by GROUP H with meta_reflect helpers)
  subprocess_runner.py  _run_claude + _kill_process_group + _stash_stream
  alerts.py             _notify_tg + _should_send_7d_alert + thin wrappers around lib/notify.py
```

Each module exposes a narrow public API. `cycle.process_project(project_path)` is the only entry from `main.py` per cycle.

### G3. Test compatibility

Tests that do `from orchestrator import process_project` (or via SourceFileLoader hack) update to `from orchestrator.cycle import process_project`. One-line search-and-replace across `tests/`. No test logic changes.

Smoke tests (bash) call `cc-autopipe start` and `cc-autopipe run` — those use the dispatcher, which is unchanged.

### G4. Acceptance for GROUP G

- `pytest tests/ -q` — all 396+1 pass (no count change; refactor is mechanical)
- `bash tests/smoke/run-all-smokes.sh` — all green
- No module exceeds 350 lines
- `cc-autopipe start --foreground` runs end-to-end on a throwaway project (mocked claude)
- `git diff --stat` shows the split is mechanical (line counts roughly preserved across moves)

Commits (5):
1. `orchestrator: scaffold package (init, main entry, dispatcher compat)`
2. `orchestrator: extract preflight + subprocess_runner + alerts`
3. `orchestrator: extract prompt + recovery + research + reflection placeholders`
4. `orchestrator: cycle.py owns process_project, main.py owns the loop`
5. `tests: update imports to use orchestrator.cycle / orchestrator.main`

After GROUP G is green, proceed. Do NOT skip it. Do NOT introduce behavior changes during G.

---

## GROUP A — Memory persistence (compaction-proof)

### A1. `findings_index.md` — auto-append in Stop hook

Every time `stage_completed` event fires (Stop hook detects new entry in `stages_completed` array of CURRENT_TASK.md), append entry to `<project>/.cc-autopipe/findings_index.md`:

```markdown
## 2026-05-04T17:24:06Z | vec_meta | stage_e_verdict
- **Task:** vec_meta (P0)
- **Stage completed:** stage_e_verdict
- **Notes:** REJECT — val AUC=0.5311 near-random, 8-feature primary_only insufficient
- **Artifacts:** data/debug/CAND_meta_PROMOTION.md
```

File format: append-only markdown. Each entry as `## <ISO ts> | <task_id> | <stage>` header + 4 bullet lines.

Implementation:

New module `src/lib/findings.py`:
```python
def append_finding(project_dir: Path, task_id: str, stage: str,
                   notes: str, artifact_paths: list[str], ts: str | None = None) -> None
def read_findings(project_dir: Path, top_n: int = 20) -> list[dict]
def read_findings_for_task(project_dir: Path, task_id: str, n: int = 5) -> list[dict]
def format_findings_for_injection(findings: list[dict]) -> str
```

Stop hook (via `src/lib/stop_helper.py`): on detected stage transition, call `findings.append_finding(...)` with values from CURRENT_TASK.md.

Idempotent: don't double-append if same stage is already last entry. File is `<project>/.cc-autopipe/findings_index.md`; do NOT add to gitignore (project memory, should be committed).

Tests: ~10 cases — append, read top-N, dedup, malformed CURRENT_TASK.md, missing fields, read_findings_for_task filtering.

### A2. `knowledge.md` — Claude-managed lessons (baseline infrastructure)

`<project>/.cc-autopipe/knowledge.md` is a markdown file Claude writes by hand via Edit/Write tool. Engine never modifies it.

Format:
```markdown
# Project knowledge

## Architectures
- patch_tst already in MODEL_REGISTRY (don't re-implement) — 2026-05-04
- TBM with TP=20%/SL=3% gives 0 SELL labels — use symmetric thresholds — 2026-05-04

## Baselines
- i_transformer baseline (smbal30): sum fixed +268.99% on 5 OOS — 2026-05-04
- PatchTST: sum fixed -114.03% (REJECTED) — 2026-05-04

## Diagnostics rules
- GAP > 25pp at epoch 1 = severe regime mismatch, abort training — 2026-05-04
```

Engine task: inject this file's content (truncated to 5KB tail if larger) at SessionStart.

New module `src/lib/knowledge.py`:
```python
def read_knowledge(project_dir: Path, max_bytes: int = 5120) -> str
def read_relevant_excerpt(project_dir: Path, task_id: str) -> str  # used by GROUP H
def format_for_injection(content: str) -> str
```

In `<project>/.cc-autopipe/rules.md` template — add MANDATORY rule:

> "After every REJECTED or PROMOTED verdict, append 1-3 lessons to `.cc-autopipe/knowledge.md` describing what was learned. Use sections: Architectures / Baselines / Diagnostics rules / Other. Don't duplicate existing lessons."

GROUP I will turn this from a soft rule into an enforced sentinel.

Tests: ~5 cases — read full file, read truncated, missing file, format for injection, relevant_excerpt extracts task-related sections.

### A3. SessionStart hook injection

Update `src/lib/session_start_helper.py`:

Existing injection (project info, current task, backlog top-3, long-op guidance) stays.

ADD:
- Top-20 entries from findings_index.md (most recent first)
- Full knowledge.md content (or last 5KB if larger)

Format injection clearly with separators:
```
=== Recent findings (last 20 stages) ===
<findings entries>
===

=== Project knowledge ===
<knowledge.md>
===
```

Place AFTER existing context blocks, BEFORE long-operation guidance.

Tests: 3-4 SessionStart integration cases.

### Acceptance for GROUP A
- pytest +~20 tests, all green
- Manual: seed a project with findings_index.md and knowledge.md, run `cc-autopipe run --once`, verify both blocks appear in the prompt sent to mocked claude.

---

## GROUP B — Recovery and stuck detection

### B1. Activity detection module

New module `src/lib/activity.py`:

```python
def detect_activity(project_dir: Path, project_name: str,
                     since_seconds: int = 1800) -> dict:
    """Returns activity signals for the project in last `since_seconds`.

    Returns:
        {
            'has_running_processes': bool,    # ps -ef | grep <project_name>
            'recent_artifact_changes': list,  # files modified in data/{models,backtest,debug}/
            'stage_changed': bool,             # CURRENT_TASK.md stage differs from prev
            'last_artifact_mtime': float | None,
            'process_pids': list[int],
            'is_active': bool,                 # any of above is true
        }
    """
```

Engine stores previous CURRENT_TASK.md stage in `state.json` as `last_observed_stage`. Compares.

Process detection: scan `ps -ef`, filter lines containing `<project_name>` or paths inside `<project_dir>`. False positives mean "still active, give it more time" — safe direction.

Filesystem detection: walk `data/models/`, `data/backtest/`, `data/debug/`, find files with mtime within `since_seconds`.

Tests: ~8 cases — running training, recent artifacts, stage change, genuinely stuck.

### B2. Replace `consecutive_in_progress` count cap with activity-based stuck detection

Current logic in orchestrator: after `max_in_progress_cycles` consecutive_in_progress → phase=failed.

New logic in `cycle.py`:
- Keep `consecutive_in_progress` count for telemetry, BUT don't fail on it
- Each cycle_in_progress, call `activity.detect_activity()`. If `is_active=True` → reset stuck timer
- Stuck timer: store `state.last_activity_at`. If `now - last_activity_at > 30 min` → log warning event `stuck_warning`
- If `now - last_activity_at > 60 min` → set phase=failed

"No work happening for 1 hour" = fail. Long training (10 hours) won't trigger because filesystem changes (new checkpoints) reset the timer.

State schema: add `last_activity_at: Optional[str]`, `last_observed_stage: Optional[str]`.

Tests: ~8 cases.

### B3. Auto-recovery from failed (no cap)

Background job in `main.py`: every 30 minutes, scan all projects with `phase=failed`. For each:

1. If `now - last_activity_at > 1 hour`:
   - Log event `auto_recovery_attempted` with reason
   - Reset state: `phase=active`, `consecutive_failures=0`, `consecutive_in_progress=0`, `last_in_progress=False`, `session_id=None`, `last_activity_at=now`
   - Increment `state.recovery_attempts` (telemetry only, no cap)
   - Continue with next project
2. Else: skip, retry next interval

GROUP H ties this into meta-reflection: if recovery hits a project that's failed because of verify pattern, the next cycle's META_REFLECT_*.md is what unblocks it (not a blind retry).

NOTE: B4 from earlier draft (auto-skip after 3 failures) is intentionally NOT included. GROUP H provides a better mechanism (META_REFLECT) — a blind retry without rethinking what failed wastes quota.

Tests: ~5 cases.

### Acceptance for GROUP B
- pytest +~21 tests
- Manual smoke: simulate a stuck project (no filesystem activity for 65 min via mtime manipulation), verify phase=failed; then simulate >1h failed, verify auto_recovery fires.

---

## GROUP C — Infrastructure resilience

### C1. systemd unit + Windows fallback

Create `deploy/systemd/cc-autopipe.service`:

```ini
[Unit]
Description=cc-autopipe orchestrator
After=network.target docker.service
Wants=docker.service

[Service]
Type=simple
User=alter
WorkingDirectory=/home/alter
Environment="PATH=/home/alter/.local/bin:/usr/local/bin:/usr/bin:/bin"
ExecStart=/mnt/c/claude/artifacts/repos/cc-autopipe-source/src/helpers/cc-autopipe start --foreground
ExecStop=/mnt/c/claude/artifacts/repos/cc-autopipe-source/src/helpers/cc-autopipe stop
Restart=always
RestartSec=30
StartLimitInterval=600
StartLimitBurst=10

[Install]
WantedBy=multi-user.target
```

Add `--foreground` flag to `cc-autopipe start` that runs orchestrator without daemonization. systemd needs this.

Document install in `deploy/systemd/INSTALL.md`.

GROUP K adds doctor verification + Windows Task Scheduler fallback (if WSL systemd unavailable).

### C2. Pre-cycle disk space check + auto-cleanup

New module `src/lib/disk.py`:

```python
def check_disk_space(project_dir: Path, min_free_gb: float = 5.0) -> dict
def cleanup_old_checkpoints(project_dir: Path, keep_per_dir: int = 3,
                             dry_run: bool = False) -> list[str]
```

Cleanup heuristic: in `data/models/<exp>/`, keep K newest `checkpoint_epoch_*.pt` files, never touch `*_<timestamp>.pt` (final ckpts) or norm_stats files.

Pre-cycle (in `preflight.py`): if `check_disk_space.ok == False` AND auto-cleanup enabled in project config:
- Call `cleanup_old_checkpoints` with `keep_per_dir=3`
- Log event `disk_cleanup` with bytes freed
- If still not ok: log error, set phase=paused with reason `disk_full`

Project config (default ON):
```yaml
disk_auto_cleanup: true
disk_min_free_gb: 5.0
disk_keep_checkpoints_per_dir: 3
```

Tests: ~6 cases.

### C3. State.json atomic writes + corruption recovery

Note: v1.2 already does atomic writes via tmpfile + os.replace. This subsection ADDS the `.bak` recovery path.

```python
def atomic_write_state(state_path: Path, state_dict: dict) -> None
def read_state_with_recovery(state_path: Path) -> dict:
    """Try load state.json. On JSONDecodeError:
    1. Look for state.json.bak (last known good)
    2. If found, restore from it, log warning
    3. If not found, return fresh state (phase=active, iteration=0)
    Always: after successful load, copy to state.json.bak."""
```

Wire into `lib/state.py`'s `read()` and `write()`. All paths go through these wrappers.

Tests: ~6 cases — atomic write, recovery from bak, no bak fresh state, bak too old, bak corrupted.

### C4. Daemon watchdog process

Watchdog is a separate Python script that pings orchestrator every 5 minutes and restarts it if dead.

`src/watchdog/watchdog.py`:
- Reads orchestrator PID from `~/.cc-autopipe/orchestrator.pid` (singleton lock file)
- `kill -0 <pid>` to check process alive
- If dead: log event `daemon_dead`, exec `cc-autopipe start --foreground &`
- If alive: log heartbeat to `~/.cc-autopipe/log/watchdog.jsonl`
- Sleep 300s, repeat

Second systemd unit `deploy/systemd/cc-autopipe-watchdog.service`:

```ini
[Unit]
Description=cc-autopipe watchdog
After=cc-autopipe.service

[Service]
Type=simple
User=alter
ExecStart=/usr/bin/python3 /mnt/c/claude/artifacts/repos/cc-autopipe-source/src/watchdog/watchdog.py
Restart=always
RestartSec=60

[Install]
WantedBy=multi-user.target
```

Tests: ~4 cases — orchestrator alive, dead, restart success, restart failure.

### Acceptance for GROUP C
- pytest +~16 tests
- Manual: kill orchestrator with `kill -9 <pid>`, verify watchdog restarts within 5 min.

---

## GROUP D — PRD lifecycle (with anti-duplication baked in)

### D1. PRD complete detection

After every cycle_end, check if all `[ ]` tasks in backlog.md are now `[x]` or `[~]` (any variant of in-progress markers).

If yes:
- Set `state.prd_complete_detected = True`
- Log event `prd_complete`
- DO NOT pause — continue cycling. Next cycle triggers D2.

State schema: add `prd_complete_detected: bool`, `research_mode_active: bool`, `research_plan_required: bool`, `research_plan_target: Optional[str]`.

### D2. Research mode WITH anti-duplication

When `prd_complete_detected=True` AND no `[ ]` tasks remain:
- Set `state.research_mode_active = True`
- Set `state.research_plan_required = True`
- Generate `state.research_plan_target = data/debug/RESEARCH_PLAN_<ts>.md`

Next SessionStart injection adds this MANDATORY block:

```
=== RESEARCH MODE ACTIVE ===

PRD complete. All open tasks resolved (rejected or accepted). Roman is offline.

Before adding ANY new candidate to backlog.md, write the research plan:

  data/debug/RESEARCH_PLAN_<ts>.md

Required structure:

## Exhausted directions (read findings_index.md to fill this)

For each cluster of past attempts:
- Cluster name (e.g. "transformer-family on primary 8-feature")
- Past task_ids in this cluster: <vec_meta, vec_tbm, vec_patchtst>
- Verdict pattern: <e.g. all REJECTED with similar AUC ~0.53>
- Why this cluster is exhausted: <one sentence>

## New vectors

For EACH new vector you propose:

### vec_<name>
- **Differs from <past_id>** because: <STRUCTURAL reason — different
  paradigm, different data, different objective. NOT cosmetic differences
  like 'larger model' or 'more features').
- **Hypothesis:** <what we expect to be true>
- **Falsification:** <what would prove the hypothesis wrong>
- **Cost estimate:** <approximate cycles + quota>

## Self-check before adding to backlog

Read your plan back. For each new vector ask: "If <past_failure> failed,
does this differ STRUCTURALLY or just COSMETICALLY?" Cosmetic differences
re-fail. Drop those vectors.

## Then add survivors to backlog

After RESEARCH_PLAN.md is written and self-checked, add the surviving
vectors to backlog.md as `- [ ] [implement] [P1] vec_<name> — ...`. Also
write `data/debug/HYPO_<name>.md` per vector. End your turn.

Engine will validate: if backlog mutated without RESEARCH_PLAN.md present,
new entries are quarantined to UNVALIDATED_BACKLOG_<ts>.md and engine
re-injects this block until plan is filed.

Quota cap: research mode is gated. If 7d quota > 70%, research mode is
suspended (avoid burning the whole budget on speculative candidates).
Limit: max 3 research-mode iterations per 7d window.
===
```

### D3. Engine enforcement of plan

In Stop hook (or post-cycle in `cycle.py`):

If `research_plan_required` and Claude added new `[ ]` items to `backlog.md` THIS CYCLE without `RESEARCH_PLAN_<ts>.md` matching `research_plan_target` existing:
- Read `backlog.md`, identify entries added with mtime > cycle_started_at
- Move these entries to `<project>/.cc-autopipe/UNVALIDATED_BACKLOG_<ts>.md`
- Remove them from `backlog.md`
- Log `research_plan_violation` event
- Keep `research_plan_required=True`

If `RESEARCH_PLAN_<ts>.md` matching target exists at end of cycle:
- Clear `research_plan_required`, keep `research_mode_active=True` until backlog gains items
- Log `research_plan_filed`

Quota gate: at research mode entry, read `quota.read_cached()`. If `seven_day_pct > 0.70`, do NOT activate research mode this iteration. Log `research_mode_suspended_quota`.

Iteration cap: track `state.research_iterations_this_window: list[ts]`. Drop entries older than 7d. If len >= 3, suspend research mode. Log `research_mode_capped`.

### D4. Tests

~10 cases:
- PRD complete detection sets flags
- Research plan block injected when active
- Backlog mutation without plan → quarantined
- Plan written → flag cleared, backlog mutations accepted next cycle
- Quota > 70% → research mode suspended
- 3 iterations in 7d → capped
- Old iterations (>7d) drop from window
- UNVALIDATED_BACKLOG file format

---

## GROUP E — Quota awareness injection

### E1. Quota-aware injection hints

In `session_start_helper.py`, after computing 7d quota %:

- 7d < 60%: no hint
- 7d 60-80%:
  ```
  === QUOTA NOTICE ===
  7-day quota at X%. Continue normally but prefer cheaper actions.
  ===
  ```
- 7d 80-95%:
  ```
  === QUOTA HIGH ===
  7-day quota at X% (resets at <ts>). Focus on completing in-progress work.
  Avoid starting new training jobs. If a task requires new training, defer.
  ===
  ```
- 7d > 95%:
  ```
  === QUOTA CRITICAL ===
  7-day quota at X% (resets at <ts>). VERDICT MODE ONLY:
  - Write PROMOTION.md verdicts for any candidates with completed Stage C-D
  - Do NOT start training, backtests, or new candidates
  - Keep CURRENT_TASK.md updated with what's blocked on quota
  ===
  ```

Text injection only. Claude decides what to do.

Tests: ~4 cases (each threshold band).

---

## GROUP F — Operations and observability

### F1. Daily summary report

Background job in `main.py`: every 24 hours (00:00 UTC, configurable), generate `<project>/.cc-autopipe/daily_<YYYY-MM-DD>.md`:

```markdown
# Daily summary — 2026-05-04

## Cycles
- Total: 35
- Successful (rc=0): 28
- Failed: 7
- Auto-recoveries: 2
- Meta-reflections triggered: 1
- Research mode iterations: 0

## Tasks
- Closed today: vec_meta (REJECT), vec_tbm (REJECT)
- In progress: vec_multihead (Stage A)
- Open: vec_rl

## Findings (today's stage_completed events)
- 17:24Z vec_meta stage_e_verdict: REJECT — val AUC=0.5311
- 17:47Z vec_tbm stage_e_verdict: REJECT — sum fixed +98.55%

## Quota
- 5h: peak 65%, current 11%
- 7d: started 40%, current 50%

## Health
- Disk: 45GB free
- Recoveries: 2
- TG alerts sent: 4
- META_REFLECT decisions: 1 skip, 0 modify, 0 continue, 0 defer
```

Implementation: `src/orchestrator/daily_report.py`. Background job in main.py loop. Don't commit reports.

### F2. Health metrics

Engine writes to `~/.cc-autopipe/log/health.jsonl` every cycle:

```json
{"ts": "2026-05-04T17:24:06Z", "project": "AI-trade", "iteration": 35,
 "phase": "active", "5h_pct": 0.34, "7d_pct": 0.50, "disk_free_gb": 45.2,
 "cycles_last_hour": 12, "recoveries_today": 2,
 "meta_reflects_today": 1}
```

CLI `src/cli/health.py`:
```bash
cc-autopipe health         # last hour summary
cc-autopipe health --24h   # 24h trends
```

Engine-side TG alert thresholds (best-effort, never blocking):
- `cycles_last_hour == 0` for 2+ hours → "engine stuck or idle"
- `recoveries_today > 5` → "frequent recoveries — possible systemic issue"
- `7d_pct > 0.95` → "quota critical"
- `disk_free_gb < 10` → "disk filling up"

Tests: ~5 cases.

### F3. Bypass file cleanup on graceful start

If `~/.claude/settings.json.cc-autopipe-bak` exists at `start` (from previous unclean shutdown):
1. Check if backup is older than 24h — if so, log warning "stale bypass backup detected"
2. Don't overwrite the backup (already correct in v1.2.1 logic)
3. Add log line for transparency

Mostly verification of existing v1.2 hooks-hotfix behavior.

### Acceptance for GROUP F
- pytest +~10 tests
- Manual: run engine for ~30 min on mocked project, verify daily_*.md generated, health.jsonl populated.

---

## GROUP H — META_REFLECT (replaces verify-pattern HUMAN_NEEDED for autonomy)

This is the core hardening. Roman is offline. HUMAN_NEEDED.md is useless when no human is reachable. META_REFLECT forces Claude to do helicopter-view thinking and either change approach or skip the task with explicit reasoning.

### H1. State schema additions

Add to `State`:

```python
meta_reflect_pending: bool = False
meta_reflect_target: Optional[str] = None    # path to META_REFLECT_*.md
meta_reflect_started_at: Optional[str] = None
meta_reflect_attempts: int = 0
```

Bump `SCHEMA_VERSION` to 4. Pre-v4 state files migrate transparently.

### H2. New module `src/orchestrator/reflection.py`

```python
def write_meta_reflect(project_dir: Path, task_id: str, stage: str,
                       failures: list[dict], findings_excerpt: str,
                       knowledge_excerpt: str, attempt: int) -> Path

def read_meta_decision(project_dir: Path, target_md_path: str) -> dict | None
    # Look for META_DECISION_<task>_<stage>_*.md in same dir as target.
    # Parse 'decision: <continue|modify|skip|defer>' line + 'reason: <text>'.
    # Returns {'decision': ..., 'reason': ..., 'path': ...} or None.

def apply_meta_decision(project_dir: Path, decision: dict, task_id: str) -> None
    # continue: no-op (Claude updated CURRENT_TASK.md with new approach)
    # modify:   no-op (Claude updated CURRENT_TASK.md + backlog with refined task)
    # skip:     mark backlog item [~won't-fix], clear current_task in state
    # defer:    mark backlog item [~deferred], clear current_task, log reason
```

META_REFLECT template:

```markdown
# Meta-reflection: <task_id> stage <stage>

**Triggered:** <ISO ts>
**Attempt:** <N>
**Failure pattern:** <N> consecutive verify_failed on this task+stage

## Recent failures (last 5)
<bulleted list with ts, score, details>

## Findings on this task (from findings_index.md)
<excerpt — last 5 entries matching task_id>

## Relevant knowledge (from knowledge.md)
<excerpt — sections that mention this task or similar architectures>

## MANDATORY ANALYSIS

Helicopter view. Before any other action, write `META_DECISION_<task>_<stage>_<ts>.md`
in this same directory with one of these decisions:

### Option A: continue (different approach)
Task is correct, approach is wrong. Update CURRENT_TASK.md with a NEW approach
(different architecture, different params, different data slice). Explain in
META_DECISION what changed and why this should fail differently.

### Option B: modify (refine task)
Task as written is too broad / too narrow / wrong scope. Update backlog entry
text and CURRENT_TASK.md with a tighter task. Engine resumes with new task.

### Option C: skip (won't fix)
Task is structurally unresolvable in this project. Mark [~won't-fix] in backlog
with reason. Document so future research mode doesn't re-propose it.

### Option D: defer (block on something)
Task needs an external prerequisite that doesn't exist yet. Park [~deferred]
with a clear unblocker. Move on.

## META_DECISION format

```
decision: <continue|modify|skip|defer>
reason: <one paragraph, why this decision over the others>
new_approach: <only if continue/modify — describe what's different>
```

End your turn after writing META_DECISION. Engine will read it next cycle and act.
```

### H3. Wire into `cycle.py` (replaces verify-pattern HUMAN_NEEDED branch)

In the existing smart escalation branch (currently `cat['recommend_human_needed']` path):

```python
if cat['recommend_human_needed']:
    if s.meta_reflect_pending and s.meta_reflect_attempts >= 2:
        # Meta-reflection itself failed twice. Fall back to HUMAN_NEEDED
        # (safety net — Roman returns, finds the file, fixes manually).
        human_needed_lib.write_verify_pattern(project_path, recent)
        s.phase = 'failed'
        # ... existing logic
    else:
        # First or second meta-reflection attempt.
        target = reflection.write_meta_reflect(
            project_path, current_task_id, current_stage,
            failures=recent,
            findings_excerpt=findings.read_findings_for_task(
                project_path, current_task_id, n=5),
            knowledge_excerpt=knowledge.read_relevant_excerpt(
                project_path, current_task_id),
            attempt=s.meta_reflect_attempts + 1,
        )
        s.meta_reflect_pending = True
        s.meta_reflect_target = str(target)
        s.meta_reflect_started_at = _now_iso()
        s.meta_reflect_attempts += 1
        s.consecutive_failures = 0  # reset so next cycle isn't immediately re-failed
        state.write(project_path, s)
        state.log_event(project_path, 'meta_reflect_triggered',
                        target=str(target), attempt=s.meta_reflect_attempts)
        # Engine continues — next cycle injects the mandatory block.
```

### H4. Mandatory injection block in `session_start_helper.py`

`build_meta_reflect_block(project_path)`:
- Read `state.meta_reflect_pending`. If False → empty string.
- If True, check if `META_DECISION_*` matching task+stage exists. If yes → emit short note "decision detected".
- If decision missing → emit MANDATORY block:

```
=== MANDATORY META-REFLECTION ===

You triggered a meta-reflection on a previous cycle. Read the file below
BEFORE doing anything else. Do not start any other work until you have
written META_DECISION.

File to read: <meta_reflect_target>
Expected output: META_DECISION_<task>_<stage>_<ts>.md in the same dir

This is not optional. Engine keeps re-injecting this block until
META_DECISION is written.
===
```

Inject at the TOP of the prompt, BEFORE current_task block.

### H5. Decision detection in Stop hook (or post-cycle in `cycle.py`)

After `process_project`'s normal flow, if `s.meta_reflect_pending`:
- Look for `META_DECISION_*.md` matching the target's task_id + stage
- If found:
  - Parse via `reflection.read_meta_decision`
  - Apply via `reflection.apply_meta_decision`
  - Clear `meta_reflect_pending`, `meta_reflect_target`, reset attempts to 0
  - Log `meta_decision_applied` event with decision + reason
- If not found AND `meta_reflect_attempts >= 2`:
  - Fall back to HUMAN_NEEDED.md path (safety net)

### H6. Tests

~12 cases:
- `write_meta_reflect` produces correct file structure
- `read_meta_decision` parses all four decisions
- `apply_meta_decision` skip → backlog item marked [~won't-fix]
- `apply_meta_decision` defer → backlog item marked [~deferred]
- `apply_meta_decision` continue → no backlog change
- `apply_meta_decision` modify → no backlog change
- Schema v3 → v4 migration sets new fields to defaults
- Triggered on 3rd verify_failed; META_REFLECT file exists
- Mandatory block injected when pending
- Decision detected → state cleared
- 2 failed reflection attempts → fall back to HUMAN_NEEDED
- consecutive_failures resets when meta-reflect triggered

---

## GROUP I — Enforced knowledge.md updates

### I1. State schema additions (still v4)

```python
knowledge_update_pending: bool = False
knowledge_baseline_mtime: Optional[float] = None
knowledge_pending_reason: Optional[str] = None
```

### I2. Trigger in `cycle.py` after stage_completed event

In the existing `stage_completed` event emission branch (when `pre_task_id == post_task_id` and stages_completed grew):

```python
for st in new_stages:
    state.log_event(project_path, 'stage_completed', ...)
    if _is_verdict_stage(st):
        knowledge_md = project_path / '.cc-autopipe' / 'knowledge.md'
        baseline = knowledge_md.stat().st_mtime if knowledge_md.exists() else 0.0
        s.knowledge_update_pending = True
        s.knowledge_baseline_mtime = baseline
        s.knowledge_pending_reason = f"{st} on {post_task_id}"
        state.write(project_path, s)
        state.log_event(project_path, 'knowledge_update_required',
                        stage=st, task_id=post_task_id)
```

`_is_verdict_stage` heuristic:

```python
_VERDICT_PATTERNS = ('verdict', 'rejected', 'promoted', 'accepted', 'shipped')

def _is_verdict_stage(stage_name: str) -> bool:
    s = stage_name.lower()
    return any(p in s for p in _VERDICT_PATTERNS)
```

### I3. Mandatory injection block in `session_start_helper.py`

`build_knowledge_update_block(project_path)`:
- Read state. If `knowledge_update_pending` False → empty string.
- If True, check current `knowledge.md.mtime`:
  - If `> knowledge_baseline_mtime` → flag stale; Stop hook will clear it
  - If `<= knowledge_baseline_mtime` → emit MANDATORY block:

```
=== MANDATORY KNOWLEDGE UPDATE ===

A verdict completed (<knowledge_pending_reason>). Before starting any
new work, append a lesson to .cc-autopipe/knowledge.md. Use the
appropriate section (Architectures / Baselines / Diagnostics rules / Other).

Format:
- <one-line lesson> — <YYYY-MM-DD>

Do not duplicate existing lessons. Do not skip this. Engine keeps
re-injecting this block every cycle until knowledge.md mtime advances.
===
```

### I4. Auto-clear in Stop hook

After Stop hook's existing logic:

```python
if s.knowledge_update_pending and s.knowledge_baseline_mtime is not None:
    knowledge_md = project_path / '.cc-autopipe' / 'knowledge.md'
    if knowledge_md.exists() and knowledge_md.stat().st_mtime > s.knowledge_baseline_mtime:
        s.knowledge_update_pending = False
        s.knowledge_baseline_mtime = None
        s.knowledge_pending_reason = None
        state.write(project_path, s)
        state.log_event(project_path, 'knowledge_updated_detected')
```

### I5. Tests

~6 cases:
- `_is_verdict_stage` matches all expected patterns
- Stage_completed with verdict pattern sets pending flag
- Mandatory block emitted when pending and mtime unchanged
- No block emitted when pending but mtime advanced
- Stop hook clears flag when mtime advances
- 5 cycles in a row without update → block re-injected each time (no escalation, just persistence)

---

## GROUP K — WSL2 systemd validation

### K1. New doctor check in `src/cli/doctor.py`

```python
def check_wsl_systemd() -> Check:
    osrel_path = Path('/proc/sys/kernel/osrelease')
    if not osrel_path.exists():
        return Check('wsl-systemd', SKIP, 'not Linux kernel')
    osrel = osrel_path.read_text().lower()
    if 'microsoft' not in osrel and 'wsl' not in osrel:
        return Check('wsl-systemd', SKIP, 'not running on WSL')
    try:
        cp = subprocess.run(
            ['systemctl', '--user', '--version'],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return Check(
            'wsl-systemd', FAIL,
            'systemctl not available in WSL',
            hint=(
                'WSL2 systemd is opt-in. Enable: '
                'echo -e "[boot]\\nsystemd=true" | sudo tee /etc/wsl.conf; '
                'in Windows: wsl --shutdown; restart your WSL distro. '
                'OR use Windows Task Scheduler — see deploy/WSL2.md.'
            ),
        )
    if cp.returncode != 0:
        return Check(
            'wsl-systemd', FAIL,
            f'systemctl --user rc={cp.returncode}',
            hint='systemd installed but user mode not active. See deploy/WSL2.md.',
        )
    return Check('wsl-systemd', OK, 'WSL systemd functional')
```

Add to `run_all()`.

### K2. Documentation `deploy/WSL2.md`

Two paths:

**Path A — Enable systemd in WSL:**

```bash
echo -e '[boot]\nsystemd=true' | sudo tee /etc/wsl.conf
# In Windows PowerShell:
wsl --shutdown
# Reopen WSL terminal
systemctl --user --version
```

Then proceed with `cc-autopipe.service` install.

**Path B — Windows Task Scheduler fallback** (no WSL systemd needed):

Generate template `deploy/wsl-task.xml`. Roman imports via:

```powershell
schtasks /Create /XML deploy\wsl-task.xml /TN "cc-autopipe"
```

Trigger: At Windows logon. Action: `wsl.exe -d <distro> -e bash -c "cd /home/alter && /path/to/cc-autopipe-source/src/helpers/cc-autopipe start --foreground >> /home/alter/.cc-autopipe/log/wsl-task.log 2>&1"`. Restart on failure: every 5 minutes, up to 999 times.

Document watchdog equivalent for Path B (separate Task Scheduler entry).

### K3. Tests

~3 cases:
- Non-WSL host → SKIP
- WSL detected, systemctl works → OK
- WSL detected, systemctl missing → FAIL with remediation hint

Mock `osrelease` content + subprocess result via monkeypatch.

---

## Acceptance gates (final)

After all groups land:

1. `pytest tests/ -q` — 396+1 baseline + ~120 new = ~516 passed, 1 skipped
2. `bash tests/smoke/run-all-smokes.sh` — all green
3. New smokes:
   - `tests/smoke/run-autonomy-smoke.sh` — engine cycles through:
     - cycle in progress with activity → no stuck
     - cycle in progress without activity 65 min → phase=failed
     - phase=failed 65 min → auto-recovery
     - PRD complete → research_mode injection with plan requirement
     - Disk full simulation → auto-cleanup triggered
   - `tests/smoke/run-meta-reflect-smoke.sh`:
     - Seed project with 3 verify_failed on same task+stage
     - Run cycle → META_REFLECT_*.md created
     - Simulate Claude writing META_DECISION with `decision: skip`
     - Run another cycle → backlog item marked [~won't-fix], state cleared
   - `tests/smoke/run-knowledge-enforce-smoke.sh`:
     - Seed stage_completed=stage_e_verdict
     - Run cycle → knowledge_update_required logged
     - Run cycle without touching knowledge.md → mandatory block re-injected
     - Touch knowledge.md → next cycle clears flag
   - `tests/smoke/run-research-plan-smoke.sh`:
     - Seed prd_complete=True
     - Run cycle → research_plan_required set
     - Simulate Claude adding backlog without RESEARCH_PLAN.md → quarantined
     - Simulate Claude writing plan + backlog → accepted next cycle
4. `cc-autopipe doctor` reports new wsl-systemd check
5. `STATUS.md` updated with v1.3 summary + schema bump v3 → v4

---

## Manual smoke test plan (Roman, after agent done)

```bash
cd /mnt/c/claude/artifacts/repos/cc-autopipe-source
git log --oneline -30

# Verify package refactor didn't break invocation
python3 src/orchestrator --help 2>&1 | head -5

# Smokes
bash tests/smoke/run-all-smokes.sh
bash tests/smoke/run-autonomy-smoke.sh
bash tests/smoke/run-meta-reflect-smoke.sh
bash tests/smoke/run-knowledge-enforce-smoke.sh
bash tests/smoke/run-research-plan-smoke.sh

# WSL2 doctor check
cc-autopipe doctor | grep wsl-systemd
# If FAIL — follow deploy/WSL2.md before going further

# Tag
git tag v1.3

# Restart on AI-trade with v1.3
cd /mnt/c/claude/artifacts/repos/AI-trade
git status   # clean
cc-autopipe status

# If WSL systemd enabled (Path A):
sudo cp deploy/systemd/cc-autopipe.service /etc/systemd/system/
sudo cp deploy/systemd/cc-autopipe-watchdog.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable cc-autopipe cc-autopipe-watchdog
sudo systemctl start cc-autopipe
journalctl -u cc-autopipe -f

# If WSL systemd unavailable (Path B):
# Follow deploy/WSL2.md to set up Windows Task Scheduler

# Watch
tail -f ~/.cc-autopipe/log/aggregate.jsonl | jq -c '{ts, project, event}'
```

---

## Stopping conditions

- pytest baseline before any change is < 396 → STOP, write BLOCKED.md
- GROUP G refactor breaks any existing test → revert that commit, fix, retry
- Any group's gate fails → fix before next group
- Smoke tests fail → STOP, write BLOCKED.md
- Build estimate exceeds 40 hours → STOP at last completed group, write progress doc

---

## Don't

- DON'T push to remote (Roman pushes after his manual smoke)
- DON'T tag (Roman tags after smoke test)
- DON'T modify hook files in `~/.claude/hooks/`
- DON'T change projects.list
- DON'T skip GROUP G (refactor) — every other group depends on clean module structure
- DON'T introduce behavior changes during GROUP G — pure mechanical split only
- DON'T remove HUMAN_NEEDED.md path entirely — it stays as safety net after 2 failed meta-reflections
- DON'T add auto-skip after N failures — META_REFLECT (GROUP H) replaces this
- DON'T let research mode run unbounded — quota gate at 70% 7d, max 3 iterations per 7d window
- DON'T skip tests — every group must have tests
- DON'T add new dependencies without checking they're already installable in Python 3.14 venv

---

## After build

Write `V13_BUILD_DONE.md` in repo root:

```markdown
# v1.3 build complete

Commits: <count>
Tests added: <count>
Total tests: 396+1 baseline + <new> = <total>
All gates green: yes/no
Files changed: <count>
Schema bump: state.json v3 → v4

## Group summaries
- G: orchestrator package refactor (pure mechanical, no behavior change)
- A: findings_index.md + knowledge.md baseline + SessionStart injection
- B: activity-based stuck detection + auto-recovery (no auto-skip — see H)
- C: systemd + disk + atomic state recovery + watchdog
- D: PRD complete + research mode WITH anti-duplication (RESEARCH_PLAN required)
- E: quota awareness injection (70/85/95 bands)
- F: daily summary + health metrics + bypass cleanup logging
- H: META_REFLECT replaces verify-pattern HUMAN_NEEDED for autonomy
- I: enforced knowledge.md updates via mtime sentinel
- K: WSL2 systemd doctor check + Windows Task Scheduler fallback docs

## SPEC ↔ repo deviations
<list any tactical adaptations>

## Known limitations
<list any compromises>

## Smoke test plan for Roman
<see Manual smoke test plan above>
```

Done.
