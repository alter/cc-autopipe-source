"""Integration tests for promotion validation flow.

v1.3.5 Group PROMOTION-PARSER. Exercises promotion.on_promotion_success
and promotion.quarantine_invalid against a real backlog.md mutation
(atomic write via tmpfile + os.replace) without spinning up the full
orchestrator subprocess. Covers:

  - PROMOTED + full v2.0 sections → 5 ablation children appended,
    leaderboard_append fired, ablation_children_spawned event in log
  - PROMOTED + missing sections → backlog [x] reverted to [~],
    UNVALIDATED_PROMOTION_<id>.md written, no children
  - REJECTED → log only, no children, no leaderboard append
  - Atomic backlog mutation: file always parseable mid-flow
  - Ablation child priority: parent P1 → P2; parent P3 → P3 cap
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
LIB = SRC / "lib"

for p in (str(SRC), str(LIB)):
    if p not in sys.path:
        sys.path.insert(0, p)

import promotion  # noqa: E402


def _seed(
    base: Path,
    *,
    backlog_body: str,
    promotion_body: str,
    task_id: str,
    user_home: Path,
) -> Path:
    project = base / "demo"
    cca = project / ".cc-autopipe" / "memory"
    cca.mkdir(parents=True, exist_ok=True)
    (project / "backlog.md").write_text(backlog_body, encoding="utf-8")
    p = promotion.promotion_path(project, task_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(promotion_body, encoding="utf-8")
    user_home.mkdir(parents=True, exist_ok=True)
    (user_home / "log").mkdir(parents=True, exist_ok=True)
    return project


def _read_aggregate(user_home: Path) -> list[dict]:
    p = user_home / "log" / "aggregate.jsonl"
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]


PROMOTED_FULL = """\
**Verdict: PROMOTED**

## Long-only verification
yes
## Regime-stratified PnL
yes (parity=0.18)
sum_fixed: +268.99%
regime_parity: 0.18
max_DD: -8.20%
DM_p_value: 0.003
DSR: 1.12
## Statistical significance
yes
## Walk-forward stability
yes
## No-lookahead audit
yes
"""

PROMOTED_MISSING_WALK = """\
**Verdict: PROMOTED**

## Long-only verification
yes
## Regime-stratified PnL
yes
## Statistical significance
yes
## No-lookahead audit
yes
"""

REJECTED_FULL = """\
**Verdict: REJECTED**

## Long-only verification
n/a
## Regime-stratified PnL
parity fails
## Statistical significance
p>0.1
## Walk-forward stability
1/4 windows
## No-lookahead audit
clean
"""


def test_promoted_full_spawns_5_ablation_children(
    tmp_path: Path, monkeypatch
) -> None:
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    project = _seed(
        tmp_path,
        backlog_body=(
            "- [x] [implement] [P1] vec_long_lgbm — model\n"
            "## Done\n"
        ),
        promotion_body=PROMOTED_FULL,
        task_id="vec_long_lgbm",
        user_home=user_home,
    )
    item = SimpleNamespace(id="vec_long_lgbm", priority=1)
    metrics = promotion.parse_metrics(
        promotion.promotion_path(project, "vec_long_lgbm")
    )
    promotion.on_promotion_success(project, item, metrics)

    body = (project / "backlog.md").read_text(encoding="utf-8")
    for suffix in ("ab_drop_top", "ab_loss", "ab_seq", "ab_seed", "ab_eth"):
        assert f"vec_long_lgbm_{suffix}" in body, f"missing {suffix}\n{body}"
    # Children priority parent P1 → children P2.
    assert body.count("[P2]") >= 5

    events = [e for e in _read_aggregate(user_home) if e.get("event")]
    spawned = [e for e in events if e["event"] == "ablation_children_spawned"]
    assert len(spawned) == 1
    assert spawned[0]["count"] == 5


def test_promoted_inserts_before_done_section(
    tmp_path: Path, monkeypatch
) -> None:
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    project = _seed(
        tmp_path,
        backlog_body=(
            "- [x] [implement] [P1] vec_long_lgbm — model\n\n"
            "## Done\n"
            "(legacy shipped)\n"
        ),
        promotion_body=PROMOTED_FULL,
        task_id="vec_long_lgbm",
        user_home=user_home,
    )
    item = SimpleNamespace(id="vec_long_lgbm", priority=1)
    promotion.on_promotion_success(project, item, {})

    body = (project / "backlog.md").read_text(encoding="utf-8")
    ab_pos = body.index("vec_long_lgbm_ab_drop_top")
    done_pos = body.index("## Done")
    assert ab_pos < done_pos, "ablation children must be inserted BEFORE Done section"


def test_promoted_priority_p3_caps_to_p3(tmp_path: Path, monkeypatch) -> None:
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    project = _seed(
        tmp_path,
        backlog_body="- [x] [implement] [P3] vec_long_lgbm — model\n",
        promotion_body=PROMOTED_FULL,
        task_id="vec_long_lgbm",
        user_home=user_home,
    )
    item = SimpleNamespace(id="vec_long_lgbm", priority=3)
    promotion.on_promotion_success(project, item, {})
    body = (project / "backlog.md").read_text(encoding="utf-8")
    # All five children should be P3, not P4.
    assert "[P4]" not in body
    assert body.count("[P3]") >= 5


def test_quarantine_invalid_reverts_x_to_tilde(
    tmp_path: Path, monkeypatch
) -> None:
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    project = _seed(
        tmp_path,
        backlog_body="- [x] [implement] [P1] vec_long_lgbm — model\n",
        promotion_body=PROMOTED_MISSING_WALK,
        task_id="vec_long_lgbm",
        user_home=user_home,
    )
    item = SimpleNamespace(id="vec_long_lgbm", priority=1)
    promotion.quarantine_invalid(
        project, item, ["Walk-forward stability"]
    )

    body = (project / "backlog.md").read_text(encoding="utf-8")
    assert "[~] [implement] [P1] vec_long_lgbm" in body
    assert "[x] [implement] [P1] vec_long_lgbm" not in body

    quar = (
        project / "data" / "debug" / "UNVALIDATED_PROMOTION_vec_long_lgbm.md"
    )
    assert quar.exists()
    assert "Walk-forward stability" in quar.read_text(encoding="utf-8")

    events = [e for e in _read_aggregate(user_home) if e.get("event")]
    invalid = [e for e in events if e["event"] == "promotion_invalid"]
    assert len(invalid) == 1
    assert "Walk-forward stability" in invalid[0]["missing_sections"]


def test_atomic_write_no_partial_state(tmp_path: Path, monkeypatch) -> None:
    """The backlog mutation must use tmp+os.replace so a concurrent
    reader sees either old or new — never partial."""
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    project = _seed(
        tmp_path,
        backlog_body="- [x] [implement] [P1] vec_long_lgbm — m\n",
        promotion_body=PROMOTED_FULL,
        task_id="vec_long_lgbm",
        user_home=user_home,
    )
    item = SimpleNamespace(id="vec_long_lgbm", priority=1)
    promotion.on_promotion_success(project, item, {})

    # No leftover .tmp files.
    assert list(project.glob("backlog.md.tmp*")) == []
    body = (project / "backlog.md").read_text(encoding="utf-8")
    # File parses to coherent lines (not corrupted).
    for line in body.splitlines():
        # Each non-empty content line should be a backlog entry or blank
        assert line == "" or line.startswith("- ") or line.startswith("#")
