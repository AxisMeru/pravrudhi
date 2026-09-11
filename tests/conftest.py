"""Shared fixtures for the engine's tests.

The tests must say the same thing in a shell and under the heartbeat unit, because a build dispatch validates its
worktree with this suite under the unit's environment. That environment carries the operator's live
configuration: `~/.config/pravrudhi/telegram.env` (a real bot token and chat id) and the pinned Claude seat. On
2026-09-11 thirteen seat-registry tests and then two telegram tests failed only there, and the loop's first
self-built change was rejected four times for it. The fixture below removes that configuration for every test;
a test that wants it sets it itself.
"""

from __future__ import annotations

import pytest

OPERATOR_ENV = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "PRAVRUDHI_CLAUDE_CONFIG_DIR")


@pytest.fixture(autouse=True)
def _without_the_operators_live_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in OPERATOR_ENV:
        monkeypatch.delenv(name, raising=False)
