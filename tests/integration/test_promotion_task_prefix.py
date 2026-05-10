"""Integration tests for v1.3.12 PROMOTION-TASK-PREFIX.

Confirms the promotion validator routes by `task_prefix` from
`config.yaml promotion:` block (default `"vec_long_"`). Phase 3 sets
`task_prefix: "vec_p3_"` to enable validation of `vec_p3_*` tasks.

Coverage:
  1. Phase 3 config (vec_p3_) + matching closed task → validated
  2. Phase 3 config + vec_long_* task → NOT validated (wrong prefix)
  3. Missing `promotion:` block → defaults to vec_long_ (backward-compat)
  4. Mid-cycle-added vec_p3_* task → delta scan path validates it
  5. _read_config_promotion unit: missing block / explicit override
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
LIB = SRC / "lib"
for _p in (str(SRC), str(LIB)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from orchestrator import cycle  # noqa: E402
from orchestrator.prompt import (  # noqa: E402
    PROMOTION_DEFAULTS,
    _read_config_promotion,
)
import backlog as backlog_lib  # noqa: E402
import promotion  # noqa: E402


_PROMOTED_FULL = """\
**Verdict: PROMOTED**

## Long-only verification
yes
## Regime-stratified PnL
yes
## Statistical significance
yes
## Walk-forward stability
yes
## No-lookahead audit
yes
"""


def _project(tmp_path: Path) -> Path:
    p = tmp_path / "demo"
    (p / ".cc-autopipe" / "memory").mkdir(parents=True)
    (p / "data" / "debug").mkdir(parents=True)
    return p


def _write_config_promotion_block(project: Path, prefix: str | None) -> None:
    """Write a minimal config.yaml. `prefix=None` writes no promotion: block."""
    cca = project / ".cc-autopipe"
    cca.mkdir(parents=True, exist_ok=True)
    body = "schema_version: 1\nname: demo\n"
    if prefix is not None:
        body += f'\npromotion:\n  task_prefix: "{prefix}"\n'
    (cca / "config.yaml").write_text(body, encoding="utf-8")


def _write_promotion(project: Path, task_id: str, body: str) -> None:
    p = promotion.promotion_path(project, task_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def _backlog_line(task_id: str, status: str = "x") -> str:
    return f"- [{status}] [implement] [P1] {task_id} — desc\n"


def _read_aggregate(user_home: Path) -> list[dict]:
    p = user_home / "log" / "aggregate.jsonl"
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]


def _events_named(user_home: Path, name: str) -> list[dict]:
    return [e for e in _read_aggregate(user_home) if e.get("event") == name]


# ---------------------------------------------------------------------------
# Test 1: explicit vec_p3_ prefix + matching closed task → validated
# ---------------------------------------------------------------------------

def test_phase3_prefix_validates_vec_p3_task(tmp_path: Path, monkeypatch) -> None:
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    project = _project(tmp_path)
    _write_config_promotion_block(project, "vec_p3_")

    (project / "backlog.md").write_text(
        _backlog_line("vec_p3_ob_pressure_ratio", status="x") + "## Done\n",
        encoding="utf-8",
    )
    _write_promotion(project, "vec_p3_ob_pressure_ratio", _PROMOTED_FULL)

    # Confirm the config reader returns the override.
    cfg = _read_config_promotion(project)
    assert cfg["task_prefix"] == "vec_p3_"

    # Drive the delta scan with vec_p3_ prefix; pre_open is empty so the
    # task is treated as mid-cycle additions.
    cycle._post_cycle_delta_scan(project, [], task_prefix="vec_p3_")

    attempts = _events_named(user_home, "promotion_validated_attempt")
    assert len(attempts) == 1
    assert attempts[0]["task_id"] == "vec_p3_ob_pressure_ratio"
    assert attempts[0]["origin"] == "post_cycle_delta"

    validated = _events_named(user_home, "promotion_validated")
    assert len(validated) == 1
    assert validated[0]["task_id"] == "vec_p3_ob_pressure_ratio"


# ---------------------------------------------------------------------------
# Test 2: vec_p3_ prefix + vec_long_ task → wrong prefix, no validation
# ---------------------------------------------------------------------------

def test_phase3_prefix_skips_vec_long_task(tmp_path: Path, monkeypatch) -> None:
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    project = _project(tmp_path)
    _write_config_promotion_block(project, "vec_p3_")

    (project / "backlog.md").write_text(
        _backlog_line("vec_long_ob_legacy", status="x") + "## Done\n",
        encoding="utf-8",
    )
    _write_promotion(project, "vec_long_ob_legacy", _PROMOTED_FULL)

    cycle._post_cycle_delta_scan(project, [], task_prefix="vec_p3_")

    # Wrong prefix → zero events. The vec_long_ task is not in scope.
    assert _events_named(user_home, "promotion_validated_attempt") == []
    assert _events_named(user_home, "promotion_validated") == []
    assert _events_named(user_home, "promotion_rejected") == []


# ---------------------------------------------------------------------------
# Test 3: missing promotion: block → defaults to vec_long_ (backward-compat)
# ---------------------------------------------------------------------------

def test_missing_promotion_block_defaults_to_vec_long(
    tmp_path: Path, monkeypatch
) -> None:
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    project = _project(tmp_path)
    _write_config_promotion_block(project, None)  # no `promotion:` block

    (project / "backlog.md").write_text(
        _backlog_line("vec_long_ob_legacy", status="x") + "## Done\n",
        encoding="utf-8",
    )
    _write_promotion(project, "vec_long_ob_legacy", _PROMOTED_FULL)

    cfg = _read_config_promotion(project)
    assert cfg == {"task_prefix": "vec_long_"}

    # Default function arg is "vec_long_", so the legacy 2-arg call shape
    # used by existing callers stays equivalent.
    cycle._post_cycle_delta_scan(project, [])

    attempts = _events_named(user_home, "promotion_validated_attempt")
    assert len(attempts) == 1
    assert attempts[0]["task_id"] == "vec_long_ob_legacy"


# ---------------------------------------------------------------------------
# Test 4: delta scan path emits origin=post_cycle_delta for vec_p3_*
# ---------------------------------------------------------------------------

def test_delta_scan_path_for_vec_p3_mid_cycle_add(
    tmp_path: Path, monkeypatch
) -> None:
    user_home = tmp_path / "uhome"
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(user_home))
    project = _project(tmp_path)
    _write_config_promotion_block(project, "vec_p3_")

    # Pre-existing task (was open at cycle start); mid-cycle-added task.
    pre_item = backlog_lib.BacklogItem(
        status=" ",
        priority=1,
        id="vec_p3_pre",
        description="pre",
        tags=["[implement]", "[P1]"],
        raw_line="- [ ] [implement] [P1] vec_p3_pre — pre",
    )
    (project / "backlog.md").write_text(
        _backlog_line("vec_p3_pre", status="x")
        + _backlog_line("vec_p3_mid", status="x")
        + "## Done\n",
        encoding="utf-8",
    )
    _write_promotion(project, "vec_p3_pre", _PROMOTED_FULL)
    _write_promotion(project, "vec_p3_mid", _PROMOTED_FULL)

    cycle._post_cycle_delta_scan(project, [pre_item], task_prefix="vec_p3_")

    # vec_p3_pre is in pre_ids so the delta scan must NOT emit for it.
    # Only vec_p3_mid should fire promotion_validated_attempt with
    # origin=post_cycle_delta.
    attempts = _events_named(user_home, "promotion_validated_attempt")
    assert len(attempts) == 1
    assert attempts[0]["task_id"] == "vec_p3_mid"
    assert attempts[0]["origin"] == "post_cycle_delta"


# ---------------------------------------------------------------------------
# Test 5: _read_config_promotion — defaults + override
# ---------------------------------------------------------------------------

def test_read_config_promotion_defaults_and_override(tmp_path: Path) -> None:
    project = _project(tmp_path)

    # No config.yaml at all → defaults
    assert _read_config_promotion(project) == dict(PROMOTION_DEFAULTS)
    assert PROMOTION_DEFAULTS["task_prefix"] == "vec_long_"

    # Config without promotion: → defaults
    _write_config_promotion_block(project, None)
    assert _read_config_promotion(project)["task_prefix"] == "vec_long_"

    # Explicit override → "vec_p3_"
    _write_config_promotion_block(project, "vec_p3_")
    assert _read_config_promotion(project)["task_prefix"] == "vec_p3_"
