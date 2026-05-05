# v1.3.1 hotfix complete

Commits: 6 (3 fix groups + smokes + STATUS + this doc)
Tests added: +25
Total tests: 548 baseline (v1.3) + 25 new = **573 passed, 0 skipped**
All gates green: yes
Smokes: 4 v1.3 + 3 new v1.3.1 = 7 total, all green
Schema bump: none (v4 unchanged)

## Group summaries

- **B-FIX (regression test):** v1.3's `c01bdcf` already removed
  the v1.2 `consecutive_in_progress >= max → phase=failed` cap and
  replaced it with activity-based stuck detection in
  `cycle.py:382-409`. The PROMPT-v1.3.1 diagnosis was based on
  AI-trade logs from a host that was still running v1.2 at that
  time. v1.3.1 adds a focused regression test pinning the invariant:
  15 cycles with `is_active=True` keep `last_activity_at` current
  and `evaluate_stuck` returns 'ok' regardless of cycle count;
  `consecutive_in_progress` still increments for telemetry. A new
  `run-stuck-detection-smoke.sh` exercises the same path
  end-to-end (with fs writes + stage transitions probing as active).

- **B3-FIX (shutdown + lock awareness):** v1.3 wired
  `auto_recover_failed_projects` into `main.py` at 30-min cadence.
  v1.3.1 hardens `recovery.maybe_auto_recover` for the 14-day
  autonomy use case:
  - Acquires the per-project lock non-blocking around the
    state.read/write window. If another process holds it (in-flight
    cycle from a second orchestrator, stale fcntl handoff), recovery
    skips rather than races. Lock released via try/finally on every
    exit path.
  - `auto_recover_failed_projects` now checks `is_shutdown()` between
    projects; SIGTERM mid-sweep stops further state mutations.
  - Tests: +3 unit (lock-held → skip; lock released after success;
    shutdown flag aborts sweep) + a new
    `run-recovery-sweep-smoke.sh` covering the same paths plus
    idempotency.

- **DETACH-CONFIG (full feature):** new resolution chain for
  `cc-autopipe-detach` defaults, top-down priority:
    1. CLI arg              `--check-every` / `--max-wait`
    2. Env var              `CC_AUTOPIPE_DEFAULT_CHECK_EVERY` /
                            `CC_AUTOPIPE_DEFAULT_MAX_WAIT`
    3. Project config       `<project>/.cc-autopipe/config.yaml`
                            `detach_defaults: {check_every_sec, max_wait_sec}`
    4. Hardcoded fallback   600 / 14400 (4h)
  - New module `src/lib/detach_defaults.py` reads the YAML block
    using the same line-oriented parser as
    `prompt._read_yaml_top_block` (no PyYAML dep). CLI gains
    `--key NAME` mode so the bash helper can extract a single
    field without depending on jq.
  - `src/helpers/cc-autopipe-detach` now consults the chain
    explicitly. Existing CLI-arg / env-var override paths still
    work unchanged (tests pin all three rungs).
  - Templates: `config.yaml` gains a `detach_defaults` block with
    inline tuning guidance for ML R&D workloads (4h / 8h / 12h /
    24h / 48h reference values). `rules.md.example` gains a
    "Long-running ML training (>4h)" subsection — explains the
    config-vs-CLI override paths and warns about the
    `test -f *.pt` glob breakage.
  - Tests: +13 unit + 8 integration covering each rung +
    new `run-detach-defaults-smoke.sh` for end-to-end coverage.

## Commit list

```
22a0928 tests/smoke: 3 v1.3.1 smokes pin the hotfix invariants
07416bf templates: add detach_defaults to config.yaml + rules.md guidance
f05cb29 helpers: cc-autopipe-detach uses env > config > hardcoded chain
69a79a3 lib: detach_defaults.py reads config.yaml detach_defaults block
cafc282 tests: B-FIX cover 15-cycle AI-trade regression scenario
f46a162 recovery: B3-FIX add shutdown safety + per-project lock awareness
```

## SPEC ↔ repo deviations

1. PROMPT-v1.3.1 §B-FIX described code work that v1.3 (`c01bdcf`)
   had already done. v1.3.1 delivered the test coverage the prompt
   required without re-implementing functionality. Original cap-hit
   trigger search confirmed: zero references in current `src/` —
   only in PROMPT-v1.3.1-hotfix.md (the prompt itself).

2. PROMPT-v1.3.1 §D2 suggested using `jq` to parse JSON output of
   `detach_defaults.py`. Replaced with a `--key NAME` mode that
   emits a single int directly — avoids adding `jq` as an install
   dependency (it isn't currently a doctor check), keeps the bash
   helper fully self-sufficient against the standard install set.

3. PROMPT-v1.3.1 acceptance gates listed `pytest 573 passed`. Hit
   exactly: 548 baseline + 25 new = 573.

## Known limitations

- Real-claude smoke not run during the hotfix build (mocked
  claude only). Roman should manually run `cc-autopipe run <project>
  --once` against AI-trade after deploying v1.3.1 on host to
  confirm the new injection blocks land correctly.

- `detach_defaults` block is added to the v1.3.1 template
  config.yaml. Existing projects (AI-trade) need to add the block
  manually if they want >4h max_wait_sec — see rules.md.example.

## Smoke test plan for Roman (post-push)

```bash
cd /mnt/c/claude/artifacts/repos/cc-autopipe-source

# Pytest
pytest tests/ -q
# expect: 573 passed

# v1.3 smokes
bash tests/smoke/run-autonomy-smoke.sh
bash tests/smoke/run-meta-reflect-smoke.sh
bash tests/smoke/run-knowledge-enforce-smoke.sh
bash tests/smoke/run-research-plan-smoke.sh

# v1.3.1 smokes
bash tests/smoke/run-stuck-detection-smoke.sh
bash tests/smoke/run-recovery-sweep-smoke.sh
bash tests/smoke/run-detach-defaults-smoke.sh

# Bump VERSION + tag
echo "1.3.1" > src/VERSION
git tag v1.3.1

# Update AI-trade config to use 12h max_wait
cat >> /mnt/c/claude/artifacts/repos/AI-trade/.cc-autopipe/config.yaml <<'EOF'

detach_defaults:
  check_every_sec: 600
  max_wait_sec: 43200
EOF

# Restart engine on host
pkill -f cc-autopipe || true
sleep 2
cc-autopipe start --foreground 2>&1 | tee ~/.cc-autopipe/log/orchestrator.log
```
