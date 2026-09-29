"""Rebuild src/pravrudhi/assets/nyaya/{bns,bnss,bsa}_sections.json from India Code (indiacode.gov.in DSpace API).

Usage: python scripts/build_nyaya_new_codes.py RAW_DIR [--reuse]   (writes RAW_DIR/<CODE>.raw, then the three asset files)
The raw sha256 in each asset's `source` is the hash of RAW_DIR/<CODE>.raw as fetched on the date recorded there."""

from __future__ import annotations

import hashlib
import html
import json
import re
import subprocess
import sys
import time
from pathlib import Path

LICENCE = (
    "Indian legislation (public); reproduced under Copyright Act 1957 s.52(1)(q). Text is India Code's "
    "section_page_note with HTML stripped; otherwise verbatim (source typos such as a doubled full stop are kept)."
)
# code -> (file, act, act number, India Code act_id, act handle, expected section count)
ACTS = {
    "BNS": ("bns_sections.json", "Bharatiya Nyaya Sanhita, 2023", "45 of 2023",
            "AC_CEN_5_23_00048_2023-45_1719292564123", "123456789/545524", 358),
    "BNSS": ("bnss_sections.json", "Bharatiya Nagarik Suraksha Sanhita, 2023", "46 of 2023",
             "AC_CEN_5_23_00049_202346_1719552320687", "123456789/496550", 531),
    "BSA": ("bsa_sections.json", "Bharatiya Sakshya Adhiniyam, 2023", "47 of 2023",
            "AC_CEN_5_23_00049_2023-47_1719292804654", "123456789/496549", 170),
}
API = "https://indiacode.gov.in/server/api/discover/search/objects"
FETCHED_AT = "2026-09-29"
URI_NOTE = (
    "records' dc.identifier.uri points at staging host test1.indiacode.nic.in; "
    "the retrieval URL is `query` on indiacode.gov.in"
)
MANIFEST = (
    "per section: api_record_sha256 = sha256 of the canonical (sorted-key, compact) JSON of the API item; "
    "raw_body_sha256 = sha256 of the HTML body. raw_sha256 covers the concatenated API pages. "
    "No Act PDF was fetched."
)
DST = Path(__file__).resolve().parent.parent / "src" / "pravrudhi" / "assets" / "nyaya"


def _page(act_id: str, page: int) -> bytes:
    for attempt in range(6):
        r = subprocess.run(
            ["curl", "-sL", "-m", "180", "-A", "Mozilla/5.0", "-G", API,
             "--data-urlencode", f'query=dc.identifier.act_id:"{act_id}" AND dc.identifier.collection:SECTION',
             "--data-urlencode", "size=100", "--data-urlencode", f"page={page}",
             "--data-urlencode", "sort=dc.identifier.order_number,asc"],
            capture_output=True,
        )
        try:
            json.loads(r.stdout)["_embedded"]["searchResult"]
            return r.stdout
        except Exception:  # noqa: BLE001 -- slow server: back off and retry
            time.sleep(5 * (attempt + 1))
    raise SystemExit(f"failed {act_id} page {page}")


def fetch(code: str, raw_dir: Path, reuse: bool) -> bytes:
    if reuse:
        return (raw_dir / f"{code}.raw").read_bytes()
    pages, p = [], 0
    while True:
        raw = _page(ACTS[code][3], p)
        pages.append(raw)
        p += 1
        if p >= json.loads(raw)["_embedded"]["searchResult"]["page"]["totalPages"]:
            break
    blob = b"\n".join(pages)
    (raw_dir / f"{code}.raw").write_bytes(blob)
    return blob


def _text(h: str) -> str:
    h = re.sub(r"<br\s*/?>|</p>|</div>|</tr>", "\n", h, flags=re.I)
    h = html.unescape(re.sub(r"<[^>]+>", " ", h)).replace("\xa0", " ")
    return re.sub(r"[ \t]+", " ", re.sub(r"\s*\n\s*", "\n", h)).strip()


def _sha(b: bytes | str) -> str:
    return hashlib.sha256(b.encode() if isinstance(b, str) else b).hexdigest()


def sections(blob: bytes, act_id: str) -> dict[str, dict[str, str]]:
    dec, raw, i, out = json.JSONDecoder(), blob.decode(), 0, {}
    while i < len(raw):
        while i < len(raw) and raw[i].isspace():
            i += 1
        if i >= len(raw):
            break
        page, i = dec.raw_decode(raw, i)
        for o in page["_embedded"]["searchResult"]["_embedded"]["objects"]:
            item = o["_embedded"]["indexableObject"]
            m = item["metadata"]

            def g(k: str, m: dict = m) -> str | None:  # type: ignore[type-arg]
                return (m.get(k) or [{}])[0].get("value")

            if g("dc.identifier.act_id") != act_id or g("dc.identifier.collection") != "SECTION":
                raise SystemExit(f"unexpected record: act_id={g('dc.identifier.act_id')}")
            body = g("dc.identifier.section_page_note") or ""
            out[g("dc.identifier.section_number") or ""] = {
                "title": g("dc.title") or "",
                "text": _text(body),
                "api_record_sha256": _sha(json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=False)),
                "raw_body_sha256": _sha(body),
            }
    return out


def main(raw_dir: Path, reuse: bool) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    for code, (fn, act, num, act_id, handle, expected) in ACTS.items():
        blob = fetch(code, raw_dir, reuse)
        secs = sections(blob, act_id)
        if sorted(map(int, secs)) != list(range(1, expected + 1)):
            print(f"MISMATCH {code}: found {len(secs)} sections, expected {expected}", file=sys.stderr)
        docs = [{"id": f"{code}/Section {k}", "act": act, "section": f"Section {k}", **secs[k]}
                for k in sorted(secs, key=int)]
        src = {
            "site": "indiacode.gov.in",
            "publisher": "India Code, Legislative Department, Ministry of Law and Justice, Government of India",
            "act": act, "act_number": num, "act_id": act_id,
            "query": (f'GET {API}?query=dc.identifier.act_id:"{act_id}" AND dc.identifier.collection:SECTION'
                      "&size=100&sort=dc.identifier.order_number,asc (pages 0..N)"),
            "act_page": f"https://indiacode.gov.in/handle/{handle}",
            "fetched_at": FETCHED_AT,
            "act_scope": "central Act only: every record's dc.identifier.act_id equals act_id (asserted while building)",
            "uri_note": URI_NOTE,
            "manifest": MANIFEST,
            "raw_sha256": hashlib.sha256(blob).hexdigest(), "raw_bytes": len(blob),
            "sections_expected": expected, "sections_found": len(docs), "licence": LICENCE,
        }
        out = {"version": 1, "built": FETCHED_AT, "source": src, "documents": docs}
        (DST / fn).write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print(code, len(docs), src["raw_sha256"])


if __name__ == "__main__":
    main(Path(sys.argv[1]), "--reuse" in sys.argv)
