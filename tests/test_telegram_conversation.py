"""The bot answers anything, in the engine's own voice, and remembers the conversation.

It used to accept six slash commands and answer prose with "I hear: <your text>" followed by the status block —
which is not a conversation, it is a receipt. The engine already has a conversational surface with tool access,
citations and an honesty pass over the model's draft (`application/chat.converse`); the bot had simply never been
wired to it, so the interface the operator actually carries was the least capable one the project owns.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pravrudhi.application import telegram_inbox as tg


class _Outcome:
    def __init__(self, reply: str, thread_id: str = "t-1") -> None:
        self.reply, self.thread_id = reply, thread_id
        self.citations: tuple[dict[str, object], ...] = ()
        self.tool_calls: tuple[object, ...] = ()
        self.refusals: tuple[str, ...] = ()


def test_prose_is_answered_by_the_engines_own_conversation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_converse(root, message, *, thread_id=None, **kw):  # type: ignore[no-untyped-def]
        seen["message"], seen["thread_id"] = message, thread_id
        return _Outcome("Night 18 pruned all twelve candidates; the incumbent is still c-0045.")

    monkeypatch.setattr(tg, "_converse", fake_converse)
    reply = tg.reply_for(tmp_path, "ask", "what happened last night?", chat_id="42")

    assert "Night 18 pruned all twelve" in reply
    assert "I hear:" not in reply, "the receipt behaviour must be gone"
    assert seen["message"] == "what happened last night?"


def test_the_thread_persists_so_it_is_a_conversation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    threads: list[str | None] = []

    def fake_converse(root, message, *, thread_id=None, **kw):  # type: ignore[no-untyped-def]
        threads.append(thread_id)
        return _Outcome("ok", thread_id="thread-abc")

    monkeypatch.setattr(tg, "_converse", fake_converse)
    tg.reply_for(tmp_path, "ask", "first", chat_id="42")
    tg.reply_for(tmp_path, "ask", "second", chat_id="42")

    assert threads[0] is None, "the first turn opens a thread"
    assert threads[1] == "thread-abc", "the second turn continues it"


def test_a_different_chat_gets_its_own_thread(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    threads: list[str | None] = []

    def fake_converse(root, message, *, thread_id=None, **kw):  # type: ignore[no-untyped-def]
        threads.append(thread_id)
        return _Outcome("ok", thread_id=f"thread-for-{len(threads)}")

    monkeypatch.setattr(tg, "_converse", fake_converse)
    tg.reply_for(tmp_path, "ask", "hello", chat_id="42")
    tg.reply_for(tmp_path, "ask", "hello", chat_id="99")

    assert threads == [None, None], "a second chat must not inherit the first chat's conversation"


def test_an_unreachable_model_says_how_to_unblock_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The operator is usually away from the machine when they message. A failure they cannot act on is noise."""

    def fake_converse(root, message, *, thread_id=None, **kw):  # type: ignore[no-untyped-def]
        raise tg.ChatEndpointUnreachable("http://127.0.0.1:8080/v1 did not answer")

    monkeypatch.setattr(tg, "_converse", fake_converse)
    reply = tg.reply_for(tmp_path, "ask", "anything", chat_id="42")

    assert "127.0.0.1:8080" in reply, "name the endpoint that is down"
    assert "PRAVRUDHI_CHAT_ENDPOINT" in reply or "pravrudhi app" in reply, "say what would fix it"
    assert "Traceback" not in reply


def test_slash_commands_still_answer_without_the_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`/status` must keep working when the model endpoint is down — that is when it is needed most."""

    def explode(*a, **k):  # type: ignore[no-untyped-def]
        raise AssertionError("a slash command must not need the model")

    monkeypatch.setattr(tg, "_converse", explode)
    assert tg.reply_for(tmp_path, "status", "", chat_id="42")


def test_every_reply_names_which_engine_answered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """One bot serves two engines, so a reply that does not say which one is ambiguous at best.

    The operator asked for the studio and product channels to be kept apart. A second Telegram chat needs a human
    to create it, so until one exists the cheap half of that separation is done here: every message says which
    engine produced it. When a second chat or forum topic does exist, the per-root settings already keep the two
    installs' chat ids apart and this label stops mattering — which is the right order to build it in.
    """
    monkeypatch.setattr(tg, "_converse", lambda *a, **k: _Outcome("all quiet"))
    monkeypatch.setattr(tg, "_edition_label", lambda: "Pravrudhi Studio")

    prose = tg.reply_for(tmp_path, "ask", "anything", chat_id="42")
    status = tg.reply_for(tmp_path, "status", "", chat_id="42")

    assert prose.startswith("[Pravrudhi Studio]"), prose
    assert status.startswith("[Pravrudhi Studio]"), status


def test_the_bot_can_put_a_request_on_record(tmp_path: Path) -> None:
    """The bot's first action, rather than another answer.

    Until this it could report what the engine had done and nothing else, so anything the operator wanted had to
    wait until they reached a terminal. A captured request is what the obligations drive works from, so this is
    the shortest path from a phone to the loop building something.
    """
    from pravrudhi.application import requests

    reply = tg.reply_for(tmp_path, "request", "seal a Sanskrit evaluation pool", chat_id="42")
    assert "Request on record: r-" in reply, reply

    backlog = requests.load(tmp_path)
    assert any("Sanskrit evaluation pool" in r.text for r in backlog), [r.text for r in backlog]


def test_an_empty_request_is_usage_not_an_empty_record(tmp_path: Path) -> None:
    from pravrudhi.application import requests

    assert "Usage:" in tg.reply_for(tmp_path, "request", "   ", chat_id="42")
    assert requests.load(tmp_path) == []


def test_no_paired_chat_means_no_authority_to_act(tmp_path: Path) -> None:
    """An unconfigured bot must answer nobody, rather than treating the first arrival as its operator."""
    called: list[str] = []
    payload = {"result": [{"update_id": 1, "message": {"chat": {"id": 7}, "text": "/request do a thing"}}]}
    answered = tg.poll_once(tmp_path, chat_id="", fetch=lambda offset=None: payload, send=called.append)
    assert answered == 0 and called == []
