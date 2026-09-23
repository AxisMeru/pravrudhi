"""SQLite FTS5 case-law index over real judgment text (P3, `docs/decisions/LEG-PLAN-2026-09-23.md`).

Two sources, one schema:

- The Supreme Court judgment PDFs at `/home/ss/fusion-project/corpus-raw/supreme_court_judgments`
  (26,688 files, `pypdf` extraction; `extract_pdf_text` reports which PDFs fail to yield text at all --
  scanned images with no OCR layer -- rather than silently indexing them as empty).
- `opennyaiorg/InJudgements` (Apache-2.0) parquet shards at `/home/ss/fusion-project/corpus-raw/hf/injudgements`,
  already plain text in the `Text` column; confirmed schema:
  `Titles, Court_Name, Cites, Cited_by, Doc_url, Text, Doc_size, Case_Type, Court_Type, Court_Name_Normalized`.

The index is a build artifact (a `.sqlite3` file under a path the caller chooses, typically
`research/nyaya/case_index/`), never committed to git -- see `research/` in `.gitignore`.

`mine_aliases` finds "X v. Y, (yyyy) N SCC P" style party-name-to-citation strings inside the same text
being indexed, so `verify()` (not yet written) can resolve a case by name as well as by citation.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError


@dataclass(frozen=True)
class CaseRecord:
    """One indexed document: an SC judgment PDF or an InJudgements row."""

    case_id: str
    title: str
    court: str
    year: int | None
    source: str  # "sc_pdf" | "injudgements"
    path_or_url: str
    text: str


@dataclass(frozen=True)
class ExtractionResult:
    """Coverage bookkeeping for one PDF extraction attempt."""

    path: Path
    text: str
    ok: bool  # False when pypdf raised or returned no extractable text at all
    error: str | None = None


def case_id(source: str, key: str) -> str:
    return hashlib.sha256(f"{source}|{key}".encode()).hexdigest()[:24]


def extract_pdf_text(path: Path) -> ExtractionResult:
    """Extract all page text from one PDF. Never raises -- a failure is reported in the result, since a
    26,688-file batch cannot afford one bad PDF to kill the run, and silently skipping it would corrupt the
    coverage count the plan asks for."""
    try:
        reader = PdfReader(path)
        pages = [p.extract_text() or "" for p in reader.pages]
        text = "\n".join(pages)
    except (PdfReadError, OSError, ValueError) as exc:
        return ExtractionResult(path=path, text="", ok=False, error=f"{type(exc).__name__}: {exc}")
    if not text.strip():
        return ExtractionResult(path=path, text="", ok=False, error="no extractable text (scanned image?)")
    return ExtractionResult(path=path, text=text, ok=True)


def iter_sc_pdfs(root: Path) -> Iterator[Path]:
    """Every judgment PDF under `root`, one subdirectory per year (matches the corpus layout confirmed by
    hand: `root/1950/*.PDF` ... `root/2025/*.PDF`)."""
    yield from sorted(root.glob("*/*.PDF")) or sorted(root.glob("*/*.pdf"))


_TITLE_FROM_FILENAME = re.compile(r"^(?P<parties>.+?)_on_\d+_\w+_(?P<year>\d{4})_\d+$")


def title_and_year_from_filename(path: Path) -> tuple[str, int | None]:
    """The corpus names each PDF `<Parties>_on_<day>_<Month>_<year>_<n>.PDF` (confirmed by directory
    listing); this recovers a readable title and the decision year without opening the file."""
    m = _TITLE_FROM_FILENAME.match(path.stem)
    if not m:
        return path.stem.replace("_", " "), None
    parties = m.group("parties").replace("_", " ")
    return parties, int(m.group("year"))


# "X v. Y, (yyyy) N SCC P" or "X v. Y (yyyy) N SCC P" -- MINED from InJudgements shard0 (see
# tests/test_case_index.py for the exact strings this pattern was built and checked against).
_ALIAS = re.compile(
    r"(?P<p1>[A-Z][A-Za-z.&\s]{2,60}?)\s+v\.?\s+(?P<p2>[A-Z][A-Za-z.&\s]{2,60}?),?\s*"
    r"\((?P<year>\d{4})\)\s*(?P<volume>\d+)\s*SCC\s*(?P<page>\d+)"
)


@dataclass(frozen=True)
class CitationAlias:
    party_1: str
    party_2: str
    year: int
    volume: int
    page: int
    span: tuple[int, int]

    @property
    def citation(self) -> str:
        return f"({self.year}) {self.volume} SCC {self.page}"


def mine_aliases(text: str) -> list[CitationAlias]:
    """Find every "party v. party, (year) vol SCC page" alias in `text`."""
    out = []
    for m in _ALIAS.finditer(text):
        out.append(
            CitationAlias(
                party_1=m["p1"].strip(),
                party_2=m["p2"].strip(),
                year=int(m["year"]),
                volume=int(m["volume"]),
                page=int(m["page"]),
                span=m.span(),
            )
        )
    return out


SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS cases USING fts5(
    case_id UNINDEXED,
    title,
    court,
    year UNINDEXED,
    source UNINDEXED,
    path_or_url UNINDEXED,
    text
);

CREATE TABLE IF NOT EXISTS citation_aliases (
    party_1 TEXT NOT NULL,
    party_2 TEXT NOT NULL,
    citation TEXT NOT NULL,
    case_id TEXT,
    PRIMARY KEY (party_1, party_2, citation)
);
"""


def open_index(db_path: Path) -> sqlite3.Connection:
    """Open (creating if needed) the FTS5 index at `db_path`."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    return conn


def insert_case(conn: sqlite3.Connection, record: CaseRecord) -> None:
    conn.execute(
        "INSERT INTO cases (case_id, title, court, year, source, path_or_url, text) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (record.case_id, record.title, record.court, record.year, record.source, record.path_or_url, record.text),
    )
    for alias in mine_aliases(record.text):
        conn.execute(
            "INSERT OR IGNORE INTO citation_aliases (party_1, party_2, citation, case_id) VALUES (?, ?, ?, ?)",
            (alias.party_1, alias.party_2, alias.citation, record.case_id),
        )


def search(conn: sqlite3.Connection, query: str, limit: int = 10) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        "SELECT case_id, title, court, year, source, path_or_url, snippet(cases, 6, '[', ']', '...', 20) AS snip "
        "FROM cases WHERE cases MATCH ? ORDER BY rank LIMIT ?",
        (query, limit),
    )
    return cur.fetchall()


def lookup_alias(conn: sqlite3.Connection, party_1: str, party_2: str) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        "SELECT * FROM citation_aliases WHERE party_1 = ? AND party_2 = ?",
        (party_1, party_2),
    )
    return cur.fetchall()
