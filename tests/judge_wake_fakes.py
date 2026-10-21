"""#1038: stdlib-only fake servers for the cold-start tests (reusable by Track A's scratch containers). Nothing here touches the
network beyond 127.0.0.1.

* `FakeHealthServer`: answers RunPod's `GET /v2/<endpoint id>/health` per endpoint id with configurable worker and job counts.
* `FakeWaker`: answers `POST /wake` the way the waker contract says: HMAC-SHA256 check over `v1|POST|/wake|ts|nonce|label|kind`
  (+ `|lease_ref`), a 60 s timestamp window, a nonce replay set, strict body shape; configurable ok/refuse/slow/garbage modes;
  every verified or rejected call is recorded in `calls` (the "no call" assertions read it)."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from pravrudhi.application.judge_wake import ROLES, sign

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


class _WakerHandler(BaseHTTPRequestHandler):
    def log_message(self, *a: object) -> None:
        pass

    def _reply(self, status: int, body: bytes = b"") -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        w: FakeWaker = self.server.owner  # type: ignore[attr-defined]
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.path != "/wake" or len(raw) > 512:
            w.calls.append(("bad_request", "", ""))
            return self._reply(400)
        try:
            b = json.loads(raw)
            ok_shape = (
                isinstance(b, dict)
                and set(b) - {"lease_ref"} == {"v", "kind", "label", "ts", "nonce"}
                and b["v"] == 1
                and b["kind"] in ("wake", "extend")
                and b["label"] in ROLES
                and isinstance(b["ts"], int)
                and isinstance(b["nonce"], str)
                and (b["kind"] == "extend") == ("lease_ref" in b)
            )
        except ValueError:
            ok_shape = False
        if not ok_shape:
            w.calls.append(("bad_request", "", ""))
            return self._reply(400)
        want = sign(w.secret, b["ts"], b["nonce"], b["label"], b["kind"], b.get("lease_ref"))
        got = (self.headers.get("X-Waker-Signature") or "").removeprefix("v1=")
        replay = b["nonce"] in w.nonces
        stale = abs(w.clock() - b["ts"]) > 60
        if not __import__("hmac").compare_digest(want, got) or replay or stale:
            w.calls.append(("unauthorised", b["label"], b["kind"]))
            return self._reply(401)
        w.nonces.add(b["nonce"])
        w.calls.append(("ok", b["label"], b["kind"]))
        if w.mode == "slow":
            time.sleep(w.delay_s)
        if w.mode == "garbage":
            return self._reply(200, b"not json")
        if w.mode == "server_error":
            return self._reply(503)
        if w.mode == "refuse":
            return self._reply(200, json.dumps({"v": 1, "state": "refused", "lease_ref": None}).encode())
        state = "already_awake" if b["label"] in w.awake else "granted"
        w.awake.add(b["label"])
        return self._reply(200, json.dumps({"v": 1, "state": state, "lease_ref": f"lease-{b['label']}"}).encode())


class FakeWaker(_Server):
    def __init__(self, secret: str = "test-secret-not-a-real-key", *, clock: Any = time.time) -> None:
        self.secret, self.clock = secret, clock
        self.mode = "ok"  # ok | refuse | slow | garbage | server_error
        self.delay_s = 0.5
        self.nonces: set[str] = set()
        self.awake: set[str] = set()
        self.calls: list[tuple[str, str, str]] = []  # (outcome, label, kind)
        super().__init__(_WakerHandler)

    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/wake"
