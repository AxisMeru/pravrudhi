#!/usr/bin/env python3
"""Partner-API smoke for a deployed engine: anonymous demo paths and a NON-ADMIN partner key only.

Run it after a release or a batched deploy against the engine origin (or the product Worker). It never takes an
admin token, a password or a provisioning secret, and prints no secret: the target host, one line per check with
the HTTP status, and a summary. Every request is read-only or deliberately INVALID (so nothing is judged,
metered as a success or written); the one request that does run the judge is opt-in.

Environment
  SMOKE_BASE_URL      engine origin only, e.g. https://engine.example.test (http only for loopback)
  SMOKE_API_KEY       optional: the e2e NON-ADMIN test organisation's partner key (X-Pravrudhi-Api-Key). Never a real
                      customer's key and never an operator credential. Without it, the key checks are skipped.
  SMOKE_TARGET_LABEL  a label for the report ("engine" or "worker")
  SMOKE_RUN_ANALYSIS  set to 1 to ALSO send one toy analyse-facts request (anonymously, and with the key if given).
                      It runs the judge and, with a key, writes one usage and audit row: use the e2e account only.

Exit: 0 every check passed, 1 a check failed, 2 configuration error, the engine unreachable, or any unexpected
error (reported by type only, no traceback), 3 nothing key-authenticated ran (INCOMPLETE; 1 wins over 3).
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

Send = Callable[[str, str, "dict[str, str]", "dict | None"], "tuple[int, dict[str, str], dict | None]"]
# (method, path, extra headers, json body) -> (status, lower-cased response headers, parsed JSON object or None)
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
KEY_HEADER = "X-Pravrudhi-Api-Key"
OUTCOMES = {"PROOF", "DENIAL", "ABSTAIN", "REFER_TO_LAWYER"}
EMPTY_FACT = {"facts": ["   "], "narrative": "", "contract_ids": ["bns69"]}  # refused 422 before any judging
TOY = {
    "facts": [
        "TOY: Kiran was engaged to Lata and told her he would marry her in the spring.",
        "TOY: Kiran had already decided never to marry Lata when he made that promise.",
        "TOY: Relying on the promise, Lata had sexual intercourse with Kiran.",
    ],
    "narrative": "TOY narrative.",
    "contract_ids": ["bns69"],
}


@dataclass(frozen=True)
class Check:
    name: str
    method: str
    path: str
    who: str  # "anonymous" | "badkey" | "key"
    expect: tuple[int, ...]
    body: dict | None = None
    verify: Callable[[dict[str, str], dict | None], bool] | None = None
    opt_in: bool = False


def _status_ok(_h: dict[str, str], b: dict | None) -> bool:
    return isinstance(b, dict) and isinstance(b.get("engine_version"), str) and bool(b["engine_version"])


def _analysis_ok(_h: dict[str, str], b: dict | None) -> bool:
    if not isinstance(b, dict) or not b.get("retention_notice") or not isinstance(b.get("standard"), dict):
        return False
    contracts = b.get("contracts")
    return isinstance(contracts, list) and bool(contracts) and all(c.get("outcome") in OUTCOMES for c in contracts)


def _audit_ok(_h: dict[str, str], b: dict | None) -> bool:
    return isinstance(b, dict) and isinstance(b.get("rows"), list)


def checks() -> list[Check]:
    ghost = "smoke-nonexistent-job"
    return [
        Check("status is served without a key", "GET", "/api/v1/status", "anonymous", (200,), verify=_status_ok),
        Check("invalid key is 401, never anonymous", "POST", "/api/v1/analyse-facts", "badkey", (401,), EMPTY_FACT),
        Check("anonymous empty fact is refused", "POST", "/api/v1/analyse-facts", "anonymous", (401, 422), EMPTY_FACT),
        Check("anonymous cannot read the audit log", "GET", "/api/v1/audit", "anonymous", (401,)),
        Check("anonymous cannot submit a job", "POST", "/api/v1/analyse-facts/jobs", "anonymous", (401,), EMPTY_FACT),
        Check("anonymous cannot read an org usage summary", "GET", "/api/v1/orgs/smoke/usage/summary", "anonymous",
              (401, 403, 429)),
        Check("key: empty fact is 422", "POST", "/api/v1/analyse-facts", "key", (422,), EMPTY_FACT),
        Check("key: audit log is readable", "GET", "/api/v1/audit?limit=1", "key", (200,), verify=_audit_ok),
        Check("key: an unknown job is 404", "GET", f"/api/v1/analyse-facts/jobs/{ghost}", "key", (404,)),
        Check("key: the operator usage summary is refused", "GET", "/api/v1/orgs/smoke/usage/summary", "key",
              (401, 403, 429)),
        Check("anonymous toy analysis (demo path)", "POST", "/api/v1/analyse-facts", "anonymous", (200, 401, 429),
              TOY, opt_in=True),
        Check("key: toy analysis answers with reply fields and rate-limit headers", "POST", "/api/v1/analyse-facts",
              "key", (200,), TOY, verify=lambda h, b: _analysis_ok(h, b) and "x-ratelimit-limit" in h, opt_in=True),
    ]


def run(
    send: Send, key: str | None, run_analysis: bool = False
) -> list[tuple[Check, int | None, bool, str]]:
    """`(check, status, passed, note)`; a skipped check has status None and passes vacuously (reported SKIP)."""
    out: list[tuple[Check, int | None, bool, str]] = []
    for c in checks():
        if c.opt_in and not run_analysis:
            out.append((c, None, True, "opt-in (SMOKE_RUN_ANALYSIS=1)"))
            continue
        if c.who == "key" and not key:
            out.append((c, None, True, "needs SMOKE_API_KEY"))
            continue
        headers = {KEY_HEADER: key or ""} if c.who == "key" else {KEY_HEADER: "smoke-not-a-real-key"} if c.who == "badkey" else {}
        status, resp_headers, body = send(c.method, c.path, headers, c.body)
        ok = status in c.expect
        note = f"got {status}, want {'/'.join(map(str, c.expect))}"
        if ok and c.verify is not None and status == 200 and not c.verify(resp_headers, body):
            ok, note = False, f"got {status} but the body or headers were not the documented shape"
        out.append((c, status, ok, note))
    return out


def http_sender(base: str) -> Send:
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a: Any, **k: Any) -> None:
            return None

    opener = urllib.request.build_opener(NoRedirect)

    def send(method: str, path: str, headers: dict[str, str], body: dict | None) -> tuple[int, dict[str, str], dict | None]:
        h = {"content-type": "application/json", "user-agent": "pravrudhi-partner-smoke", **headers}
        req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                     method=method, headers=h)
        try:
            with opener.open(req, timeout=120) as r:  # noqa: S310 -- operator-supplied https/loopback URL
                status, raw, rh = int(r.status), r.read(1_000_000), {k.lower(): v for k, v in r.headers.items()}
        except urllib.error.HTTPError as e:
            return int(e.code), {k.lower(): v for k, v in e.headers.items()}, None
        try:
            parsed = json.loads(raw) if raw else None
        except ValueError:
            parsed = None
        return status, rh, parsed if isinstance(parsed, dict) else None

    return send


def config_error(message: str) -> int:
    print(f"CONFIG ERROR: {message}", file=sys.stderr)
    return 2


def main(env: dict[str, str] | None = None, send: Send | None = None) -> int:
    env = dict(os.environ) if env is None else env
    base = env.get("SMOKE_BASE_URL", "").strip().rstrip("/")
    key = env.get("SMOKE_API_KEY", "").strip() or None
    if not base:
        return config_error("set SMOKE_BASE_URL (an origin, e.g. https://engine.example.test)")
    try:
        parsed = urllib.parse.urlparse(base)
        port, hostname = parsed.port, parsed.hostname or ""
        bad_parts = parsed.path or parsed.params or parsed.query or parsed.fragment
        has_credentials = parsed.username is not None or parsed.password is not None
    except ValueError:
        return config_error("SMOKE_BASE_URL is not a valid URL")
    if not hostname:
        return config_error("SMOKE_BASE_URL has no host")
    if has_credentials or bad_parts:
        return config_error("SMOKE_BASE_URL must be an origin only (no credentials, path, query or fragment)")
    if parsed.scheme != "https" and (parsed.scheme != "http" or hostname not in LOOPBACK):
        return config_error("SMOKE_BASE_URL must be https (http only for loopback)")
    label = env.get("SMOKE_TARGET_LABEL", "").strip()  # fail-open-ok: a display label only, not a measurement
    shown = f"[{hostname}]" if ":" in hostname else hostname
    print(f"target: {parsed.scheme}://{shown}{f':{port}' if port else ''}" + (f" ({label})" if label else "")
          + f", key {'given' if key else 'not given'}")
    try:
        results = run(send or http_sender(base), key, env.get("SMOKE_RUN_ANALYSIS") == "1")
    except urllib.error.URLError as e:
        return config_error(f"the engine could not be reached ({type(e).__name__})")
    except Exception as e:  # noqa: BLE001 -- never a traceback (it could carry a header); the type alone is reported
        return config_error(f"the smoke failed unexpectedly ({type(e).__name__})")
    width = max(len(c.name) for c, *_ in results)
    for c, status, ok, note in results:
        verdict = "SKIP" if status is None else ("PASS" if ok else "FAIL")
        print(f"{verdict}  {c.name:<{width}}  {c.method} {c.path.split('?')[0]}  {note}")
    failed = [c.name for c, _s, ok, _n in results if not ok]
    ran = sum(1 for _c, s, _ok, _n in results if s is not None)
    keyed = sum(1 for c, s, *_ in results if s is not None and c.who == "key")
    print(f"\n{ran - len(failed)}/{ran} passed ({len(results) - ran} skipped)"
          + (f"; FAILED: {', '.join(failed)}" if failed else ""))
    if failed:
        return 1
    if keyed == 0:
        print("INCOMPLETE: no key-authenticated check ran (set SMOKE_API_KEY to the e2e non-admin key)", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
