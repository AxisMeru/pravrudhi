# ruff: noqa: E501
"""Rebuild src/pravrudhi/assets/nyaya/ipc_sections.json from the official Act text (Ministry of Home Affairs PDF of the Indian Penal Code, 1860).

Usage: python scripts/build_ipc_asset.py PDF [--report REPORT.json] [--retrieved ISO] [--url URL]
Needs `pdftotext` (poppler). The asset keeps the ids "IPC/Section N" it always carried and takes `title` and `text` from the official PDF; a
row whose old text equals the PDF text after whitespace/quote normalisation is reported `identical` / `whitespace_only`, any other row takes
the PDF text and is reported `different`. Ids the PDF does not contain (sections repealed before it was consolidated or inserted after it)
are DROPPED and listed: no text of the IL-TUR-derived asset is carried over. '294(b)' and '376(2)' carry the whole section's text.
The PDF's sha256 is recorded in the asset's `source`."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ipc_pdf_parse as P  # noqa: E402

ASSET = Path(__file__).resolve().parent.parent / "src" / "pravrudhi" / "assets" / "nyaya" / "ipc_sections.json"
DEFAULT_URL = "https://www.mha.gov.in/sites/default/files/IPC1860_1.pdf"


def norm(t: str) -> str:
    t = t.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"').replace("—", "-").replace("–", "-")
    t = re.sub(r"\s+([,.;:)])", r"\1", t)
    t = re.sub(r"\(\s+", "(", t)
    return re.sub(r"\s+", " ", t).strip().lower()


def main(pdf: Path, report: Path | None, retrieved: str, url: str) -> None:
    raw = subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True, check=True).stdout.replace("\x0c", "\n")
    secs = P.parse(raw)
    pdf_bytes = pdf.read_bytes()
    pages = int(re.search(r"Pages:\s+(\d+)", subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True, check=True).stdout).group(1))  # type: ignore[union-attr]
    latest = max(int(y) for y in re.findall(r"Act \d+ of (\d{4})", raw))
    old = json.loads(ASSET.read_text(encoding="utf-8"))
    rep: dict[str, list[str]] = {"identical": [], "whitespace_only": [], "different": [], "part_of_section": [], "dropped_not_in_pdf": []}
    docs = []
    for d in old["documents"]:
        sid = d["section"].split(" ", 1)[1]
        base = re.sub(r"\(.*$", "", sid)
        s = secs.get(base)
        if s is None or not s["text"]:
            rep["dropped_not_in_pdf"].append(sid)
            continue
        if base != sid:
            rep["part_of_section"].append(sid)
        a, b = d["text"], s["text"]
        rep["identical" if a == b else ("whitespace_only" if norm(a) == norm(b) else "different")].append(sid)
        docs.append({"id": d["id"], "act": d["act"], "section": d["section"], "title": s["title"], "text": b,
                     "section_text_sha256": hashlib.sha256(b.encode()).hexdigest()})
    src = {
        "publisher": "Ministry of Home Affairs, Government of India (an official copy of the text of Act 45 of 1860)",
        "act": "Indian Penal Code, 1860", "act_number": "45 of 1860",
        "file": url, "retrieved_at": retrieved, "sha256": hashlib.sha256(pdf_bytes).hexdigest(), "bytes": len(pdf_bytes), "pages": pages,
        "text_as_of": f"the PDF's amendment footnotes cite no Act later than {latest}: amendments since then (for example the Criminal Law "
                      "(Amendment) Acts of 2005, 2013 and 2018) are NOT in this text, and sections inserted after it (for example 174A, 229A) or "
                      "omitted before it (13, 161, 164) are not in this asset",
        "method": "pdftotext -layout; page footers, footnote blocks, chapter headings and amendment markers N*[ ... ] removed; line breaks and "
                  "runs of spaces collapsed; the words are the PDF text layer's (typesetting slips such as 'o f' stay)",
        "selection": "the section ids the asset has always carried, less those the PDF does not contain; ids '294(b)' and '376(2)' carry the whole "
                     "section's text",
        "sections_in_pdf": len(secs), "sections_selected": len(docs),
        "licence": "Indian Acts are government works (Copyright Act 1957 s.52(1)(q)); the text is the Act's own. No third-party dataset text is carried.",
    }
    ASSET.write_text(json.dumps({"version": 2, "built": retrieved[:10], "source": src, "documents": docs}, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    counts = {k: len(v) for k, v in rep.items()}
    print(json.dumps({"counts": counts, "sections_in_pdf": len(secs), "latest_amending_act_year": latest, "pdf_sha256": src["sha256"]}))
    if report:
        report.write_text(json.dumps({"counts": counts, **rep}, indent=1), encoding="utf-8")


if __name__ == "__main__":
    a = [x for x in sys.argv[1:] if not x.startswith("--")]

    def opt(k: str, d: str) -> str:
        return sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d

    main(Path(a[0]), Path(opt("--report", "")) if "--report" in sys.argv else None, opt("--retrieved", "2026-10-17T17:08:00Z"), opt("--url", DEFAULT_URL))
