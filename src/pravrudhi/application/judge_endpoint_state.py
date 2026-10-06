"""Is a serverless judge endpoint parked? (#311)

The production judges are parked at min 0 / max 0 by operator directive. A request submitted to a parked endpoint
would sit queued and run on the NEXT warm, and the app would show a "warming up" counter that never ends. So the
engine asks the endpoint first, and a parked endpoint answers `judges_offline` at once, with nothing queued.

Two reads decide it, and both must agree before the engine says "offline":
  * RunPod `GET {origin}/v2/{endpoint}/health` (the inference key already used for the judge): the workers block
    (`ready`, `idle`, `running`, `initializing`), which is the endpoint's live state;
  * the endpoint's configured maximum: `workersMax` from RunPod's management API
    (`GET https://rest.runpod.io/v1/endpoints/{endpoint}`, needs a management key in `RUNPOD_MANAGEMENT_KEY`) or from
    the health body if it carries one. A scale-from-zero endpoint also shows no workers, so "no workers" alone is NOT
    "parked": only `max == 0` is.

Failure mode (decided, #311): the engine never claims "offline" it has not read. A health call that fails, times
out, returns something unparseable, a missing max, or a missing management key all give `unknown`, and `unknown`
PROCEEDS exactly as before this check existed (the existing `judge_unavailable` / `judges_warming` path then answers
if the judge really is down). The cost of the opposite choice, refusing on a failed probe, is a healthy judge
locked out by a flaky health call. A parked judge that the check could not read is therefore still queued, which
is today's behaviour, and the status says `unknown` so it is visible.

No statute text, no model call, no wake: `/health` and the management read never start a worker.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

Status = Literal["ready", "warming", "scaled_to_zero", "parked", "unknown"]
#: (url, headers) -> parsed JSON object; raises on any failure (network, HTTP error, bad JSON).
Fetch = Callable[[str, Mapping[str, str]], dict[str, Any]]

RUNPOD_HOST = "api.runpod.ai"
MANAGEMENT_URL = "https://rest.runpod.io/v1/endpoints/{endpoint}"
MANAGEMENT_KEY_ENV = "RUNPOD_MANAGEMENT_KEY"
TIMEOUT_S = 4.0


@dataclass(frozen=True)
class EndpointState:
    status: Status
    detail: str  # a short, secret-free reason for logs


def runpod_endpoint_id(base_url: str | None) -> str | None:
    """The serverless endpoint id in `https://api.runpod.ai/v2/<id>/openai/v1`, or None for any other URL
    (a local vLLM judge is never checked)."""
    if not base_url:
        return None
    u = urllib.parse.urlparse(base_url)
    parts = [p for p in u.path.split("/") if p]
    if u.hostname != RUNPOD_HOST or len(parts) < 2 or parts[0] != "v2":
        return None
    return parts[1]


def _int(v: Any) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None


def classify(health: Mapping[str, Any] | None, max_workers: int | None) -> EndpointState:
    """Pure: the endpoint's state from its health body and its configured max. Unreadable input is `unknown`."""
    workers = (health or {}).get("workers")
    if not isinstance(workers, Mapping):
        return EndpointState("unknown", "health carries no workers block")
    counts = {k: _int(workers.get(k)) for k in ("ready", "idle", "running", "initializing")}
    if any(v is None for k, v in counts.items() if k in workers):
        return EndpointState("unknown", "a worker count is not a non-negative integer")
    live = sum(v or 0 for k, v in counts.items() if k in ("ready", "idle", "running"))
    if live > 0:
        return EndpointState("ready", f"{live} worker(s) up")
    if (counts["initializing"] or 0) > 0:
        return EndpointState("warming", "a worker is initializing")
    if max_workers is None:
        return EndpointState("unknown", "no workers, and the endpoint's max is not readable")
    if max_workers == 0:
        return EndpointState("parked", "no workers and max 0")
    return EndpointState("scaled_to_zero", f"no workers, max {max_workers}")


def _http_fetch(url: str, headers: Mapping[str, str]) -> dict[str, Any]:
    req = urllib.request.Request(url, headers=dict(headers))
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:  # noqa: S310 (https URL built from a parsed RunPod endpoint id)
        body = json.loads(r.read().decode("utf-8"))
    if not isinstance(body, dict):
        raise ValueError("not a JSON object")
    return body


def endpoint_state(
    base_url: str, api_key: str | None, *, fetch: Fetch = _http_fetch, management_key: str | None = None
) -> EndpointState:
    """Read one endpoint. Never raises: any failure is `unknown`."""
    endpoint = runpod_endpoint_id(base_url)
    if endpoint is None:
        return EndpointState("unknown", "not a RunPod serverless endpoint")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        health = fetch(f"https://{RUNPOD_HOST}/v2/{endpoint}/health", headers)
    except (OSError, ValueError, urllib.error.URLError):
        return EndpointState("unknown", "health call failed")
    max_workers = _int(health.get("workersMax"))
    mkey = management_key if management_key is not None else os.environ.get(MANAGEMENT_KEY_ENV)
    if max_workers is None and mkey:
        try:
            cfg = fetch(MANAGEMENT_URL.format(endpoint=endpoint), {"Authorization": f"Bearer {mkey}"})
            max_workers = _int(cfg.get("workersMax"))
        except (OSError, ValueError, urllib.error.URLError):
            max_workers = None
    return classify(health, max_workers)


class JudgeOfflineCheck:
    """The judges the engine needs, checked together and cached for `ttl_s` (so a burst of requests is one health
    read per endpoint, and the health endpoint is not a load amplifier)."""

    def __init__(self, *, ttl_s: float = 5.0, fetch: Fetch = _http_fetch, clock: Callable[[], float] = time.monotonic):
        self.ttl_s, self._fetch, self._clock = ttl_s, fetch, clock
        self._cache: dict[str, tuple[float, EndpointState]] = {}

    def state(self, base_url: str, api_key: str | None) -> EndpointState:
        now = self._clock()
        hit = self._cache.get(base_url)
        if hit is not None and now - hit[0] <= self.ttl_s:
            return hit[1]
        st = endpoint_state(base_url, api_key, fetch=self._fetch)
        self._cache[base_url] = (now, st)
        return st

    def offline(self, judges: Mapping[str, Mapping[str, Any]], key_env: Mapping[str, str]) -> bool:
        """True only when a judge this deployment needs (primary, and the second judge when configured) is PARKED.
        Every other state, `unknown` included, is False."""
        for name, block in judges.items():
            url = str((block or {}).get("base_url") or "")
            if runpod_endpoint_id(url) is None:
                continue
            if self.state(url, os.environ.get(key_env.get(name, "")) or (block or {}).get("api_key")).status == "parked":
                return True
        return False
