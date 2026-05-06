"""Unit tests for v1.3.4 cycle.py additions (Group R3 + R4).

R3: _network_gate_ok probes api.anthropic.com before each cycle, sleeps
    with exponential backoff on failure, recovers when probe returns
    True. Returns False (deferred cycle) only after exhausting backoff.

R4: transient classification routes rc != 0 + transient stderr to a
    retry path that does NOT increment consecutive_failures. After
    MAX_TRANSIENT_RETRIES the path falls through to the structural
    failure handler.

These tests stub the probe + sleep at the module level so they run
in milliseconds.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "src" / "lib"))

from orchestrator import cycle  # noqa: E402
import state  # noqa: E402


@pytest.fixture
def fresh_project(tmp_path: Path) -> Path:
    project = tmp_path / "proj"
    (project / ".cc-autopipe" / "memory").mkdir(parents=True)
    s = state.State.fresh("proj")
    state.write(project, s)
    return project


@pytest.fixture(autouse=True)
def _restore_env() -> None:
    """Each test starts with the gate-disable env var cleared."""
    saved = os.environ.pop("CC_AUTOPIPE_NETWORK_PROBE_DISABLED", None)
    yield
    if saved is not None:
        os.environ["CC_AUTOPIPE_NETWORK_PROBE_DISABLED"] = saved
    else:
        os.environ.pop("CC_AUTOPIPE_NETWORK_PROBE_DISABLED", None)


# ---------------------------------------------------------------------------
# _network_gate_ok
# ---------------------------------------------------------------------------


def test_network_gate_ok_short_circuits_when_env_disabled(
    fresh_project: Path,
) -> None:
    os.environ["CC_AUTOPIPE_NETWORK_PROBE_DISABLED"] = "1"
    s = state.read(fresh_project)
    with mock.patch.object(cycle.transient_lib, "is_anthropic_reachable") as probe:
        assert cycle._network_gate_ok(fresh_project, s) is True
        probe.assert_not_called()


def test_network_gate_ok_passes_when_probe_succeeds(fresh_project: Path) -> None:
    s = state.read(fresh_project)
    with mock.patch.object(
        cycle.transient_lib, "is_anthropic_reachable", return_value=True
    ):
        assert cycle._network_gate_ok(fresh_project, s) is True


def test_network_gate_ok_recovers_after_first_backoff(
    fresh_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Probe fails once, then succeeds during the first backoff slot.
    The gate logs network_probe_failed + network_probe_recovered."""
    monkeypatch.setenv("CC_AUTOPIPE_NETWORK_PROBE_BACKOFF_OVERRIDE", "0,0,0")
    s = state.read(fresh_project)

    calls = {"n": 0}

    def fake_probe() -> bool:
        calls["n"] += 1
        return calls["n"] >= 2  # second call succeeds

    with (
        mock.patch.object(
            cycle.transient_lib, "is_anthropic_reachable", side_effect=fake_probe
        ),
        mock.patch.object(
            cycle.transient_lib, "is_internet_reachable", return_value=False
        ),
        mock.patch.object(cycle, "_interruptible_sleep"),
    ):
        assert cycle._network_gate_ok(fresh_project, s) is True

    log = (fresh_project / ".cc-autopipe" / "memory" / "progress.jsonl").read_text()
    assert "network_probe_failed" in log
    assert "network_probe_recovered" in log


def test_network_gate_ok_gives_up_after_exhausting_backoff(
    fresh_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Probe never recovers — gate returns False, logs giving_up."""
    monkeypatch.setenv("CC_AUTOPIPE_NETWORK_PROBE_BACKOFF_OVERRIDE", "0,0")
    s = state.read(fresh_project)

    with (
        mock.patch.object(
            cycle.transient_lib, "is_anthropic_reachable", return_value=False
        ),
        mock.patch.object(
            cycle.transient_lib, "is_internet_reachable", return_value=False
        ),
        mock.patch.object(cycle, "_interruptible_sleep"),
    ):
        assert cycle._network_gate_ok(fresh_project, s) is False

    log = (fresh_project / ".cc-autopipe" / "memory" / "progress.jsonl").read_text()
    assert "network_probe_failed" in log
    assert "network_probe_giving_up" in log


# ---------------------------------------------------------------------------
# _backoff_override
# ---------------------------------------------------------------------------


def test_backoff_override_returns_default_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CC_AUTOPIPE_TRANSIENT_BACKOFF_OVERRIDE", raising=False)
    out = cycle._backoff_override(
        "CC_AUTOPIPE_TRANSIENT_BACKOFF_OVERRIDE", (30, 60, 120)
    )
    assert out == (30, 60, 120)


def test_backoff_override_parses_csv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_TRANSIENT_BACKOFF_OVERRIDE", "1,2,3")
    out = cycle._backoff_override(
        "CC_AUTOPIPE_TRANSIENT_BACKOFF_OVERRIDE", (30, 60, 120)
    )
    assert out == (1, 2, 3)


def test_backoff_override_falls_back_on_garbage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CC_AUTOPIPE_TRANSIENT_BACKOFF_OVERRIDE", "abc")
    out = cycle._backoff_override(
        "CC_AUTOPIPE_TRANSIENT_BACKOFF_OVERRIDE", (30, 60, 120)
    )
    assert out == (30, 60, 120)
