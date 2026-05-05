# v1.3 build complete

Commits: 10 (9 group commits + final)
Tests added: 151
Total tests: 397 baseline + 151 new = **548 passed, 0 skipped**
All gates green: yes
Schema bump: state.json v3 → v4

## Group summaries

- **G:** orchestrator package refactor (pure mechanical, no behavior
  change). 1440-line monolith → 10 modules under 350 lines each:
  `_runtime`, `main`, `cycle`, `preflight`, `prompt`, `phase`,
  `recovery`, `research`, `reflection`, `daily_report`,
  `subprocess_runner`, `alerts`. Bash dispatcher unchanged.
- **A:** `findings_index.md` auto-append in Stop hook (one entry
  per `stages_completed` transition) + `knowledge.md` baseline read +
  SessionStart injection. New modules `src/lib/findings.py`,
  `src/lib/knowledge.py`. Compaction-proof project memory.
- **B:** `src/lib/activity.py` (process scan + fs mtime walk +
  stage-change detection). `cycle.py` replaces v1.2's blind
  `consecutive_in_progress` cap with activity-based stuck detection
  (>30 min: `stuck_warning`; >60 min: phase=failed). `recovery.py`
  adds 30-min auto-recovery sweep that revives projects stuck in
  `failed` for >1h with no activity (no cap on attempts).
- **C:** systemd units `deploy/systemd/cc-autopipe.service` +
  `cc-autopipe-watchdog.service` + INSTALL.md. `--foreground` flag
  on `cc-autopipe start`. `src/lib/disk.py` (check_disk_space +
  cleanup_old_checkpoints, never touches non-`epoch_*.pt` files).
  `state.read/write` with `state.json.bak` corruption recovery.
  `src/watchdog/watchdog.py` 5-min ping/restart loop.
- **D:** PRD-complete detection + research mode WITH anti-duplication
  baked in. RESEARCH_PLAN_<ts>.md required before backlog mutations
  (else quarantined to UNVALIDATED_BACKLOG_*). Quota gate at 70% 7d.
  Iteration cap 3 per 7d window. New module `src/orchestrator/
  research.py`.
- **E:** quota-aware injection bands at 60/80/95% in
  `session_start_helper.build_quota_notice_block`.
- **F:** daily summary report (`src/orchestrator/daily_report.py`),
  per-cycle health.jsonl + `cc-autopipe health` CLI, stale-bypass-
  backup warning in claude_settings.
- **H:** META_REFLECT replaces verify-pattern HUMAN_NEEDED.
  `src/orchestrator/reflection.py` writes META_REFLECT_<task>_
  <stage>_<ts>.md after 3rd verify failure; SessionStart injects
  mandatory block until META_DECISION.md is filed; engine applies
  decision (continue/modify/skip/defer). Falls back to HUMAN_NEEDED
  after 2 failed reflection attempts (safety net).
- **I:** knowledge.md update enforcement via mtime sentinel.
  `is_verdict_stage` heuristic (substring of verdict|rejected|
  promoted|accepted|shipped). On verdict stage_completed, engine
  arms `knowledge_update_pending` with current mtime baseline.
  Mandatory SessionStart injection until mtime advances. Stop hook
  auto-clears flag.
- **K:** `cc-autopipe doctor` gains `wsl-systemd` check (skip on
  non-WSL, fail with remediation hint pointing to `deploy/WSL2.md`
  on WSL hosts without systemd). `deploy/WSL2.md` covers both Path
  A (enable systemd) and Path B (Windows Task Scheduler XML for
  orchestrator + watchdog).

## SPEC ↔ repo deviations

1. `src/orchestrator/recovery.py` is split — the prompt G2 listing
   has all failure-handling under one module, but DETACHED + PRD
   phase logic naturally belongs in a separate `phase.py` (would
   push recovery.py over 350 lines). Both paths still expose the
   public surface the prompt described.
2. v1.3 B3 auto-recovery preserves the v1.2 manual-resume contract
   for projects that have NO `last_activity_at` recorded
   (pre-v1.3). Only projects that fell into `failed` under v1.3
   supervision (so we know when activity stopped) auto-revive.
3. `RESEARCH_PLAN_<ts>.md` uses compact ts (no `:` or `-`) instead
   of the prompt's exact format — file-system safety on Windows
   when projects sync via WSL.
4. `cc-autopipe health` CLI added to dispatcher (PROMPT mentioned
   the file but didn't list it under helpers); F2 acceptance gate
   now passes.

## Known limitations

- Real-claude smoke not run during the build (mocked claude only).
  Roman should manually run `cc-autopipe run <project> --once`
  against a real project with low-cost prompt to validate the new
  injection blocks land correctly.
- Activity detection uses substring match against `ps -ef` for
  process scanning. False positives are intentional (preserve
  "still active" signal) but may keep an unrelated process from
  triggering stuck timer reset on shared hosts. Document for
  operator awareness.

## Smoke test plan for Roman

```bash
cd /mnt/c/claude/artifacts/repos/cc-autopipe-source
git log --oneline -15

# Verify package refactor didn't break invocation
python3 src/orchestrator --help 2>&1 | head -5

# Pytest (4 min)
pytest tests/ -q

# Stage smokes (~22min, mocked claude)
bash tests/smoke/run-all-smokes.sh

# v1.3 smokes (~5s each, mocked claude)
bash tests/smoke/run-autonomy-smoke.sh
bash tests/smoke/run-meta-reflect-smoke.sh
bash tests/smoke/run-knowledge-enforce-smoke.sh
bash tests/smoke/run-research-plan-smoke.sh

# WSL2 doctor check
cc-autopipe doctor | grep wsl-systemd
# If FAIL — follow deploy/WSL2.md before going further

# Tag v1.3 (after Roman is satisfied)
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
cc-autopipe health
```
