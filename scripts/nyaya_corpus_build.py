#!/usr/bin/env python3
"""
Build the Constitution of India corpus from Wikisource.

Fetches all subpages of "Constitution of India (2020)" from en.wikisource.org,
saves raw JSON responses, parses to articles, and writes a corpus JSON file.
Supports --verify mode to cross-check text against raw sources.

Follows the verified recipe: prefixsearch for subpages, then action=parse
for each subpage to get rendered HTML. No statute text literals in code.
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


def strip_html(html_text: str) -> str:
    """
    Strip HTML tags and unescape entities. Drop footnote markers like [ 1 ].
    Collapse whitespace.
    """
    # Unescape HTML entities
    text = html_unescape(html_text)

    # Remove script and style tags and content
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", text, flags=re.DOTALL | re.IGNORECASE)

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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build Constitution of India corpus from Wikisource"
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify that extracted text occurs verbatim in raw files",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("/home/ss/projects/pravrudhi"),
        help="Root directory of the project",
    )
    args = parser.parse_args()

    root = args.root
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
    if args.verify:
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
    corpus = {
        "version": 1,
        "built": datetime.now(UTC).strftime("%Y-%m-%d"),
        "source": {
            "site": "en.wikisource.org",
            "work": "Constitution of India (2020)",
            "pages": pages_meta,
            "fetched_at": datetime.now(UTC).isoformat(),
            "licence": "Indian legislation (public); transcription CC BY-SA per Wikisource",
        },
        "documents": [a.to_dict() for a in articles],
    }
    corpus_file.write_text(json.dumps(corpus, indent=1, ensure_ascii=False), encoding="utf-8")

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


if __name__ == "__main__":
    main()
