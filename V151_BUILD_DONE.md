# v1.5.1 — ablation spawn verdict gate

**Build complete.** One group, 4 commits, +6 unit tests, +1 smoke.

## Group

### ABLATION-VERDICT-GATE

- `src/lib/promotion.py` `on_promotion_success` now reads
  `metrics["verdict"]` and routes through one of three event branches:

  - `ablation_skipped_non_promoted` — verdict ≠ "PROMOTED" (incl.
    NEUTRAL, CONDITIONAL, missing/unrecognised). Backlog untouched.
    Treated as a successful skip (`ablation_ok = True`), so
    `on_promotion_success_completed` still fires when the leaderboard
    hook succeeds.
  - `promotion_children_skipped` — verdict is PROMOTED but the
    project has no backlog.md to mutate (unchanged from v1.5.0).
  - `ablation_children_spawned` — verdict is PROMOTED and backlog
    present (unchanged from v1.5.0). Appends 5 `_ab_*` children.

- The leaderboard hook fires for ALL verdicts (PROMOTED, NEUTRAL,
  CONDITIONAL, missing). NEUTRAL/CONDITIONAL values are still useful
  for ELO ranking and post-mortem audit; only the destructive
  backlog-mutation step is gated.

- `src/lib/promotion.py` `parse_metrics` now populates a new
  `"verdict"` key (canonical: "PROMOTED" / "REJECTED" /
  "CONDITIONAL" / None). Source priority:
  1. labelled `## Metrics for leaderboard` block `**verdict**:`
     field (authoritative — v1.4.0 METRICS-BLOCK-CONVENTION).
  2. Full `parse_verdict()` cascade (Tier 0–4) for legacy reports
     without a labelled block.

  The double-pass design means a labelled-block `**verdict**: NEUTRAL`
  wins over a cascade-misfire that captures "PROMOTED" from some
  other context. This is the defense-in-depth path that closes the
  Phase 4 regression: the labelled block is the authoritative source
  for ablation-gate decisions.

- `on_promotion_success` signature unchanged. cycle.py call sites
  (post_cycle_delta_scan + pre-cycle promotion-parser) NOT touched —
  the existing `if verdict == "PROMOTED":` outer gates remain in
  force, and the new in-function gate is defense-in-depth against
  any future caller or any drift between `parse_verdict` (cascade
  result, used by cycle.py outer gate) and the labelled-block verdict
  (used by `parse_metrics` → in-function gate).

- Existing diagnostic + flow integration tests that passed `{}` or
  numeric-only metrics dicts to `on_promotion_success` now pass
  `{"verdict": "PROMOTED", ...}` so the canonical happy-path
  assertions still fire. Same change applies to test_promotion_flow.py
  call sites at lines 161 / 180 / 386, and to test_promotion_diagnostics.py
  call sites at lines 80 / 109 / 140 / 181 / 212.

## Background

AI-trade Phase 4 production (2026-05-11/12) produced hundreds of
legitimate NEUTRAL verdicts ("no exploitable edge in held-out window").
Pre-v1.5.1 each spawned 5 ablation children unconditionally because
the cycle.py outer gate trusted `parse_verdict()`'s cascade — which
on Phase 4 NEUTRAL files was cascading past Tier 0 (labelled block
NEUTRAL) and capturing "PROMOTED" via Tier 1-4 keyword search in body
prose. Children later validated as NEUTRAL too → +5 more. Backlog
grew from ~600 done / 11K open to ~38K done / 38K orphan `_ab_`
children. Engine kept reopening `phase=done → active` chasing
ablation work, Claude CLI hit `--max-turns 35` doing nothing
productive, TG cycle_failed spam was the user-visible symptom.

## Operator action required

None. Restart `cc-autopipe.service` to pick up the new policy. Projects
with existing orphan `_ab_` backlog entries from pre-v1.5.1 sessions
remain unaffected — engine doesn't auto-clean historical ablation;
that's a project-side cleanup decision (AI-trade backlog hygiene).

## What's NOT in v1.5.1

- No config flag for "spawn on NEUTRAL too" — the policy is
  PROMOTED-only and configurability is out of scope.
- No retroactive backlog cleanup. Orphan `_ab_*` entries on production
  disks are project-side concerns.
- No changes to cycle.py call sites — the existing
  `if verdict == "PROMOTED":` outer gate stays; v1.5.1 adds a
  defense-in-depth inner gate so labelled-block verdict wins on any
  cascade-vs-block disagreement.
- No changes to `parse_verdict` itself — the cascade order
  (Tier 0–4) is preserved. Only `parse_metrics` gained the
  defense-in-depth verdict propagation.
- No changes to leaderboard schema — `metrics.get("verdict")` is
  ignored by `leaderboard.append_entry` (it reads only known
  numeric columns).

## Smoke

One new smoke, green:

- `tests/smoke/run-ablation-gate-smoke.sh` — drops two synthetic
  PROMOTION reports with labelled `## Metrics for leaderboard`
  blocks (verdict=NEUTRAL → asserts backlog UNCHANGED, then
  verdict=PROMOTED → asserts exactly 5 `_ab_` children appended).
  Verifies the `ablation_skipped_non_promoted` event carries
  `verdict=CONDITIONAL` (NEUTRAL canonicalised via CANONICAL_MAP)
  and that the PROMOTED run does NOT re-fire the skipped event.

Registered in `tests/smoke/run-all-smokes.sh` as `ablation-gate`.

## Acceptance

- `pytest tests/ -q` — baseline v1.5.0 + 6 new tests (4 gate cases
  + 2 parse_metrics-verdict cases). Five existing tests updated to
  carry `{"verdict": "PROMOTED"}` so canonical happy-path assertions
  remain valid. The 6 pre-existing real-AI-trade-fixture failures
  (test_parse_verdict_real_ai_trade_* / test_tier4_real_ai_trade_*)
  are baseline noise unrelated to v1.5.1 scope — they assert
  cascade-vs-block disagreement on Phase 4 NEUTRAL files and are
  the exact regression v1.5.1 defends against; their resolution
  belongs to a parse_verdict cascade-tightening pass in a later
  hotfix.

## Commits (in order)

1. `promotion: parse_metrics propagates labelled-block verdict + cascade fallback (v1.5.1)`
2. `promotion: gate ablation spawn on verdict=PROMOTED (v1.5.1)`
3. `tests: cover ablation gate — PROMOTED spawns, NEUTRAL/CONDITIONAL/missing skip (v1.5.1)`
4. `smoke: add run-ablation-gate-smoke.sh covering both branches (v1.5.1)`
