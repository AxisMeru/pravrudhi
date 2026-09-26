#!/usr/bin/env python3
"""Build `research/nyaya/corpus/ibc.json` from the IBC 2016 PDF fetched from ibbi.gov.in (P3, LEG-PLAN-2026-09-23).

Fetched under the 2026-09-23 India-Code-authorisation widening (`docs/decisions/LEG-LEDGER-2026-09-23.md`),
routed to ibbi.gov.in instead of indiacode.gov.in after indiacode.gov.in and legislative.gov.in both proved
unreachable/blocked from this host (see the session's report to Lead-2-assistant for the diagnostic detail:
Akamai edge 403s on legislative.gov.in/meity.gov.in, a hard connection timeout on lddashboard's PDF host).
ibbi.gov.in was checked first: robots.txt 404s (no restriction file present) and no discoverable terms-of-
use/copyright-policy page exists on the site at all -- a materially different posture from India Code's
explicit anti-automation ToU clause. One file, one request, no crawling.

The PDF's own header reads "[AMENDED UPTO 23-09-2020]" -- this is IBBI's own posted consolidated text, not
independently checked against amendments since that date; labelled honestly in `source.work` rather than
implied current.

No "ARRANGEMENT OF SECTIONS" index precedes the body here (unlike the indiacode.gov.in Bare Acts
`india_code_extract.py` handles), so this parser is simpler: split on `^<num>. ` line headers, stop the
moment numbering restarts (the Eleventh Schedule's own amendment list re-starts "1., 2., ..." after the
real sections end at 255 -- confirmed by hand against the extracted text before writing this rule).
"""

import hashlib
import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

PDF = Path("research/nyaya/pdf_indiacode/ibc_2016.pdf")
OUT = Path("research/nyaya/corpus/ibc.json")

text = subprocess.run(["pdftotext", str(PDF), "-"], capture_output=True, text=True, check=True).stdout
lines = text.split("\n")

HEADER = re.compile(r"^(?P<num>\d{1,3}[A-Z]?)\.\s+(?P<rest>.*)$")

sections = []
current = None
highest = 0

def base_num(n):
    m = re.match(r"\d+", n)
    return int(m.group()) if m else 0

for line in lines:
    if line.strip() in ("THE FIRST SCHEDULE",) or line.strip().startswith("THE ") and "SCHEDULE" in line:
        break
    m = HEADER.match(line)
    if m:
        n = base_num(m.group("num"))
        if n < highest - 2:  # numbering restarted (schedule/amendment list) -- stop, don't follow it down
            break
        if current:
            sections.append(current)
        highest = max(highest, n)
        current = {"num": m.group("num"), "buf": [m.group("rest")]}
    elif current is not None:
        current["buf"].append(line)

if current:
    sections.append(current)

documents = []
for s in sections:
    body = "\n".join(s["buf"]).strip()
    body = re.sub(r"\n{2,}", "\n", body)
    # Title is the text up to the first ". " or " (1)" / " –" marker, whichever comes first and is short.
    flat = " ".join(body.split())
    title_match = re.match(r"^(.{3,160}?)(?:\.\s|\s+–|\s+-\s|\s*\(1\))", flat)
    title = title_match.group(1).strip() if title_match else body[:60].strip()
    documents.append({
        "id": f"Insolvency and Bankruptcy Code/Section {s['num']}",
        "act": "Insolvency and Bankruptcy Code",
        "section": f"Section {s['num']}",
        "title": title,
        "text": body,
    })

sha = hashlib.sha256(PDF.read_bytes()).hexdigest()
corpus = {
    "version": 1,
    "built": datetime.now(UTC).strftime("%Y-%m-%d"),
    "source": {
        "site": "ibbi.gov.in",
        "work": (
            "The Insolvency and Bankruptcy Code, 2016 (as amended up to 23-09-2020, per the PDF's own "
            "header -- IBBI's own posted consolidated text, not independently verified against later "
            "amendments)"
        ),
        "pages": [
            {
                "title": "The Insolvency and Bankruptcy Code, 2016",
                "url": "https://ibbi.gov.in/uploads/legalframwork/2021-11-16-173128-h609x-e942e8ee824aa2c4ba4767b93aad0e5d.pdf",
                "sha256": sha,
                "fetched_at": datetime.now(UTC).isoformat(),
            }
        ],
    },
    "documents": documents,
    "excluded_ambiguous": [],
}

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(corpus, indent=2, ensure_ascii=False))
print(f"wrote {len(documents)} sections, {highest} max section number, to {OUT}")
print("first 3 ids:", [d["id"] for d in documents[:3]])
print("last 3 ids:", [d["id"] for d in documents[-3:]])
