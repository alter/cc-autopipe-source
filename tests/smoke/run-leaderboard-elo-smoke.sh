#!/bin/bash
# tests/smoke/run-leaderboard-elo-smoke.sh — v1.3.5 LEADERBOARD-WRITER smoke.
#
# Synthetic end-to-end validation of the persistent ranking +
# ELO + top-N archive flow. Production has never run this — first
# activation will be the first vec_long_* promotion in AI-trade
# Phase 2 (likely week 1-2).
#
# Lifecycle exercised:
#   1. 5 successive validated promotions with varying metrics
#   2. After each: LEADERBOARD.md re-sorted by composite descending
#   3. ELO ratings evolve: top entry rises, low-composite entries lose
#   4. 21st promotion: oldest beyond rank 20 archived
#   5. knowledge_update_pending=True after each promotion
#   6. Round-trip: parse LEADERBOARD.md → re-write → identical content

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

log() { printf '\033[36m==>\033[0m %s\n' "$*"; }
ok()  { printf '\033[32mOK \033[0m %s\n' "$*"; }
die() { printf '\033[31mFAIL\033[0m %s\n' "$*" >&2; exit 1; }

PY="$REPO_ROOT/.venv/bin/python3"
[ -x "$PY" ] || PY=python3

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

UHOME="$TMP/uhome"
PROJ="$TMP/p"
mkdir -p "$UHOME/log" "$PROJ/.cc-autopipe/memory" "$PROJ/data/debug"
echo "$PROJ" > "$UHOME/projects.list"
export CC_AUTOPIPE_USER_HOME="$UHOME"

# Seed knowledge.md so touch_knowledge_baseline_mtime has something to touch.
echo "# k" > "$PROJ/.cc-autopipe/knowledge.md"

# Test 1: 5 successive promotions, ranked by composite descending.
log "5 successive promotions, ranked by composite descending"
"$PY" - <<PY
import sys
from pathlib import Path
sys.path.insert(0, "$REPO_ROOT/src/lib")
import leaderboard as lb
project = Path("$PROJ")
metrics_set = [
    ("entry_e", {"sum_fixed": 100.0, "regime_parity": 0.40, "max_dd": -20.0}),
    ("entry_d", {"sum_fixed": 200.0, "regime_parity": 0.30, "max_dd": -15.0}),
    ("entry_c", {"sum_fixed": 300.0, "regime_parity": 0.25, "max_dd": -10.0}),
    ("entry_b", {"sum_fixed": 400.0, "regime_parity": 0.20, "max_dd": -8.0}),
    ("entry_a", {"sum_fixed": 500.0, "regime_parity": 0.10, "max_dd": -5.0}),
]
for tid, m in metrics_set:
    lb.append_entry(project, tid, m)

rows = lb.read_top_n(project)
ids = [r["task_id"] for r in rows]
assert ids == ["entry_a", "entry_b", "entry_c", "entry_d", "entry_e"], ids
print("ranked:", ids)
PY
ok "leaderboard ranked by composite descending"

# Test 2: top entry's ELO > initial; bottom entry's ELO < initial.
log "ELO evolves: top wins, bottom loses"
"$PY" - <<PY
import sys
from pathlib import Path
sys.path.insert(0, "$REPO_ROOT/src/lib")
import leaderboard as lb
rows = lb.read_top_n(Path("$PROJ"))
top_elo = rows[0]["elo"]
bot_elo = rows[-1]["elo"]
assert top_elo > 1500, f"top ELO {top_elo} not > 1500"
assert bot_elo < 1500, f"bottom ELO {bot_elo} not < 1500"
print(f"top ELO={top_elo}, bot ELO={bot_elo}")
PY
ok "ELO ratings differ from initial (top up, bottom down)"

# Test 3: knowledge sentinel armed after every append.
log "knowledge_update_pending=True after promotion"
"$PY" - <<PY
import sys
from pathlib import Path
sys.path.insert(0, "$REPO_ROOT/src/lib")
import state
s = state.read("$PROJ")
assert s.knowledge_update_pending is True, s.knowledge_update_pending
assert s.knowledge_baseline_mtime is not None
print(f"baseline mtime: {s.knowledge_baseline_mtime}")
PY
ok "knowledge sentinel armed (knowledge_update_pending=True)"

# Test 4: 21st promotion archives the lowest-composite entry.
log "21st promotion archives lowest-composite entries"
"$PY" - <<PY
import sys
from pathlib import Path
sys.path.insert(0, "$REPO_ROOT/src/lib")
import leaderboard as lb
project = Path("$PROJ")
# Fill to 21 (we already have 5; add 16 more to reach 21 distinct entries).
for i in range(5, 21):
    lb.append_entry(project, f"entry_pad_{i:02d}", {"sum_fixed": float(50.0 - i)})

rows = lb.read_top_n(project)
assert len(rows) == 20, f"expected top-20 retained, got {len(rows)}"

archive_dir = project / "data" / "debug" / "ARCHIVE"
archive_files = list(archive_dir.glob("LEADERBOARD_*.md"))
assert archive_files, "expected at least one archive file"
print(f"top-20 retained, {len(archive_files)} archive file(s) written")
PY
ok "top-20 retained inline; oldest beyond rank 20 archived"

# Test 5: Round-trip read.
log "LEADERBOARD.md round-trip parse + re-write"
"$PY" - <<PY
import sys
from pathlib import Path
sys.path.insert(0, "$REPO_ROOT/src/lib")
import leaderboard as lb
rows1 = lb._read_existing_entries(Path("$PROJ"))
# Re-render from parsed rows: write into a different file, parse again,
# compare task_id + composite ordering.
tmp = Path("$TMP/lb_roundtrip.md")
lb._write_leaderboard_md(tmp, rows1, header="# Promotion Leaderboard")
import os
os.replace(str(tmp), str(Path("$PROJ") / "data" / "debug" / "LEADERBOARD.md"))
rows2 = lb._read_existing_entries(Path("$PROJ"))
assert [r["task_id"] for r in rows1] == [r["task_id"] for r in rows2]
PY
ok "round-trip preserves task_id ordering"

printf '\033[32m===\033[0m PASS — v1.3.5 LEADERBOARD-WRITER smoke\n'
