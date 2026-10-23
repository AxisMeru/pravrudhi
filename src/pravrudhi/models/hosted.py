"""Hosted free-tier models, for the operator's own engine only.

The proposer and the chat surface have been running against a local GGUF server, which holds the GPU the trainee
needs and is too small to drive a tool-calling loop reliably. A hosted free-tier endpoint solves both, but it
belongs to whoever owns the API key, so it must never become a default that a stranger's installation picks up:
an engine someone else installs has no key, and if it somehow had one it would be spending the operator's quota.

Two gates, and both must pass. The key is read only from the operator's own owner-only file by the shared
`free-tier-llm` client, so it is absent by construction on any other machine. And the surface is refused unless
the operator has explicitly opted in AND, when identity is switched on, the caller is the configured admin.
Neither gate is on by default, and nothing here is packaged into the wheel's runtime path.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

# The shared client lives at the user level so every project uses one ledger and one key. Overridable so a
# different checkout can point at its own copy; absent means the capability is simply not available here.
DEFAULT_CLIENT_PATH = Path.home() / ".claude" / "skills" / "free-tier-llm" / "scripts"
OPT_IN = "PRAVRUDHI_HOSTED_MODELS"
ADMIN_EMAIL = "PRAVRUDHI_ADMIN_EMAIL"


class HostedRefused(RuntimeError):
    """The hosted surface is not available to this caller, or not enabled on this engine."""


def client_path() -> Path:
    """Path to the hosted free-model client: the environment override if set, otherwise the default location."""
    return Path(os.environ.get("PRAVRUDHI_FREELLM_PATH", "") or DEFAULT_CLIENT_PATH)


def opted_in() -> bool:
    """Whether the operator switched this on for this engine. Off unless explicitly set."""
    return os.environ.get(OPT_IN, "").strip() in ("1", "true", "yes")


def available() -> tuple[bool, str]:
    """Whether a hosted call could be made here, and why not when it could not."""
    if not opted_in():
        return False, f"not enabled: set {OPT_IN}=1 on the operator's own engine to use hosted free-tier models"
    path = client_path()
    if not (path / "freellm.py").exists():
        return False, f"the shared free-tier client is not installed at {path}"
    try:
        _load()
    except HostedRefused as e:
        return False, str(e)
    return True, "available"


def _load() -> Any:
    path = str(client_path())
    if path not in sys.path:
        sys.path.insert(0, path)
    try:
        import freellm  # type: ignore[import-not-found]
    except ImportError as e:  # pragma: no cover - exercised only where the skill is absent
        raise HostedRefused(f"cannot import the free-tier client from {path}: {e}") from e
    return freellm


def assert_admin(user: Any | None) -> None:
    """Refuse anyone but the operator.

    With identity disabled the engine is the operator's own local process, so the opt-in is the whole gate. With
    identity on, the caller must be the configured admin: a logged-in stranger on a hosted engine must not be able
    to spend the operator's quota, which is the one thing this module is here to prevent.
    """
    from pravrudhi.api.identity import AuthMode, auth_mode

    if not opted_in():
        raise HostedRefused(f"hosted models are not enabled on this engine ({OPT_IN} is unset)")
    if auth_mode() == AuthMode.DISABLED:
        return
    admin = os.environ.get(ADMIN_EMAIL, "").strip().lower()
    if not admin:
        raise HostedRefused(f"identity is on but {ADMIN_EMAIL} is unset; refusing rather than guessing who is admin")
    email = (getattr(user, "email", "") or "").strip().lower()
    role = (getattr(user, "role", "") or "").strip().lower()
    if role == "admin" or (email and email == admin):
        return
    raise HostedRefused("hosted models are the operator's own quota and are not available to this account")


def chat(model: str, messages: list[dict[str, Any]], *, user: Any | None = None, **kw: Any) -> str:
    """One hosted completion, refused unless both gates pass. Returns the reply text.

    Quota accounting, the region check and the refusal when a model's free tier is spent all live in the shared
    client; this function adds only the two gates and the text extraction, so there is one ledger across projects.
    """
    assert_admin(user)
    freellm = _load()
    client = freellm.FreeTierClient()
    return str(client.reply_text(client.chat(model, messages, **kw)))


def usage() -> list[dict[str, Any]]:
    """What the shared ledger has recorded, for the operator's own inspection. Empty when unavailable."""
    ok, _ = available()
    if not ok:
        return []
    freellm = _load()
    return [
        {"model": u.model, "tokens": u.tokens, "calls": u.calls, "cap": u.cap,
         "remaining": u.remaining, "exhausted": u.exhausted}
        for u in freellm.FreeTierClient().usage().values()
        if u.calls or u.exhausted
    ]
