"""Unit tests for src/lib/transient.py (v1.3.4 Group R1).

Three classifier outcomes:
  - transient — engine retries with exponential backoff
  - structural — immediate failure, existing v1.3.3 path
  - unknown — preserve v1.3.3 behaviour (default to structural)
"""

from __future__ import annotations

import socket
import sys
from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src" / "lib"))

import transient  # noqa: E402


# ---------------------------------------------------------------------------
# classify_failure: transient patterns
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stderr",
    [
        "Server is temporarily limiting requests",
        "Error: Server is temporarily limiting requests, retry later",
        "rate-limited by upstream",
        "Got rate limited",
        "rate limit exceeded for token bucket",
        "HTTP 429: Too Many Requests",
        "503 Service Unavailable",
        "upstream returned 502 Bad Gateway",
        "504 Gateway Timeout from edge",
        "Connection refused by api.anthropic.com",
        "Connection reset by peer",
        "Connection timed out after 10s",
        "Network is unreachable",
        "Temporary failure in name resolution",
        "Cannot resolve host: api.anthropic.com",
        "curl: getaddrinfo failed",
        "SSL handshake timed out",
        "upstream connect error or disconnect",
        "socket hang up",
    ],
)
def test_transient_patterns_classified_as_transient(stderr: str) -> None:
    assert transient.classify_failure(1, stderr) == "transient"


# ---------------------------------------------------------------------------
# classify_failure: structural patterns
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stderr",
    [
        "401 Unauthorized",
        "API returned 403 Forbidden",
        "Invalid API key supplied",
        "authentication failed: token expired",
        "claude: command not found",
        "ENOENT: No such file or directory",
        "Permission denied while reading credentials",
        "Error: When using --print, --output-format=stream-json requires --verbose",
    ],
)
def test_structural_patterns_classified_as_structural(stderr: str) -> None:
    assert transient.classify_failure(1, stderr) == "structural"


# ---------------------------------------------------------------------------
# classify_failure: rc tiebreakers + edge cases
# ---------------------------------------------------------------------------


def test_empty_stderr_with_likely_transient_rc_is_transient() -> None:
    assert transient.classify_failure(124, "") == "transient"
    assert transient.classify_failure(7, "") == "transient"
    assert transient.classify_failure(6, None) == "transient"


def test_empty_stderr_with_unknown_rc_is_unknown() -> None:
    assert transient.classify_failure(1, "") == "unknown"
    assert transient.classify_failure(255, None) == "unknown"


def test_long_generic_stderr_with_likely_transient_rc_is_unknown() -> None:
    # Long stderr (>=100 chars) without a transient pattern → don't claim transient.
    long_text = "stack trace: " + "x" * 200
    assert transient.classify_failure(124, long_text) == "unknown"


def test_structural_wins_over_transient_when_both_present() -> None:
    mixed = "Connection refused. Also: 401 Unauthorized"
    assert transient.classify_failure(1, mixed) == "structural"


def test_transient_pattern_with_unknown_rc_still_transient() -> None:
    assert transient.classify_failure(1, "rate limit exceeded") == "transient"


# ---------------------------------------------------------------------------
# is_anthropic_reachable
# ---------------------------------------------------------------------------


def test_is_anthropic_reachable_true_when_socket_opens() -> None:
    fake_sock = mock.MagicMock()
    fake_sock.__enter__.return_value = fake_sock
    fake_sock.__exit__.return_value = False
    with mock.patch("socket.create_connection", return_value=fake_sock) as cc:
        assert transient.is_anthropic_reachable() is True
    cc.assert_called_once_with(("api.anthropic.com", 443), timeout=5.0)


def test_is_anthropic_reachable_false_on_dns_failure() -> None:
    with mock.patch("socket.create_connection", side_effect=socket.gaierror):
        assert transient.is_anthropic_reachable() is False


def test_is_anthropic_reachable_false_on_timeout() -> None:
    with mock.patch("socket.create_connection", side_effect=socket.timeout):
        assert transient.is_anthropic_reachable() is False


def test_is_anthropic_reachable_false_on_connection_refused() -> None:
    with mock.patch("socket.create_connection", side_effect=ConnectionRefusedError):
        assert transient.is_anthropic_reachable() is False


def test_is_anthropic_reachable_false_on_oserror() -> None:
    with mock.patch("socket.create_connection", side_effect=OSError("boom")):
        assert transient.is_anthropic_reachable() is False


# ---------------------------------------------------------------------------
# is_internet_reachable
# ---------------------------------------------------------------------------


def test_is_internet_reachable_true_when_socket_opens() -> None:
    fake_sock = mock.MagicMock()
    fake_sock.__enter__.return_value = fake_sock
    fake_sock.__exit__.return_value = False
    with mock.patch("socket.create_connection", return_value=fake_sock) as cc:
        assert transient.is_internet_reachable() is True
    cc.assert_called_once_with(("1.1.1.1", 53), timeout=3.0)


def test_is_internet_reachable_false_on_failure() -> None:
    with mock.patch("socket.create_connection", side_effect=OSError):
        assert transient.is_internet_reachable() is False
