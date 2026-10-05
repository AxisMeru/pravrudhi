"""Dev-stack (local) smoke for analyse-facts: one toy request per posture against a running local engine.

Checks the live response, not a double: (#220) `standard` is present, well-formed and agrees with the
posture sent; (#197/#212) every established element carries a fact_id and quote, and no non-established
element carries a fact_id (fail closed). The judge model ids are pinned: each `--judge ID=BASE_URL` asserts
that BASE_URL/models serves ID, and the report records what was listed. Prints one JSON report; exit 1 on any
failed check or an HTTP error from the engine (reported with its status), 2 on an unreachable engine or judge, or
a judge-unavailable 503 (an outage is never reported as a pass).

The report is labelled "dev stack (local)" with cost 0 only when the engine URL is loopback or a host named by
`--dev-host`. Any other URL is refused unless `--label` names it explicitly; the cost is then reported as null.

Usage: dev_stack_smoke.py --url http://127.0.0.1:8301 --judge nyaya-judge-4b=http://127.0.0.1:8110/v1
       [--judge judge32b=http://127.0.0.1:8111/v1] [--dev-host HOST] [--label TEXT]
       [--token-file .pravrudhi/app_token] [--contract bns69]
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

LABEL = "dev stack (local)"
TOY_FACTS = [
    "TOY: Kiran was engaged to Lata and told her he would marry her in the spring.",
    "TOY: Kiran had already decided never to marry Lata when he made that promise.",
    "TOY: Relying on the promise, Lata had sexual intercourse with Kiran.",
]
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
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
    if set(std) != {"requested", "applied", "source", "proceeding_posture", "in_judge_prompt"}:
        fails.append(f"standard keys {sorted(std)}")
    if std.get("requested") not in STAND_ALONE:
        fails.append(f"standard.requested {std.get('requested')!r}")
    in_prompt = std.get("in_judge_prompt")
    if std.get("applied") != (std.get("requested") if in_prompt is True else None):
        fails.append(f"standard.applied {std.get('applied')!r} must equal requested only when in_judge_prompt is true")
    if std.get("proceeding_posture") not in (None, posture):
        fails.append(f"standard.proceeding_posture {std.get('proceeding_posture')!r} != sent {posture!r}")
    if not isinstance(std.get("in_judge_prompt"), bool):
        fails.append(f"standard.in_judge_prompt {std.get('in_judge_prompt')!r} is not a bool")
    if posture is None and (std.get("requested"), std.get("source")) != ("proved", "default"):
        fails.append(f"absent posture must give proved/default, got {std}")
    for res in resp.get("results", []):
        for el in res.get("elements", []):
            est = el.get("status") == "established"
            if est and not (el.get("fact_id") and el.get("quote")):
                fails.append(f"established element without fact_id/quote: {el.get('element')!r}")
            if not est and el.get("fact_id"):
                fails.append(f"non-established element carries fact_id: {el.get('element')!r}")
    return fails


def label_for(url: str, dev_hosts: set[str], explicit: str | None) -> tuple[str, int | None]:
    """`(label, cost_usd)`. Loopback or a named dev host is "dev stack (local)" at cost 0; anything else needs an
    explicit label and reports an unknown cost, because a hosted engine spends real judge money."""
    host = urlparse(url).hostname or ""
    if host in LOOPBACK_HOSTS or host in dev_hosts:
        return LABEL, 0
    if not explicit:
        raise SystemExit(
            f"REFUSING: {url} is not loopback or a --dev-host; pass --label to run it against a non-dev engine"
        )
    return explicit, None


def served_models(base_url: str) -> list[str]:
    req = urllib.request.Request(base_url.rstrip("/") + "/models")
    with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 -- operator-supplied judge URL
        return [str(m.get("id")) for m in json.loads(r.read()).get("data", [])]


def check_judges(pins: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
    """Assert every `ID=BASE_URL` pin is served. Returns (what was listed, failures)."""
    seen: list[dict[str, Any]] = []
    fails: list[str] = []
    for pin in pins:
        model, _, base = pin.partition("=")
        listed = served_models(base)
        seen.append({"expected": model, "base_url": base, "listed": listed})
        if model not in listed:
            fails.append(f"judge {model!r} is not served at {base} (lists {listed})")
    return seen, fails


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--judge", action="append", default=[], metavar="ID=BASE_URL")
    ap.add_argument("--dev-host", action="append", default=[])
    ap.add_argument("--label")
    ap.add_argument("--token-file")
    ap.add_argument("--contract", default="bns69")
    args = ap.parse_args()
    if not args.judge or any("=" not in j for j in args.judge):
        print("REFUSING: pin the judge model id(s) with --judge ID=BASE_URL (a null model can serve the base model)",
              file=sys.stderr)
        return 2
    label, cost = label_for(args.url, set(args.dev_host), args.label)
    token = Path(args.token_file).read_text().strip() if args.token_file else None
    try:
        judges, judge_fails = check_judges(args.judge)
    except (urllib.error.URLError, OSError, ValueError) as e:
        print(f"ENGINE/JUDGE UNAVAILABLE (judge model list): {e}", file=sys.stderr)
        return 2
    report: dict[str, Any] = {
        "label": label, "cost_usd": cost, "contract": args.contract, "judges": judges, "judge_failures": judge_fails,
        "runs": [],
    }
    failed = bool(judge_fails)
    for posture in POSTURES:
        body: dict[str, Any] = {"facts": TOY_FACTS, "contract_ids": [args.contract]}
        if posture:
            body["proceeding_posture"] = posture
        try:
            resp = post(args.url, token, body)
        except urllib.error.HTTPError as e:
            if e.code == 503:
                print(f"ENGINE/JUDGE UNAVAILABLE ({posture}): HTTP 503 {e.reason}", file=sys.stderr)
                return 2
            failed = True
            report["runs"].append({"posture": posture, "http_status": e.code, "failures": [f"HTTP {e.code} {e.reason}"]})
            continue
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
