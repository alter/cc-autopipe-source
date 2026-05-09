# V139_BUILD_DONE — cc-autopipe v1.3.9 hotfix

**Built:** 2026-05-09
**Branch:** main (commits below; no remote push, no tag)
**Driver:** AI-trade Phase 2 v2.1 production aggregate.jsonl post-v1.3.8
deploy: 31 `promotion_verdict_unrecognized` events in 12 hours, all
for compact measurement-task reports (`vec_long_features_*`,
`vec_long_lgbm/tft/nhits/etc.`, `vec_long_elo_rating`,
`vec_long_tournament_*`, `vec_long_optuna_mo`) that close with a
single bold-metadata line and no heading section. v1.3.7 parse_verdict's
three tiers all need a heading and miss this format; result is 31
silent promotion drops in 12h (no `on_promotion_success`, 0 ablation
children spawned, LEADERBOARD only updated when Claude wrote it
manually).

Production format (real `CAND_elo_rating_PROMOTION.md`):

```
# CAND_elo_rating_PROMOTION
**Status**: PASS ✓
**Note**: ELO computed for 8 models. Champion: M6_synth_v3 (1557).

---
*eval_tournament.py | 23s*
```

Tier 1 (Verdict heading) misses, tier 2 (`**Verdict: PROMOTED**` literal)
misses, tier 3 (Acceptance/Conclusion/Status heading) misses — all need
a heading. Tier 4 catches the inline `**Field**: KEYWORD` form.

## Group summary

### BOLD-METADATA-VERDICT — tier-4 inline `**Field**: KEYWORD` fallback

`src/lib/promotion.py`:

  - **`BOLD_METADATA_VERDICT_RE`** — multi-line regex matching
    `^\s*\*\*\s*(?:Status|Result|Outcome|Verdict|Decision|Conclusion)\s*\*\*\s*[:\s]+\s*\**\s*(<vocab>)\b`
    where `<vocab>` is the tier 1+3 keyword set (PROMOTED, REJECTED,
    ACCEPT[ED], REJECT, PASS[ED], FAIL[ED], STABLE, CONDITIONAL,
    PARTIAL, LONG_LOSES_MONEY).
  - **`_parse_verdict_tier4_bold_metadata(text)`** — first-match-wins
    search; returns CANONICAL_MAP[keyword] or None.
  - **`parse_verdict(path)`** — wired to fall through to tier 4 only
    when tiers 1-3 returned None. Earlier tiers keep their precedence
    (additive change).

Field-name allowlist is the load-bearing safety property: it restricts
tier 4 to closure-synonym fields so `**Note**: PASS but unrelated`,
`**Pareto points**: 7 non-dominated`, etc., do NOT trigger the tier.
Without the allowlist, any bold-metadata line containing a vocabulary
keyword would drive the verdict.

Production effect: 4 production-shape measurement reports
(`CAND_elo_rating`, `CAND_tournament_round_robin`,
`CAND_tournament_swiss`, `CAND_optuna_mo`) that v1.3.8 logged
`promotion_verdict_unrecognized` for now resolve PROMOTED → fire
`on_promotion_success` → ablation children + leaderboard append. The
31-event regression closes.

## Test counts

| Surface | Pre-v1.3.9 | v1.3.9 | Delta |
|---|---|---|---|
| pytest tests/ | 820 | **833** | +13 |
| Hotfix smokes | 24 (17 hotfix-style + 7 stage-letter) | **25** | +1 |
| Real AI-trade Phase 2 v2.1 fixtures parsed | 9/9 | **13/13** | +4 |

Pytest breakdown of the +13:

- test_promotion.py: +13
  - 9 unit cases — `**Status**: PASS` PROMOTED, `**Status**: FAIL`
    REJECTED, `**Result**: PROMOTED`, `**Outcome**: REJECTED`,
    `**Conclusion**: CONDITIONAL`, in-progress non-verdict negative,
    field-name guard (`**Note**: PASS` and `**Pareto points**: 7`
    must NOT trigger tier 4), tier-1 wins over tier 4 when both
    present, tier-2 (legacy strict) wins over tier 4.
  - 4 real-fixture parses for the AI-trade Phase 2 v2.1
    measurement-task PROMOTION reports the production driver
    surfaced (elo_rating, tournament_round_robin, tournament_swiss,
    optuna_mo). Skipped when the AI-trade repo isn't checked out so
    the suite stays self-contained.

Pre-existing baseline failures (`stage-a` … `stage-f` chain on the
ruff-on-tests rule, `stage-k` orchestrator-startup-log predicate
drift) — all documented in v1.3.5 STATUS.md as deferred and confirmed
green pre-v1.3.9. v1.3.9 introduces no new smoke regressions.

## Acceptance gate §3 — real AI-trade fixtures

```
[PASS] CAND_elo_rating_PROMOTION.md                  → PROMOTED      (tier 4)
[PASS] CAND_tournament_round_robin_PROMOTION.md      → PROMOTED      (tier 4)
[PASS] CAND_tournament_swiss_PROMOTION.md            → PROMOTED      (tier 4)
[PASS] CAND_optuna_mo_PROMOTION.md                   → PROMOTED      (tier 4)
[PASS] CAND_long_only_baseline_PROMOTION.md          → REJECTED      (tier 1)
[PASS] CAND_dr_synth_v1_PROMOTION.md                 → CONDITIONAL   (tier 1)
[PASS] CAND_focal_loss_PROMOTION.md                  → REJECTED      (tier 1)
[PASS] CAND_long_stat_dm_test_PROMOTION.md           → PROMOTED      (tier 3)
```

Tiers 1+3 still own all earlier fixtures (no regression). Tier 4
exclusively picks up the bold-metadata cases that v1.3.8 dropped.

## New events

None. Tier 4 is purely additive — it returns a verdict from
`parse_verdict` exactly the way tiers 1-3 do. The downstream cycle
event trail (`promotion_validated`, `promotion_rejected`,
`promotion_verdict_unrecognized`, `ablation_children_spawned`,
`leaderboard_updated`) is unchanged.

## Schema

**Unchanged at v6.** No new persisted fields per PROMPT_v1.3.9
§"Don't" (rule 3: no new state.json fields).

## Atomic commits

Three atomic commits + 1 STATUS/V139 docs commit (next):

```
f90c9c5 promotion: tier-4 bold-metadata verdict parser (v1.3.9 — Status/Result/Outcome inline)
5e171fb tests: cover bold-metadata verdict patterns + real Phase 2 PROMOTION fixtures
799cfb9 smoke: add run-bold-metadata-smoke.sh covering bold-field tier-4 parsing
TBD     docs: v1.3.9 — STATUS.md + V139_BUILD_DONE.md + VERSION bump
```

## Manual smoke for Roman (after deploy)

```bash
pytest tests/ -q                              # 833 passed
bash tests/smoke/run-all-smokes.sh            # 25 hotfix smokes green
bash tests/smoke/run-bold-metadata-smoke.sh   # standalone

# Verify v1.3.9 closes the v1.3.8 unrecognized regression on the
# actual production-shape PROMOTION files:
python3 - <<'EOF'
import sys
sys.path.insert(0, 'src/lib')
from promotion import parse_verdict
from pathlib import Path

real_files = [
    ('CAND_elo_rating_PROMOTION.md',                'PROMOTED'),
    ('CAND_tournament_round_robin_PROMOTION.md',    'PROMOTED'),
    ('CAND_tournament_swiss_PROMOTION.md',          'PROMOTED'),
    ('CAND_optuna_mo_PROMOTION.md',                 'PROMOTED'),
    ('CAND_long_only_baseline_PROMOTION.md',        'REJECTED'),
    ('CAND_dr_synth_v1_PROMOTION.md',               'CONDITIONAL'),
    ('CAND_focal_loss_PROMOTION.md',                'REJECTED'),
    ('CAND_long_stat_dm_test_PROMOTION.md',         'PROMOTED'),
]
base = Path('/mnt/c/claude/artifacts/repos/AI-trade/data/debug')
ok = True
for fn, expected in real_files:
    path = base / fn
    if not path.exists():
        print(f'? {fn:55s} NOT FOUND')
        continue
    actual = parse_verdict(path)
    mark = 'PASS' if actual == expected else 'FAIL'
    if actual != expected:
        ok = False
    print(f'[{mark}] {fn:55s} → {actual} (expected {expected})')
print('ALL PASS' if ok else 'FAILURES')
EOF

# Bump version + tag
cat src/VERSION  # already 1.3.9
git tag v1.3.9
```

## Stopping conditions met / not met

- v1.3.8 baseline pytest broken pre-build → **NOT MET** (820 green at
  start, 833 green after — clean superset, no regressions).
- Build estimate exceeded 2 hours → **NOT MET** (build wall-time
  ~12 minutes net of the two pytest baseline runs that dominated
  wall clock).
- Real bold-metadata files don't parse to PROMOTED after fix → **NOT
  MET** — all 4 production-shape fixtures (elo_rating,
  tournament_round_robin, tournament_swiss, optuna_mo) parse
  PROMOTED via tier 4.

Done.
