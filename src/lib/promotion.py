#!/usr/bin/env python3
"""promotion — parse and validate PROMOTION.md reports + on_promotion_success hook.

Refs: PROMPT_v1.3.5-hotfix.md GROUP PROMOTION-PARSER,
      PROMPT_v1.3.6-hotfix.md GROUP VERDICT-LENIENT,
      PROMPT_v1.3.7-hotfix.md GROUP ACCEPTANCE-FALLBACK.

PROMOTION.md v2.0 required structure (per AI-trade rules.md
"PROMOTION report format v2.0"):

    - Verdict heading + body keyword (v1.3.6 lenient parse, see below)
    - § Long-only verification
    - § Regime-stratified PnL
    - § Statistical significance
    - § Walk-forward stability
    - § No-lookahead audit
    - Plus all v1.2 sections (Acceptance, Evidence, etc.) — not enforced
      by this module.

v1.3.9: Verdict parsing has a 4-tier fallback. Each tier returns a
canonical {PROMOTED, REJECTED, CONDITIONAL} or None and the next tier
fires only when the prior one returned None.

  Tier 1 (v1.3.6, unchanged):  Verdict heading + body keyword.
  Tier 2 (v1.3.5, unchanged):  legacy strict **Verdict: PROMOTED**.
  Tier 3 (v1.3.7, unchanged):  Acceptance/Conclusion/Result/Outcome/
                               Status heading + verdict-equivalent
                               keyword (or ✅/❌ marker).
  Tier 4 (v1.3.9 + v1.4.0):    inline `**Field**: KEYWORD` bold-
                               metadata pattern. v1.4.0 split into
                               two passes: PRIMARY scans verdict-
                               semantic fields (Result / Verdict /
                               Outcome / Decision / Conclusion);
                               STATUS pass falls back to `**Status**`
                               only. Verdict-semantic always wins
                               when both are present in the file.

Tier 3 exists because measurement / infrastructure tasks legitimately
omit a Verdict heading and close with ## Acceptance (criteria checklist)
or ## Conclusion (analysis summary). Without this fallback, ~50% of
Phase 2 measurement/infra reports return None and the engine logs
`promotion_verdict_unrecognized` even when the report is unambiguously
PROMOTED — disabling ablation spawn, leaderboard updates, and
infrastructure validation that v1.3.5 + v1.3.6 layered on top.

Tier 3 keyword vocabulary (case-insensitive, scanned in the section
under the heading for up to 30 lines or until the next ##/### boundary):

    PROMOTED:    'criteria met', 'all met', 'fully met', whole-word
                 'met', 'met ✅', '✅ met', 'pass', 'passed', bare ✅
    REJECTED:    'criteria not met', 'not met', 'fail', 'failed',
                 bare ❌
    CONDITIONAL: 'partial', 'partially met', 'mixed', 'conditional'

Symmetric ✅/❌ markers complement the keyword set: AI-trade
documentation-style Acceptance sections (e.g. seed_var) confirm work
with bare ✅ checkmarks alone. The 30-line cap keeps a stray ✅ deep
in a body section from accidentally driving the verdict.

Tier 4 (v1.3.9) catches the compact bold-metadata format used by
AI-trade Phase 2 measurement reports — no heading, just an inline
`**Status**: PASS ✓` / `**Result**: FAIL` line. AI-trade Phase 2 v2.1
production logged 31 `promotion_verdict_unrecognized` events in 12
hours from this format alone, dropping all measurement promotions
silently. Field names are restricted to closure synonyms (Status /
Result / Outcome / Verdict / Decision / Conclusion) so unrelated
bold-metadata lines like `**Note**: ...` or `**Pareto points**: 7`
do NOT trigger the tier.

Heading patterns recognized for the Verdict tier (Tier 1):
    ## Verdict
    ## Verdict: PROMOTED
    ## Verdict: LONG_LOSES_MONEY
    ## Stage D: Verdict
    **Verdict: PROMOTED**
    # Verdict
    ### Verdict

Verdict keywords (in heading or body) for Tier 1:
    PROMOTED, ACCEPT, ACCEPTED, PASS, PASSED, STABLE → PROMOTED
    REJECTED, REJECT, FAIL, FAILED, LONG_LOSES_MONEY → REJECTED
    CONDITIONAL, PARTIAL, NEUTRAL                     → CONDITIONAL

v1.3.13: NEUTRAL added across tiers 1, 3, and 4. AI-trade Phase 3
DA-track experiments (information-ceiling probes that close with no
exploitable edge, no clear bug) report `**Status**: NEUTRAL`. Without
NEUTRAL in `CANONICAL_MAP` / `VERDICT_KEYWORD_RE` /
`BOLD_METADATA_VERDICT_RE` / `ACCEPTANCE_KEYWORD_RE`, `parse_verdict`
returned None and `promotion_verdict_unrecognized` was logged — 12
DA-track tasks silently dropped on first deploy. NEUTRAL → CONDITIONAL
keeps the backlog door open for re-probes in different regimes.

Public surface:
    - promotion_path(project, task_id)        -> Path
    - parse_verdict(promotion_path)           -> 'PROMOTED' | 'REJECTED' | 'CONDITIONAL' | None
    - validate_v2_sections(promotion_path)    -> tuple[bool, list[str]]
    - parse_metrics(promotion_path)           -> dict
    - on_promotion_success(project, item, metrics) -> None
    - quarantine_invalid(project, item, missing) -> None
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

REQUIRED_V2_SECTIONS = [
    "Long-only verification",
    "Regime-stratified PnL",
    "Statistical significance",
    "Walk-forward stability",
    "No-lookahead audit",
]

# v1.3.8 PROMOTION-HOOK-DIAGNOSTICS: which task IDs require full v2.0
# PROMOTION sections? Strategy candidates require it (regime parity,
# leakage, walk-forward, etc.). Measurement / infrastructure tasks
# (vec_long_quantile, vec_long_stat_dm_test, vec_long_features_v1, …)
# don't — they legitimately close with an Acceptance section and a
# verdict and have no strategy backtest to gate. Prefix-based so a
# single tuple update extends the gate as new strategy families land
# without coupling promotion.py to the AI-trade roadmap.
STRATEGY_PROMOTION_PREFIXES = (
    "vec_long_synth_",
    "vec_dr_synth_",
    "vec_long_pack_",
    "vec_long_moe_",
    "vec_long_cascade_",
    "vec_long_ensemble_",
    "vec_long_committee_",
    "vec_long_stacking_",
    "vec_long_hybrid_",
)


def requires_full_v2_validation(task_id: str | None) -> bool:
    """v1.3.8: True iff the task ID indicates a strategy candidate that
    must carry the full v2.0 PROMOTION section list. False (relaxed) for
    measurement / infrastructure / research tasks that just need a
    verdict.

    The strict default for unknown / blank task_id is False — when the
    caller can't supply a task_id we treat it as a non-strategy task
    and skip strict validation. Strategy gating is enabled by an
    explicit task_id with a known prefix, not implicitly. (Pre-v1.3.8
    callers that didn't pass task_id continue through validate_v2_sections's
    None-task-id branch which preserves the v1.3.5 strict behaviour.)
    """
    if not task_id:
        return False
    return task_id.startswith(STRATEGY_PROMOTION_PREFIXES)

# v1.3.5 (legacy): exact `**Verdict: PROMOTED**` / `**Verdict: REJECTED**`.
# v1.3.6: kept as a fallback so v1.3.5 PROMOTION fixtures still parse.
VERDICT_RE = re.compile(
    r"^\*\*Verdict:\s*(PROMOTED|REJECTED)\*\*\s*$",
    re.MULTILINE | re.IGNORECASE,
)

# v1.3.6 lenient parser. Recognizes any heading containing "Verdict"
# (any heading level, optionally prefixed with "Stage X:") followed by a
# verdict keyword in the next 20 lines or until the next heading. Maps
# fuzzy verdicts to canonical {PROMOTED, REJECTED, CONDITIONAL}.
#
# Heading patterns recognized:
#   ## Verdict
#   ## Verdict: PROMOTED
#   ## Verdict: LONG_LOSES_MONEY
#   ## Stage D: Verdict
#   **Verdict: PROMOTED**
#   # Verdict
#   ### Verdict
VERDICT_HEADING_RE = re.compile(
    r"^(?P<bold>\*\*)?(?P<hashes>#{0,4})\s*(?:Stage\s+\w+\s*:\s*)?"
    r"Verdict\b[:\s]*[\w\s\-]*(?:\*\*)?\s*$",
    re.MULTILINE | re.IGNORECASE,
)

VERDICT_KEYWORD_RE = re.compile(
    r"\b(PROMOTED|REJECTED|ACCEPTED|ACCEPT|REJECT|PASSED|PASS|FAILED|FAIL|"
    r"STABLE|CONDITIONAL|PARTIAL|LONG_LOSES_MONEY|NEUTRAL)\b",
    re.IGNORECASE,
)


def _next_heading_re(verdict_level: int) -> re.Pattern[str]:
    """Match the next heading at the same level as the Verdict heading or
    higher (fewer hashes). Subsections (`### body` under `## Verdict`) are
    considered part of the verdict body and must not terminate scanning —
    real AI-trade fixtures place the verdict keyword on a `### KEYWORD —
    ...` line directly under `## Verdict`.

    `verdict_level` is the number of `#` characters in the Verdict heading
    (1-4). For a bold-marker form (`**Verdict: ...**`) without any `#`,
    the level is treated as 1 so any subsequent heading bounds the body.
    """
    level = max(1, min(verdict_level, 4))
    return re.compile(rf"^#{{1,{level}}}\s+", re.MULTILINE)

CANONICAL_MAP = {
    "PROMOTED": "PROMOTED",
    "ACCEPT": "PROMOTED",
    "ACCEPTED": "PROMOTED",
    "PASS": "PROMOTED",
    "PASSED": "PROMOTED",
    "STABLE": "PROMOTED",
    "REJECTED": "REJECTED",
    "REJECT": "REJECTED",
    "FAIL": "REJECTED",
    "FAILED": "REJECTED",
    "LONG_LOSES_MONEY": "REJECTED",
    "CONDITIONAL": "CONDITIONAL",
    "PARTIAL": "CONDITIONAL",
    "NEUTRAL": "CONDITIONAL",  # v1.3.13: inconclusive outcome, no exploitable edge
}

# v1.3.7 ACCEPTANCE-FALLBACK: tier 3 vocabulary. Headings recognised
# when no Verdict heading is found anywhere in the file. Each entry is a
# heading at level 2-4 carrying one of the closure-style words.
ACCEPTANCE_HEADING_RE = re.compile(
    r"^(?:\*\*)?#{2,4}\s*"
    r"(?:Acceptance(?:\s+Criteria)?|Conclusion|Result(?:s)?|Outcome|Status)"
    r"\b[:\s]*(?:\*\*)?\s*$",
    re.MULTILINE | re.IGNORECASE,
)

# Three capture groups → three canonical states. First match wins; the
# surrounding code keys off which group landed the match. ✅/❌ have no
# word boundaries (non-word chars), so they sit at the end of each group
# without \b — bare ✅ in a documentation-style Acceptance section
# (e.g. AI-trade `CAND_long_baseline_seed_var_PROMOTION.md` which only
# stamps ✅ next to each documented criterion) signals PROMOTED, and ❌
# signals REJECTED symmetrically. Whole-word `met` is intentionally
# included so a bold **MET** verdict is caught; group-2 alternatives
# `criteria not met` / `not met` are tried before `met` so REJECTED
# never gets misclassified as PROMOTED on negated text.
ACCEPTANCE_KEYWORD_RE = re.compile(
    r"(?:"
    # Group 1 — PROMOTED indicators.
    r"(\bcriteria\s+met\b|\ball\s+met\b|\bfully\s+met\b|"
    r"\bmet\s+✅|✅\s*met\b|\bpassed\b|\bpass\b|\bmet\b|✅)|"
    # Group 2 — REJECTED indicators.
    r"(\bcriteria\s+not\s+met\b|\bnot\s+met\b|\bfailed\b|\bfail\b|❌)|"
    # Group 3 — CONDITIONAL indicators.
    r"(\bpartial(?:ly\s+met)?\b|\bmixed\b|\bconditional\b|\bneutral\b)"
    r")",
    re.IGNORECASE,
)

# Body-section bound for the Acceptance fallback. Acceptance / Conclusion
# closures rarely nest sub-headings before the verdict keyword, so a
# level-agnostic `^#{1,4}\s+` boundary is fine here (compare to the
# verdict-tier _next_heading_re which respects level for sub-heading-
# style verdicts). 30-line cap below the heading guards against picking
# up a stray ✅ deep in a body table.
_ACCEPTANCE_NEXT_HEADING_RE = re.compile(r"^#{1,4}\s+", re.MULTILINE)

# v1.4.0 METRICS-BLOCK-CONVENTION: canonical labelled-key parser. The
# block format is documented in src/orchestrator/prompt.py
# (_implement_task_prompt_block). Field names must match the engine's
# metric keys exactly:
#   verdict, sum_fixed, regime_parity, max_dd, dm_p_value, dsr, auc, sharpe
# This block is the PRIMARY source for both verdict (Tier 0) and metrics
# (pre-fill before the v1.3.x regex extractors). Free-form prose, tables,
# and bold-metadata stay as best-effort fallback for legacy / human-
# written PROMOTION.md files.
_METRICS_BLOCK_HEADING_RE = re.compile(
    r"^#{2,4}\s+Metrics\s+for\s+leaderboard\s*$",
    re.MULTILINE | re.IGNORECASE,
)

_METRICS_BLOCK_FIELD_RE = re.compile(
    r"^\s*[-*]\s*\*\*\s*(?P<key>[a-zA-Z_]+)\s*\*\*\s*[:=]\s*(?P<value>[^\n]+?)\s*$",
    re.MULTILINE,
)

_METRICS_BLOCK_KEYS: set[str] = {
    "verdict", "sum_fixed", "regime_parity", "max_dd",
    "dm_p_value", "dsr", "auc", "sharpe",
}


def _parse_metrics_block(text: str) -> dict[str, str]:
    """Extract labelled key-value pairs from a `## Metrics for leaderboard` block.

    Returns a dict mapping recognised keys to RAW string values. Caller is
    responsible for coercing numeric fields. Returns {} when the block is
    absent. Block is bounded by its heading and the next `##`/`###`/`####`
    heading (or end-of-file).
    """
    heading = _METRICS_BLOCK_HEADING_RE.search(text)
    if not heading:
        return {}
    tail = text[heading.end():]
    next_heading = re.search(r"^#{2,4}\s+", tail, re.MULTILINE)
    section = tail[:next_heading.start()] if next_heading else tail
    out: dict[str, str] = {}
    for m in _METRICS_BLOCK_FIELD_RE.finditer(section):
        key = m.group("key").lower()
        if key in _METRICS_BLOCK_KEYS:
            out[key] = m.group("value").strip()
    return out


# v1.4.0 TABLE-METRICS — markdown-table column aliases. Maps lower-
# cased header cells to canonical metric keys. Defense-in-depth for
# files that follow the v1.4.0 contract loosely (e.g. Phase 3 LA tasks
# that include a `| sf | Sharpe(bar) | Sharpe(daily) |` table but
# forget to populate every labelled-block field).
#
# v1.4.1: bare `"dd"` removed. AI-trade documentation tables frequently
# use `dd` for date-format columns (`Date (dd-MM)`, `dd/yyyy`) or
# unrelated abbreviations; the alias claimed those headers as `max_dd`
# and corrupted the leaderboard with date numerals. `max_dd` and
# `max dd` remain (those are unambiguous v1.3.x extractor names).
_TABLE_COLUMN_ALIASES: dict[str, str] = {
    "sf": "sum_fixed",
    "sum_fixed": "sum_fixed",
    "sum fixed": "sum_fixed",
    "regime_parity": "regime_parity",
    "regime parity": "regime_parity",
    "max_dd": "max_dd",
    "max dd": "max_dd",
    "dm_p": "dm_p_value",
    "dm p": "dm_p_value",
    "dm p-value": "dm_p_value",
    "dm_p_value": "dm_p_value",
    "dsr": "dsr",
    "auc": "auc",
    "roc auc": "auc",
    "roc_auc": "auc",
    "sharpe(daily)": "sharpe",
    "daily sharpe": "sharpe",
    "sharpe daily": "sharpe",
}


def _coerce_table_cell(raw: str) -> float | None:
    """Coerce a markdown-table data cell to float.

    v1.4.1 TABLE-CELL-HARDENING. Handles real Phase 3 PROMOTION.md cell
    shapes that the v1.4.0 inline `float(raw.rstrip('%').lstrip('+'))`
    silently dropped:
      - Bold markers:        `**0.78762**` → 0.78762   (Phase 3 NN tables)
      - Unicode minus:       `−3.32`       → -3.32     (U+2212; Phase 3 LA Δ rows)
      - En-dash / em-dash:   `–4.1` / `—5` → -4.1 / -5 (U+2013 / U+2014)
      - Trailing percent:    `692.84%`     → 692.84
      - Leading plus:        `+0.00576`    → 0.00576
      - Em-dash placeholder: `—` / `--`    → None
      - N/A markers:         `N/A` / `-`   → None
      - Trailing emoji:      `0.86003 ✓`   → 0.86003   (strip trailing non-numeric)

    Returns None when the cell is empty, a placeholder, or doesn't reduce
    to a parseable float.
    """
    s = raw.strip()
    while s.startswith("**") and s.endswith("**") and len(s) >= 4:
        s = s[2:-2].strip()
    s = s.strip("*_").strip()
    if not s or s.lower() in ("n/a", "na", "none", "null", "—", "--", "-"):
        return None
    s = s.replace("−", "-").replace("–", "-").replace("—", "-")
    head: list[str] = []
    seen_digit = False
    for i, ch in enumerate(s):
        if ch.isdigit():
            seen_digit = True
            head.append(ch)
        elif ch in "+-." and not seen_digit:
            head.append(ch)
        elif ch in "+-." and seen_digit and (i == 0 or s[i - 1].isdigit()):
            head.append(ch)
        elif ch.lower() == "e" and seen_digit:
            head.append(ch)
        else:
            break
    cleaned = "".join(head).rstrip("+-.").rstrip("%")
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_table_metrics(text: str) -> dict[str, float]:
    """Best-effort markdown-table parser.

    Returns the FIRST table whose header contains a recognised metric
    alias, parsed cell-by-cell from the first data row. Empty dict
    when no usable table is found. Intentionally narrow: requires a
    leading `|`, trailing `|`, at least 3 column separators, a
    `---`-bearing alignment row, and a data row with the same shape.

    v1.4.1: data-cell coercion delegated to `_coerce_table_cell` so
    bold markers, Unicode minus, em-dash placeholders and trailing
    emoji don't silently swallow the metric.
    """
    lines = text.split("\n")
    n = len(lines)
    for i in range(n - 2):
        header = lines[i].strip()
        if not (header.startswith("|") and header.endswith("|")
                and header.count("|") >= 3):
            continue
        cells = [c.strip().lower() for c in header.strip("|").split("|")]
        col_to_metric: dict[int, str] = {}
        for j, cell in enumerate(cells):
            metric = _TABLE_COLUMN_ALIASES.get(cell)
            if metric is not None:
                col_to_metric[j] = metric
        if not col_to_metric:
            continue
        if "---" not in lines[i + 1]:
            continue
        data_line = lines[i + 2].strip()
        if not (data_line.startswith("|") and data_line.endswith("|")):
            continue
        data_cells = [c.strip() for c in data_line.strip("|").split("|")]
        result: dict[str, float] = {}
        for col_idx, metric in col_to_metric.items():
            if col_idx >= len(data_cells):
                continue
            val = _coerce_table_cell(data_cells[col_idx])
            if val is not None:
                result[metric] = val
        return result
    return {}


def _coerce_metric_value(raw: str) -> float | None:
    """'692.84%' → 692.84, '-8.2' → -8.2, 'N/A' → None, '' → None."""
    s = raw.strip().rstrip("%").lstrip("+").strip()
    if not s or s.lower() in ("n/a", "na", "none", "null", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


# v1.3.9 BOLD-METADATA-VERDICT (tier 4): inline `**Field**: KEYWORD`
# bold-metadata patterns observed in AI-trade Phase 2 v2.1 measurement-
# task PROMOTION reports — `CAND_elo_rating`, `CAND_tournament_*`,
# `CAND_optuna_mo`, etc. These compact reports have NO heading section
# (`## Verdict` / `## Acceptance` / `## Conclusion` / `## Status`), just
# an inline bold-metadata line like:
#
#     # CAND_elo_rating_PROMOTION
#     **Status**: PASS ✓
#     **Note**: ELO computed for 8 models. ...
#
# Tier 1 misses (no Verdict heading), tier 2 misses (no
# `**Verdict: PROMOTED**` literal), tier 3 misses (no level-2/3/4
# closure heading). Without tier 4, AI-trade Phase 2 v2.1 production
# logged 31 `promotion_verdict_unrecognized` events in 12 hours,
# silently dropping every measurement-task promotion.
#
# Field names are restricted to closure synonyms (Status / Result /
# Outcome / Verdict / Decision / Conclusion) so this tier does NOT fire
# on unrelated bold-metadata lines like `**Note**: ...` or
# `**Pareto points**: 7 non-dominated`. The keyword vocabulary mirrors
# tiers 1+3 (PROMOTED / REJECTED / ACCEPT[ED] / REJECT / PASS[ED] /
# FAIL[ED] / STABLE / CONDITIONAL / PARTIAL / LONG_LOSES_MONEY /
# NEUTRAL) so the canonical mapping stays consistent across all tiers.
# v1.3.13: NEUTRAL → CONDITIONAL handles Phase 3 DA-track
# `**Status**: NEUTRAL` closures.
# v1.4.0 RESULT-OVER-STATUS split. Two regexes, two passes. Verdict-
# semantic fields (Result/Verdict/Outcome/Decision/Conclusion) win over
# Status (which Phase 3 conventions use for BIAS audit sign-off, not for
# the promotion verdict). Symmetry: a file with ONLY `**Status**: NEUTRAL`
# still parses correctly via the second pass.
# Both `**Field**:` (colon outside bold close) and `**Field:**` (colon
# inside bold close) shapes are observed in AI-trade Phase 3 production
# (e.g. `**Result:** REJECTED` on NN-track files). `\s*:?\s*\*\*\s*:?\s*`
# accepts either form: optional colon before close, mandatory `**`, then
# optional colon after.
BOLD_METADATA_VERDICT_PRIMARY_RE = re.compile(
    r"^\s*\*\*\s*"
    r"(?:Result|Outcome|Verdict|Decision|Conclusion)"
    r"\s*:?\s*\*\*\s*:?\s*\**\s*"
    r"(PROMOTED|REJECTED|ACCEPT(?:ED)?|REJECT|PASS(?:ED)?|FAIL(?:ED)?|"
    r"STABLE|CONDITIONAL|PARTIAL|LONG_LOSES_MONEY|NEUTRAL)\b",
    re.IGNORECASE | re.MULTILINE,
)

BOLD_METADATA_VERDICT_STATUS_RE = re.compile(
    r"^\s*\*\*\s*Status\s*:?\s*\*\*\s*:?\s*\**\s*"
    r"(PROMOTED|REJECTED|ACCEPT(?:ED)?|REJECT|PASS(?:ED)?|FAIL(?:ED)?|"
    r"STABLE|CONDITIONAL|PARTIAL|LONG_LOSES_MONEY|NEUTRAL)\b",
    re.IGNORECASE | re.MULTILINE,
)


# v1.4.0 MULTI-PREFIX-STRIP — AI-trade Phase 3 conventions split task_id
# into two semantic prefixes (`vec_<phase>_<track>_<descr>`), but the
# PROMOTION.md filename omits either the phase, the track, or both
# depending on which sub-team / iteration produced it. Engine tolerates
# all three forms; the canonical form (`vec_` stripped) is tried first
# so new content lands at the predictable path.
_TASK_ID_PREFIXES: tuple[str, ...] = (
    "vec_long_",   # Phase 2 legacy
    "vec_p1_",     # Phase 1
    "vec_p2_",     # Phase 2 v2.x
    "vec_p3_",     # Phase 3
    "vec_p4_",     # Future Phase 4
    "vec_",        # bare vec_ — keep last so longer prefixes win
)


def _promotion_basename_candidates(task_id: str) -> list[str]:
    """Yield filename basenames to try, in priority order.

    For `vec_p3_meta_anti_winner_bias` returns:
      1. `p3_meta_anti_winner_bias` (strip `vec_` only — canonical)
      2. `meta_anti_winner_bias`    (strip `vec_p3_` — phase prefix included)
      3. `vec_p3_meta_anti_winner_bias` (no stripping — defensive fallback)

    For `vec_long_synth_v3` returns:
      1. `long_synth_v3` (strip `vec_`)
      2. `synth_v3`      (strip `vec_long_`)
      3. `vec_long_synth_v3`
    """
    out: list[str] = []
    # Form 1: strip only `vec_` (canonical).
    if task_id.startswith("vec_"):
        out.append(task_id[len("vec_"):])
    # Form 2: strip the full phase/track prefix.
    for pfx in _TASK_ID_PREFIXES:
        if task_id.startswith(pfx):
            stripped = task_id[len(pfx):]
            if stripped and stripped not in out:
                out.append(stripped)
            break
    # Form 3: defensive — no stripping at all.
    if task_id not in out:
        out.append(task_id)
    return out


def _promotion_basename(task_id: str) -> str:
    """Canonical basename for engine-emitted / write-side paths.

    Returns the first candidate (`vec_` stripped). Read-side callers
    should use `promotion_path()` (which probes all candidates) or
    `promotion_path_candidates()` (which exposes the full chain).
    """
    return _promotion_basename_candidates(task_id)[0]


def promotion_path_candidates(project: Path, task_id: str) -> list[Path]:
    """All filename forms to probe when reading a PROMOTION.md.

    Used by read-side callers (parse_verdict, parse_metrics via the
    cycle scan and the retroactive validator). Write-side callers use
    `promotion_path()` for the canonical form.
    """
    base = project / "data" / "debug"
    return [
        base / f"CAND_{candidate}_PROMOTION.md"
        for candidate in _promotion_basename_candidates(task_id)
    ]


def promotion_path(project: Path, task_id: str) -> Path:
    """Return the FIRST existing candidate path; if none exist, return
    the canonical (Form 1, `vec_`-stripped) path so write-side callers
    get a deterministic target.

    Read-side callers (parse_verdict / parse_metrics) call `.exists()`
    on the result, which now does the multi-candidate probe internally.
    No call site needs to change — but `promotion_path_candidates()` is
    available for callers that want to enumerate explicitly.
    """
    candidates = promotion_path_candidates(project, task_id)
    for p in candidates:
        if p.exists():
            return p
    return candidates[0]


def _parse_verdict_tier1(text: str) -> str | None:
    """Tier 1 (v1.3.6): Verdict heading + body keyword.

    Lenient two-pass discovery. First find a Verdict heading at any level
    (optionally prefixed `Stage X:`), then scan up to 20 lines (or until
    the next same-or-higher-level heading) for a verdict keyword.
    First keyword wins. Returns None when no Verdict heading exists or
    the section under it has no recognised keyword.
    """
    heading_match = VERDICT_HEADING_RE.search(text)
    if not heading_match:
        return None
    after_idx = heading_match.start()
    tail = text[after_idx:]
    verdict_level = len(heading_match.group("hashes") or "")
    next_heading_pattern = _next_heading_re(verdict_level)
    # Skip past the heading line itself before looking for the next heading.
    next_heading = next_heading_pattern.search(
        tail, pos=len(heading_match.group(0))
    )
    section_end = next_heading.start() if next_heading else len(tail)
    section = tail[:section_end]
    # Cap at 20 newlines so an enormous body section past the verdict
    # heading doesn't catch a stray verdict keyword in unrelated prose.
    lines = section.split("\n")[:20]
    section_capped = "\n".join(lines)

    keyword_match = VERDICT_KEYWORD_RE.search(section_capped)
    if not keyword_match:
        return None
    raw = keyword_match.group(1).upper()
    return CANONICAL_MAP.get(raw)


def _parse_verdict_acceptance(text: str) -> str | None:
    """Tier 3 (v1.3.7): Acceptance/Conclusion/Result/Outcome/Status
    heading + verdict-equivalent keyword (or ✅/❌ marker).

    Two-pass mirror of tier 1 but for documentation-style closure
    sections that omit a Verdict heading entirely. Returns None when no
    such heading exists, or when the section under it has neither a
    keyword match nor a checkmark.
    """
    heading_match = ACCEPTANCE_HEADING_RE.search(text)
    if not heading_match:
        return None
    after_idx = heading_match.start()
    tail = text[after_idx:]
    next_heading = _ACCEPTANCE_NEXT_HEADING_RE.search(
        tail, pos=len(heading_match.group(0))
    )
    section_end = next_heading.start() if next_heading else len(tail)
    section = tail[:section_end]
    # 30-line cap (vs tier 1's 20) — Acceptance sections often itemise
    # criteria with several context lines before stamping the verdict.
    lines = section.split("\n")[:30]
    section_capped = "\n".join(lines)

    keyword_match = ACCEPTANCE_KEYWORD_RE.search(section_capped)
    if keyword_match is None:
        return None
    if keyword_match.group(1) is not None:
        return "PROMOTED"
    if keyword_match.group(2) is not None:
        return "REJECTED"
    if keyword_match.group(3) is not None:
        return "CONDITIONAL"
    return None


def _parse_verdict_block(text: str) -> str | None:
    """Tier 0 (v1.4.0): `## Metrics for leaderboard` block `**verdict**: X`.

    Primary source. Returns None when the block is absent or has no
    parseable verdict; engine falls through to Tier 1-4.
    """
    block = _parse_metrics_block(text)
    raw = block.get("verdict")
    if not raw:
        return None
    return CANONICAL_MAP.get(raw.strip().upper())


def _parse_verdict_tier4_bold_metadata(text: str) -> str | None:
    """Tier 4 (v1.3.9 + v1.4.0 two-pass): bold-metadata fallback.

    First pass: verdict-semantic fields (Result / Verdict / Outcome /
    Decision / Conclusion). Second pass: Status field (Phase 3 BIAS
    audit reuses Status for `PASS ✓`; verdict-semantic always wins
    when both are present in the same file).

    Restricted to closure-synonym field names so unrelated bold
    metadata (e.g. `**Note**: ...`, `**Pareto points**: 7`) does NOT
    trigger this tier.
    """
    primary = BOLD_METADATA_VERDICT_PRIMARY_RE.search(text)
    if primary is not None:
        return CANONICAL_MAP.get(primary.group(1).upper())
    status = BOLD_METADATA_VERDICT_STATUS_RE.search(text)
    if status is not None:
        return CANONICAL_MAP.get(status.group(1).upper())
    return None


def parse_verdict(path: Path) -> str | None:
    """Return canonical verdict 'PROMOTED' | 'REJECTED' | 'CONDITIONAL' | None.

    Five-tier fallback (v1.4.0):
      0. `## Metrics for leaderboard` block `**verdict**:` field — primary
         source written by Claude under the v1.4.0 prompt contract.
      1. Verdict heading + body keyword (v1.3.6 lenient parse).
      2. Legacy strict `**Verdict: <X>**` pattern (v1.3.5 backward compat).
      3. Acceptance / Conclusion / Result / Outcome / Status heading +
         verdict-equivalent keyword or ✅/❌ marker. Defense-in-depth —
         measurement / infrastructure tasks legitimately omit Verdict
         headings.
      4. Inline `**Field**: KEYWORD` bold-metadata pattern (v1.3.9) —
         compact AI-trade Phase 2 measurement reports drop the heading
         entirely and stamp the verdict on a `**Status**: PASS ✓` line.

    Each tier returns the first match; the next tier fires only when
    the prior one returned None. Returns None when no tier finds a
    verdict.
    """
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    # Tier 0 (v1.4.0): labelled `## Metrics for leaderboard` block.
    result = _parse_verdict_block(text)
    if result is not None:
        return result

    # Tier 1: Verdict heading + body keyword (v1.3.6).
    result = _parse_verdict_tier1(text)
    if result is not None:
        return result

    # Tier 2: legacy strict `**Verdict: PROMOTED**` (v1.3.5 backward compat).
    legacy = VERDICT_RE.search(text)
    if legacy:
        return CANONICAL_MAP.get(legacy.group(1).upper())

    # Tier 3 (v1.3.7): Acceptance / Conclusion / Result fallback.
    result = _parse_verdict_acceptance(text)
    if result is not None:
        return result

    # Tier 4 (v1.3.9): inline `**Status**: PASS` bold-metadata fallback.
    return _parse_verdict_tier4_bold_metadata(text)


def validate_v2_sections(
    path: Path, task_id: str | None = None
) -> tuple[bool, list[str]]:
    """Returns (all_present, missing_list).

    Each section is matched as `## | ### | ####` heading containing the
    section name (case-insensitive). The test is intentionally loose:
    `### Regime-stratified PnL (5 regimes)` and `## REGIME-STRATIFIED PNL`
    both pass.

    v1.3.8 PROMOTION-HOOK-DIAGNOSTICS: when `task_id` is supplied AND it
    does NOT match a strategy-candidate prefix, strict v2.0 validation
    is skipped and (True, []) returned. The 5 sections are the right
    enforcement for strategy backtests (long-only check, regime parity,
    DM significance, walk-forward, leakage audit) but inappropriate for
    measurement / infra tasks (e.g. vec_long_quantile distribution
    summary, vec_long_stat_dm_test pipeline-readiness check). Without
    this gate, AI-trade Phase 2 v2.0 closed 4 measurement tasks with
    PROMOTED verdicts that v1.3.5 silently quarantined for "missing
    sections" — never spawning ablation children or appending to the
    leaderboard.

    Pre-v1.3.8 callers that pass only the path (task_id=None) continue
    to receive the full strict validation, preserving the v1.3.5
    behaviour. New call sites (cycle.py) pass task_id so the gate
    activates.
    """
    if not path.exists():
        return False, list(REQUIRED_V2_SECTIONS)
    if task_id is not None and not requires_full_v2_validation(task_id):
        return True, []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False, list(REQUIRED_V2_SECTIONS)
    missing: list[str] = []
    for sec in REQUIRED_V2_SECTIONS:
        pat = re.compile(
            r"^#{2,4}\s+.*" + re.escape(sec),
            re.MULTILINE | re.IGNORECASE,
        )
        if not pat.search(text):
            missing.append(sec)
    return (not missing), missing


def parse_metrics(path: Path) -> dict[str, Any]:
    """Best-effort numeric extraction from PROMOTION.md.

    Missing fields default to None. Engine consumers should treat None
    as 'unknown', NOT 'zero' — composite scoring penalises None as 0
    contribution but never raises on missing keys.
    """
    out: dict[str, Any] = {
        "sum_fixed": None,
        "regime_parity": None,
        "max_dd": None,
        "dm_p_value": None,
        "dsr": None,
        "auc": None,     # v1.3.13: Phase 3 ROC AUC in [0, 1]
        "sharpe": None,  # v1.3.13: Phase 3 annualised Sharpe ratio
    }
    if not path.exists():
        return out
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out

    # v1.4.0 PRIMARY: labelled `## Metrics for leaderboard` block.
    # Wins over every other extractor when present. Each subsequent
    # legacy regex / cascade is guarded by `if out[<key>] is None`,
    # so a labelled value is never overwritten.
    block = _parse_metrics_block(text)
    for k in ("sum_fixed", "regime_parity", "max_dd", "dm_p_value",
              "dsr", "auc", "sharpe"):
        if k in block:
            out[k] = _coerce_metric_value(block[k])

    if out["sum_fixed"] is None:
        m = re.search(
            r"sum[_\s]?fixed\s*[:=]?\s*([+-]?\d+(?:\.\d+)?)\s*%",
            text,
            re.IGNORECASE,
        )
        if m:
            out["sum_fixed"] = float(m.group(1))

    if out["regime_parity"] is None:
        m = re.search(
            r"regime[_\s]?parity\s*[:=]?\s*(\d+(?:\.\d+)?)",
            text,
            re.IGNORECASE,
        )
        if m:
            out["regime_parity"] = float(m.group(1))

    if out["max_dd"] is None:
        m = re.search(
            r"max[_\s]?DD\s*[:=]?\s*([+-]?\d+(?:\.\d+)?)\s*%",
            text,
            re.IGNORECASE,
        )
        if m:
            out["max_dd"] = float(m.group(1))

    # v1.3.13: hyphen + markdown-bold-close support added —
    # `**DM p-value**: 0.031` is the canonical Phase 3 format.
    if out["dm_p_value"] is None:
        m = re.search(
            r"DM[_\s\-]?p(?:[_\s\-]?value)?\**\s*[:=]?\s*(\d+(?:\.\d+)?)",
            text,
            re.IGNORECASE,
        )
        if m:
            out["dm_p_value"] = float(m.group(1))

    if out["dsr"] is None:
        m = re.search(r"\bDSR\b\s*[:=]?\s*(\d+(?:\.\d+)?)", text, re.IGNORECASE)
        if m:
            out["dsr"] = float(m.group(1))

    # v1.3.13 Phase 3 — ROC AUC. Handles inline `AUC: 0.86`,
    # `ROC AUC: 0.86`, markdown-bold `**AUC**: 0.86`, and table-cell
    # `| AUC | 0.86 |`. The post-keyword `\**` consumes a trailing
    # markdown-bold close; `[|:=]?` accepts the table cell separator.
    if out["auc"] is None:
        m = re.search(
            r"\b(?:ROC[_\s]?)?AUC\b\**\s*[|:=]?\s*(\d+(?:\.\d+)?)",
            text,
            re.IGNORECASE,
        )
        if m:
            out["auc"] = float(m.group(1))

    # v1.4.0 DAILY-SHARPE cascade. Only fires when GROUP 1's metrics
    # block didn't supply sharpe.
    #
    # Priority 1: explicit daily form — `Sharpe(daily)`, `Sharpe daily`,
    # `daily Sharpe`, `daily-Sharpe`. Used by Phase 3 LA tasks that also
    # carry an inflated `Per-bar Sharpe 90.8` note higher in the file.
    #
    # Priority 2: bare Sharpe (Phase 2 / Phase 3 NN), with lookbehind /
    # lookahead exclusion of per-bar contexts. The PROMPT's narrower
    # `\bSharpe\b` shape was widened to keep the v1.3.13 markdown-bold-
    # close + `ratio` tolerance (`**Sharpe ratio**: 1.45` is a v1.3.13
    # test fixture); the per-bar exclusion still does the load-bearing
    # work for the inflation-note case.
    if out["sharpe"] is None:
        m = re.search(
            r"(?:Sharpe[\s_(]+daily[\s)]*|daily[\s_-]+Sharpe)"
            r"\**\s*[|:=]?\s*([+-]?\d+(?:\.\d+)?)",
            text,
            re.IGNORECASE,
        )
        if m:
            out["sharpe"] = float(m.group(1))

    if out["sharpe"] is None:
        for m in re.finditer(
            r"\bSharpe(?:[_\s]ratio)?\b\**\s*[|:=]?\s*([+-]?\d+(?:\.\d+)?)",
            text,
            re.IGNORECASE,
        ):
            start = m.start()
            before = text[max(0, start - 12):start].lower()
            if "per-bar " in before or "per_bar " in before:
                continue
            if "(bar)" in text[m.end():m.end() + 5].lower():
                continue
            out["sharpe"] = float(m.group(1))
            break

    # v1.4.0 TABLE-METRICS fallback: fill any remaining None slots from
    # the first markdown table whose header contains a recognised alias.
    # The `out.get(k) is None` guard preserves priority: labelled block
    # > legacy regex > Sharpe cascade > table fallback.
    table = _parse_table_metrics(text)
    for k, v in table.items():
        if out.get(k) is None:
            out[k] = v

    return out


def _ablation_children_for(parent_id: str, parent_priority: int) -> list[str]:
    """Generate 5 backlog lines for ablation children.

    parent_priority is the integer priority from BacklogItem (0=P0,
    1=P1, ...). Children get parent priority + 1, capped at P3 (the
    lowest tier). PROMPT specifies "P3 caps to P3" so very-low-priority
    parents don't spawn even-lower-priority children that would
    starve.
    """
    new_pri = min(parent_priority + 1, 3)
    pri = f"P{new_pri}"
    return [
        f"- [ ] [implement] [{pri}] {parent_id}_ab_drop_top — "
        f"Drop top SHAP feature group from parent {parent_id}, retrain, "
        f"measure delta. Acceptance: sum_fixed delta documented; if "
        f"≥+5pp → flag as new candidate parent.",
        f"- [ ] [implement] [{pri}] {parent_id}_ab_loss — "
        f"Swap loss function (CE↔focal or focal↔PnL surrogate) on parent "
        f"{parent_id} architecture. Acceptance: AUC + sum_fixed comparison.",
        f"- [ ] [implement] [{pri}] {parent_id}_ab_seq — "
        f"Halve or double sequence length on parent {parent_id}. "
        f"Acceptance: receptive-field sensitivity documented.",
        f"- [ ] [implement] [{pri}] {parent_id}_ab_seed — "
        f"Same arch as parent {parent_id}, different random seed. "
        f"Acceptance: variance estimate vs vec_long_baseline_seed_var "
        f"noise floor.",
        f"- [ ] [implement] [{pri}] {parent_id}_ab_eth — "
        f"Cross-asset replication of parent {parent_id} on ETH. "
        f"Acceptance: parity with BTC reference; document any "
        f"regime-specific divergence.",
    ]


def _resolve_backlog_path(project: Path) -> Path | None:
    """Return the project's backlog.md path or None when no backlog
    file exists. Mirrors orchestrator.research's resolution: prefers
    <project>/backlog.md, falls back to .cc-autopipe/backlog.md."""
    primary = project / "backlog.md"
    if primary.exists():
        return primary
    fallback = project / ".cc-autopipe" / "backlog.md"
    if fallback.exists():
        return fallback
    return None


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def on_promotion_success(project: Path, item: Any, metrics: dict[str, Any]) -> None:
    """Atomic backlog mutation: append 5 ablation children. Fire LB hook.

    `item` may be a backlog.BacklogItem or any object exposing `id` and
    `priority` attributes. The leaderboard hook (lib.leaderboard) is
    imported lazily so this module remains usable in environments that
    lack the leaderboard module (tests, partial deployments).

    v1.3.8 PROMOTION-HOOK-DIAGNOSTICS: emits a stage-tagged event trail
    so the operator can grep aggregate.jsonl and see exactly where each
    PROMOTED task lands. AI-trade Phase 2 v2.0 production observed 4
    PROMOTED measurement tasks that produced no ablation children and
    no leaderboard append — without per-stage events, root-causing was
    impossible. Events emitted (in order):
      - on_promotion_success_entered      (always)
      - promotion_children_skipped         (no backlog) OR
        ablation_children_spawned          (success path)
      - on_promotion_success_failed        (per stage on raise)
      - on_promotion_success_completed     (only when both stages OK)
    """
    import state as _state  # noqa: PLC0415

    task_id = getattr(item, "id", "")
    _state.log_event(
        project,
        "on_promotion_success_entered",
        task_id=task_id,
    )

    ablation_ok = False
    target = _resolve_backlog_path(project)
    if target is None:
        _state.log_event(
            project,
            "promotion_children_skipped",
            task_id=task_id,
            reason="backlog_missing",
        )
    else:
        try:
            children = _ablation_children_for(
                task_id, int(getattr(item, "priority", 1))
            )
            text = target.read_text(encoding="utf-8")
            # Insert children at end of body, but BEFORE a "## Done"
            # section if one exists. This keeps the backlog sorted as
            # active → ablations → done.
            insertion_marker = "## Done"
            if insertion_marker in text:
                head, _, tail = text.partition(insertion_marker)
                new_text = (
                    head.rstrip()
                    + "\n\n"
                    + "\n".join(children)
                    + "\n\n"
                    + insertion_marker
                    + tail
                )
            else:
                new_text = (
                    text.rstrip() + "\n\n" + "\n".join(children) + "\n"
                )
            _atomic_write(target, new_text)
            _state.log_event(
                project,
                "ablation_children_spawned",
                parent=task_id,
                count=len(children),
            )
            ablation_ok = True
        except Exception as exc:  # noqa: BLE001
            _state.log_event(
                project,
                "on_promotion_success_failed",
                task_id=task_id,
                stage="ablation_spawn",
                error=repr(exc),
            )

    # Leaderboard hook is best-effort — a missing module must not
    # prevent the promotion path from completing.
    leaderboard_ok = False
    try:
        import leaderboard as _lb  # noqa: PLC0415

        _lb.append_entry(project, task_id, metrics)
        leaderboard_ok = True
    except Exception as exc:  # noqa: BLE001
        _state.log_event(
            project,
            "on_promotion_success_failed",
            task_id=task_id,
            stage="leaderboard",
            error=repr(exc),
        )
        # Backwards compatibility: keep the v1.3.5 event name too so any
        # tooling filtering on `leaderboard_append_skipped` keeps working.
        _state.log_event(
            project,
            "leaderboard_append_skipped",
            task_id=task_id,
            reason=repr(exc),
        )

    if ablation_ok and leaderboard_ok:
        _state.log_event(
            project,
            "on_promotion_success_completed",
            task_id=task_id,
        )


def quarantine_invalid(
    project: Path, item: Any, missing: list[str]
) -> None:
    """Revert backlog task to [~] and write quarantine marker.

    Engine treats this like a meta_reflect-pending state: the next
    cycle's prompt will surface the missing sections via the standard
    backlog top-N injection. Operator (or claude on the next turn)
    completes the missing sections, then re-marks [x].
    """
    import state as _state  # noqa: PLC0415

    quar = project / "data" / "debug" / f"UNVALIDATED_PROMOTION_{getattr(item, 'id')}.md"
    quar.parent.mkdir(parents=True, exist_ok=True)
    body = (
        f"# Unvalidated promotion: {getattr(item, 'id')}\n\n"
        "Verdict was PROMOTED but the following v2.0 PROMOTION sections "
        "are missing:\n\n"
        + "\n".join(f"- {s}" for s in missing)
        + "\n\nEngine reverted backlog mark to [~]. Add missing sections "
        f"to `data/debug/CAND_{getattr(item, 'id')}_PROMOTION.md`, then "
        "re-mark [x].\n"
    )
    quar.write_text(body, encoding="utf-8")

    target = _resolve_backlog_path(project)
    if target is not None:
        text = target.read_text(encoding="utf-8")
        # Match the specific task line: `- [x] [implement] [P?] <id> ...`
        # with a word-boundary guard so we don't match a longer id.
        pattern = re.compile(
            r"^(\s*-\s*)\[x\](\s*\[implement\]\s*\[\w+\]\s*"
            + re.escape(getattr(item, "id"))
            + r"\b)",
            re.MULTILINE,
        )
        new_text, n = pattern.subn(r"\1[~]\2", text, count=1)
        if n:
            _atomic_write(target, new_text)

    _state.log_event(
        project,
        "promotion_invalid",
        task_id=getattr(item, "id"),
        missing_sections=",".join(missing),
    )
