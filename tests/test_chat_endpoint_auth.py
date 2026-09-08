"""The chat model may live behind a key, not only on localhost.

`chat_endpoint` has always been configurable, but `default_complete` built a client with no credential, so the
only endpoint it could actually reach was an unauthenticated local one. That made the Telegram bot — the
interface the operator uses when away from the machine — depend on a GPU server running at home, which is
exactly when it is not. The Lite Plan seat is an OpenAI-compatible endpoint the operator already pays for.
"""

from __future__ import annotations

import pytest

from pravrudhi.application import chat


def test_the_client_is_given_the_configured_key_and_model(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    class FakeClient:
        def __init__(self, endpoint: str, model: str = "local", api_key: str | None = None) -> None:
            seen.update(endpoint=endpoint, model=model, api_key=api_key)

        def chat(self, *a, **k):  # type: ignore[no-untyped-def]
            return type("R", (), {"text": "{}"})()

    monkeypatch.setattr("pravrudhi.models.openai_compat.ChatClient", FakeClient)
    monkeypatch.setenv("PRAVRUDHI_CHAT_ENDPOINT", "https://example.invalid/v1")
    monkeypatch.setenv("PRAVRUDHI_CHAT_API_KEY", "k-not-a-real-key")
    monkeypatch.setenv("PRAVRUDHI_CHAT_MODEL", "qwen3.8-max")

    chat.default_complete()

    assert seen["endpoint"] == "https://example.invalid/v1"
    assert seen["api_key"] == "k-not-a-real-key"
    assert seen["model"] == "qwen3.8-max"


def test_no_key_configured_stays_unauthenticated(monkeypatch: pytest.MonkeyPatch) -> None:
    """A local llama-server takes no credential and must not be sent one."""
    seen: dict[str, object] = {}

    class FakeClient:
        def __init__(self, endpoint: str, model: str = "local", api_key: str | None = None) -> None:
            seen.update(endpoint=endpoint, model=model, api_key=api_key)

        def chat(self, *a, **k):  # type: ignore[no-untyped-def]
            return type("R", (), {"text": "{}"})()

    monkeypatch.setattr("pravrudhi.models.openai_compat.ChatClient", FakeClient)
    monkeypatch.delenv("PRAVRUDHI_CHAT_API_KEY", raising=False)
    monkeypatch.delenv("PRAVRUDHI_CHAT_MODEL", raising=False)
    chat.default_complete()
    assert seen["api_key"] is None
    assert seen["model"] == "local"
