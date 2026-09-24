"""A non-blocking warm-up ping for the house judge (2026-09-24, Lead-2's production test): on a serverless
deployment with no warm workers, the engine's own cold start (~24s) and the judge's (~200s) otherwise chain
serially -- the engine answers `/ping` quickly, but the FIRST real request still has to wait out the judge's
full cold start on top. Firing a real `GET /models` at the judge as soon as the engine itself starts lets the
two cold starts overlap instead.

This is a best-effort optimization, never a dependency: `warm_up_house_judge` never raises (a failed or slow
warm-up just means the first real request pays the full cold-start cost, exactly like before this existed),
and the key is never included in any exception message or log line this module produces.
"""

from __future__ import annotations

import contextlib
import threading
import urllib.request
from collections.abc import Callable, Mapping

#: The real transport: a plain GET with an optional bearer header, timing out after `timeout_s`. Injectable
#: for tests -- production code never has to construct a real urllib.request.Request to exercise this.
_Getter = Callable[..., None]


def _urllib_get(url: str, *, headers: Mapping[str, str], timeout_s: float) -> None:
    req = urllib.request.Request(url, headers=dict(headers))
    with urllib.request.urlopen(req, timeout=timeout_s):  # noqa: S310 -- an http(s) URL from our own config
        pass


def warm_up_house_judge(
    *,
    base_url: str | None,
    api_key: str | None,
    timeout_s: float,
    get: _Getter = _urllib_get,
) -> None:
    """One synchronous `GET {base_url}/models`, with `Authorization: Bearer <api_key>` when a key is
    configured. A no-op when no `base_url` is configured at all (nothing to warm). Any failure -- a timeout,
    a connection error, an HTTP error status -- is swallowed here: this function never raises, and the
    exception itself (never re-raised, but also never logged with its own text, which could echo request
    headers back) is discarded rather than reported, so the key cannot leak through a log line this module
    writes."""
    if not base_url:
        return
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    # Best-effort warm-up; a slow/failed judge just means no head start today, not a reason to raise.
    with contextlib.suppress(Exception):
        get(f"{base_url.rstrip('/')}/models", headers=headers, timeout_s=timeout_s)


def start_house_judge_warmup(
    *,
    base_url: str | None,
    api_key: str | None,
    timeout_s: float,
    get: _Getter = _urllib_get,
) -> None:
    """Fires `warm_up_house_judge` on a background daemon thread and returns immediately -- never blocks
    engine startup, whatever the judge does or how long it takes to answer."""
    threading.Thread(
        target=warm_up_house_judge, kwargs={"base_url": base_url, "api_key": api_key, "timeout_s": timeout_s, "get": get},
        daemon=True,
    ).start()
