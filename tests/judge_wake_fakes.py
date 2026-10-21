"""#1038: stdlib-only fake servers for the cold-start tests (reusable by Track A's scratch containers). Nothing here touches the
network beyond 127.0.0.1.

* `FakeHealthServer`: answers RunPod's `GET /v2/<endpoint id>/health` per endpoint id with configurable worker and job counts.
* `box_poll_headers` / `box_result_request`: what the box-side waker sends (a signed GET poll and a signed result delivery),
  for tests of the engine's `/internal/wake-requests` and `/internal/wake-results` routes (PR-3) and for Track A's containers."""

from __future__ import annotations

import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from pravrudhi.application.judge_wake import sign_http

_PARKED = {"idle": 0, "initializing": 0, "ready": 0, "running": 0, "throttled": 0, "unhealthy": 0}
_QUIET = {"inQueue": 0, "inProgress": 0}


class _Server:
    def __init__(self, handler: type[BaseHTTPRequestHandler]) -> None:
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.httpd.owner = self  # type: ignore[attr-defined]
        self._t = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._t.start()

    @property
    def port(self) -> int:
        return int(self.httpd.server_address[1])

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def __enter__(self) -> _Server:
        return self

    def __exit__(self, *a: object) -> None:
        self.close()


class _HealthHandler(BaseHTTPRequestHandler):
    def log_message(self, *a: object) -> None:  # silent
        pass

    def do_GET(self) -> None:  # noqa: N802
        owner: FakeHealthServer = self.server.owner  # type: ignore[attr-defined]
        parts = [p for p in self.path.split("/") if p]
        if len(parts) == 3 and parts[0] == "v2" and parts[2] == "health":
            owner.requests.append(parts[1])
            body = owner.bodies.get(parts[1])
            if body is not None:
                raw = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(raw)
                return
        self.send_response(404)
        self.end_headers()


class FakeHealthServer(_Server):
    def __init__(self) -> None:
        self.bodies: dict[str, dict[str, Any]] = {}
        self.requests: list[str] = []
        super().__init__(_HealthHandler)

    def set(self, endpoint: str, *, workers: dict[str, int] | None = None, jobs: dict[str, int] | None = None) -> None:
        self.bodies[endpoint] = {"workers": {**_PARKED, **(workers or {})}, "jobs": {**_QUIET, **(jobs or {})}}

    def url(self, endpoint: str) -> str:
        return f"http://127.0.0.1:{self.port}/v2/{endpoint}/health"


POLL_PATH = "/internal/wake-requests"
RESULT_PATH = "/internal/wake-results"


def box_poll_headers(secret: str, *, ts: int | None = None, nonce: str | None = None) -> dict[str, str]:
    """Headers of the waker's signed GET poll of the engine."""
    t, n = int(time.time()) if ts is None else ts, nonce or secrets.token_hex(16)
    sig = sign_http(secret, "GET", POLL_PATH, t, n)
    return {"X-Waker-Timestamp": str(t), "X-Waker-Nonce": n, "X-Waker-Signature": "v1=" + sig}


def box_result_request(
    secret: str, results: list[dict[str, object]], *, ts: int | None = None, nonce: str | None = None
) -> tuple[bytes, dict[str, str]]:
    """Body and headers of the waker's signed POST of its results."""
    body = json.dumps({"v": 1, "results": results}, separators=(",", ":")).encode()
    t, n = int(time.time()) if ts is None else ts, nonce or secrets.token_hex(16)
    sig = sign_http(secret, "POST", RESULT_PATH, t, n, body)
    headers = {"X-Waker-Timestamp": str(t), "X-Waker-Nonce": n, "X-Waker-Signature": "v1=" + sig}
    return body, {**headers, "Content-Type": "application/json"}
