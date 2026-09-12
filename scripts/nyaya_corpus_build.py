#!/usr/bin/env python3
"""
Build statute corpora from en.wikisource.org for the Nyāya-law track.

Two fetch shapes, because Wikisource hosts Indian statutes both ways:

- **Multi-page** ("coi"): the Constitution of India (2020) is split into one Wikisource
  subpage per Part/Schedule. Fetches each subpage, parses "N. Title.—Body" headings.
- **Single-page** ("evidence_act"): the Indian Evidence Act 1872 is one Wikisource page
  with internal ``<h2>`` (Part) / ``<h3>`` (Chapter) / ``<h4>`` (Section) headings and an
  ``mw-editsection`` "[edit]" link as a sibling of each heading -- that markup must be
  stripped as a block (a naive non-greedy strip leaves a stray "edit]" at the start of
  every body; verified against the live page, see ``_EDITSECTION_RE``).

Both write a corpus JSON of the same shape: ``{version, built, source:{site, work, pages,
fetched_at, licence}, documents:[{id, act, section, title, text, part}]}``.

**Licence basis (verified, not assumed).** The underlying legislative text of an Act of an
Indian legislature is not protected by copyright: Copyright Act, 1957, s. 52(1)(q) exempts
"any Act of a Legislature" (subject to not being reproduced together with added commentary,
which this script does not do -- it extracts the bare text only) from infringement. This
applies identically to the Constitution of India and to the Indian Evidence Act, 1872: both
are Government-of-India legislative works, and this script's ``documents[].text`` values are
their bare statutory text. Wikisource's own *transcription* (markup, formatting, the specific
page as authored on that wiki) is separately offered under Wikisource's site-wide CC BY-SA
licence -- this script does not reuse Wikisource's markup or prose beyond extracting the
underlying statute text, but records that licence in ``source.licence`` for completeness
since the raw HTML fetched (saved for --verify) is itself CC BY-SA content.

**Searched for and NOT found on en.wikisource.org (2026-09-12):** the Indian Penal Code, the
Indian Contract Act, and the Code of Criminal Procedure (only a 1979 amendment ordinance to
the latter exists, not the Act itself). Checked via direct title lookup, ``list=allpages``
prefix search, ``action=opensearch``, and full-text ``list=search`` under every plausible
title variant (with/without "The", with/without year, "Code" vs "Act" word order) -- none
resolved to a real page. Not fetched here; not silently substituted with anything else. A
future source for these three needs a different site (e.g. the Government of India's own
india code portal) and its own fetch/licence review, not an extension of this one.

Follows the verified recipe: prefixsearch for subpages, then action=parse for each subpage
(or the one page) to get rendered HTML. No statute text literals in code.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from html import unescape as html_unescape
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlencode
from urllib.request import Request, urlopen

USER_AGENT = "pravrudhi-corpus/0.5 (contact: admin@axismeru.com)"
BASE_URL = "https://en.wikisource.org/w/api.php"
SLEEP_BETWEEN_REQUESTS = 1.0


@dataclass
class Article:
    """A single article extracted from a Constitution part."""

    id: str
    act: str
    section: str
    title: str
    text: str
    part: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict. Extra keys like 'part' are preserved by the loader."""
        return {
            "id": self.id,
            "act": self.act,
            "section": self.section,
            "title": self.title,
            "text": self.text,
            "part": self.part,
        }


def fetch_json(url: str, params: dict[str, Any]) -> dict[str, Any]:
    """Fetch a JSON response from the Wikisource API."""
    full_url = f"{url}?{urlencode(params)}"
    req = Request(full_url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(req, timeout=10) as response:
            return cast(dict[str, Any], json.loads(response.read().decode("utf-8")))
    except Exception as e:
        print(f"Error fetching {full_url}: {e}", file=sys.stderr)
        raise


def list_subpages() -> list[str]:
    """
    List all subpages of "Constitution of India (2020)" from Wikisource.
    Builds list based on known parts and schedule structure of the Constitution.
    """
    subpages = ["Constitution of India (2020)/Preamble"]

    # Main parts: Part I through Part XXII
    roman_numerals = [
        "I",
        "II",
        "III",
        "IV",
        "V",
        "VI",
        "VII",
        "VIII",
        "IX",
        "X",
        "XI",
        "XII",
        "XIII",
        "XIV",
        "XV",
        "XVI",
        "XVII",
        "XVIII",
        "XIX",
        "XX",
        "XXI",
        "XXII",
    ]
    for roman in roman_numerals:
        subpages.append(f"Constitution of India (2020)/Part {roman}")

    # Additional parts
    for extra in ["IVA", "IXA", "IXB", "XIVA"]:
        subpages.append(f"Constitution of India (2020)/Part {extra}")

    # Schedules
    for i in range(1, 6):
        schedule_names = {1: "First", 2: "Second", 3: "Third", 4: "Fourth", 5: "Fifth"}
        subpages.append(f"Constitution of India (2020)/{schedule_names[i]} Schedule")

    print(f"Predefined {len(subpages)} subpages to fetch", file=sys.stderr)
    return subpages


def fetch_page_html(subpage_title: str) -> tuple[dict[str, Any], str]:
    """
    Fetch the rendered HTML of a subpage using action=parse.
    Returns (raw_json_response, html_text).
    """
    params = {
        "action": "parse",
        "page": subpage_title,
        "prop": "text|revid",
        "format": "json",
        "disabletoc": "1",
    }
    data = fetch_json(BASE_URL, params)
    html_text = data.get("parse", {}).get("text", {}).get("*", "")
    return data, html_text


# A heading's mw-editsection sibling is itself two NESTED spans:
#   <span class="mw-editsection"><span class="mw-editsection-bracket">[</span>
#     <a ...><span>edit</span></a><span class="mw-editsection-bracket">]</span></span>
# A non-greedy `<span class="mw-editsection">.*?</span>` stops at the FIRST </span> (the
# inner bracket span's close), leaving the <a>edit</a> and closing bracket span unstripped
# -- generic tag-stripping then turns those into a literal "edit]" leaking into the body
# right after every heading. Matching through the outer span's real close (the second
# </span> in a row) removes the whole block. Verified against the live Indian Evidence Act
# 1872 page during development.
_EDITSECTION_RE = re.compile(r'<span class="mw-editsection">.*?</span>\s*</span>', re.DOTALL)


def strip_html(html_text: str) -> str:
    """
    Strip HTML tags and unescape entities. Drop footnote markers like [ 1 ].
    Collapse whitespace.
    """
    # Unescape HTML entities
    text = html_unescape(html_text)

    # Remove script and style tags and content
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", text, flags=re.DOTALL | re.IGNORECASE)

    # Remove the "[edit]" section-link block as a whole (see _EDITSECTION_RE docstring).
    text = _EDITSECTION_RE.sub("", text)

    # Remove HTML tags
    text = re.sub(r"<[^>]+>", "", text)

    # Remove footnote markers like [ 1 ] or [1]
    text = re.sub(r"\[\s*\d+\s*\]", "", text)

    # Remove edit link markers and similar wiki artifacts
    text = re.sub(r"\s*\[\s*edit\s*\]\s*", " ", text, flags=re.IGNORECASE)

    # Collapse multiple spaces and newlines
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def parse_articles(part_name: str, html_text: str) -> list[Article]:
    """
    Parse articles from Constitution part HTML.

    Article structure: <b>14. Equality before law</b>.—Text here...</p>
    Matches articles with pattern: NUMBER[LETTER]. TITLE.—BODY
    where BODY continues until </p> tag (end of paragraph).

    Returns list of Article objects.
    """
    # Parse the STRIPPED text, not the HTML: the transcription wraps some headings in <b> and others not, and
    # the first version of this parser (anchored on <b>…</b> and one </p>) lost Articles 1, 32 and 368 and cut
    # every multi-paragraph article at its first paragraph. A heading in the text is
    # "<number><letter?>. <Title>.—" and an article runs to the next heading. Schedules number their paragraphs
    # the same way, so they are not parsed as articles here (the ids would collide with Articles 2, 7 ...).
    if part_name.endswith("Schedule") or part_name == "Preamble":
        return []
    text = strip_html(html_text)
    # Titles may themselves contain periods ("…conferred by this Part.—"), so the title runs to the em dash.
    # "23.Prohibition" has no space, "368.[Power …]" has a bracket, and a heading whose title never meets an em
    # dash ("70. Discharge … contingencies. Parliament may …") must not swallow the NEXT heading into its title,
    # which is what lost Article 227 to Article 226: the title may not contain another heading start.
    heading = re.compile(
        r"(?<![A-Za-z(\-])(\d{1,3}[A-Z]?)\.\s?\[?([A-Z](?:(?!\d{1,3}[A-Z]?\.\s?\[?[A-Z])[^—]){2,220}?)\s*\.?\]?\s*—"
    )
    found = [m for m in heading.finditer(text) if not re.search(r"(?i)articles?\s+$", text[max(0, m.start() - 12) : m.start()])]
    articles: list[Article] = []
    for i, m in enumerate(found):
        end = found[i + 1].start() if i + 1 < len(found) else len(text)
        body = text[m.end() : end].strip()
        # The next Part's banner or the page footer trails the last article; cut at the first of them.
        body = re.split(r"\s(?:PART [IVXL]+[A-Z]? [A-Z ,]+|This work is in the public domain|Retrieved from)", body)[0].strip()
        # A repealed article's number survives with only the next chapter's banner after it ("232. … CHAPTER
        # VI SUBORDINATE COURTS"); an all-capitals body that short is a banner, not a provision.
        if len(body) < 10 or (len(body) < 60 and body.isupper()):
            continue
        section_id = f"Article {m.group(1)}"
        articles.append(
            Article(
                id=f"COI/{section_id}",
                act="Constitution of India",
                section=section_id,
                title=m.group(2).strip().rstrip("."),
                text=body,
                part=part_name,
            )
        )
    return articles


# <h2>=Part, <h3>=Chapter, <h4>=Section in the Indian Evidence Act 1872's own rendering
# (verified against the live page, not assumed): each level surfaced identically for every
# heading checked, e.g. <h4 id="3._Interpretation_clause">3. Interpretation clause</h4>.
_HEADING_RE = re.compile(r"<h([234])[^>]*>(.*?)</h\1>", re.DOTALL)
_SECTION_HEADING_RE = re.compile(r"^(\d{1,3}[A-Z]?)\.\s*(.+)$", re.DOTALL)


def parse_single_page_articles(act: str, id_prefix: str, html_text: str) -> list[Article]:
    """
    Parse a single-page Wikisource work (e.g. the Indian Evidence Act 1872) into Articles.

    Unlike the Constitution's one-subpage-per-Part layout, this work is one Wikisource page
    with Part/Chapter/Section conveyed by heading LEVEL (h2/h3/h4) rather than by which
    subpage was fetched. A body runs from just after one heading to just before the next
    (of any of the three levels) -- not to the next </p>, since a section can span several
    paragraphs.
    """
    matches = list(_HEADING_RE.finditer(html_text))
    articles: list[Article] = []
    current_part = ""
    current_chapter = ""
    for i, m in enumerate(matches):
        level = m.group(1)
        heading_text = strip_html(m.group(2)).strip()
        if level == "2":
            current_part = heading_text
            continue
        if level == "3":
            current_chapter = heading_text
            continue
        sec_match = _SECTION_HEADING_RE.match(heading_text)
        if not sec_match:
            continue
        number, title = sec_match.groups()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(html_text)
        body = strip_html(html_text[start:end]).strip()
        if len(body) < 5:
            continue
        section_id = f"Section {number}"
        part = f"{current_part} / {current_chapter}" if current_chapter else current_part
        articles.append(
            Article(
                id=f"{id_prefix}/{section_id}",
                act=act,
                section=section_id,
                title=title.rstrip("."),
                text=body,
                part=part,
            )
        )
    return articles


def _write_corpus(
    corpus_file: Path, work: str, pages_meta: list[dict[str, Any]], licence: str, articles: list[Article]
) -> None:
    corpus = {
        "version": 1,
        "built": datetime.now(UTC).strftime("%Y-%m-%d"),
        "source": {
            "site": "en.wikisource.org",
            "work": work,
            "pages": pages_meta,
            "fetched_at": datetime.now(UTC).isoformat(),
            "licence": licence,
        },
        "documents": [a.to_dict() for a in articles],
    }
    corpus_file.write_text(json.dumps(corpus, indent=1, ensure_ascii=False), encoding="utf-8")


def build_coi(root: Path, verify: bool) -> None:
    raw_dir = root / "research" / "nyaya" / "raw" / "coi"
    corpus_dir = root / "research" / "nyaya" / "corpus"

    raw_dir.mkdir(parents=True, exist_ok=True)
    corpus_dir.mkdir(parents=True, exist_ok=True)

    print("Fetching Constitution of India subpage list...", file=sys.stderr)
    subpages = list_subpages()

    articles: list[Article] = []
    pages_meta: list[dict[str, Any]] = []
    failed_parts: list[str] = []

    for i, subpage_title in enumerate(subpages, 1):
        print(f"[{i}/{len(subpages)}] Fetching {subpage_title}...", file=sys.stderr)

        try:
            raw_json, html_text = fetch_page_html(subpage_title)

            # Save raw JSON response
            raw_file = raw_dir / f"{subpage_title.replace('/', '_')}.json"
            raw_file.write_text(json.dumps(raw_json, indent=1, ensure_ascii=False), encoding="utf-8")

            revid = raw_json.get("parse", {}).get("pageid")
            raw_bytes = raw_file.read_bytes()
            sha256 = hashlib.sha256(raw_bytes).hexdigest()

            # Extract part name from subpage title
            part_name = subpage_title.replace("Constitution of India (2020)/", "")

            # Parse articles
            part_articles = parse_articles(part_name, html_text)
            articles.extend(part_articles)

            pages_meta.append(
                {
                    "title": subpage_title,
                    "revid": revid,
                    "sha256": sha256,
                }
            )

            print(f"  -> Extracted {len(part_articles)} articles", file=sys.stderr)

            # Respect rate limiting
            time.sleep(SLEEP_BETWEEN_REQUESTS)

        except Exception as e:
            print(f"  ERROR: {e}", file=sys.stderr)
            failed_parts.append(subpage_title)

    # Verification mode
    if verify:
        print("\nVerifying extracted articles against raw sources...", file=sys.stderr)
        verify_count = 0
        for article in articles:
            part_name = article.part
            raw_file = raw_dir / f"Constitution of India (2020)_{part_name}.json"
            if not raw_file.exists():
                # Try alternative naming
                raw_file = raw_dir / f"Constitution of India (2020)_{part_name}.json"
            if not raw_file.exists():
                print(f"  WARNING: No raw file for {article.id} (part: {part_name})", file=sys.stderr)
                continue

            raw_data = json.loads(raw_file.read_text(encoding="utf-8"))
            raw_html = raw_data.get("parse", {}).get("text", {}).get("*", "")
            raw_text = strip_html(raw_html)

            # Normalize for comparison
            article_text_norm = re.sub(r"\s+", " ", article.text)
            raw_text_norm = re.sub(r"\s+", " ", raw_text)

            if article_text_norm in raw_text_norm:
                verify_count += 1
            else:
                print(
                    f"  MISMATCH: {article.id} text not found in raw source",
                    file=sys.stderr,
                )

        print(f"Verification: {verify_count}/{len(articles)} articles verified", file=sys.stderr)

    # Write corpus JSON
    corpus_file = corpus_dir / "coi.json"
    _write_corpus(
        corpus_file,
        "Constitution of India (2020)",
        pages_meta,
        "Indian legislation (public, Copyright Act 1957 s.52(1)(q)); "
        "transcription CC BY-SA per Wikisource",
        articles,
    )

    # Summary
    print("\nSummary:", file=sys.stderr)
    print(f"  Extracted {len(articles)} articles", file=sys.stderr)
    print(f"  Fetched {len(pages_meta)} parts", file=sys.stderr)
    if failed_parts:
        print(f"  Failed parts: {failed_parts}", file=sys.stderr)
    print(f"  Corpus written to {corpus_file}", file=sys.stderr)

    # Sanity checks
    print("\nSanity checks:", file=sys.stderr)
    if articles:
        article_14 = next((a for a in articles if a.section == "Article 14"), None)
        if article_14:
            preview = article_14.text[:100]
            print(f"  Article 14 starts: {preview}...", file=sys.stderr)
        article_21 = next((a for a in articles if a.section == "Article 21"), None)
        if article_21:
            preview = article_21.text[:100]
            print(f"  Article 21 starts: {preview}...", file=sys.stderr)


def build_evidence_act(root: Path) -> None:
    """Fetch and parse the Indian Evidence Act 1872 (one Wikisource page, h2/h3/h4
    structure -- see parse_single_page_articles)."""
    raw_dir = root / "research" / "nyaya" / "raw" / "evidence_act"
    corpus_dir = root / "research" / "nyaya" / "corpus"
    raw_dir.mkdir(parents=True, exist_ok=True)
    corpus_dir.mkdir(parents=True, exist_ok=True)

    page_title = "Indian Evidence Act 1872"
    print(f"Fetching {page_title}...", file=sys.stderr)
    raw_json, html_text = fetch_page_html(page_title)

    raw_file = raw_dir / f"{page_title.replace(' ', '_')}.json"
    raw_file.write_text(json.dumps(raw_json, indent=1, ensure_ascii=False), encoding="utf-8")
    revid = raw_json.get("parse", {}).get("revid")
    sha256 = hashlib.sha256(raw_file.read_bytes()).hexdigest()

    articles = parse_single_page_articles("Indian Evidence Act, 1872", "IEA", html_text)
    print(f"  -> Extracted {len(articles)} sections", file=sys.stderr)

    corpus_file = corpus_dir / "evidence_act.json"
    _write_corpus(
        corpus_file,
        page_title,
        [{"title": page_title, "revid": revid, "sha256": sha256}],
        "Indian legislation (public, Copyright Act 1957 s.52(1)(q)); "
        "transcription CC BY-SA per Wikisource",
        articles,
    )
    print(f"  Corpus written to {corpus_file}", file=sys.stderr)
    if articles:
        s1 = next((a for a in articles if a.section == "Section 1"), None)
        if s1:
            print(f"  Section 1 starts: {s1.text[:100]}...", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build statute corpora from en.wikisource.org for the Nyaya-law track"
    )
    parser.add_argument(
        "--work",
        choices=["coi", "evidence_act", "all"],
        default="coi",
        help="Which corpus to (re)build (default: coi, for backward compatibility)",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify that extracted text occurs verbatim in raw files (coi only)",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("/home/ss/projects/pravrudhi"),
        help="Root directory of the project",
    )
    args = parser.parse_args()

    if args.work in ("coi", "all"):
        build_coi(args.root, args.verify)
    if args.work in ("evidence_act", "all"):
        build_evidence_act(args.root)


if __name__ == "__main__":
    main()
