"""Talking back to the engine from a phone.

Outbound already worked: the loop can say what happened. This is the other direction, and it is the one that
needs care, because a message arriving from the network is untrusted input that asks the engine to do things.

Two rules make that safe, and both are asserted here rather than described. Only the configured chat is obeyed,
so a stranger who finds the bot is talking to nobody. And a command is a name from a fixed set, never a string
handed to a shell — the engine answers questions it already knows how to answer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from pravrudhi.application.telegram_inbox import (
    COMMANDS,
    parse_command,
    poll_once,
    reply_for,
)


class TestReadingAMessage:
    def test_a_command_is_recognised_with_and_without_arguments(self) -> None:
        assert parse_command("/status") == ("status", "")
        assert parse_command("  /requests  ") == ("requests", "")
        assert parse_command("/ask what is the loop doing") == ("ask", "what is the loop doing")

    def test_the_bots_own_name_is_stripped(self) -> None:
        """Telegram appends @thebot in group chats."""
        assert parse_command("/status@pravrudhi_bot") == ("status", "")

    def test_plain_prose_is_treated_as_a_question(self) -> None:
        # A person types a sentence, not a slash command. Refusing it would make the bot useless.
        assert parse_command("what is the loop doing?") == ("ask", "what is the loop doing?")

    def test_nothing_at_all_is_not_a_command(self) -> None:
        assert parse_command("") is None
        assert parse_command("   ") is None

    def test_an_unknown_command_is_not_invented(self) -> None:
        command, _ = parse_command("/deploy everything") or ("", "")
        assert command not in COMMANDS or command == "ask"


class TestWhoIsObeyed:
    @staticmethod
    def _update(chat_id: str, text: str, update_id: int = 1) -> dict[str, Any]:
        return {"update_id": update_id, "message": {"chat": {"id": int(chat_id)}, "text": text}}

    def test_a_message_from_another_chat_is_ignored(self, tmp_path: Path) -> None:
        """The bot's own token is enough for anyone who finds it to message it. Only the operator is answered."""
        sent: list[str] = []
        poll_once(
            tmp_path, chat_id="8679892510",
            fetch=lambda *_a, **_k: {"result": [self._update("99999", "/status")]},
            send=lambda text: sent.append(text),
        )
        assert sent == [], "the engine answered a chat it was not configured for"

    def test_a_message_from_the_configured_chat_is_answered(self, tmp_path: Path) -> None:
        sent: list[str] = []
        poll_once(
            tmp_path, chat_id="8679892510",
            fetch=lambda *_a, **_k: {"result": [self._update("8679892510", "/status")]},
            send=lambda text: sent.append(text),
        )
        assert len(sent) == 1 and sent[0]

    def test_an_update_without_a_message_is_skipped_rather_than_crashing(self, tmp_path: Path) -> None:
        # Telegram sends edits, reactions and channel posts through the same endpoint.
        sent: list[str] = []
        poll_once(
            tmp_path, chat_id="8679892510",
            fetch=lambda *_a, **_k: {"result": [{"update_id": 5}, {"update_id": 6, "edited_message": {}}]},
            send=lambda text: sent.append(text),
        )
        assert sent == []


class TestNotSayingTheSameThingTwice:
    def test_an_update_already_seen_is_not_answered_again(self, tmp_path: Path) -> None:
        """Telegram redelivers every update until the offset moves. Without it a poller answers the same
        question every minute, which is exactly the overloading the operator asked to avoid."""
        update = {"update_id": 41, "message": {"chat": {"id": 8679892510}, "text": "/status"}}
        sent: list[str] = []
        for _ in range(3):
            poll_once(tmp_path, chat_id="8679892510",
                      fetch=lambda *_a, **_k: {"result": [update]}, send=lambda t: sent.append(t))
        assert len(sent) == 1, f"answered {len(sent)} times"

    def test_the_offset_asked_for_is_one_past_the_last_seen(self, tmp_path: Path) -> None:
        asked: list[int | None] = []

        def fetch(offset: int | None = None, **_k: Any) -> dict[str, Any]:
            asked.append(offset)
            return {"result": [{"update_id": 100, "message": {"chat": {"id": 1}, "text": "hi"}}]}

        poll_once(tmp_path, chat_id="1", fetch=fetch, send=lambda _t: None)
        poll_once(tmp_path, chat_id="1", fetch=fetch, send=lambda _t: None)
        assert asked[1] == 101, f"asked for {asked[1]}, which would redeliver or skip"


class TestWhatItAnswers:
    def test_every_command_has_an_answer(self, tmp_path: Path) -> None:
        for command in COMMANDS:
            text = reply_for(tmp_path, command, "")
            assert isinstance(text, str) and text.strip(), f"{command} answered nothing"

    def test_help_names_the_commands_that_exist(self, tmp_path: Path) -> None:
        text = reply_for(tmp_path, "help", "")
        for command in COMMANDS:
            assert command in text, f"help does not mention {command}"

    def test_an_unknown_command_is_answered_with_help_not_an_error(self, tmp_path: Path) -> None:
        assert "help" in reply_for(tmp_path, "nonsense", "").lower()

    def test_a_failing_answer_does_not_take_the_poller_down(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An unattended poller that raises stops answering the operator altogether."""
        from pravrudhi.application import telegram_inbox

        monkeypatch.setattr(telegram_inbox, "_status_text", lambda _r: (_ for _ in ()).throw(RuntimeError("ledger gone")))
        text = reply_for(tmp_path, "status", "")
        assert "ledger gone" in text or "could not" in text.lower()
