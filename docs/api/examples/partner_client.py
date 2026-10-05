"""Partner API sample client (stdlib only). See ../partner-quickstart-client.md.

Flow: read a local text file, split it into facts on the client, POST /api/v1/analyse-facts (sync JSON), print
each contract's outcome, the standard that was applied (and whether the judge actually saw it), and flag
REFER_TO_LAWYER. The API has no matter, server-side upload, SSE or job-poll route in this contract; the "upload"
here is only reading a file on your side.

    export PRAVRUDHI_API_KEY=...        # your organisation's key; never commit it
    python partner_client.py --base-url https://api.example.com --facts-file toy_facts.txt --contract bns69
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

API_KEY_HEADER = "X-Pravrudhi-Api-Key"
TENANCY_SECRET_HEADER = "X-Pravrudhi-Tenancy-Secret"
MAX_FACTS = 8
MAX_FACT_CHARS = 4000

#: (method, path, json body or None, headers) -> (status, response headers, parsed JSON body)
Send = Callable[[str, str, "dict[str, Any] | None", "dict[str, str]"], "tuple[int, dict[str, str], Any]"]


def split_facts(text: str) -> list[str]:
    """One fact per blank-line-separated paragraph. Refuses what the API would refuse, before any request."""
    facts = [" ".join(p.split()) for p in text.split("\n\n") if p.strip()]
    if not facts:
        raise ValueError("the file contains no non-empty paragraph")
    if len(facts) > MAX_FACTS:
        raise ValueError(f"{len(facts)} facts; the API accepts at most {MAX_FACTS} per call")
    long = [i for i, f in enumerate(facts, 1) if len(f) > MAX_FACT_CHARS]
    if long:
        raise ValueError(f"fact(s) {long} exceed {MAX_FACT_CHARS} characters")
    return facts


def http_send(base_url: str) -> Send:
    def send(method: str, path: str, body: dict[str, Any] | None, headers: dict[str, str]) -> tuple[int, dict[str, str], Any]:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(base_url.rstrip("/") + path, data=data, method=method,
                                     headers={"Content-Type": "application/json", **headers})
        try:
            with urllib.request.urlopen(req, timeout=300) as r:  # noqa: S310 -- caller-chosen https base URL
                return r.status, dict(r.headers), json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), json.loads(e.read() or b"null")

    return send


class PartnerClient:
    def __init__(self, send: Send, api_key: str) -> None:
        self._send, self._key = send, api_key

    def analyse_facts(self, facts: list[str], contract_ids: list[str], *, narrative: str = "",
                      proceeding_posture: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"facts": facts, "narrative": narrative, "contract_ids": contract_ids}
        if proceeding_posture:
            body["proceeding_posture"] = proceeding_posture
        status, headers, data = self._send("POST", "/api/v1/analyse-facts", body, {API_KEY_HEADER: self._key})
        if status != 200:
            retry = {k.lower(): v for k, v in headers.items()}.get("retry-after")
            raise ApiError(status, data, retry)
        return dict(data)


class ApiError(Exception):
    def __init__(self, status: int, body: Any, retry_after: str | None) -> None:
        super().__init__(f"HTTP {status}: {body}" + (f" (retry after {retry_after}s)" if retry_after else ""))
        self.status, self.body, self.retry_after = status, body, retry_after


def provision(send: Send, tenancy_secret: str, org_id: str) -> str:
    """Operator-side setup: create an organisation and one key. Returns the key secret, shown exactly once."""
    h = {TENANCY_SECRET_HEADER: tenancy_secret}
    status, _, body = send("POST", "/api/v1/orgs", {"org_id": org_id, "name": org_id}, h)
    if status not in (200, 201, 409):
        raise ApiError(status, body, None)
    status, _, body = send("POST", f"/api/v1/orgs/{org_id}/keys", {}, h)
    if status not in (200, 201):
        raise ApiError(status, body, None)
    return str(body["secret"])


def summarise(result: dict[str, Any]) -> list[str]:
    std = result.get("standard") or {}
    if std.get("in_judge_prompt"):
        lines = [f"standard: {std.get('applied')} (given to the judge; source: {std.get('source')})"]
    else:
        lines = [f"standard requested: {std.get('requested')}, NOT applied: the judge was never told it "
                 f"(source: {std.get('source')})"]
    for c in result.get("contracts", []):
        lines.append(f"{c['contract_id']}: {c['outcome']} ({c['reason']})")
        if c["outcome"] == "REFER_TO_LAWYER":
            lines.append(f"  -> {c['contract_id']}: the engine declines to give a verdict; a lawyer must decide this one")
    return lines


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base-url", required=True, help="e.g. https://api.example.com")
    p.add_argument("--facts-file", type=Path, required=True)
    p.add_argument("--contract", action="append", required=True, help="contract id; repeat for up to 5")
    p.add_argument("--posture", choices=["quash", "discharge", "trial", "appeal"])
    a = p.parse_args(argv)
    key = os.environ.get("PRAVRUDHI_API_KEY", "")
    if not key:
        print("set PRAVRUDHI_API_KEY", file=sys.stderr)
        return 2
    try:
        facts = split_facts(a.facts_file.read_text())
        result = PartnerClient(http_send(a.base_url), key).analyse_facts(facts, a.contract, proceeding_posture=a.posture)
    except (ValueError, ApiError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print("\n".join(summarise(result)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
