"""v1.5.8 MAY-13-RECOVERY-SCRIPT integration test.

`recovery_revert_fake_closures.revert_fake_closures` reverts [x] rows
whose PROMOTION file is either missing or older than the operator-
supplied --since timestamp. Used once on AI-trade to clean up the
~351 closures the pre-v1.5.8 gate missed.

Single scenario covers the three input shapes the script must handle:
  (a) [x] + fresh PROMOTION post-since      → kept
  (b) [x] + stale PROMOTION pre-since       → reverted
  (c) [x] + no PROMOTION file               → reverted
"""

from __future__ import annotations

import importlib
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
LIB = SRC / "lib"
for p in (str(SRC), str(LIB)):
    if p not in sys.path:
        sys.path.insert(0, p)

rfc = importlib.import_module("recovery_revert_fake_closures")


def _project(tmp_path: Path) -> Path:
    p = tmp_path / "demo"
    (p / ".cc-autopipe").mkdir(parents=True)
    (p / "data" / "debug").mkdir(parents=True)
    return p


def _set_mtime(path: Path, mtime: float) -> None:
    os.utime(path, (mtime, mtime))


def _build_fixture(tmp_path: Path, since_ts: float) -> Path:
    project = _project(tmp_path)
    body = (
        "- [x] [implement] [P0] vec_kept_fresh    — fresh promo\n"
        "- [x] [implement] [P0] vec_stale_promo   — stale promo\n"
        "- [x] [implement] [P0] vec_no_promo      — no promo at all\n"
        "- [ ] [implement] [P0] vec_open_row      — already open\n"
    )
    (project / "backlog.md").write_text(body, encoding="utf-8")

    debug = project / "data" / "debug"
    fresh = debug / "CAND_vec_kept_fresh_PROMOTION.md"
    fresh.write_text(
        "**Task:** vec_kept_fresh\n**Verdict:** PROMOTED\n",
        encoding="utf-8",
    )
    _set_mtime(fresh, since_ts + 600)  # 10 min after since

    stale = debug / "CAND_vec_stale_promo_PROMOTION.md"
    stale.write_text(
        "**Task:** vec_stale_promo\n**Verdict:** PROMOTED\n",
        encoding="utf-8",
    )
    _set_mtime(stale, since_ts - 7 * 86400)  # 7 days before since
    return project


def test_revert_fake_closures_dry_run_then_apply(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "uhome"
    (home / "log").mkdir(parents=True)
    monkeypatch.setenv("CC_AUTOPIPE_USER_HOME", str(home))

    since_ts = time.time() - 3600  # one hour ago
    since_iso = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(since_ts)
    )
    project = _build_fixture(tmp_path, since_ts)

    # Dry-run: identifies the two reverters, leaves backlog untouched.
    dry = rfc.revert_fake_closures(project, since_iso, apply=False)
    assert dry["applied"] is False
    assert dry["reverted"] == 0
    candidates = list(dry["candidates"])  # type: ignore[arg-type]
    assert set(candidates) == {"vec_stale_promo", "vec_no_promo"}
    # Backlog still has all three closures.
    body = (project / "backlog.md").read_text()
    assert body.count("- [x]") == 3

    # --apply: rewrites backlog and emits the summary event.
    applied = rfc.revert_fake_closures(project, since_iso, apply=True)
    assert applied["applied"] is True
    assert applied["reverted"] == 2
    body = (project / "backlog.md").read_text()
    # Fresh one stays closed.
    assert "- [x] [implement] [P0] vec_kept_fresh" in body
    # Stale and no-promo flipped back to open.
    assert "- [ ] [implement] [P0] vec_stale_promo" in body
    assert "- [ ] [implement] [P0] vec_no_promo" in body
    assert "- [x] [implement] [P0] vec_stale_promo" not in body
    assert "- [x] [implement] [P0] vec_no_promo" not in body
    # Already-open row untouched.
    assert "- [ ] [implement] [P0] vec_open_row" in body

    # Summary event landed in aggregate.jsonl.
    log = home / "log" / "aggregate.jsonl"
    assert log.exists()
    lines = [l for l in log.read_text().splitlines() if l.strip()]
    assert any(
        '"revert_fake_closures_applied"' in l and '"reverted":2' in l
        for l in lines
    )
