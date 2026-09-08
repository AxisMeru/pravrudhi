"""A bot with no chat id yet adopts the first conversation someone opens with it.

Telegram will not reveal a chat id until a person messages the bot, so a freshly created bot cannot be configured
in advance: the operator would have to open the conversation, go and find the numeric id, and paste it back. That
is three steps of clerical work to connect a thing they already own, and every one of them is a place to give up.

Adoption is deliberately narrow. It happens only when this workspace has no chat id at all, it takes the first
chat that speaks and nothing after, and it is written down so the next poll is an ordinary one. A bot that
re-adopted would follow whoever spoke most recently, which is not a pairing, it is a hijack.
"""

from __future__ import annotations

import json
from pathlib import Path

from pravrudhi.application import telegram_inbox as tg


def _update(chat_id: int, text: str, update_id: int = 1) -> dict[str, object]:
    return {"update_id": update_id, "message": {"chat": {"id": chat_id, "type": "private"}, "text": text}}


def test_the_first_chat_to_speak_is_adopted(tmp_path: Path) -> None:
    sent: list[str] = []
    payload = {"result": [_update(555, "hello")]}

    def record(target: str, text: str) -> None:
        sent.append(f"{target}:{text}")

    adopted = tg.adopt_chat(tmp_path, fetch=lambda offset=None: payload, send=record)

    assert adopted == "555"
    assert tg.paired_chat(tmp_path) == "555", "the pairing must survive into the next poll"
    assert sent and sent[0].startswith("555:"), "the greeting must go to the chat just adopted"


def test_an_already_paired_workspace_is_not_re_adopted(tmp_path: Path) -> None:
    tg._remember_chat(tmp_path, "111")
    adopted = tg.adopt_chat(tmp_path, fetch=lambda offset=None: {"result": [_update(999, "hi")]}, send=lambda target, text: None)

    assert adopted is None, "a paired bot must not follow whoever spoke last"
    assert tg.paired_chat(tmp_path) == "111"


def test_nothing_to_adopt_is_not_an_error(tmp_path: Path) -> None:
    assert tg.adopt_chat(tmp_path, fetch=lambda offset=None: {"result": []}, send=lambda target, text: None) is None
    assert tg.paired_chat(tmp_path) is None


def test_an_unreachable_telegram_does_not_raise(tmp_path: Path) -> None:
    def boom(offset: int | None = None) -> dict[str, object]:
        raise OSError("network down")

    assert tg.adopt_chat(tmp_path, fetch=boom, send=lambda target, text: None) is None


def test_the_pairing_file_is_this_workspaces_own(tmp_path: Path) -> None:
    """Studio and product are different roots, so one pairing must never be read as the other's."""
    studio, product = tmp_path / "studio", tmp_path / "product"
    studio.mkdir()
    product.mkdir()
    tg._remember_chat(studio, "1001")

    assert tg.paired_chat(studio) == "1001"
    assert tg.paired_chat(product) is None
    assert json.loads((studio / tg._PAIR_FILE).read_text())["chat_id"] == "1001"
