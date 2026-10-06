"""Is a serverless judge endpoint parked? (#311)

The production judges are parked at min 0 / max 0 by operator directive. A request submitted to a parked endpoint
would sit queued and run on the NEXT warm, and the app would show a "warming up" counter that never ends. So the
engine asks the endpoint first, and a parked endpoint answers `judges_offline` at once, with nothing queued.

Detection (Lead-2's option (b), 6 Oct): RunPod `GET https://api.runpod.ai/v2/{endpoint}/health`, with the inference key
the judge already uses, and nothing else (no management key, no new credential). PARKED means: no worker is ready,
idle, running, initializing, throttled or unhealthy, AND nothing is queued or in progress. Throttled (waiting for capacity)
and unhealthy (being replaced) workers mean the endpoint is trying to serve, so they read as warming and the call proceeds.
The same `/v2/<id>/health` is read for a queue endpoint (`api.runpod.ai/v2/<id>/...`) and for a load-balancer endpoint
(`<id>.api.runpod.ai/...`; its container-side `/health` is not read).

Known limit: an endpoint that is scaled to zero but ALLOWED to scale (min 0, max above 0) looks exactly the same on
/health when it is idle, so it too reads as parked. That is accepted while the judges are deliberately parked at
min 0 / max 0; if a judge is ever put back on scale-from-zero, this check must be turned off
(`judge_health_check: false`) or replaced by a read of the endpoint's configured max (deferred option (a)).

Warm window: `judge_health_check` is true in the shipped partner_api.yaml, so an authorised warm of a parked judge
sets min >= 1 (a worker is then up and the check reads `ready`) or turns the check off for that window, then waits
about 5 s (`judge_health_ttl_s`) for the cached reading to expire.

Failure mode (decided, #311): the engine never claims "offline" it has not read. A health call that fails, times out,
returns something unparseable, or lacks the workers or jobs counts all give `unknown`, and `unknown` PROCEEDS exactly
as before this check existed (the existing `judge_unavailable` / `judges_warming` path then answers if the judge
really is down). Refusing on a failed probe would lock a healthy judge out on a flaky health call.

No statute text, no model call, no wake: `/health` never starts a worker.
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

Status = Literal["ready", "warming", "parked", "unknown"]
#: (url, headers) -> parsed JSON object; raises on any failure (network, HTTP error, bad JSON).
Fetch = Callable[[str, Mapping[str, str]], dict[str, Any]]

RUNPOD_HOST = "api.runpod.ai"
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
    host = u.hostname or ""
    parts = [p for p in u.path.split("/") if p]
    if host == RUNPOD_HOST and len(parts) >= 2 and parts[0] == "v2":
        return parts[1]
    # A load-balancer endpoint is addressed as https://<id>.api.runpod.ai/...; its QUEUE statistics are still RunPod's
    # `/v2/<id>/health` (the worker-side `https://<id>.api.runpod.ai/health` is the container's own route, not read).
    sub_id, _, rest = host.partition(".")
    if rest == RUNPOD_HOST and sub_id:
        return sub_id
    return None


def _int(v: Any) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None


WORKER_COUNTS = ("ready", "idle", "running", "initializing", "throttled", "unhealthy")
JOB_COUNTS = ("inQueue", "inProgress")


def classify(health: Mapping[str, Any] | None) -> EndpointState:
    """Pure: the endpoint's state from its /health body alone.

    Every worker count and both job counts must be PRESENT as non-negative integers, else the answer is `unknown`
    (an empty body or a renamed field must never read as "everything is zero, so parked"). PARKED needs all of them
    zero; any other nonzero count (throttled and unhealthy included) means the endpoint is up or trying to be, so
    the call proceeds."""
    workers = (health or {}).get("workers")
    jobs = (health or {}).get("jobs")
    if not isinstance(workers, Mapping) or not isinstance(jobs, Mapping):
        return EndpointState("unknown", "health lacks a workers or jobs block")
    w = {k: _int(workers.get(k)) if k in workers else None for k in WORKER_COUNTS}
    q = {k: _int(jobs.get(k)) if k in jobs else None for k in JOB_COUNTS}
    if any(v is None for v in (*w.values(), *q.values())):
        return EndpointState("unknown", "a worker or job count is missing or not a non-negative integer")
    up = (w["ready"] or 0) + (w["idle"] or 0) + (w["running"] or 0)
    if up > 0:
        return EndpointState("ready", f"{up} worker(s) up")
    if any((v or 0) > 0 for v in (*w.values(), *q.values())):
        return EndpointState("warming", "a worker is initializing, throttled or unhealthy, or work is queued or in progress")
    return EndpointState("parked", "no worker, nothing queued or in progress")


def _http_fetch(url: str, headers: Mapping[str, str]) -> dict[str, Any]:
    req = urllib.request.Request(url, headers=dict(headers))
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:  # noqa: S310 (https URL built from a parsed RunPod endpoint id)
        body = json.loads(r.read().decode("utf-8"))
    if not isinstance(body, dict):
        raise ValueError("not a JSON object")
    return body


def endpoint_state(base_url: str, api_key: str | None, *, fetch: Fetch = _http_fetch) -> EndpointState:
    """Read one endpoint. Never raises: any failure is `unknown`."""
    endpoint = runpod_endpoint_id(base_url)
    if endpoint is None:
        return EndpointState("unknown", "not a RunPod serverless endpoint")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        health = fetch(f"https://{RUNPOD_HOST}/v2/{endpoint}/health", headers)
    except (OSError, ValueError, urllib.error.URLError):
        return EndpointState("unknown", "health call failed")
    return classify(health)


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
