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

v1.3.7: Verdict parsing has a 3-tier fallback. Each tier returns a
canonical {PROMOTED, REJECTED, CONDITIONAL} or None and the next tier
fires only when the prior one returned None.

  Tier 1 (v1.3.6, unchanged):  Verdict heading + body keyword.
  Tier 2 (v1.3.5, unchanged):  legacy strict **Verdict: PROMOTED**.
  Tier 3 (v1.3.7, new):        Acceptance/Conclusion/Result/Outcome/
                               Status heading + verdict-equivalent
                               keyword (or ✅/❌ marker).

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
    CONDITIONAL, PARTIAL                              → CONDITIONAL

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
    r"STABLE|CONDITIONAL|PARTIAL|LONG_LOSES_MONEY)\b",
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
    r"(\bpartial(?:ly\s+met)?\b|\bmixed\b|\bconditional\b)"
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


def _promotion_basename(task_id: str) -> str:
    """AI-trade convention: PROMOTION files drop 'vec_long_' / 'vec_' prefix.
    'vec_long_only_baseline' -> 'long_only_baseline'
    'vec_meta'               -> 'meta'
    """
    base = task_id
    for pfx in ('vec_long_', 'vec_'):
        if base.startswith(pfx):
            base = base[len(pfx):]
            break
    return base


def promotion_path(project: Path, task_id: str) -> Path:
    return project / 'data' / 'debug' / f'CAND_{_promotion_basename(task_id)}_PROMOTION.md'


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


def parse_verdict(path: Path) -> str | None:
    """Return canonical verdict 'PROMOTED' | 'REJECTED' | 'CONDITIONAL' | None.

    Three-tier fallback (v1.3.7):
      1. Verdict heading + body keyword (v1.3.6 lenient parse).
      2. Legacy strict `**Verdict: <X>**` pattern (v1.3.5 backward compat).
      3. Acceptance / Conclusion / Result / Outcome / Status heading +
         verdict-equivalent keyword or ✅/❌ marker. Defense-in-depth —
         measurement / infrastructure tasks legitimately omit Verdict
         headings.

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

    # Tier 1: Verdict heading + body keyword (v1.3.6).
    result = _parse_verdict_tier1(text)
    if result is not None:
        return result

    # Tier 2: legacy strict `**Verdict: PROMOTED**` (v1.3.5 backward compat).
    legacy = VERDICT_RE.search(text)
    if legacy:
        return CANONICAL_MAP.get(legacy.group(1).upper())

    # Tier 3 (v1.3.7): Acceptance / Conclusion / Result fallback.
    return _parse_verdict_acceptance(text)


def validate_v2_sections(path: Path) -> tuple[bool, list[str]]:
    """Returns (all_present, missing_list).

    Each section is matched as `## | ### | ####` heading containing the
    section name (case-insensitive). The test is intentionally loose:
    `### Regime-stratified PnL (5 regimes)` and `## REGIME-STRATIFIED PNL`
    both pass.
    """
    if not path.exists():
        return False, list(REQUIRED_V2_SECTIONS)
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
    }
    if not path.exists():
        return out
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out

    m = re.search(
        r"sum[_\s]?fixed\s*[:=]?\s*([+-]?\d+(?:\.\d+)?)\s*%",
        text,
        re.IGNORECASE,
    )
    if m:
        out["sum_fixed"] = float(m.group(1))

    m = re.search(
        r"regime[_\s]?parity\s*[:=]?\s*(\d+(?:\.\d+)?)",
        text,
        re.IGNORECASE,
    )
    if m:
        out["regime_parity"] = float(m.group(1))

    m = re.search(
        r"max[_\s]?DD\s*[:=]?\s*([+-]?\d+(?:\.\d+)?)\s*%",
        text,
        re.IGNORECASE,
    )
    if m:
        out["max_dd"] = float(m.group(1))

    m = re.search(
        r"DM[_\s]?p(?:[_\s]?value)?\s*[:=]?\s*(\d+(?:\.\d+)?)",
        text,
        re.IGNORECASE,
    )
    if m:
        out["dm_p_value"] = float(m.group(1))

    m = re.search(r"\bDSR\b\s*[:=]?\s*(\d+(?:\.\d+)?)", text, re.IGNORECASE)
    if m:
        out["dsr"] = float(m.group(1))

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
    """
    import state as _state  # noqa: PLC0415

    target = _resolve_backlog_path(project)
    if target is None:
        _state.log_event(
            project,
            "promotion_children_skipped",
            task_id=getattr(item, "id", ""),
            reason="backlog_missing",
        )
    else:
        children = _ablation_children_for(
            getattr(item, "id"), int(getattr(item, "priority", 1))
        )
        text = target.read_text(encoding="utf-8")
        # Insert children at end of body, but BEFORE a "## Done" section
        # if one exists. This keeps the backlog sorted as
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
            new_text = text.rstrip() + "\n\n" + "\n".join(children) + "\n"
        _atomic_write(target, new_text)
        _state.log_event(
            project,
            "ablation_children_spawned",
            parent=getattr(item, "id"),
            count=len(children),
        )

    # Leaderboard hook is best-effort — a missing module must not
    # prevent the promotion path from completing.
    try:
        import leaderboard as _lb  # noqa: PLC0415

        _lb.append_entry(project, getattr(item, "id"), metrics)
    except Exception as exc:  # noqa: BLE001
        _state.log_event(
            project,
            "leaderboard_append_skipped",
            task_id=getattr(item, "id", ""),
            reason=repr(exc),
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
