#!/usr/bin/env python3
"""Extract section-boundary text from an indiacode.gov.in Bare Act PDF and write a
`coi.json`-shaped corpus file (see ``nyaya_corpus_build.py``).

**Extractor**: Poppler's ``pdftotext`` (plain mode, no ``-layout``) via subprocess -- the
system tool already on this box, not a new Python dependency. ``-layout`` was tried first and
rejected: it re-flows the per-page "IndiaCode" watermark INTO the middle of real words and
lines rather than onto separate lines, which plain mode does not do (verified against the
real fetched PDFs during development).

**What the real fetched PDFs actually look like**, verified by hand against all five and
driving every rule below:

- Every page carries a diagonal "IndiaCode" watermark that ``pdftotext`` renders as five
  short standalone lines -- ``In`` / ``di`` / ``aC`` / ``od`` / ``e`` -- once per page across
  all five PDFs (counts matched each PDF's own page count). `strip_watermark` removes exactly
  these five tokens by exact line match; nothing else (a real short line like "or" or "of" is
  left untouched -- this is not a generic "drop short lines" filter).
- Every Act's PDF opens with an "ARRANGEMENT OF SECTIONS" index that repeats every section
  number and title WITHOUT the heading/body dash the real body uses -- so a naive
  ``^\\d+\\.`` scan over the whole file double-counts every section. Every Act's real body
  instead opens with a line matching ``ACT NO. <n> OF <year>`` (confirmed present in all four
  Acts this script was actually run against); `find_body_start` cuts everything before that
  line.
- A genuine section header is ``<number>. <heading>`` followed, possibly after the heading
  wraps onto one or more further lines, by a dash -- either a single em dash (``—``, the
  Indian Penal Code and Indian Contract Act) or a doubled en dash (``––``, the
  Bharatiya Nyaya Sanhita and Bharatiya Nagarik Suraksha Sanhita) -- before the section's own
  body text begins. Marginal footnotes (amendment history: "2. Subs. by Act 3 of 1951...")
  also start with a bare ``<digit>.`` but are never followed by a dash before the next
  numbered line -- `split_sections` tells the two apart by searching a bounded window of
  lines for that dash, discarding a candidate that never finds one rather than guessing.
- After the last real section, a Schedule restarts its own "1., 2., ..." numbering (forms,
  fee tables). `split_sections` stops at the first section number that is not
  monotonically non-decreasing relative to the highest confirmed so far (letter suffixes like
  "19A" share their base number and are always accepted) rather than following the Schedule's
  numbers back down into the output.

**What this script does NOT attempt to fix**: OCR-quality corruption. The Code of Criminal
Procedure, 1973 PDF on indiacode.gov.in (``1974-2.pdf``) is a scanned image, not a
digitally-typeset document like the other four -- ``pdftotext`` on it produces pervasive
character-level garbling (confirmed by hand: dropped headings, "Criminat" for "Criminal",
"su.ch" for "such", table cells rendered as line noise) that this section-boundary parser
cannot reliably separate from genuine text. Running it through this extractor would inject
OCR noise into the SFT corpus as if it were the Act's real text, which this project's "no
synthetic stand-ins" rule and CHARTER Sakshi rule both forbid treating as evidence. This
script is therefore only run against the Indian Penal Code, the Bharatiya Nyaya Sanhita, the
Bharatiya Nagarik Suraksha Sanhita, and the Indian Contract Act; the Code of Criminal
Procedure is reported separately as unsuitable for automated extraction from this source, not
silently included or silently dropped.

Usage: ``python3 scripts/india_code_extract.py --pdf <path> --act "..." --year YYYY
--site indiacode.gov.in --item-url <the Act's portal page> --out <corpus json path>
--section-word Section``
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_WATERMARK_TOKENS = frozenset({"In", "di", "aC", "od", "e"})
_HEADER_RE = re.compile(r"^\[?(\d+[A-Z]{0,2})\.\s*(.*)$")
_DASH_RE = re.compile(r"[–—]{1,2}")
_BODY_START_RE = re.compile(r"^ACT NO\.\s+\d+\s+OF\s+\d{4}", re.MULTILINE)


def run_pdftotext(pdf_path: Path) -> str:
    """Extract raw text from `pdf_path` with Poppler's pdftotext (plain mode)."""
    result = subprocess.run(  # noqa: S603
        ["pdftotext", str(pdf_path), "-"],
        capture_output=True, text=True, check=True,
    )
    return result.stdout


def strip_watermark(text: str) -> str:
    """Remove lines that are exactly one of the five known 'IndiaCode' watermark fragments."""
    return "\n".join(
        line for line in text.splitlines() if line.strip() not in _WATERMARK_TOKENS
    )


def find_body_start(text: str) -> int:
    """Index into `text` where the real enacted text begins (skips the arrangement-of-sections
    index). Raises if the `ACT NO. <n> OF <year>` anchor is not found -- reported, not guessed
    around."""
    m = _BODY_START_RE.search(text)
    if m is None:
        raise ValueError("could not find an 'ACT NO. <n> OF <year>' anchor to start from")
    return m.start()


def _base_number(num: str) -> int:
    digits = re.match(r"\d+", num)
    assert digits is not None
    return int(digits.group())


def split_sections(text: str, max_heading_lines: int = 6) -> list[dict[str, Any]]:
    """Split the (body-only, watermark-stripped) text into section records.

    A candidate `<number>. ...` line is accepted as a real section header only if a dash is
    found within it or the following `max_heading_lines` lines (stopping early if another
    candidate line appears first); acceptance additionally requires the number's leading
    integer to be non-decreasing relative to the highest confirmed so far (letter suffixes on
    the same base number are always accepted), so a trailing Schedule's own restarted
    numbering is never absorbed into the Act's sections.
    """
    lines = text.splitlines()
    n = len(lines)
    header_idxs: list[int] = []
    highest_base = 0
    i = 0
    while i < n:
        m = _HEADER_RE.match(lines[i])
        if not m:
            i += 1
            continue
        num = m.group(1)
        joined = lines[i]
        found = _DASH_RE.search(joined) is not None
        k = 0
        while not found and k < max_heading_lines:
            k += 1
            if i + k >= n or _HEADER_RE.match(lines[i + k]):
                break
            joined += "\n" + lines[i + k]
            found = _DASH_RE.search(joined) is not None
        if found and _base_number(num) >= highest_base:
            header_idxs.append(i)
            highest_base = _base_number(num)
        i += 1

    sections: list[dict[str, Any]] = []
    for pos, start in enumerate(header_idxs):
        end = header_idxs[pos + 1] if pos + 1 < len(header_idxs) else n
        span = "\n".join(lines[start:end])
        m = _HEADER_RE.match(lines[start])
        assert m is not None
        num = m.group(1)
        after_num = span[len(lines[start][: m.start(2)]) :]
        dash_match = _DASH_RE.search(after_num)
        assert dash_match is not None  # guaranteed by the header_idxs selection above
        title = re.sub(r"\s+", " ", after_num[: dash_match.start()]).strip()
        body = re.sub(r"\s+", " ", after_num[dash_match.end() :]).strip()
        sections.append({"number": num, "title": title, "text": body})
    return sections


def dedupe_ambiguous_sections(
    sections: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split `sections` into (clean, excluded) where `excluded` lists every section number
    that appeared more than once with a different title -- e.g. a state-amendment block
    inserting its own same-numbered section alongside the central Act's own, unrelated,
    later section of that number (verified against the real Indian Penal Code PDF). Neither
    side of a genuine collision is guessed at as "the" text; both are dropped and reported,
    since a wrong guess would put mismatched statute text into the corpus as if it were the
    Act's real, uncontested section.
    """
    by_number: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for s in sections:
        if s["number"] not in by_number:
            order.append(s["number"])
        by_number.setdefault(s["number"], []).append(s)

    clean: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for num in order:
        group = by_number[num]
        if len(group) == 1:
            clean.append(group[0])
        else:
            excluded.append({"number": num, "titles": [g["title"] for g in group]})
    return clean, excluded


def build_corpus(
    *,
    pdf_path: Path,
    act: str,
    year: str,
    site: str,
    item_url: str,
    section_word: str = "Section",
) -> dict[str, Any]:
    raw = run_pdftotext(pdf_path)
    cleaned = strip_watermark(raw)
    body = cleaned[find_body_start(cleaned):]
    sections, excluded = dedupe_ambiguous_sections(split_sections(body))
    work = f"{act} ({year})"
    sha256 = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    documents = [
        {
            "id": f"{act}/{section_word} {s['number']}",
            "act": act,
            "section": f"{section_word} {s['number']}",
            "title": s["title"],
            "text": s["text"],
            "part": "",
        }
        for s in sections
    ]
    return {
        "version": 1,
        "built": datetime.now(UTC).date().isoformat(),
        "source": {
            "site": site,
            "work": work,
            "pages": [
                {
                    "title": work,
                    "url": item_url,
                    "sha256": sha256,
                    "fetched_at": datetime.now(UTC).isoformat(),
                }
            ],
        },
        "documents": documents,
        "excluded_ambiguous": excluded,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--act", required=True)
    parser.add_argument("--year", required=True)
    parser.add_argument("--site", default="indiacode.gov.in")
    parser.add_argument("--item-url", required=True)
    parser.add_argument("--section-word", default="Section")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    corpus = build_corpus(
        pdf_path=args.pdf, act=args.act, year=args.year, site=args.site,
        item_url=args.item_url, section_word=args.section_word,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(corpus, indent=2, ensure_ascii=False), encoding="utf-8")
    print(
        f"{args.act}: {len(corpus['documents'])} sections "
        f"({corpus['documents'][0]['section']} .. {corpus['documents'][-1]['section']}) "
        f"-> {args.out}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
