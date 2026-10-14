"""The Anthropic Managed Agents HTTP client for the assisted route.

* PATH ALLOWLIST: only the session calls the route needs (create, send events, list events, the event stream, get, list
  by agent, delete). Anything else (a Files-API upload, a vault, a memory store, archive, an agents call)
  raises `TransportRefused` before any request is made: the client's facts go ONLY in the user message, so nothing that
  survives a session delete may be written.
* The `anthropic-workspace-id` header is REQUIRED (a client without it cannot be built) and is sent on every call; the
  API key never appears in an error, a repr or a record.
* `record(status)` is called after EVERY call: the HTTP status, or 599 for a timeout / connection failure. It is
  `assistant_tier.record_outcome` in production; an exception from it never breaks a call.
* No retries here: a retry could create a second session or post the message twice. The caller decides."""

from __future__ import annotations

import contextlib
import json
import re
from collections.abc import Callable, Iterator
from typing import Any

import httpx

BETA = "managed-agents-2026-04-01"
API = "https://api.anthropic.com"
_ID = r"[A-Za-z0-9_-]{1,64}"
ALLOWED: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (m, re.compile(p))
    for m, p in (
        ("POST", r"^/v1/sessions$"),
        ("GET", r"^/v1/sessions$"),
        ("GET", r"^/v1/models$"),
        ("GET", rf"^/v1/sessions/{_ID}$"),
        ("DELETE", rf"^/v1/sessions/{_ID}$"),
        ("POST", rf"^/v1/sessions/{_ID}/events$"),
        ("GET", rf"^/v1/sessions/{_ID}/events$"),
        ("GET", rf"^/v1/sessions/{_ID}/events/stream$"),
    )
)
MAX_PAGES = 200


class TransportRefused(RuntimeError):
    """A call outside the allowlist, or a client built without its key or workspace id: nothing was sent."""


class UpstreamError(RuntimeError):
    def __init__(self, status: int, detail: str = "") -> None:
        super().__init__(f"Anthropic API {status}" + (f": {detail}" if detail else ""))
        self.status = status


class EventStream:
    """The open SSE response. The connection is already open when this is returned (the caller posts the user message
    only afterwards, so no early event is missed)."""

    def __init__(self, response: httpx.Response) -> None:
        self._r = response

    def __iter__(self) -> Iterator[dict[str, Any]]:
        data: list[str] = []
        try:
            for raw in self._r.iter_lines():
                if raw.startswith("data:"):
                    data.append(raw[5:].lstrip())
                elif raw == "" and data:
                    yield json.loads("\n".join(data))
                    data = []
        finally:
            self.close()

    def close(self) -> None:
        self._r.close()


class AnthropicTransport:
    def __init__(
        self,
        api_key: str,
        workspace_id: str,
        *,
        client: httpx.Client | None = None,
        base: str = API,
        timeout_s: float = 60.0,
        record: Callable[[int], None] | None = None,
    ) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise TransportRefused("no API key")
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise TransportRefused("no workspace id: every call must carry the anthropic-workspace-id header")
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or not 0 < float(timeout_s) <= 600:
            raise TransportRefused("timeout_s must be in (0, 600]")
        self._key = api_key.strip()
        self._headers = {
            "x-api-key": self._key,
            "anthropic-version": "2023-06-01",
            "anthropic-beta": BETA,
            "anthropic-workspace-id": workspace_id.strip(),
            "content-type": "application/json",
        }
        self._base, self._timeout, self._record = base.rstrip("/"), float(timeout_s), record
        self._client = client if client is not None else httpx.Client(timeout=self._timeout)

    def __repr__(self) -> str:  # never the key
        return "AnthropicTransport(<redacted>)"

    # ---- plumbing ----------------------------------------
    def _check(self, method: str, path: str) -> None:
        if "?" in path or not any(m == method and rx.fullmatch(path) for m, rx in ALLOWED):
            raise TransportRefused(f"{method} {path} is not an allowlisted call")

    def note(self, status: int) -> None:
        """Report an HTTP status (or 599) the caller observed on a call this client does not itself send (the open stream)."""
        self._note(status)

    def _note(self, status: int) -> None:
        if self._record is not None:
            with contextlib.suppress(Exception):
                self._record(status)

    @staticmethod
    def _error_type(text: str) -> str:
        """Only the API's error.type token (a short identifier). The rest of an error body can echo what was sent, so it
        never reaches an exception message, a log or an alert."""
        try:
            kind = json.loads(text)["error"]["type"]
        except (ValueError, KeyError, TypeError, RecursionError):
            return ""
        return kind if isinstance(kind, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", kind) else ""

    def _send(
        self, method: str, path: str, *, params: dict[str, Any] | None = None, body: Any = None, stream: bool = False
    ) -> httpx.Response:
        self._check(method, path)
        request = self._client.build_request(
            method,
            self._base + path,
            params=params,
            headers=self._headers,
            content=None if body is None else json.dumps(body).encode(),
            timeout=self._timeout,
        )
        try:
            response = self._client.send(request, stream=stream)
        except httpx.HTTPError as exc:
            self._note(599)
            raise UpstreamError(599, type(exc).__name__) from None
        self._note(response.status_code)
        return response

    def _object(self, method: str, path: str, **kw: Any) -> dict[str, Any]:
        """A JSON answer that must be an object (a session); anything else is an upstream error, never a guess."""
        out = self._json(method, path, **kw)
        if not isinstance(out, dict):
            raise UpstreamError(200, "the answer is not a JSON object")
        return out

    def _json(self, method: str, path: str, **kw: Any) -> Any:
        r = self._send(method, path, **kw)
        if r.status_code >= 400:
            raise UpstreamError(r.status_code, self._error_type(r.text))
        try:
            return r.json() if r.content else {}
        except ValueError:
            raise UpstreamError(r.status_code, "the answer is not JSON") from None

    # ---- the calls ----------------------------------------
    def create_session(self, agent_id: str, version: int, environment_id: str, budget_cents: int) -> dict[str, Any]:
        if isinstance(budget_cents, bool) or not isinstance(budget_cents, int) or not 1 <= budget_cents <= 100:
            raise TransportRefused("budget_cents must be an integer in [1, 100]")
        if isinstance(version, bool) or not isinstance(version, int) or version < 1 or not agent_id or not environment_id:
            raise TransportRefused("the session needs a pinned agent id and version and an environment id")
        return self._object(
            "POST",
            "/v1/sessions",
            body={
                "agent": {"type": "agent", "id": agent_id, "version": version},
                "environment_id": environment_id,
                "budget": {"type": "limit", "max_list_cost": {"amount": str(budget_cents), "currency": "USD"}},
            },
        )

    def open_stream(self, session_id: str) -> EventStream:
        r = self._send("GET", f"/v1/sessions/{session_id}/events/stream", params={"beta": "true"}, stream=True)
        if r.status_code != 200:
            detail = self._error_type(r.read().decode(errors="replace"))
            r.close()
            raise UpstreamError(r.status_code, detail)
        return EventStream(r)

    def send_events(self, session_id: str, events: list[dict[str, Any]]) -> Any:
        return self._json("POST", f"/v1/sessions/{session_id}/events", body={"events": events})

    def interrupt(self, session_id: str) -> Any:
        return self.send_events(session_id, [{"type": "user.interrupt"}])

    def get_session(self, session_id: str) -> dict[str, Any]:
        return self._object("GET", f"/v1/sessions/{session_id}")

    def list_events(self, session_id: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        after: str | None = None
        for _ in range(MAX_PAGES):
            r = self._json("GET", f"/v1/sessions/{session_id}/events", params={"after_id": after} if after else None)
            out += r.get("data", [])
            if not r.get("has_more") or not r.get("data"):
                return out
            after = r["data"][-1]["id"]
        raise UpstreamError(599, "too many event pages")

    def delete_session(self, session_id: str) -> int:
        """The HTTP status of DELETE /v1/sessions/{id}: 200/204 = deleted, 404 = already gone, anything else the caller
        treats as a failed delete. Raises only on a transport failure (599)."""
        return self._send("DELETE", f"/v1/sessions/{session_id}").status_code

    def workspace_ok(self) -> bool:
        """True only when GET /v1/models answers 200 with this key and the configured workspace id. A key that is not valid in
        that workspace (or any failure, timeout or error) is False: fail closed. Nothing about the answer is returned."""
        try:
            return self._send("GET", "/v1/models", params={"limit": 1}).status_code == 200
        except UpstreamError:
            return False

    def list_sessions(self, agent_id: str, *, limit: int = 100) -> Iterator[dict[str, Any]]:
        """EVERY session of the given agent: walks every page until next_page is null."""
        if not agent_id or isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise TransportRefused("list_sessions needs an agent id and a limit in [1, 1000]")
        page: str | None = None
        for _ in range(MAX_PAGES):
            params: dict[str, Any] = {"agent_id": agent_id, "limit": limit}
            if page:
                params["page"] = page
            r = self._json("GET", "/v1/sessions", params=params)
            yield from r.get("data", [])
            page = r.get("next_page")
            if not page:
                return
        raise UpstreamError(599, "too many session pages")
