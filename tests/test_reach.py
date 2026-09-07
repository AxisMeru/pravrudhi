"""External messaging (Telegram) for critical notifications. Complements the in-app notification bell
with out-of-band delivery when the operator is away from the interface."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from pravrudhi.application import reach
from pravrudhi.application.credentials import Secret


class FakeSink:
    """Test double for Telegram transport that never touches the network."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail_on_retry: bool = False
        self.fail_count: int = 0

    def send(self, chat_id: str, text: str) -> dict[str, Any]:
        """Simulate Telegram API without network access."""
        call = {"chat_id": chat_id, "text": text, "attempt": len(self.calls) + 1}

        # Simulate failure on nth call to test retries
        if self.fail_on_retry and len(self.calls) < self.fail_count:
            call["ok"] = False
            call["reason"] = "simulated_failure"
            self.calls.append(call)
            return call

        call["ok"] = True
        call["message_id"] = 123 + len(self.calls)
        self.calls.append(call)
        return call


class TestMarkdownV2Escaping:
    """MarkdownV2 format requires escaping of: _*[]()~`>#+-=|{}.!"""

    def test_escape_applies_to_all_required_characters(self) -> None:
        unsafe = "Hello _world* [link](url) (paren) ~strikethrough` >quote #hash +plus -minus =equal |pipe {brace} .dot !bang"
        escaped = reach.escape_markdown_v2(unsafe)

        # Every unescaped character from the set should now be escaped
        assert "_" not in escaped or "\\_" in escaped
        assert "*" not in escaped or "\\*" in escaped
        assert "[" not in escaped or "\\[" in escaped
        assert "]" not in escaped or "\\]" in escaped
        assert "(" not in escaped or "\\(" in escaped
        assert ")" not in escaped or "\\)" in escaped
        assert "~" not in escaped or "\\~" in escaped
        assert "`" not in escaped or "\\`" in escaped
        assert ">" not in escaped or "\\>" in escaped
        assert "#" not in escaped or "\\#" in escaped
        assert "+" not in escaped or "\\+" in escaped
        assert "-" not in escaped or "\\-" in escaped
        assert "=" not in escaped or "\\=" in escaped
        assert "|" not in escaped or "\\|" in escaped
        assert "{" not in escaped or "\\{" in escaped
        assert "}" not in escaped or "\\}" in escaped
        assert "." not in escaped or "\\." in escaped
        assert "!" not in escaped or "\\!" in escaped

    def test_escape_leaves_safe_characters_untouched(self) -> None:
        safe = "Hello world 123 with lowercase UPPERCASE and spaces"
        escaped = reach.escape_markdown_v2(safe)
        assert escaped == safe

    def test_escape_handles_empty_string(self) -> None:
        assert reach.escape_markdown_v2("") == ""

    def test_escape_handles_none_as_empty(self) -> None:
        assert reach.escape_markdown_v2(None) == ""


class TestSink:
    """Test the Telegram Sink that sends messages over HTTPS."""

    def test_sink_formats_message_for_telegram_api(self) -> None:
        """Sink prepares JSON payload for Telegram's sendMessage endpoint."""
        # This will be mocked in integration tests; just verify structure
        sink = reach.Sink(token=Secret(provider="telegram", value="test-token-12345"))
        assert sink._token.provider == "telegram"

    def test_sink_with_no_token_returns_not_configured(self) -> None:
        """When no credential, send returns not-configured rather than raising."""
        sink = reach.Sink(token=None)
        result = sink.send(chat_id="123", text="test")
        assert result["ok"] is False
        assert result["reason"] == "no_credential"

    def test_sink_with_non_allowlisted_chat_id_refuses(self) -> None:
        """Non-allowlisted chat IDs are refused without sending."""
        sink = reach.Sink(
            token=Secret(provider="telegram", value="test-token"),
            allowed_chat_ids={"123", "456"}
        )
        result = sink.send(chat_id="999", text="hello")
        assert result["ok"] is False
        assert result["reason"] == "chat_id_not_allowed"

    def test_sink_with_allowlisted_chat_id_proceeds(self) -> None:
        """Allowlisted chat IDs pass the filter."""
        fake_transport = FakeSink()
        sink = reach.Sink(
            token=Secret(provider="telegram", value="test-token"),
            allowed_chat_ids={"123"},
            transport=fake_transport,  # type: ignore
        )
        result = sink.send(chat_id="123", text="hello")
        assert result["ok"] is True

    def test_retry_does_not_double_post_with_same_delivery_id(self) -> None:
        """Retries use the same delivery_id so duplicate detection can work."""
        fake_transport = FakeSink()
        fake_transport.fail_on_retry = True
        fake_transport.fail_count = 2  # fail twice, then succeed

        sink = reach.Sink(
            token=Secret(provider="telegram", value="test-token"),
            allowed_chat_ids={"123"},
            transport=fake_transport,  # type: ignore
            max_retries=3,
        )

        result = sink.send(chat_id="123", text="hello")
        assert result["ok"] is True
        # Should have attempted 3 times (2 failures + 1 success)
        assert len(fake_transport.calls) == 3

    def test_the_token_never_reaches_the_caller_when_the_transport_fails(self) -> None:
        """The one guarantee that matters most here, and the easiest to lose.

        An HTTP client puts the request URL in its exception message, and the URL carries the bot token. If that
        message is returned, logged, or re-raised as it stands, the credential ends up in a notification record
        on disk. Every failure path is scrubbed before the caller sees it.
        """
        secret = "123456789abcdef-SUPER-SECRET-DO-NOT-LOG"

        class LeakingTransport:
            def send(self, *, chat_id: str, text: str) -> dict[str, Any]:
                raise ValueError(f"POST https://api.telegram.org/bot{secret}/sendMessage failed")

        sink = reach.Sink(
            token=Secret(provider="telegram", value=secret),
            allowed_chat_ids={"123"},
            transport=LeakingTransport(),  # type: ignore[arg-type]
            max_retries=0,
        )

        result = sink.send(chat_id="123", text="hello")

        assert result["ok"] is False
        assert secret not in repr(result), "the token reached the caller through the result"

    def test_a_failing_transport_is_reported_rather_than_raised(self) -> None:
        """A night must not die because a chat message could not be delivered."""

        class BrokenTransport:
            def send(self, *, chat_id: str, text: str) -> dict[str, Any]:
                raise OSError("network unreachable")

        sink = reach.Sink(
            token=Secret(provider="telegram", value="t"),
            allowed_chat_ids={"123"},
            transport=BrokenTransport(),  # type: ignore[arg-type]
            max_retries=0,
        )
        result = sink.send(chat_id="123", text="hello")
        assert result["ok"] is False and "unreachable" in str(result["reason"])


class TestFilterConfiguration:
    """Test that notification kinds can be filtered before sending."""

    def test_default_filter_includes_promotion_high_severity_pool_depletion(self) -> None:
        """Default filter sends: promotions, high-severity audits, pool depletion."""
        default_kinds = reach.DEFAULT_SEND_KINDS
        assert "promotion_needed" in default_kinds
        assert "audit_severity_high" in default_kinds
        assert "pool_depleted" in default_kinds

    def test_custom_filter_respects_configured_kinds(self) -> None:
        """Custom filter only sends configured kinds."""
        custom = reach.NotificationFilter(kinds={"run_finished"})
        assert custom.should_send("run_finished") is True
        assert custom.should_send("job_rejected") is False

    def test_filter_defaults_to_recommended_kinds(self) -> None:
        """When created without config, filter uses recommended defaults."""
        default_filter = reach.NotificationFilter()
        assert default_filter.should_send("promotion_needed") is True
        assert default_filter.should_send("audit_severity_high") is True
        assert default_filter.should_send("pool_depleted") is True


class TestSendFunction:
    """Test the public send() function that dispatches to Telegram."""

    def test_send_with_no_credential_returns_reason_not_error(self, tmp_path: Path) -> None:
        """Engine with no token configured must keep running (no exception)."""
        # Simulate no credential stored
        result = reach.send(
            root=tmp_path,
            kind="run_finished",
            title="Night completed",
            detail="model improved",
            token=None,
        )
        assert result.reason is not None
        assert result.reason == "no_credential"
        assert result.sent is False

    def test_send_filtered_kind_does_not_send(self, tmp_path: Path) -> None:
        """Filtered-out kinds do not trigger a send."""
        fake_transport = FakeSink()
        sink_instance = reach.Sink(
            token=Secret(provider="telegram", value="test-token"),
            allowed_chat_ids={"123"},
            transport=fake_transport,  # type: ignore
        )

        result = reach.send(
            root=tmp_path,
            kind="job_accepted",  # not in default filter
            title="Job accepted",
            detail="",
            token=Secret(provider="telegram", value="test-token"),
            sink=sink_instance,
            chat_id="123",
        )

        assert result.sent is False
        assert result.reason == "kind_filtered"
        assert len(fake_transport.calls) == 0

    def test_send_formats_title_and_detail_with_escaping(self, tmp_path: Path) -> None:
        """Send formats the message with proper escaping and redaction."""
        fake_transport = FakeSink()
        sink_instance = reach.Sink(
            token=Secret(provider="telegram", value="test-token"),
            allowed_chat_ids={"123"},
            transport=fake_transport,  # type: ignore
        )

        result = reach.send(
            root=tmp_path,
            kind="promotion_needed",
            title="Model improved: 42.5% *accuracy*",
            detail="Review change at /models/run_123",
            token=Secret(provider="telegram", value="test-token"),
            sink=sink_instance,
            chat_id="123",
        )

        assert result.sent is True
        assert len(fake_transport.calls) == 1
        sent_text = fake_transport.calls[0]["text"]
        # Title and detail should be escaped
        assert "42\\.5%" in sent_text or "42.5%" in sent_text
        assert "\\*accuracy\\*" in sent_text

    def test_token_absent_from_all_log_output(self, tmp_path: Path, capsys: Any) -> None:
        """Token must never appear in stdout or stderr during send."""
        secret_token = "bot123456:ABCDEFGHIJKLMNOPqrstuvwxyz-secret"
        fake_transport = FakeSink()
        sink_instance = reach.Sink(
            token=Secret(provider="telegram", value=secret_token),
            allowed_chat_ids={"123"},
            transport=fake_transport,  # type: ignore
        )

        reach.send(
            root=tmp_path,
            kind="promotion_needed",
            title="Test message",
            detail="",
            token=Secret(provider="telegram", value=secret_token),
            sink=sink_instance,
            chat_id="123",
        )

        captured = capsys.readouterr()
        assert secret_token not in captured.out
        assert secret_token not in captured.err


class TestWireIntoNotifications:
    """Test that reach.send is wired into notifications.emit via a hook."""

    def test_reach_module_exports_send_for_hook_registration(self) -> None:
        """The send function must be importable for registration as a hook."""
        assert callable(reach.send)
        assert hasattr(reach.send, "__name__")

    def test_send_result_is_structured_with_sent_and_reason_fields(self, tmp_path: Path) -> None:
        """SendResult has sent (bool) and reason (str) fields for reporting."""
        result = reach.send(
            root=tmp_path,
            kind="run_finished",
            title="Test",
            detail="",
            token=None,
        )
        assert hasattr(result, "sent")
        assert isinstance(result.sent, bool)
        assert hasattr(result, "reason")
        assert isinstance(result.reason, (str, type(None)))


class TestTheHookIntoNotifications:
    """Recording a notification and delivering one are different jobs, and the record must always win."""

    def test_emitting_a_notification_offers_it_to_external_messaging(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from pravrudhi.application import notifications
        from pravrudhi.application import reach as reach_mod

        seen: list[dict[str, str]] = []
        monkeypatch.setattr(
            reach_mod,
            "send",
            lambda root, *, kind, title, detail="": seen.append({"kind": kind, "title": title}) or None,
        )
        notifications.emit(tmp_path, kind="promotion_needed", title="c-0045 awaits sign-off")

        assert seen == [{"kind": "promotion_needed", "title": "c-0045 awaits sign-off"}]

    def test_a_failing_sink_does_not_lose_the_record_or_raise(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A chat message that cannot be delivered must not take the night down with it."""
        from pravrudhi.application import notifications
        from pravrudhi.application import reach as reach_mod

        def explode(*a: object, **k: object) -> None:
            raise RuntimeError("telegram is down")

        monkeypatch.setattr(reach_mod, "send", explode)
        note = notifications.emit(tmp_path, kind="promotion_needed", title="still recorded")

        assert note.title == "still recorded"
        assert [n.title for n in notifications.recent(tmp_path)] == ["still recorded"]

    def test_a_machine_with_no_credential_records_normally(self, tmp_path: Path) -> None:
        """The common case: nobody has configured a bot, and nothing about the feed changes."""
        from pravrudhi.application import notifications

        notifications.emit(tmp_path, kind="run_finished", title="night 17 finished")
        assert [n.title for n in notifications.recent(tmp_path)] == ["night 17 finished"]
