#!/usr/bin/env python3
"""Fetch a fixed, named set of five Act PDFs from indiacode.gov.in.

**Operator decision (recorded here, not made by this script).** On 2026-09-12 the operator
explicitly authorized fetching the Indian Penal Code 1860, the Code of Criminal Procedure
1973, the Bharatiya Nyaya Sanhita 2023 (BNS), the Bharatiya Nagarik Suraksha Sanhita 2023
(BNSS), and the Indian Contract Act 1872 from indiacode.gov.in, despite that portal's own
Terms of Use ("Not use any automated means to access the Portal for any purpose without our
... permission") and Copyright Policy (prohibiting "systematic extraction, scraping, or
harvesting" of the database) -- see ``nyaya_corpus_build.py``'s docstring for the underlying
research (robots.txt absent/erroring, PDF-only format, ToU/Copyright-Policy text quoted).
That conflict is the operator's decision and risk to accept, not this script's to resolve --
the underlying statutory text remains exempt from copyright regardless (Copyright Act, 1957,
s. 52(1)(q)).

**Constraints this script holds itself to**, so an automated fetch still behaves like one
person downloading a few named documents once, not a crawler:

- Fetches ONLY the five fixed bitstreams in ``ACTS`` below. It never searches, paginates a
  collection, or follows a link it discovers at runtime -- every bitstream id here was found
  by hand in one prior browser session (site search -> an Act's own page -> that page's own
  "Download <name>.pdf" link resolving through DSpace's REST bitstream-content endpoint).
- One file at a time, with a real pause (default 30-60s, randomised) between files -- never
  before the first.
- A truthful ``User-Agent`` naming this project and a contact address (see ``USER_AGENT``).
- No retries. Exactly one request per file. A 403 or 429 response stops the whole run
  immediately (``FetchStopped``); any other exception (timeout, connection error, other
  HTTP status) is left to propagate and also stops the run -- nothing here catches-and-tries
  again.
- Every fetched file's source URL, sha256, and byte size are written to a manifest JSON
  beside the PDFs, updated after each successful file so a stop midway still leaves prior
  successes recorded. Both the PDFs and the manifest live under ``research/`` (gitignored;
  never a public commit -- see the pravrudhi repo-publication rule).

Usage: ``python3 scripts/india_code_fetch.py --out-dir research/nyaya/pdf_indiacode``
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import time
import urllib.error
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

USER_AGENT = (
    "prabhasa-samskrutam-research/1.0 "
    "(+contact: sharath.ai.colab@gmail.com; one-time research fetch of 5 named Act PDFs)"
)

_BITSTREAM_URL = "https://indiacode.gov.in/server/api/core/bitstreams/{bitstream_id}/content"

_DEFAULT_PAUSE_RANGE = (30.0, 60.0)

# Located by hand on indiacode.gov.in on 2026-09-12 (see the task report for the click path);
# `item_url` is the Act's own portal page, kept for reference only -- never fetched by this
# script. `known_section_count` is the portal's OWN structured section index count where one
# was available (BNS/BNSS/Contract Act each expose a DSpace "/sections" view with an
# authoritative total); the Indian Penal Code and Code of Criminal Procedure are hosted only
# as a plain PDF item (fully repealed acts get no "/sections" view on this portal), so no
# portal-verified count exists for those two -- left absent rather than filled with a
# remembered number this script did not check.
ACTS: list[dict[str, Any]] = [
    {
        "act": "Indian Penal Code",
        "year": "1860",
        "filename": "A1860-45.pdf",
        "bitstream_id": "337c7a03-60c6-4953-8dd6-90e65c343629",
        "item_url": "https://indiacode.gov.in/items/972afbe0-a2de-415d-9df5-8d70038056ad",
    },
    {
        "act": "Code of Criminal Procedure",
        "year": "1973",
        "filename": "1974-2.pdf",
        "bitstream_id": "f40e830f-5fd8-4220-87f9-88a60b35d3de",
        "item_url": "https://indiacode.gov.in/items/973d60aa-105c-4a2f-94f1-7640f0b5d6a7",
    },
    {
        "act": "Bharatiya Nyaya Sanhita",
        "year": "2023",
        "filename": "a2023-45.pdf",
        "bitstream_id": "8007a80e-259b-429d-9ca4-1a2f474b5000",
        "item_url": "https://indiacode.gov.in/act/4bb3e636-ed67-4731-8fb1-7ebfd83e1584/sections",
        "known_section_count": 358,
    },
    {
        "act": "Bharatiya Nagarik Suraksha Sanhita",
        "year": "2023",
        "filename": "A2023-46.pdf",
        "bitstream_id": "73f93470-5f6a-4f0f-bc23-1309e32b9554",
        "item_url": "https://indiacode.gov.in/act/72592ac7-d084-4545-95d3-0c98dc49fac3/sections",
        "known_section_count": 531,
    },
    {
        "act": "Indian Contract Act",
        "year": "1872",
        "filename": "a1872-9.pdf",
        "bitstream_id": "e7571d39-11a7-4a2c-86e3-c515ae43e617",
        "item_url": "https://indiacode.gov.in/act/0e782442-f035-4d53-aace-81b1552a8cf2/sections",
        "known_section_count": 268,
    },
]


class FetchStopped(RuntimeError):
    """A 403/429 (or a caller-supplied stop condition) halted the run; never retried."""


def bitstream_url(bitstream_id: str) -> str:
    return _BITSTREAM_URL.format(bitstream_id=bitstream_id)


def download_bitstream(url: str, timeout: float = 60.0) -> bytes:
    """The real network fetch: one request, truthful UA, no retry.

    A 403 or 429 is re-raised as `FetchStopped` so callers can tell "the portal refused us"
    apart from any other failure. Every other HTTPError/URLError/timeout propagates as-is --
    still a stop, just not one this function relabels.
    """
    req = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(req, timeout=timeout) as response:  # noqa: S310 - fixed https host
            return response.read()
    except urllib.error.HTTPError as e:
        if e.code in (403, 429):
            raise FetchStopped(f"{e.code} {e.reason} from {url} -- stopping, no retry") from e
        raise


def fetch_one(
    act: dict[str, Any],
    out_dir: Path,
    fetch_bytes: Callable[[str], bytes],
) -> dict[str, Any]:
    """Fetch one act's PDF, write it under `out_dir`, and return its manifest record."""
    url = bitstream_url(act["bitstream_id"])
    data = fetch_bytes(url)
    dest = out_dir / act["filename"]
    out_dir.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return {
        "act": act["act"],
        "year": act["year"],
        "filename": act["filename"],
        "bitstream_id": act["bitstream_id"],
        "url": url,
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
        "fetched_at": datetime.now(UTC).isoformat(),
    }


def fetch_all(
    acts: list[dict[str, Any]],
    out_dir: Path,
    manifest_path: Path,
    fetch_bytes: Callable[[str], bytes] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    pause_range: tuple[float, float] = _DEFAULT_PAUSE_RANGE,
    rng: random.Random | None = None,
) -> list[dict[str, Any]]:
    """Fetch every act in `acts`, one at a time, pausing between (never before the first).

    Stops immediately -- propagating the exception -- on the first failure of any kind. The
    manifest is (re)written after every success, so a run that stops partway still leaves
    prior successes recorded on disk instead of only in memory.
    """
    rng = rng or random.Random()
    fetch_bytes = fetch_bytes or download_bitstream
    records: list[dict[str, Any]] = []
    for i, act in enumerate(acts):
        if i > 0:
            sleep_fn(rng.uniform(*pause_range))
        record = fetch_one(act, out_dir, fetch_bytes=fetch_bytes)
        records.append(record)
        manifest_path.write_text(json.dumps(records, indent=2), encoding="utf-8")
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir", type=Path, default=Path("research/nyaya/pdf_indiacode"),
        help="Where to write the PDFs and manifest.json (must stay under the gitignored "
             "research/ tree).",
    )
    args = parser.parse_args()

    try:
        records = fetch_all(ACTS, args.out_dir, args.out_dir / "manifest.json")
    except FetchStopped as e:
        print(f"STOPPED: {e}", file=sys.stderr)
        return 1
    print(json.dumps(records, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
