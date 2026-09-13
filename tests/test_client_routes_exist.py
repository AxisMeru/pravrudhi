"""Every path the frontend client (`app/frontend/src/lib/api.ts`) asks the engine for must resolve to a
route the engine actually registers. `dispatchSubagents` once POSTed `/subagents/dispatch` while the engine
only ever registered POST `/subagents` -- a client call to a route that was never built, invisible to `tsc`
because the path is just a string, and shipped with no test covering the call. This walks every typed client
call in the file and checks its path shape (with `${...}` interpolations treated as a path parameter) against
the engine's own OpenAPI schema -- the same source of truth used to first establish this class of defect --
so the next one of these fails a test instead of a user's click."""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from pravrudhi.api.server import create_app

API_TS = Path(__file__).resolve().parents[1] / "app" / "frontend" / "src" / "lib" / "api.ts"

_METHOD_BY_HELPER = {"get": "GET", "post": "POST", "put": "PUT", "delete": "DELETE"}

# `postJSON<T>("/api/foo", ...)` / `getJSON<T>(\`/api/foo/${id}\`)` -- the four typed helpers every ordinary
# call in the file goes through.
_HELPER_CALL = re.compile(r"(get|post|put|delete)JSON<[^>]*>\(\s*[`\"]([^`\"]+)[`\"]")

# The few calls that bypass the helpers and hit `engineFetch` directly (e.g. the SSE stream), where the
# HTTP method sits in the same `{...}` init object as the path.
_DIRECT_CALL = re.compile(
    r"engineFetch\(`\$\{detectBase\(\)\}([^`]*)`,\s*\{([^}]*(?:\{[^}]*\}[^}]*)*)\}", re.DOTALL
)


def _client_calls(source: str) -> list[tuple[str, str]]:
    calls = [(_METHOD_BY_HELPER[verb], path) for verb, path in _HELPER_CALL.findall(source)]
    for path, init in _DIRECT_CALL.findall(source):
        if path == "${path}":
            # The `getJSON`/`postJSON`/`putJSON`/`deleteJSON` helper bodies themselves, forwarding
            # whatever path their caller passed in -- not a call site with a path of its own.
            continue
        m = re.search(r'method:\s*"(\w+)"', init)
        calls.append((m.group(1) if m else "GET", path))
    return calls


def _shape(path: str) -> tuple[str, ...]:
    """Segment-wise shape: a `${...}` interpolation (client) or a `{param}` placeholder (server's OpenAPI
    path) both collapse to the same wildcard, so the two compare equal regardless of the parameter name."""
    segments = path.strip("/").split("/")
    return tuple("*" if s.startswith("${") or (s.startswith("{") and s.endswith("}")) else s for s in segments)


def _registered_routes() -> set[tuple[str, tuple[str, ...]]]:
    """The engine's own claim about what it serves: `app.openapi()["paths"]`, not a hand-maintained list, so
    this stays true automatically as routes are added, renamed or removed."""
    with tempfile.TemporaryDirectory() as d:
        schema = create_app(Path(d)).openapi()
    return {
        (method.upper(), _shape(path))
        for path, methods in schema["paths"].items()
        for method in methods
        if method.upper() not in ("HEAD", "OPTIONS")
    }


def test_every_client_call_path_extracts_something() -> None:
    # A guard on the guard: if the file's call style changes and the regexes stop matching anything, the
    # test below would pass vacuously. It must always find the known-good calls this file has today.
    calls = _client_calls(API_TS.read_text())
    assert ("GET", "/api/objectives") in calls
    assert ("POST", "/api/objectives") in calls
    assert len(calls) > 30


def test_every_client_call_resolves_to_a_registered_route() -> None:
    registered = _registered_routes()
    calls = _client_calls(API_TS.read_text())
    assert calls, "no client calls were extracted -- see test_every_client_call_path_extracts_something"

    missing = sorted({(method, path) for method, path in calls if (method, _shape(path)) not in registered})
    assert missing == [], (
        f"client calls a route the engine's OpenAPI schema never registers: {missing}\n"
        "either the client's path is stale or the server route was renamed/removed -- check history "
        "before picking a side, per systematic-debugging."
    )
