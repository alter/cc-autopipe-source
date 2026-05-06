"""Unit tests for v1.3.4 R5 quota.fetch_quota retry loop.

Three terminal outcomes:
  - Success on first try → returns the parsed dict, no retry sleep.
  - Transient failure on first attempt(s) then success → retries kick
    in, returns the eventual response.
  - 4xx HTTPError → no retry, returns None immediately.
"""

from __future__ import annotations

import io
import json
import sys
import urllib.error
from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src" / "lib"))

import quota  # noqa: E402


def _good_response_body() -> bytes:
    return json.dumps(
        {
            "five_hour": {"utilization": 38, "resets_at": "2026-05-06T15:00:00Z"},
            "seven_day": {"utilization": 86, "resets_at": "2026-05-13T15:00:00Z"},
        }
    ).encode("utf-8")


@pytest.fixture(autouse=True)
def _stub_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(quota, "read_oauth_token", lambda: "test-token")
    # Skip the real time.sleep in retry path so tests run instantly.
    monkeypatch.setattr(quota.time, "sleep", lambda _s: None)


class _FakeResp:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def read(self) -> bytes:
        return self._body


def test_fetch_quota_returns_dict_on_first_success() -> None:
    body = _good_response_body()
    with mock.patch.object(
        quota.urllib.request, "urlopen", return_value=_FakeResp(body)
    ) as op:
        out = quota.fetch_quota()
    assert isinstance(out, dict)
    assert out["five_hour"]["utilization"] == 38
    assert op.call_count == 1


def test_fetch_quota_retries_until_success() -> None:
    """Two URLError failures, then a good response on attempt 3."""
    body = _good_response_body()
    sequence: list[object] = [
        urllib.error.URLError("dns hiccup"),
        OSError("connection reset"),
        _FakeResp(body),
    ]

    def side_effect(*_a: object, **_k: object) -> _FakeResp:
        nxt = sequence.pop(0)
        if isinstance(nxt, BaseException):
            raise nxt
        return nxt

    with mock.patch.object(
        quota.urllib.request, "urlopen", side_effect=side_effect
    ) as op:
        out = quota.fetch_quota()
    assert isinstance(out, dict)
    assert out["seven_day"]["utilization"] == 86
    assert op.call_count == 3


def test_fetch_quota_gives_up_after_max_attempts() -> None:
    """All attempts fail with URLError → returns None."""
    with mock.patch.object(
        quota.urllib.request,
        "urlopen",
        side_effect=urllib.error.URLError("permanent dns failure"),
    ) as op:
        out = quota.fetch_quota()
    assert out is None
    assert op.call_count == len(quota.QUOTA_RETRY_BACKOFF_SEC)


def test_fetch_quota_does_not_retry_on_4xx() -> None:
    """HTTP 401 / 403 → invalid token, immediate None, no retry."""
    err = urllib.error.HTTPError(
        url="https://api.anthropic.com/api/oauth/usage",
        code=401,
        msg="Unauthorized",
        hdrs=None,  # type: ignore[arg-type]
        fp=io.BytesIO(b""),
    )
    with mock.patch.object(quota.urllib.request, "urlopen", side_effect=err) as op:
        out = quota.fetch_quota()
    assert out is None
    assert op.call_count == 1


def test_fetch_quota_returns_none_when_token_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(quota, "read_oauth_token", lambda: None)
    # urlopen should never be called — guard with a sentinel.
    with mock.patch.object(
        quota.urllib.request, "urlopen", side_effect=AssertionError("never called")
    ):
        assert quota.fetch_quota() is None
