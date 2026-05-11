"""Unit tests for v1.4.1 TIER1-NEGATION-GUARD.

`_parse_verdict_tier1` was a single `re.search` against an alternation
covering every verdict keyword in any polarity. A Verdict section with
prose like `did NOT pass — Result: REJECTED` silently captured `pass`
as PROMOTED because `pass` appeared FIRST. Tier 3 already mitigated
this via group ordering; Tier 4 already mitigated this via a two-pass
split; Tier 1 had no equivalent guard.

v1.4.1 splits Tier 1 into three ordered passes:
  Pass 1: REJECTED-class keywords (REJECTED/REJECT/FAILED/FAIL/
          LONG_LOSES_MONEY) win unconditionally.
  Pass 2: CONDITIONAL-class keywords (CONDITIONAL/PARTIAL/NEUTRAL).
  Pass 3: PROMOTED-class keywords (PROMOTED/ACCEPTED/ACCEPT/
          PASSED/PASS/STABLE) with an 8-char negation lookbehind
          that filters `not pass` / `n't pass` / `fail to pass` /
          `didn't pass` / `won't pass` etc.

Refs: PROMPT_v1.4.1-hotfix.md GROUP TIER1-NEGATION-GUARD.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_LIB = REPO_ROOT / "src" / "lib"

sys.path.insert(0, str(SRC_LIB))

import promotion  # noqa: E402


def _write(tmp_path: Path, body: str) -> Path:
    """Write body to a PROMOTION.md path the parser can `.exists()`."""
    p = tmp_path / "CAND_test_PROMOTION.md"
    p.write_text(body, encoding="utf-8")
    return p


def test_did_not_pass_with_rejected_resolves_to_rejected(
    tmp_path: Path,
) -> None:
    """`did NOT pass ... Result: REJECTED` must resolve to REJECTED.

    Pass 1 catches REJECTED before Pass 3 would even consider `pass`.
    Independently, Pass 3's negation guard would also reject the
    `pass` candidate (8-char window contains `not `)."""
    p = _write(
        tmp_path,
        "## Verdict\n\nThis task did NOT pass.\n\nResult: REJECTED\n",
    )
    assert promotion.parse_verdict(p) == "REJECTED"


def test_fully_promoted_resolves_to_promoted(tmp_path: Path) -> None:
    """`Fully promoted — no issues.` — Pass 3 fires (no REJECT/
    CONDITIONAL keywords); the 8-char window before `promoted`
    contains no negation prefix, so the keyword wins."""
    p = _write(
        tmp_path,
        "## Verdict\n\nFully promoted — no issues.\n",
    )
    assert promotion.parse_verdict(p) == "PROMOTED"


def test_failed_with_partial_resolves_to_rejected(tmp_path: Path) -> None:
    """`failed initial validation but partial credit applies.` — Pass 1
    catches `failed` first; the CONDITIONAL keyword `partial` later
    in the section is ignored because Pass 1 already returned. Pin
    the pass-ordering contract: REJECTED beats CONDITIONAL."""
    p = _write(
        tmp_path,
        "## Verdict\n\n"
        "The model failed initial validation but partial credit applies.\n",
    )
    assert promotion.parse_verdict(p) == "REJECTED"


def test_match_promote_keyword_negation_filter() -> None:
    """Direct exercise of `_match_promote_keyword`. A section containing
    ONLY a negated PROMOTED keyword returns None so the caller can
    fall through to subsequent tiers. The 8-char window catches
    `not `, `n't `, `fail to `, etc."""
    assert promotion._match_promote_keyword("did NOT pass") is None
    assert promotion._match_promote_keyword("didn't pass") is None
    assert promotion._match_promote_keyword("fail to pass") is None
    # Without negation, the candidate wins (raw keyword returned).
    assert (
        promotion._match_promote_keyword("Fully promoted").upper()
        == "PROMOTED"
    )
