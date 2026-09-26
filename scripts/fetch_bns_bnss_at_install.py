#!/usr/bin/env python3
"""PROTOTYPE -- Tag/Lead-2, 2026-09-26/27: NEVER MERGE. Not wired into `pravrudhi init` or any install flow.

Demonstrates the "fetch-at-install" alternative Tag raised to the NOTICE/exclusion option, for the
operator+lawyer to choose between (pravrudhi PR #55's hold; prabhasa-nyaya docs/decisions/CORPUS-SOURCES.md).
The problem #55 ran into was never the statute TEXT (Copyright Act 1957, s.52(1)(q) exempts it) -- it was
SHIPPING text fetched from indiacode.gov.in as a package asset, which conflicts with that portal's own
anti-automation Terms of Use/Copyright Policy regardless of the text's own copyright status. This script
tests whether that specific conflict disappears if the SAME already-authorized fetch (hand-found bitstream
ids, one file per Act, no crawling, source URL + sha256 recorded -- `india_code_fetch.py`'s own 2026-09-12
operator authorization) happens as the INSTALLING USER's own action, at their own install time, rather than
as something AxisMeru's package ships pre-baked. Nothing this script produces is committed to the repository
or shipped in a release; it writes into `research/nyaya/corpus/`, the exact "user's own additions" extension
point `nyaya.load_corpus()` already merges in and this repo's own `.gitignore` already excludes.

**This does NOT resolve the legal question by itself.** Fetch-at-install still means an install of this
product performs an automated GET against indiacode.gov.in without the portal's "prior written permission" --
whether that is meaningfully different from the vendor doing the same fetch once and shipping the result is
exactly the operator+lawyer's call to make, not this script's. It exists so that call can be made against a
working prototype instead of only a description of one.

Only fetches BNS and BNSS (Bharatiya Nyaya Sanhita / Bharatiya Nagarik Suraksha Sanhita, 2023) -- the two
Acts #55 originally added. Requires explicit, in-person consent (`--i-understand-this-fetches-from-indiacode`)
before making any network request: an install running unattended (a CI job, a container build, an automated
deploy) must never trigger this fetch on someone's behalf without them having typed that flag themselves.

Usage:
    python3 scripts/fetch_bns_bnss_at_install.py --i-understand-this-fetches-from-indiacode [--root .]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent


def _import_script(name: str) -> Any:
    """`india_code_fetch.py`/`india_code_extract.py` are scripts, not package modules -- loaded by path
    rather than added to a shared `sys.path` entry that could shadow an unrelated module of the same name."""
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CONSENT_FLAG = "--i-understand-this-fetches-from-indiacode"

#: The two Acts this prototype fetches -- filtered from india_code_fetch.ACTS, never restated, so the
#: bitstream ids/urls/known section counts stay the single already-authorized source of truth.
_WANTED_ACTS = {"Bharatiya Nyaya Sanhita", "Bharatiya Nagarik Suraksha Sanhita"}


def run(root: Path) -> list[dict[str, Any]]:
    """Fetch, extract, and write BNS+BNSS into `<root>/research/nyaya/corpus/` -- returns the manifest
    records `india_code_fetch.fetch_all` produced (source URL + sha256 per PDF, same shape #55's own PR
    recorded), so the caller can print or log exactly what was fetched from where."""
    fetch = _import_script("india_code_fetch")
    extract = _import_script("india_code_extract")

    acts = [a for a in fetch.ACTS if a["act"] in _WANTED_ACTS]
    assert len(acts) == len(_WANTED_ACTS), "india_code_fetch.ACTS no longer names both BNS and BNSS"

    pdf_dir = root / ".pravrudhi" / "fetched" / "india_code"
    corpus_dir = root / "research" / "nyaya" / "corpus"
    corpus_dir.mkdir(parents=True, exist_ok=True)

    manifest: list[dict[str, Any]] = fetch.fetch_all(acts, pdf_dir, pdf_dir / "manifest.json")

    for act, record in zip(acts, manifest, strict=True):
        corpus = extract.build_corpus(
            pdf_path=pdf_dir / act["filename"],
            act=act["act"],
            year=act["year"],
            site="indiacode.gov.in",
            item_url=act["item_url"],
        )
        # Short id form ("BNS"/"BNSS"), matching every contract id and judge_statute_text key this repo
        # already uses, and the shape nyaya.py's own CITE regex requires -- same choice #55's own commit
        # made, for the identical reason.
        short = "BNS" if act["act"] == "Bharatiya Nyaya Sanhita" else "BNSS"
        for doc in corpus["documents"]:
            doc["id"] = doc["id"].replace(f"{act['act']}/", f"{short}/")
            doc["act"] = short
        out_path = corpus_dir / f"{short.lower()}_sections.json"
        out_path.write_text(json.dumps(corpus, indent=2, ensure_ascii=False), encoding="utf-8")
        print(
            f"{short}: {len(corpus['documents'])} sections -> {out_path} "
            f"(source sha256 {record['sha256'][:12]}...)",
            file=sys.stderr,
        )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        CONSENT_FLAG, action="store_true",
        help="Required. Confirms you are choosing, as the person running this install, to fetch two Act "
             "PDFs from indiacode.gov.in -- a portal whose own Terms of Use/Copyright Policy restrict "
             "automated access. Never set this on someone else's behalf or from an unattended process.",
    )
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Project root to write research/nyaya/corpus/ under.")
    args = parser.parse_args()

    if not args.i_understand_this_fetches_from_indiacode:
        print(
            f"Refusing: this is a PROTOTYPE (never merged, never wired into any install flow) that fetches "
            f"BNS/BNSS from indiacode.gov.in. Pass {CONSENT_FLAG} yourself, in an interactive terminal, to "
            f"confirm you are choosing to do that.",
            file=sys.stderr,
        )
        return 2

    run(args.root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
