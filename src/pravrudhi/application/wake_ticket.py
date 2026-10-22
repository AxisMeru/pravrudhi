"""#1038 PR-4: the wake ticket. A short-lived, user-bound proof that a cold-start wake ended `ready` or `partial`.

The status read issues one; the client holds it in memory and sends it as `X-Wake-Ticket` on the next screening. It is never
stored on the server, and it carries no case content: only the user id it was issued to, an expiry and a mode. A missing, bad,
expired or other-user ticket is IGNORED (the screening then goes through the ordinary checks, so a parked judge still refuses
with `judges_offline`); a valid one can only make a run stricter (`partial`: the second judge is not called, every established
ingredient is referred) or extend the judge leases (`full`). The key is derived from the waker secret, never the waker's
signing key itself. Pure functions; never raises."""

from __future__ import annotations

import hashlib
import hmac
import re
import time
from collections.abc import Callable
from typing import Literal

Mode = Literal["full", "partial"]
HEADER = "X-Wake-Ticket"
_PREFIX = "v1"
_SHAPE = re.compile(r"v1\.(\d{1,12})\.(full|partial)\.([0-9a-f]{64})")


def _key(secret: str | bytes) -> bytes:
    raw = secret.encode() if isinstance(secret, str) else secret
    return hmac.new(raw, b"pravrudhi-wake-ticket-v1", hashlib.sha256).digest()


def _sig(secret: str | bytes, user_id: str, expires: int, mode: str) -> str:
    msg = f"{_PREFIX}|ticket|{user_id.strip().lower()}|{expires}|{mode}"
    return hmac.new(_key(secret), msg.encode(), hashlib.sha256).hexdigest()


def issue(secret: str | bytes, user_id: str, mode: Mode, ttl_s: float, *, clock: Callable[[], float] = time.time) -> str:
    if mode not in ("full", "partial") or not user_id.strip():
        raise ValueError("not a valid ticket")
    expires = int(clock() + ttl_s)
    return f"{_PREFIX}.{expires}.{mode}.{_sig(secret, user_id, expires, mode)}"


def verify(secret: str | bytes, value: str | None, user_id: str | None, *, clock: Callable[[], float] = time.time) -> Mode | None:
    """The ticket's mode when it is well formed, unexpired, signed for this user and this secret; else None."""
    try:
        if not value or not user_id or not user_id.strip() or len(value) > 120:
            return None
        m = _SHAPE.fullmatch(value)
        if m is None:
            return None
        expires, mode, sig = int(m.group(1)), m.group(2), m.group(3)
        if clock() > expires:
            return None
        if not hmac.compare_digest(_sig(secret, user_id, expires, mode), sig):
            return None
        return "partial" if mode == "partial" else "full"
    except Exception:  # noqa: BLE001 - any oddity is "no ticket"
        return None
