"""External messaging (Telegram) for critical notifications when the operator is away from the interface.

This module sends structured notifications to Telegram using the Bot API, with escape, retry and delivery-id
discipline. When no credential is configured, send() is a no-op returning a stated reason rather than an
exception, so an engine without a token can keep running.

Patterns adapted from the OpenClaw swarm-platform telegram implementation:
- MarkdownV2 escaping for all text fields
- Bounded retries with exponential backoff
- Delivery IDs to prevent double-posting on retry
- Allowlist of chat IDs for security
- No-op with reason when no credential exists
"""

from __future__ import annotations

import contextlib
import secrets
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from pravrudhi.application.credentials import Secret, redact

# Characters that must be escaped in Telegram MarkdownV2 format.
_MARKDOWN_V2_ESCAPE_CHARS = r"_*[]()~`>#+-=|{}\.!"


def escape_markdown_v2(text: str | None) -> str:
    """Prefix every MarkdownV2 metacharacter with a backslash, in one pass.

    One pass is the whole correctness argument. Replacing each metacharacter in turn escapes the backslashes the
    earlier replacements just inserted, so `*bold*` became `\\\\*bold\\\\*` and Telegram rendered the backslashes
    instead of the emphasis. Building the string character by character cannot double-escape, because each
    character of the input is considered exactly once and the backslashes added are never revisited.
    """
    if not text:
        return ""
    return "".join(f"\\{c}" if c in _MARKDOWN_V2_ESCAPE_CHARS else c for c in str(text))


# Kinds of notifications worth sending externally. Default: only wake for sign-offs, critical issues, resource
# depletion, and a loop stalled where quiet hours or resting resolve themselves and this does not (ADR-0053 \u00a73
# amendment: a heartbeat alive while convergence is zero, with nobody told, is the failure the operator named).
DEFAULT_SEND_KINDS = {
    "promotion_needed",      # A night finished and needs sign-off
    "audit_severity_high",   # Critical audit finding
    "pool_depleted",         # Compute pool is empty or nearly so
    "rebase_conflict_streak",  # A loop root cannot sync with origin/main and nobody has looked
}


#: What a reader is meant to DO about each kind that reaches them. Only the kinds on the send list have an
#: entry: inventing a next step for a kind nobody has thought about is worse than omitting one, because a
#: reader who follows a made-up instruction has been actively misled rather than merely under-informed.
NEXT_STEP = {
    "promotion_needed": "sign or refuse it: `pravrudhi inbox`",
    "audit_severity_high": "read the finding: `pravrudhi status`",
    "pool_depleted": "the pool is out of eligible items; seal a larger one or raise the exposure cap",
    "rebase_conflict_streak": "resolve the conflict by hand in the loop root; the loop will not resolve it itself",
}

#: A glyph per kind so a phone notification can be triaged before it is read. Deliberately dull and few.
_GLYPH = {
    "promotion_needed": "\u2713", "audit_severity_high": "\u26a0", "pool_depleted": "\u25cb",
    "rebase_conflict_streak": "\u26a0",
}


def compose(*, kind: str, title: str, detail: str = "", edition: str = "", when: str = "") -> str:
    """The text a person actually receives, in MarkdownV2.

    The whole message used to be `f"*{title}*\n{detail}"`, which is why the operator reported the bot's
    communication as uninformative: `"MyTask was accepted"` arrived with no indication of which install sent
    it, when, what kind of event it was, or whether it wanted anything. Every one of those is known at the
    call site and none of them was travelling.

    The footer carries the machine `kind` verbatim rather than a prettified version of it, because that is the
    string a reader greps the notification feed for, and a message that cannot be traced back to its record is
    a dead end.
    """
    head = f"{_GLYPH.get(kind, '')} *{escape_markdown_v2(title)}*".strip()
    lines = [head]
    if detail:
        lines.append(escape_markdown_v2(detail))
    step = NEXT_STEP.get(kind)
    if step:
        lines.append(escape_markdown_v2("\u2192 " + step))
    # The kind goes in a CODE SPAN, not plain text. MarkdownV2 escapes `_`, so a plain-text
    # `promotion_needed` renders as `promotion\_needed` and stops being the string a reader can grep the
    # notification feed for -- which is the one job the machine tag has in the message.
    tail = " \u00b7 ".join(escape_markdown_v2(x) for x in (edition, when) if x)
    footer = "`" + kind.replace("\\", "").replace("`", "") + "`"
    if tail:
        footer += " \u00b7 " + tail
    lines.append(footer)
    return "\n".join(lines).strip()


class TransportError(RuntimeError):
    """A delivery failure whose message has already had the token scrubbed out of it."""


@dataclass
class NotificationFilter:
    """Configurable filter over which notification kinds trigger an external send."""

    kinds: set[str] = field(default_factory=lambda: set(DEFAULT_SEND_KINDS))
    """Which notification kinds are worth reaching a person for. An empty set sends nothing, which is a
    deliberate way to switch external messaging off without removing the credential."""

    def should_send(self, kind: str) -> bool:
        return kind in self.kinds


@dataclass
class SendResult:
    """Result of a send attempt. `sent` is True only if delivery was attempted; `reason` explains why not."""

    sent: bool
    reason: str | None = None


class Sink:
    """Transport layer: sends one message to Telegram over HTTPS, with bounded retries and escape discipline.

    The token is never revealed in logs, exceptions, or return values. When no token is configured, methods
    return a not-configured reason instead of raising. Retries use the same delivery_id so downstream can
    detect and suppress duplicates.
    """

    def __init__(
        self,
        token: Secret | None,
        allowed_chat_ids: set[str] | None = None,
        max_retries: int = 3,
        retry_base_ms: float = 1200.0,
        transport: Any = None,
    ) -> None:
        self._token = token
        self._allowed_chat_ids = allowed_chat_ids or set()
        self._max_retries = max_retries
        self._retry_base_ms = retry_base_ms
        self._transport = transport

    def send(self, chat_id: str, text: str) -> dict[str, Any]:
        """Send one message to a Telegram chat. Returns a dict with ok (bool) and reason/message_id."""
        if self._token is None:
            return {"ok": False, "reason": "no_credential"}

        if self._allowed_chat_ids and chat_id not in self._allowed_chat_ids:
            return {"ok": False, "reason": "chat_id_not_allowed"}

        # One delivery id for the whole attempt sequence, so a retry is recognisably the same message rather
        # than a second one. Generating it per attempt would make every retry look like a fresh post.
        delivery_id = secrets.token_hex(6)
        last: str = "send_failed_after_retries"

        for attempt in range(self._max_retries + 1):
            try:
                result = self._attempt(chat_id=chat_id, text=text, delivery_id=delivery_id)
                if result.get("ok"):
                    return result
                last = str(result.get("reason") or last)
            except Exception as exc:  # noqa: BLE001 (a failed send is a reason to retry, not to crash the night)
                # The exception may carry the token: an HTTP client that echoes the request URL puts it in the
                # message. It is redacted here and the exception itself is never propagated or logged raw.
                last = self._scrub(str(exc)) or "send_failed"
            if attempt < self._max_retries:
                time.sleep(self._retry_base_ms * (attempt + 1) / 1000.0)

        return {"ok": False, "reason": self._scrub(last), "delivery_id": delivery_id}

    def _scrub(self, text: str) -> str:
        """Remove this sink's own token from a string, then apply the shared pattern redaction.

        `redact` recognises credential shapes it has been taught, and a Telegram bot token is not one of them —
        a test that fed a real-shaped token through a failing transport got it back verbatim in the result. The
        sink does not have to guess: it holds the exact secret, so it removes that first and lets the pattern
        pass catch anything else the message happens to carry.
        """
        out = text
        if self._token is not None:
            revealed = self._token.reveal()
            if revealed:
                out = out.replace(revealed, "[REDACTED]")
        return redact(out)

    def _attempt(self, chat_id: str, text: str, delivery_id: str) -> dict[str, Any]:
        """One delivery, through the injected transport when there is one and over HTTPS otherwise."""
        if self._transport is not None:
            out = self._transport.send(chat_id=chat_id, text=text)
            return out if isinstance(out, dict) else {"ok": False, "reason": "transport returned no result"}
        return self._send_attempt(chat_id=chat_id, text=text, delivery_id=delivery_id)

    def _send_attempt(self, chat_id: str, text: str, delivery_id: str) -> dict[str, Any]:
        """One attempt to POST to Telegram's sendMessage endpoint."""
        token_revealed = self._token.reveal() if self._token else ""
        url = f"https://api.telegram.org/bot{token_revealed}/sendMessage"

        # httpx puts the request URL in its exception messages, and the URL contains the token. Every failure
        # from here is re-raised with the token redacted rather than passed through.
        try:
            response = httpx.post(
                url,
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "MarkdownV2",
                    "disable_web_page_preview": True,
                },
                timeout=10.0,
            )
        except Exception as exc:  # noqa: BLE001 (re-raised, but only after the token is scrubbed)
            raise TransportError(self._scrub(str(exc)) or "network error") from None

        parsed = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
        data: dict[str, Any] = parsed if isinstance(parsed, dict) else {}
        return {
            "ok": response.status_code == 200 and data.get("ok", False),
            "delivery_id": delivery_id,
            "message_id": data.get("result", {}).get("message_id") if data.get("ok") else None,
            "reason": data.get("description", "unknown") if not data.get("ok") else None,
        }


def send(
    root: Path,
    *,
    kind: str,
    title: str,
    detail: str = "",
    token: Secret | None = None,
    sink: Sink | None = None,
    chat_id: str | None = None,
    filter_config: NotificationFilter | None = None,
) -> SendResult:
    """Send a notification to Telegram if configured and the kind passes the filter.

    This is a no-op (returns a reason) when no token is configured, so an engine can keep running
    without external messaging set up. The title and detail are escaped for MarkdownV2 format,
    and the token never appears in any log line, exception message, or response.

    Args:
        root: Project root (for future credential retrieval if needed).
        kind: Notification kind (e.g. 'promotion_needed', 'audit_severity_high').
        title: Human-readable title (will be escaped).
        detail: Additional context (will be escaped).
        token: The Telegram bot token (Secret). If None, returns not-configured.
        sink: Injected transport for testing. If None, a real Sink is created.
        chat_id: Target chat ID. If None, uses a sensible default (operator's configured chat).
        filter_config: Custom filter. If None, uses DEFAULT_SEND_KINDS.

    Returns:
        SendResult with sent (bool) and reason (str | None).
    """
    if token is None:
        return SendResult(sent=False, reason="no_credential")

    if filter_config is None:
        filter_config = NotificationFilter()

    if not filter_config.should_send(kind):
        return SendResult(sent=False, reason="kind_filtered")

    # If no sink was provided (normal case), create one.
    if sink is None:
        sink = Sink(token=token)

    # If no chat_id was provided, this would typically come from config. For now, return not-configured.
    if chat_id is None:
        return SendResult(sent=False, reason="no_chat_id_configured")

    # `compose` carries the kind, the install and the time, and the next step for the kinds that have one.
    # The message used to be title-and-detail alone, which is why a notification could not be told apart from
    # any other install's, placed in time, or acted on without going and looking.
    edition = ""
    with contextlib.suppress(Exception):  # a label is a nicety; failing to find one must not lose the message
        from pravrudhi.application.telegram_inbox import _edition_label  # noqa: PLC0415 (circular at module scope)

        edition = str(_edition_label())
    message = compose(
        kind=kind,
        title=title,
        detail=detail,
        edition=edition,
        when=datetime.now(UTC).strftime("%H:%M UTC"),
    )

    # Send via the sink.
    result = sink.send(chat_id=chat_id, text=message)

    if result.get("ok"):
        return SendResult(sent=True, reason=None)

    # Redact any key-shaped strings from the reason before returning.
    reason = redact(result.get("reason", "unknown"))
    return SendResult(sent=False, reason=reason)


__all__ = [
    "DEFAULT_SEND_KINDS",
    "NotificationFilter",
    "SendResult",
    "Sink",
    "escape_markdown_v2",
    "send",
]
