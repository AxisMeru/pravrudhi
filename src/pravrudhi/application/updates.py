"""Whether this checkout is behind the newest tagged release, and the exact command that would catch it up.

The library, the web interface, and the desktop shell had no way to tell an operator a newer version exists.
This module answers that question over the network (never raising, since the operator may be offline or
rate-limited) but performs no update itself: an engine that can start GPU work must not replace its own code
without the operator saying so.
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pravrudhi import KERNEL_VERSION, __version__

PACKAGE_NAME = "pravrudhi"
REPO = "AxisMeru/pravrudhi"
RELEASES_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
FETCH_TIMEOUT_S = 5.0

FetchFn = Callable[[str, float], Any]


def _package_dir() -> Path:
    return Path(__file__).resolve().parent


def _run_git(*args: str) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            ["git", *args], cwd=_package_dir(), capture_output=True, text=True, timeout=FETCH_TIMEOUT_S
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _git_describe() -> str | None:
    result = _run_git("describe", "--tags", "--always", "--dirty")
    if result is None or result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _is_git_checkout() -> bool:
    result = _run_git("rev-parse", "--is-inside-work-tree")
    return result is not None and result.returncode == 0 and result.stdout.strip() == "true"


def current() -> dict[str, Any]:
    """The version this checkout is actually running, plus its git describe when the checkout is a work tree."""
    out: dict[str, Any] = {"version": __version__, "kernel_version": KERNEL_VERSION}
    describe = _git_describe()
    if describe is not None:
        out["git_describe"] = describe
    return out


def _github_token() -> str:
    """A token for the GitHub API, if this machine has one.

    Unauthenticated GitHub allows sixty requests an hour per address, and this machine spends that from three
    places at once: the engine's hourly release check, each desktop's update check, and whatever else is on the
    box. The budget ran out while cutting 0.4.0, and the engine then reported "no update available" for a
    release that existed — the failure `checked` now distinguishes, and this is the reason it stops happening.

    Read from the environment so no credential lives in the repository, and its absence changes nothing: the
    request goes out anonymously exactly as before.
    """
    for name in ("PRAVRUDHI_GITHUB_TOKEN", "GITHUB_TOKEN"):
        token = os.environ.get(name, "").strip()
        if token:
            return token
    return ""


_GITHUB_HOSTS = ("api.github.com", "github.com", "uploads.github.com")


class _StripAuthOnRedirect(urllib.request.HTTPRedirectHandler):
    """Drop the credential the moment a redirect leaves GitHub.

    urllib copies headers onto the redirected request, and a release asset's URL always redirects to a CDN
    host — so a request that carries a token and follows redirects hands that token to whatever it lands on.
    Not theoretical: fetching a CI log in this project redirected to Azure blob storage with the header
    attached, which is how it was noticed.

    Only https on a known GitHub host keeps the credential. A lookalike like `api.github.com.evil.test` ends
    with the right characters and is a different host, so the check is on the parsed hostname and not on how
    the string reads.
    """

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is None:
            return None
        parsed = urllib.parse.urlparse(newurl)
        if parsed.scheme != "https" or parsed.hostname not in _GITHUB_HOSTS:
            for name in list(new.headers):
                if name.lower() == "authorization":
                    del new.headers[name]
            new.unredirected_hdrs.pop("Authorization", None)
        return new


def github_opener() -> urllib.request.OpenerDirector:
    """An opener that will not carry a credential off GitHub."""
    return urllib.request.build_opener(_StripAuthOnRedirect())


def _default_fetch(url: str, timeout: float) -> Any:
    headers = {"Accept": "application/vnd.github+json"}
    token = _github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with github_opener().open(request, timeout=timeout) as response:  # noqa: S310 (fixed GitHub API host)
        return json.loads(response.read().decode("utf-8"))


def latest(fetch: FetchFn | None = None) -> dict[str, Any] | None:
    """The newest tagged GitHub release, or None if the request fails for any reason - offline is not an error.

    `fetch` is injectable so tests never touch the network; the default calls the GitHub releases API with a
    5s timeout. The broad except is deliberate: an injected fetch, a rate-limited API, or a malformed payload
    must all degrade to None, never propagate.
    """
    fetch = fetch or _default_fetch
    try:
        payload = fetch(RELEASES_URL, FETCH_TIMEOUT_S)
    except Exception:  # noqa: BLE001 (any failure here means "couldn't check", not a crash)
        return None
    if not isinstance(payload, dict):
        return None
    tag = payload.get("tag_name")
    if not isinstance(tag, str) or not tag:
        return None
    url = payload.get("html_url")
    return {"tag": tag, "url": url if isinstance(url, str) else ""}


def _parse_version(text: str) -> tuple[int, ...] | None:
    stripped = text[1:] if text.startswith("v") else text
    parts = stripped.split(".")
    try:
        return tuple(int(part) for part in parts)
    except ValueError:
        return None


def _normalize(text: str) -> str:
    return text[1:] if text.startswith("v") else text


def _is_newer(latest_tag: str, current_version: str) -> bool:
    latest_parsed = _parse_version(latest_tag)
    current_parsed = _parse_version(current_version)
    if latest_parsed is not None and current_parsed is not None:
        return latest_parsed > current_parsed
    return _normalize(latest_tag) != _normalize(current_version)


def _how() -> str:
    if _is_git_checkout():
        return "git pull && uv sync"
    return f"pip install --upgrade {PACKAGE_NAME}"


def status(fetch: FetchFn | None = None) -> dict[str, Any]:
    """current, latest, whether an update is available, whether the check ran, and the command to run.

    `checked` exists because `update_available: false` answered two different questions: "GitHub says nothing is
    newer" and "GitHub could not be reached". A rate-limited or offline machine therefore reported itself
    current, which for an unattended updater is the worst shape of failure — it stops updating and says
    everything is fine. Observed while cutting a release from a rate-limited address: the engine answered "no
    update available" for a release that existed.

    `update_available` stays false when the check did not run. Not knowing is never a reason to offer an
    update; it is a reason to say so.
    """
    cur = current()
    lat = latest(fetch)
    update_available = lat is not None and _is_newer(lat["tag"], cur["version"])
    return {
        "current": cur, "latest": lat, "update_available": update_available,
        "checked": lat is not None, "how": _how(),
    }


def doctor_check() -> dict[str, Any]:
    """A doctor entry for update status. Never fails the run: a stale checkout is worth flagging, not a fault."""
    st = status()
    if st["latest"] is None:
        detail = f"Running {st['current']['version']}; could not reach GitHub to check for a newer release."
    elif st["update_available"]:
        detail = f"A newer release is available ({st['latest']['tag']}). Run: {st['how']}"
    else:
        detail = f"Running the latest release ({st['current']['version']})."
    return {"name": "update_check", "ok": True, "detail": detail}
