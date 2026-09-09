"""Where a Telegram bot lives, and whose it is.

`notifications._reach` read `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` straight from the process environment.
On the operator's own machine that is correct: the credential is set in the engine's systemd unit and the
notifications are the operator's. In the multi-tenant server it is a leak waiting for its first caller, because
a workspace belongs to a user and the environment belongs to whoever started the process. Every call site
happens to pass the engine's own root today and every one of those routes is operator-only, so nothing has gone
wrong — but that is a property of the call sites, not of this code, and the next user-facing route to emit a
notification would have delivered it to the operator's phone.

So the environment is consulted only when a caller names the engine's own root, resolved rather than compared as
a string, and a caller that names nothing gets no delivery. The unsafe path has to be asked for by name.

A user brings their own bot instead, which is the boundary the operator set: Studio's messaging is the
operator's, and a user configures theirs in settings. The token is stored the way provider keys are — one 0600
file, written with the mode in the `O_CREAT` call so it is never briefly readable, and refused inside a git work
tree — while the chat id and the on/off switch are ordinary settings and live in JSON beside it. Turning
delivery off keeps the credential, because silencing notifications for an afternoon should not cost a trip back
to BotFather.

Telegram is deliberately not a `credentials.PROVIDERS` entry: that registry describes model providers, carries a
base URL and a probe model, and drives routing and validation. A messaging bot answers none of those questions.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from pravrudhi.application.credentials import Secret, _is_inside_git_worktree

TOKEN_ENV = "TELEGRAM_BOT_TOKEN"
CHAT_ENV = "TELEGRAM_CHAT_ID"

PAIR_FILE = ".pravrudhi/telegram-chat.json"
"""Where the chat the operator paired by messaging the bot is recorded. Defined here because which chat a bot
delivers to is decided in this module; `telegram_inbox` writes the file and imports the name from here."""


class MessagingError(ValueError):
    """Configuration that would look complete and deliver nothing."""


@dataclass(frozen=True, slots=True)
class TelegramStatus:
    """What a route may say about a workspace's bot. Carries no token, and is the only shape that leaves this
    module for the API: `configured` answers "is there a credential" without being one.

    `from_environment` distinguishes the operator's own engine, whose credential is set in its service
    environment rather than stored here, from a workspace that configured a bot in settings. Without it the
    settings page would tell the operator "not configured" while Telegram was delivering — a surface disagreeing
    with what the system actually does, which is the failure this whole module is about.
    """

    configured: bool
    enabled: bool
    chat_id: str
    from_environment: bool = False


def _dir(root: Path) -> Path:
    return Path(root) / ".pravrudhi" / "messaging"


def _token_path(root: Path) -> Path:
    return _dir(root) / "telegram.token"


def _settings_path(root: Path) -> Path:
    return _dir(root) / "telegram.json"


def _settings(root: Path) -> dict[str, object]:
    path = _settings_path(root)
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _stored_token(root: Path) -> str:
    path = _token_path(root)
    if not path.exists():
        return ""
    try:
        return path.read_text().strip()
    except OSError:
        return ""


def paired_chat(root: Path) -> str:
    """The chat the operator identified themselves to this bot from, or empty when they never have."""
    try:
        value = json.loads((Path(root) / PAIR_FILE).read_text())["chat_id"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return ""
    return str(value).strip()


def _environment_bot(root: Path) -> tuple[str, str] | None:
    """The operator's own credential, as their service supplies it.

    Both halves or neither, because a token with nowhere to deliver reads as configured and delivers nothing.
    The second half may come from the pairing rather than the environment: the product install shipped with
    `TELEGRAM_CHAT_ID` present but empty, so this resolved to neither and prabhasa_bot sent nothing for a day
    while its settings said it was configured - Telegram refused every send with "chat_id is empty". The
    operator had already told that bot who they were by messaging it, and that pairing was on disk the whole
    time. The environment still wins; the pairing is only consulted when it has nothing to say.
    """
    token = os.environ.get(TOKEN_ENV, "").strip()
    if not token:
        return None
    chat_id = os.environ.get(CHAT_ENV, "").strip() or paired_chat(root)
    return (token, chat_id) if chat_id else None


def _is_engine_root(root: Path, engine_root: Path | None) -> bool:
    """Resolved, not compared as strings: `engine/../engine` is the engine and `engine-2` is not — the same
    reason `credentials.store_for_project` resolves before it compares."""
    return engine_root is not None and Path(root).resolve() == Path(engine_root).resolve()


def telegram_status(root: Path, *, engine_root: Path | None = None) -> TelegramStatus:
    """Whether this root has a bot, whether it is delivering, and where to.

    `engine_root` defaults to nothing, so a caller that does not say whose engine this is never learns about the
    operator's environment credential — the same default `resolve_telegram` takes, and for the same reason. The
    two agree by construction, because which bot delivers is decided here and nowhere else.
    """
    settings = _settings(root)
    if _stored_token(root):
        return TelegramStatus(
            configured=True,
            enabled=bool(settings.get("enabled", True)),
            chat_id=str(settings.get("chat_id", "")),
            from_environment=False,
        )
    inherited = _environment_bot(root) if _is_engine_root(root, engine_root) else None
    if inherited is not None:
        return TelegramStatus(configured=True, enabled=True, chat_id=inherited[1], from_environment=True)
    return TelegramStatus(configured=False, enabled=False, chat_id="", from_environment=False)


def set_telegram(
    root: Path, *, token: str | None = None, chat_id: str | None = None, enabled: bool | None = None
) -> TelegramStatus:
    """Store or amend this workspace's bot. Each argument left out is left alone, so the on/off switch can be
    flipped without the token being sent again — which matters, because a settings form that has to re-post a
    secret to change an unrelated field teaches people to keep the secret somewhere convenient."""
    root = Path(root)
    current = telegram_status(root)  # engine_root omitted: only a stored bot is this root's to amend
    next_chat = current.chat_id if chat_id is None else chat_id.strip()

    if token is not None:
        value = token.strip()
        if not value:
            raise MessagingError("refusing to store an empty bot token")
        if not next_chat:
            raise MessagingError(
                "a bot token needs a chat id: without one there is nowhere to deliver, and the settings would "
                "read as configured while nothing arrived"
            )
        if _is_inside_git_worktree(root):
            raise MessagingError(f"refusing to write a bot token under {root} — it is inside a git work tree")
        directory = _dir(root)
        directory.mkdir(parents=True, exist_ok=True)
        path = _token_path(root)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(value + "\n")
        os.chmod(path, 0o600)

    if enabled and not (token is not None or current.configured):
        raise MessagingError("there is no bot to enable: add a token and a chat id first")

    if chat_id is not None and not next_chat and (token is not None or current.configured):
        raise MessagingError(
            "a stored bot needs a chat id: clearing it would leave the settings reading as configured while "
            "nothing arrived. Remove the bot instead."
        )

    settings = _settings(root)
    if chat_id is not None:
        settings["chat_id"] = next_chat
    if enabled is not None:
        settings["enabled"] = bool(enabled)
    elif token is not None:
        settings.setdefault("enabled", True)
    _dir(root).mkdir(parents=True, exist_ok=True)
    _settings_path(root).write_text(json.dumps(settings, sort_keys=True, indent=2) + "\n")
    return telegram_status(root)


def clear_telegram(root: Path) -> bool:
    """Forget this workspace's bot entirely. Returns whether there was one."""
    path = _token_path(root)
    had = path.exists()
    if had:
        path.unlink()
    settings_path = _settings_path(root)
    if settings_path.exists():
        settings_path.unlink()
    return had


def resolve_telegram(root: Path, *, engine_root: Path | None) -> tuple[Secret, str] | None:
    """The bot this root should deliver through, or `None` for no delivery.

    A credential stored in the root wins wherever it is found, including at the engine's own root: something
    somebody deliberately configured is what they meant. Otherwise the process environment applies only if this
    root *is* the engine's own, which is why `engine_root` has no default — a caller that does not know whose
    engine it is is exactly the caller that must not reach the operator's bot.
    """
    root = Path(root)
    status = telegram_status(root, engine_root=engine_root)
    if not status.configured or not status.enabled or not status.chat_id:
        return None
    if status.from_environment:
        inherited = _environment_bot(root)
        return (Secret(provider="telegram", value=inherited[0]), status.chat_id) if inherited else None
    return Secret(provider="telegram", value=_stored_token(root)), status.chat_id


__all__ = [
    "CHAT_ENV", "PAIR_FILE", "TOKEN_ENV", "MessagingError", "TelegramStatus",
    "clear_telegram", "resolve_telegram", "set_telegram", "telegram_status",
]
