# V1311_BUILD_DONE — cc-autopipe v1.3.11

**Built:** 2026-05-10
**Branch:** main (commits below; no remote push, no tag)
**Driver:** `cc-autopipe quota` was missing from the dispatcher — the
command existed only as a low-level `python3 .../lib/quota.py read`
surface (hook-oriented, jq-friendly, raw JSON). Operators and scripts
had no user-facing CLI with human-readable output or the normalised
`Quota.to_jsonable()` shape.

## Group summary

### CC-AUTOPIPE-QUOTA-CMD — add `cc-autopipe quota` user-facing command

`src/cli/quota.py` (new file):

- **Default mode:** human-readable summary to stdout:
  ```
  5h: 47% (resets 2026-05-10T14:30:00Z)
  7d: 12% (resets 2026-05-13T20:00:00Z)
  cache age: 23s
  ```
  Percentages are rounded integers. `resets_at=None` prints
  `(resets unknown)`. Cache age reads `quota.cache_age_sec()`;
  `None` (no cache file yet) prints `cache age: n/a`.

- **`--json`:** emits `Quota.to_jsonable()` — normalised float
  0.0–1.0 fields (`five_hour_pct`, `seven_day_pct`, `*_resets_at`)
  — one JSON object per line. Suitable for scripting without
  depending on the raw endpoint shape.

- **`--raw`:** passthrough of the raw endpoint response dict (same
  bytes as `python3 .../lib/quota.py read`). Kept for scripts that
  were already parsing the integer-percent shape.

- **`--refresh`:** forces a fresh fetch bypassing the 60s cache.
  Composable with all three modes.

- **`--json` and `--raw`** are mutually exclusive
  (`add_mutually_exclusive_group`).

- **Exit 2** when quota data is unavailable (no token, endpoint
  unreachable, or `CC_AUTOPIPE_QUOTA_DISABLED=1`). Stderr prints
  a two-line hint directing the operator to `claude` authentication.
  Stdout is empty — no partial JSON in `--json`/`--raw` modes.

- **Exit 0** when data is available.

`src/helpers/cc-autopipe`:

- `quota)` case added to the bash dispatcher.
- One usage line added under "Project lifecycle":
  `quota [--json|--raw] [--refresh]  Show 5h/7d Claude quota  [v1.3.11]`

## Test counts

| Surface | Pre-v1.3.11 | v1.3.11 | Delta |
|---|---|---|---|
| pytest tests/ | 840 | **847** | +7 |

Pytest breakdown of the +7:

- `tests/integration/test_cli_quota.py` — 7 cases:
  - `test_default_human_output_after_refresh` — mock set to 47/12,
    populate cache via `--refresh`, run default; assert `5h: 47%`,
    `7d: 12%`, `cache age:`, `(resets `.
  - `test_json_flag_emits_normalized_object` — cache written with
    `utilization: 42/13`; `--json` parses to
    `five_hour_pct == 0.42`, `seven_day_pct == 0.13`.
  - `test_raw_flag_emits_raw_response` — same cache; `--raw`
    passthrough gives `data["five_hour"]["utilization"] == 42`
    (integer, not normalised).
  - `test_json_and_raw_mutually_exclusive` — argparse rejects
    `--json --raw`; rc != 0.
  - `test_refresh_with_json_bypasses_cache` — stale cache 99/99,
    mock set 25/30; `--refresh --json` gives `five_hour_pct == 0.25`.
  - `test_unavailable_returns_rc2_with_hint` — no creds; rc=2,
    stdout empty, stderr contains "quota unavailable" and "claude".
  - `test_disabled_env_returns_rc2` — `CC_AUTOPIPE_QUOTA_DISABLED=1`;
    rc=2, stderr contains "disabled".

## No new events / no schema changes

Purely a CLI surface addition. `src/lib/quota.py` and all orchestrator
internals unchanged.

## Atomic commits

```
TBD  feat: cc-autopipe quota command — human/json/raw/refresh modes (v1.3.11)
TBD  docs: v1.3.11 — STATUS.md + V1311_BUILD_DONE.md + VERSION bump
```

## Stopping conditions met / not met

- v1.3.10 baseline pytest broken pre-build → **NOT MET** (840 green
  at start, 847 green after — clean superset, no regressions).
- Build estimate exceeded 2 hours → **NOT MET** (~10 minutes).

Done.
