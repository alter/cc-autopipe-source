#!/bin/bash
# tools/seed-fixtures.sh
# Regenerate test fixtures from canonical templates.
#
# Run: bash tools/seed-fixtures.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FIXTURES="$ROOT/tests/fixtures"

mkdir -p "$FIXTURES"

# 1. Sample valid state.json (active)
cat > "$FIXTURES/state-active.json" <<'EOF'
{
  "schema_version": 1,
  "name": "fixture-project",
  "phase": "active",
  "iteration": 5,
  "session_id": "fixture-sess-abc",
  "last_score": 0.78,
  "last_passed": false,
  "prd_complete": false,
  "consecutive_failures": 1,
  "last_cycle_started_at": "2026-04-28T15:00:00Z",
  "last_progress_at": "2026-04-28T15:24:00Z",
  "threshold": 0.85,
  "paused": null
}
EOF

# 2. Paused state
cat > "$FIXTURES/state-paused.json" <<'EOF'
{
  "schema_version": 1,
  "name": "fixture-project",
  "phase": "paused",
  "iteration": 12,
  "session_id": "fixture-sess-paused",
  "last_score": 0.65,
  "last_passed": false,
  "prd_complete": false,
  "consecutive_failures": 0,
  "last_cycle_started_at": "2026-04-28T16:00:00Z",
  "last_progress_at": "2026-04-28T16:30:00Z",
  "threshold": 0.85,
  "paused": {
    "resume_at": "2026-04-28T18:30:00Z",
    "reason": "rate_limit_5h"
  }
}
EOF

# 3. Done state
cat > "$FIXTURES/state-done.json" <<'EOF'
{
  "schema_version": 1,
  "name": "fixture-project",
  "phase": "done",
  "iteration": 47,
  "session_id": "fixture-sess-done",
  "last_score": 0.96,
  "last_passed": true,
  "prd_complete": true,
  "consecutive_failures": 0,
  "last_cycle_started_at": "2026-04-28T17:00:00Z",
  "last_progress_at": "2026-04-28T17:45:00Z",
  "threshold": 0.85,
  "paused": null
}
EOF

# 4. Corrupted state (for recovery tests)
cat > "$FIXTURES/state-corrupted.json" <<'EOF'
{ this is not valid json
EOF

# 5. verify.sh that always passes
cat > "$FIXTURES/verify-passing.sh" <<'EOF'
#!/bin/bash
echo '{"passed": true, "score": 0.92, "prd_complete": false, "details": {"tests_pass": true}}'
EOF
chmod +x "$FIXTURES/verify-passing.sh"

# 6. verify.sh that always fails
cat > "$FIXTURES/verify-failing.sh" <<'EOF'
#!/bin/bash
echo '{"passed": false, "score": 0.45, "prd_complete": false, "details": {"tests_pass": false, "reason": "fixture failure"}}'
EOF
chmod +x "$FIXTURES/verify-failing.sh"

# 7. verify.sh that's malformed
cat > "$FIXTURES/verify-malformed.sh" <<'EOF'
#!/bin/bash
echo "this is not json"
EOF
chmod +x "$FIXTURES/verify-malformed.sh"

# 8. verify.sh that hangs
cat > "$FIXTURES/verify-hanging.sh" <<'EOF'
#!/bin/bash
sleep 120
echo '{"passed": true, "score": 1.0, "prd_complete": false, "details": {}}'
EOF
chmod +x "$FIXTURES/verify-hanging.sh"

# 9. Sample backlog.md
cat > "$FIXTURES/backlog-sample.md" <<'EOF'
# Backlog

## Active
- [ ] [implement] First task — Acceptance: pytest tests/test_one.py
- [ ] [implement] Second task — Acceptance: integration test passes
- [~] [implement] [agent-1] In-progress task
- [!] [implement] Blocked task — needs human review

## Done
- [x] [research] Initial research
- [x] [implement] Project skeleton
EOF

# 10. Sample PRD with one open and one done criterion
cat > "$FIXTURES/prd-partial.md" <<'EOF'
# PRD: fixture-project

## Goal
Demonstrate cc-autopipe loop on a minimal project.

## Acceptance criteria
- [ ] Feature A — verifiable: tests pass
- [x] Feature B — already complete

## Out of scope
- Anything else
EOF

echo "[seed-fixtures] Fixtures generated in $FIXTURES"
ls -la "$FIXTURES"
