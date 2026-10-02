"""Dev-stack (local) smoke for analyse-facts: one toy request per posture against a running local engine.

Checks the live response, not a double: (#220) `standard` is present, well-formed and agrees with the
posture sent; (#197/#212) every established element carries a fact_id and quote, and no non-established
element carries a fact_id (fail closed). Prints one JSON report labelled "dev stack (local)"; exit 1 on any
failed check, 2 on an unreachable engine or judge (an outage is never reported as a pass).

Usage: dev_stack_smoke.py --url http://127.0.0.1:8301 [--token-file .pravrudhi/app_token] [--contract bns69]
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

LABEL = "dev stack (local)"
TOY_FACTS = [
    "TOY: Kiran was engaged to Lata and told her he would marry her in the spring.",
    "TOY: Kiran had already decided never to marry Lata when he made that promise.",
    "TOY: Relying on the promise, Lata had sexual intercourse with Kiran.",
]
POSTURES: list[str | None] = [None, "quash", "discharge", "trial", "appeal"]
STAND_ALONE = {"proved", "prima_facie_disclosed"}


def post(url: str, token: str | None, body: dict[str, Any]) -> dict[str, Any]:
    headers = {"content-type": "application/json"}
    if token:
        headers["x-pravrudhi-token"] = token
    req = urllib.request.Request(url.rstrip("/") + "/api/v1/analyse-facts", json.dumps(body).encode(), headers)
    with urllib.request.urlopen(req, timeout=300) as r:  # noqa: S310 -- operator-supplied local URL
        return json.loads(r.read())


def check_response(resp: dict[str, Any], posture: str | None) -> list[str]:
    fails: list[str] = []
    std = resp.get("standard")
    if not isinstance(std, dict):
        return ["standard missing"]
    if set(std) != {"applied", "source", "proceeding_posture"}:
        fails.append(f"standard keys {sorted(std)}")
    if std.get("applied") not in STAND_ALONE:
        fails.append(f"standard.applied {std.get('applied')!r}")
    if std.get("proceeding_posture") not in (None, posture):
        fails.append(f"standard.proceeding_posture {std.get('proceeding_posture')!r} != sent {posture!r}")
    if posture is None and (std.get("applied"), std.get("source")) != ("proved", "default"):
        fails.append(f"absent posture must give proved/default, got {std}")
    for res in resp.get("results", []):
        for el in res.get("elements", []):
            est = el.get("status") == "established"
            if est and not (el.get("fact_id") and el.get("quote")):
                fails.append(f"established element without fact_id/quote: {el.get('element')!r}")
            if not est and el.get("fact_id"):
                fails.append(f"non-established element carries fact_id: {el.get('element')!r}")
    return fails


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--token-file")
    ap.add_argument("--contract", default="bns69")
    args = ap.parse_args()
    token = Path(args.token_file).read_text().strip() if args.token_file else None
    report: dict[str, Any] = {"label": LABEL, "cost_usd": 0, "contract": args.contract, "runs": []}
    failed = False
    for posture in POSTURES:
        body: dict[str, Any] = {"facts": TOY_FACTS, "contract_ids": [args.contract]}
        if posture:
            body["proceeding_posture"] = posture
        try:
            resp = post(args.url, token, body)
        except (urllib.error.URLError, OSError, ValueError) as e:
            print(f"ENGINE/JUDGE UNAVAILABLE ({posture}): {e}", file=sys.stderr)
            return 2
        fails = check_response(resp, posture)
        failed |= bool(fails)
        report["runs"].append({"posture": posture, "standard": resp.get("standard"), "failures": fails})
    report["passed"] = not failed
    print(json.dumps(report, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
