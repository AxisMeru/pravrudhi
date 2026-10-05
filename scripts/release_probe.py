#!/usr/bin/env python3
"""Read-mostly release probe for a deployed engine: who may reach which route.

Run it against a deployment after a release (or against a local engine) to check the operator-only gates
(#249, #246, #257) from the OUTSIDE, with real tokens, instead of trusting the tests. It hardcodes no hostname:
everything comes from the environment, and tokens are never printed (only status codes).

Environment
  PROBE_BASE_URL       engine base URL, e.g. https://engine.example.test (http only for loopback)
  PROBE_ADMIN_TOKEN    a bearer token for an administrator (PRAVRUDHI_ADMINS)
  PROBE_USER_TOKEN     a bearer token for a NON-admin test account (never a real user's token)
  PROBE_EDITION        product | studio (default product): a Studio refuses a non-admin on every /api route
  PROBE_WORKSPACE      the test account's workspace name (default release-probe)
  PROBE_IDENTITY_HEADER  header that carries the bearer token if the deployment moves it (default Authorization)
  PROBE_LOCAL_TOKEN    the engine's local write token, only if its local guard is on (default none)
  PROBE_TARGET_LABEL   a label for the report ("engine" or "worker"), printed with the target host
  PROBE_ALLOW_CLI_ASK  set to 1 to ALSO run the two /api/nyaya/ask probes. OFF by default: they need a VALID body, so
                       on an engine missing the gate they would run a host CLI vendor and write an ask record. The
                       release covers that gate with a container-side check; opt in only against a disposable engine.

Target: say which one you probed. Prefer the ENGINE origin (the RunPod engine or the Studio engine itself): through the
product Worker the interim write block answers 403 for /api/runs and /api/update before the engine ever sees the
request, which masks a missing engine gate. The release probes both, the engine origin and the Worker, with a
different PROBE_BASE_URL each time; the report prints the target host and label so the two runs are not confused.

Safety: every state-changing probe is sent with the NON-admin (or no) token and a deliberately INVALID body or a
guaranteed non-existent object, so a gate that is missing answers 4xx and nothing is started or written. The
administrator token is only ever sent to the read-only /api/me, and no token or response body is ever printed (the
`access` word in /api/me is read to prove the admin token really is an admin, and is not printed). Probes name no
workspace, so a missing gate answers "name a workspace" (400) and creates no workspace scaffold; the opt-in ask
probes do use PROBE_WORKSPACE, and a missing gate there creates that test account's (empty) workspace, so point it
at an existing test workspace.

Exit: 0 every check passed, 1 a check failed, 2 configuration error, the engine unreachable, or any unexpected error
(reported by type only, no traceback).
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import dataclass

Send = Callable[[str, str, "str | None", "dict | None", bool], "tuple[int, dict | None]"]
# (method, path, token, json body, want_json) -> (HTTP status, parsed JSON body only when want_json)
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
INVALID_RUN = {"target": "rocket"}
INVALID_UPDATE = {"channel": "__release_probe_invalid__", "auto_apply": "maybe"}
ASK_CLI = {"question": "release probe", "vendors": ["claude-cli", "codex-cli"]}


@dataclass(frozen=True)
class Check:
    name: str
    method: str
    path: str
    who: str  # "admin" | "user" | "anonymous"
    expect: tuple[int, ...]
    body: dict | None = None
    access: str | None = None  # "admin" or "not-admin": also require /api/me's `access` word to be (or not be) admin
    opt_in: bool = False  # only run with PROBE_ALLOW_CLI_ASK=1


def checks(edition: str, workspace: str) -> list[Check]:
    ws = f"?workspace={urllib.parse.quote(workspace)}"
    ghost = f"release-probe-nonexistent-{uuid.uuid4().hex}"  # no such objective can exist: a missing gate gives 404
    out = [
        Check("admin token is really an admin (/api/me)", "GET", "/api/me", "admin", (200,), access="admin"),
        Check("anonymous /api/me is refused", "GET", "/api/me", "anonymous", (401,)),
        Check("anonymous POST /api/runs is refused", "POST", "/api/runs", "anonymous", (401,), INVALID_RUN),
        Check("non-admin POST /api/runs is 403", "POST", "/api/runs", "user", (403,), INVALID_RUN),
        Check("non-admin GET /api/runs is 403", "GET", "/api/runs", "user", (403,)),
        Check("non-admin GET /api/app-token is 403", "GET", "/api/app-token", "user", (403,)),
        Check("non-admin PUT /api/update/config is 403", "PUT", "/api/update/config", "user", (403,), INVALID_UPDATE),
        Check("non-admin POST /api/update/apply is 403", "POST", "/api/update/apply", "user", (403,), {"channel": "x"}),
        Check("non-admin dispatch of a plan is 403", "POST", f"/api/objectives/{ghost}/subagents", "user", (403,)),
        Check("non-admin POST /api/nyaya/ask (cli vendors) is 403", "POST", f"/api/nyaya/ask{ws}", "user", (403,), ASK_CLI,
              opt_in=True),
        Check("anonymous POST /api/nyaya/ask is refused", "POST", "/api/nyaya/ask", "anonymous", (401, 403), ASK_CLI,
              opt_in=True),
    ]
    if edition == "studio":
        out.append(Check("Studio refuses a non-admin on /api/me", "GET", "/api/me", "user", (403,)))
    else:
        out.append(Check("product serves a non-admin /api/me, not as admin", "GET", "/api/me", "user", (200,),
                         access="not-admin"))
    return out


def run(
    send: Send, edition: str, workspace: str, tokens: dict[str, str | None], allow_cli_ask: bool = False
) -> list[tuple[Check, int | None, bool]]:
    """`(check, status, passed)`; a skipped opt-in check has status None and passes vacuously (reported as SKIP)."""
    results: list[tuple[Check, int | None, bool]] = []
    for c in checks(edition, workspace):
        if c.opt_in and not allow_cli_ask:
            results.append((c, None, True))
            continue
        status, body = send(c.method, c.path, tokens.get(c.who), c.body, c.access is not None)
        ok = status in c.expect
        if ok and c.access is not None:
            word = body.get("access") if isinstance(body, dict) else None
            # An empty or unparseable body proves nothing: only a real, non-empty access word can pass either check.
            ok = word == "admin" if c.access == "admin" else (isinstance(word, str) and bool(word) and word != "admin")
        results.append((c, status, ok))
    return results


def http_sender(base: str, identity_header: str, local_token: str | None) -> Send:
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):  # type: ignore[no-untyped-def]
            return None

    opener = urllib.request.build_opener(NoRedirect)

    def send(method: str, path: str, token: str | None, body: dict | None, want_json: bool) -> tuple[int, dict | None]:
        headers = {"content-type": "application/json", "user-agent": "pravrudhi-release-probe"}
        if token:
            headers[identity_header] = f"Bearer {token}"
        if local_token:
            headers["x-pravrudhi-token"] = local_token
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
        try:
            with opener.open(req, timeout=30) as r:  # noqa: S310 -- operator-supplied https/loopback URL
                status = int(r.status)
                raw = r.read(65536) if want_json else b""
        except urllib.error.HTTPError as e:
            return int(e.code), None
        try:
            parsed = json.loads(raw) if raw else None
        except ValueError:
            parsed = None
        return status, parsed if isinstance(parsed, dict) else None

    return send


def config_error(message: str) -> int:
    print(f"CONFIG ERROR: {message}", file=sys.stderr)
    return 2


def main(env: dict[str, str] | None = None, send: Send | None = None) -> int:
    env = dict(os.environ) if env is None else env
    base = env.get("PROBE_BASE_URL", "").strip().rstrip("/")
    admin, user = env.get("PROBE_ADMIN_TOKEN", "").strip(), env.get("PROBE_USER_TOKEN", "").strip()
    edition = env.get("PROBE_EDITION", "product").strip().lower()
    if not base or not admin or not user:
        return config_error("set PROBE_BASE_URL, PROBE_ADMIN_TOKEN and PROBE_USER_TOKEN")
    if edition not in ("product", "studio"):
        return config_error("PROBE_EDITION must be product or studio")
    if admin == user:
        return config_error("the admin and the non-admin token are the same: that proves nothing")
    try:
        parsed = urllib.parse.urlparse(base)
        port = parsed.port  # raises ValueError on a malformed port
        hostname = parsed.hostname or ""
        has_credentials = parsed.username is not None or parsed.password is not None
    except ValueError:
        return config_error("PROBE_BASE_URL is not a valid URL")
    if not hostname:
        return config_error("PROBE_BASE_URL has no host")
    if has_credentials:
        return config_error("PROBE_BASE_URL must not carry credentials (user:password@); pass tokens by environment")
    if parsed.scheme != "https" and (parsed.scheme != "http" or hostname not in LOOPBACK):
        return config_error("PROBE_BASE_URL must be https (http only for loopback)")
    transport = send or http_sender(base, env.get("PROBE_IDENTITY_HEADER", "authorization").strip().lower(),
                                    env.get("PROBE_LOCAL_TOKEN", "").strip() or None)
    label = env.get("PROBE_TARGET_LABEL", "").strip()  # fail-open-ok: a display label only, not a measurement
    shown = f"[{hostname}]" if ":" in hostname else hostname  # an IPv6 literal is bracketed
    host = shown + (f":{port}" if port else "")  # never netloc: it can carry userinfo
    print(f"target: {parsed.scheme}://{host}" + (f" ({label})" if label else "") + f", edition {edition}")
    try:
        results = run(transport, edition, env.get("PROBE_WORKSPACE", "release-probe"),
                      {"admin": admin, "user": user, "anonymous": None}, env.get("PROBE_ALLOW_CLI_ASK") == "1")
    except urllib.error.URLError as e:
        return config_error(f"the engine could not be reached ({type(e).__name__})")
    except Exception as e:  # noqa: BLE001 -- never a traceback (it could carry a header); the type alone is reported
        return config_error(f"the probe failed unexpectedly ({type(e).__name__})")
    width = max(len(c.name) for c, _s, _ok in results)
    for c, status, ok in results:
        want = "/".join(map(str, c.expect))
        verdict = "SKIP" if status is None else ("PASS" if ok else "FAIL")
        got = "opt-in (PROBE_ALLOW_CLI_ASK=1)" if status is None else f"got {status}, want {want}"
        print(f"{verdict}  {c.name:<{width}}  {c.method} {c.path.split('?')[0]}  {got}")
    failed = [c.name for c, _s, ok in results if not ok]
    ran = sum(1 for _c, s_, _ok in results if s_ is not None)
    tail = f"; FAILED: {', '.join(failed)}" if failed else ""
    print(f"\n{ran - len(failed)}/{ran} passed ({len(results) - ran} skipped){tail}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
