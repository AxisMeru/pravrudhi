"""Talking back to the engine from a phone.

The loop could already say what happened; this is the other direction. It matters more for safety, because a
message arriving over the network is untrusted input asking the engine to do something.

Only the configured chat is obeyed — the bot's token is enough for anyone who finds it to send messages, and the
answer to those is silence. That check is the one that matters and it is enforced here rather than trusted to a
caller.

Anything that is not a slash command is a conversation, answered by `chat.converse`: the engine's own surface,
with the tool access, citations and honesty pass it already applies to the chat page. The bot used to accept six
commands and answer prose with "I hear: <your text>" followed by a status block, which is a receipt rather than a
reply — the interface the operator actually carries was the least capable one the project owns.

Free text does NOT weaken the earlier argument that there is "nothing here for an injected instruction to reach".
`converse` reaches the engine's own record through a fixed tool set; it does not hand strings to a shell. The
boundary is the same one the chat page already trusts, and it is reached only from the configured chat.

The operator asked not to be overloaded, which is why the offset is persisted. Telegram redelivers every update
until the offset moves past it; a poller that forgets answers the same question every minute.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

Fetch = Callable[..., dict[str, Any]]
Send = Callable[[str], None]

_THREADS_FILE = ".pravrudhi/telegram-threads.json"


def _converse(root: Path, message: str, *, thread_id: str | None = None, **kw: Any) -> Any:
    """Indirection so a test can drive the bot without a model endpoint, and so an import of the chat stack is
    paid only when someone actually talks."""
    from pravrudhi.application.chat import converse

    return converse(root, message, thread_id=thread_id, **kw)


def _unreachable_type() -> type[BaseException]:
    """The chat stack's own exception, imported lazily so this module stays importable without it."""
    try:
        from pravrudhi.application.chat import ChatEndpointUnreachable as _Real

        return _Real
    except Exception:  # noqa: BLE001
        return ChatEndpointUnreachable


class ChatEndpointUnreachable(RuntimeError):
    """Stand-in for the chat stack's exception, so a test can raise the condition without importing it."""


def _threads(root: Path) -> dict[str, str]:
    try:
        data = json.loads((Path(root) / _THREADS_FILE).read_text())
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError, AttributeError):
        return {}


def _remember_thread(root: Path, chat_id: str, thread_id: str) -> None:
    """One conversation per chat, so a follow-up question means what it looks like it means.

    Keyed by chat: the studio workspace and a product install answer from different roots, and two people in two
    chats must never inherit each other's context.
    """
    path = Path(root) / _THREADS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    current = _threads(root)
    current[str(chat_id)] = thread_id
    path.write_text(json.dumps(current, indent=1, sort_keys=True))

COMMANDS: tuple[str, ...] = ("status", "requests", "routes", "beat", "ask", "help")
"""Every name this engine answers to. A command outside this set is answered with help, never guessed at."""

_OFFSET_FILE = ".pravrudhi/telegram-offset.json"


def parse_command(text: str) -> tuple[str, str] | None:
    """Split a message into a command and its argument.

    Prose is a question rather than an error: a person types a sentence, and a bot that only accepts slash
    commands is a bot nobody talks to.
    """
    body = (text or "").strip()
    if not body:
        return None
    if not body.startswith("/"):
        return ("ask", body)
    head, _, rest = body[1:].partition(" ")
    command = head.split("@", 1)[0].strip().lower()  # Telegram appends @thebot in groups
    return (command, rest.strip())


def _offset_path(root: Path) -> Path:
    return Path(root) / _OFFSET_FILE


def _read_offset(root: Path) -> int | None:
    try:
        return int(json.loads(_offset_path(root).read_text())["offset"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _write_offset(root: Path, offset: int) -> None:
    path = _offset_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"offset": offset}))


def _status_text(root: Path) -> str:
    from pravrudhi.application.status import status

    s = status(root)
    return (f"events {s['events']}, candidates {s['candidates']}, chain "
            f"{'ok' if s.get('chain_ok') else 'BROKEN'}")


def _requests_text(root: Path) -> str:
    from pravrudhi.application import requests as reqs

    # `backlog` returns the shape the interface and CLI render, not Request objects.
    rows = reqs.backlog(root).get("requests") or []
    open_rows = [r for r in rows if r.get("state") not in ("verified", "declined")]
    if not open_rows:
        return "nothing owed: every captured request is verified"
    lines = []
    for r in open_rows[:6]:
        criteria = r.get("criteria") or []
        met = sum(1 for c in criteria if c.get("met"))
        lines.append(f"{r.get('id')} [{r.get('state')}] {met}/{len(criteria)} — {str(r.get('text', ''))[:70]}")
    return "\n".join(lines)


def _routes_text(root: Path) -> str:
    from pravrudhi.application.roster import roster

    seats = roster(root)
    usable = [s for s in seats if getattr(s, "usable", True)]
    return f"{len(usable)} of {len(seats)} seats usable: " + ", ".join(s.id for s in usable[:8])


def _beat_text(root: Path) -> str:
    from pravrudhi.application.heartbeat import beat

    result = beat(root)
    return f"{result.sentence}\n{result.reason}"[:900]


def _unreachable_help(error: Exception) -> str:
    """A failure the operator can act on, because they are usually not at the machine when they message.

    Naming the endpoint and the one command that fixes it is the difference between a message worth sending and
    noise. The slash commands are named too, because they keep working without a model and that is exactly when
    someone needs them.
    """
    return (
        f"I could not reach the model endpoint, so I cannot answer that one in full.\n\n{error}\n\n"
        "To unblock: start the engine with `pravrudhi app` on that machine, or point it elsewhere by setting "
        "PRAVRUDHI_CHAT_ENDPOINT to an OpenAI-compatible URL.\n\n"
        "Meanwhile /status, /requests, /routes and /beat all answer without a model."
    )


def _edition_label() -> str:
    """Which engine is answering.

    One bot serves both installs until a second chat exists, and a message that does not say which engine
    produced it is worse than no message: a studio answer read as the product's would send the operator looking
    for work on the wrong machine. When the two do have separate chats the per-root settings keep their ids
    apart and this label becomes redundant rather than wrong.
    """
    try:
        from pravrudhi.api.edition import engine_edition

        return str(engine_edition())
    except Exception:  # noqa: BLE001 - a label must never be the reason a reply fails
        return "Pravrudhi"


def reply_for(root: Path, command: str, argument: str, *, chat_id: str = "") -> str:
    """What to say back. Every branch returns text; an answer that raised would stop the poller answering at
    all, and an unattended operator link that goes quiet on an error is worse than one that says it failed.

    The slash commands are deliberately kept as fast paths that never touch the model: they are what the
    operator needs when the engine is unwell, which is the moment a model-backed answer is least likely to work.
    """
    return f"[{_edition_label()}] {_body_for(root, command, argument, chat_id=chat_id)}"


def _body_for(root: Path, command: str, argument: str, *, chat_id: str) -> str:
    try:
        if command == "status":
            return _status_text(root)
        if command == "requests":
            return _requests_text(root)
        if command == "routes":
            return _routes_text(root)
        if command == "beat":
            return _beat_text(root)
        if command == "ask":
            return _answer(root, argument, chat_id=chat_id)
    except Exception as error:  # noqa: BLE001 (a failed answer must still be an answer)
        return f"could not answer /{command}: {error}"
    return "Commands: " + ", ".join(f"/{c}" for c in COMMANDS)


def _answer(root: Path, message: str, *, chat_id: str) -> str:
    """Anything that is not a command, answered by the engine's own conversation.

    `converse` carries the tool access and the honesty pass that strips numbers the tools did not return, so what
    arrives on the phone is held to the same standard as the chat page rather than to a lower one because the
    screen is smaller.
    """
    if not message.strip():
        return "Ask me anything about this engine, or use " + ", ".join(f"/{c}" for c in COMMANDS) + "."
    prior = _threads(root).get(str(chat_id)) if chat_id else None
    try:
        outcome = _converse(root, message, thread_id=prior)
    except Exception as error:  # noqa: BLE001
        if isinstance(error, _unreachable_type() | ChatEndpointUnreachable):
            return _unreachable_help(error)
        return f"I could not answer that: {error}"
    thread_id = getattr(outcome, "thread_id", "") or ""
    if chat_id and thread_id:
        _remember_thread(root, str(chat_id), thread_id)
    reply = (getattr(outcome, "reply", "") or "").strip() or "I have nothing to add to that."
    refusals = tuple(getattr(outcome, "refusals", ()) or ())
    if refusals:
        # The honesty pass removed something. Saying so is the point of having it.
        reply += "\n\n(dropped from my draft, unsupported by the record: " + "; ".join(refusals[:3]) + ")"
    return reply


def poll_once(root: Path, *, chat_id: str, fetch: Fetch, send: Send) -> int:
    """Answer whatever has arrived since the last poll. Returns how many messages were answered."""
    offset = _read_offset(root)
    try:
        payload = fetch(offset=offset)
    except Exception:  # noqa: BLE001 (an unreachable Telegram is not a reason to stop the engine)
        return 0

    answered, highest = 0, None
    for update in payload.get("result") or []:
        update_id = update.get("update_id")
        if isinstance(update_id, int):
            # Telegram is asked not to redeliver, and is also not trusted not to. If the offset write failed, or
            # the same update arrives twice, answering it again would send the operator the same message every
            # minute — the overloading they asked to avoid, caused by the mechanism meant to prevent it.
            if offset is not None and update_id < offset:
                continue
            highest = update_id if highest is None else max(highest, update_id)
        message = update.get("message")
        if not isinstance(message, dict):
            continue  # edits, reactions and channel posts arrive here too
        if str(message.get("chat", {}).get("id", "")) != str(chat_id):
            continue  # somebody else found the bot
        parsed = parse_command(str(message.get("text") or ""))
        if parsed is None:
            continue
        send(reply_for(root, parsed[0], parsed[1], chat_id=str(chat_id)))
        answered += 1

    if highest is not None:
        _write_offset(root, highest + 1)
    return answered


def live_poll(root: Path) -> int:
    """One poll against the real Telegram, using the operator's own credentials.

    The token is read here and never returned or logged: `Secret` carries it to the transport and the URL it is
    embedded in is built inside `reach`, which already keeps it out of exceptions and log lines.
    """
    import os
    import urllib.request

    from pravrudhi.application import reach
    from pravrudhi.application.credentials import Secret

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return 0

    def fetch(offset: int | None = None) -> dict[str, Any]:
        url = f"https://api.telegram.org/bot{token}/getUpdates?timeout=0"
        if offset is not None:
            url += f"&offset={offset}"
        with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310 (a fixed, known host)
            return json.loads(response.read().decode("utf-8"))  # type: ignore[no-any-return]

    def send(text: str) -> None:
        reach.send(root, kind="operator_reply", title=text, detail="",
                   token=Secret(provider="telegram", value=token), chat_id=chat_id,
                   filter_config=reach.NotificationFilter(kinds={"operator_reply"}))

    return poll_once(root, chat_id=chat_id, fetch=fetch, send=send)


__all__ = ["COMMANDS", "live_poll", "parse_command", "poll_once", "reply_for"]
