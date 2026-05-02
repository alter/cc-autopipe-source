"""Test-suite-wide isolation guards.

Two failure modes this conftest prevents:

1. Real Telegram from test runs.
   tg.sh resolves a secrets file at exec time and reads TG_BOT_TOKEN /
   TG_CHAT_ID from it. Many integration tests subprocess-spawn the
   orchestrator, which calls _notify_tg on quota pre-flight pause
   (>=95% 7d), phase transitions, detached timeouts, and quota_monitor
   warnings. Without an isolated SECRETS_FILE, any of those code paths
   silently send real Telegram messages.

2. Cross-test contamination via $HOME/.cc-autopipe.
   The orchestrator and quota.py default user_home to $HOME/.cc-autopipe
   when CC_AUTOPIPE_USER_HOME is unset. A test that forgets to set it
   would read the real quota-cache.json (potentially stale high values
   from earlier runs) and write to the real projects.list.

Defenses (autouse session fixture below):
- CC_AUTOPIPE_SECRETS_FILE -> tmp path that never exists. tg.sh treats
  unreadable secrets as "no creds" and exits 0 without curling.
- CC_AUTOPIPE_USER_HOME -> tmp dir. Tests that need a specific user_home
  override via subprocess env or monkeypatch.setenv (both win over
  os.environ). The session-level default is the safety net for any test
  that omits the override.

Discovered: 2026-05-02 (Q20). After Stage K + Stage N integration tests
caused real-bot TG spam claiming 95% quota during pytest runs.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def _isolate_user_state(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    base = tmp_path_factory.mktemp("cc-autopipe-isolation")
    secrets = base / "secrets.env"  # intentionally not created
    user_home = base / "user-home"
    user_home.mkdir()

    prior_secrets = os.environ.get("CC_AUTOPIPE_SECRETS_FILE")
    prior_user_home = os.environ.get("CC_AUTOPIPE_USER_HOME")

    os.environ["CC_AUTOPIPE_SECRETS_FILE"] = str(secrets)
    if prior_user_home is None:
        os.environ["CC_AUTOPIPE_USER_HOME"] = str(user_home)

    try:
        yield
    finally:
        if prior_secrets is None:
            os.environ.pop("CC_AUTOPIPE_SECRETS_FILE", None)
        else:
            os.environ["CC_AUTOPIPE_SECRETS_FILE"] = prior_secrets
        if prior_user_home is None:
            os.environ.pop("CC_AUTOPIPE_USER_HOME", None)
