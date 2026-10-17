# ruff: noqa: E501
"""S3, slice 1: the quashing categories agenda (relief: quashing of an FIR or complaint under BNSS s.528). Pure: plain records in, a page out.

A review agenda, not a finding. The page shows the categories the Supreme Court gave in para 102 of State of Haryana v. Bhajan Lal, as
quoted in Amanullah v. State of Bihar, in the Court's own words, and the papers the user added (listed, not read). It matches no
category to any paper and says nothing about whether any applies. The words ship in an asset that the BUILD-TIME gate
(`scripts/check_s3_quotes.py`) verifies against a sha-pinned archive of a full-text index; production holds no judgment text, so at
run time the only check is that the pinned case exists in the case index (`pin_status`).

Imported only when the flag `wf_b2_quashing_agenda` is on.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

ASSET_DIR = Path(__file__).resolve().parent.parent / "assets" / "quashing"
ASSET = ASSET_DIR / "bhajan_lal_para102.json"
ATTESTATION = ASSET_DIR / "attestation.json"
ROUTE = "/api/v1/quashing-agenda"
RELIEF_ID = "quash_fir_or_complaint"
PIN_CACHE_S = 60.0
#: the closed set of reason codes the pin (and the health route) can report; each names no id, title or year
REASONS = ("asset_changed", "index_unavailable", "case_missing", "case_duplicate", "year_missing", "case_changed")
ELLIPSIS = " ..."

# ---- the fixed strings (R1's rulings; each is hash-pinned in tests/test_quashing_strings.py) ---------------------------------
STANDING_LINE = (
    "A review agenda, not a finding. It lists the categories the Supreme Court gave, as quoted in the judgment named below, and the "
    "papers you added. It says nothing about whether any category applies or whether a petition will succeed."
)
RELIEF_LINE = (
    "Relief: quashing of an FIR or complaint under BNSS s.528 (India Code copy captured 29 Sep 2026, held by us; this page does not "
    "apply the section)."
)
CATEGORIES_LINE = (
    "Categories: as set out in para 102 of State of Haryana v. Bhajan Lal, as quoted in Amanullah v. State of Bihar, decided in {year}, "
    "in the judgment text we hold. The text was copied in when this page was built; this service checks only that the case exists in "
    "its index."
)
HEADING = "Categories in para 102 of State of Haryana v. Bhajan Lal, as quoted in Amanullah v. State of Bihar"
CAUTION_LABEL = "The Court's note of caution (para 103)"
OUR_LINE = (
    "The Court gave these by way of illustration. The list is not a test a petition must meet, and the order carries no priority."
)
QUOTES_LINE = (
    "Quotes are kept as in the indexed text of the judgment that quotes them; no word was changed. The note of caution is shortened; "
    "the last part of the sentence is not shown."
)
ORDER_LINE = "Listed in the order the Court gave them, not by weight."
CODE_NUMBERS_LINE = "Section numbers in the Court's words are those of the Code of Criminal Procedure, 1973."
FACT_LINE = (
    "The Court gave these categories for the exercise of the extraordinary power under Article 226 of the Constitution or the inherent "
    "powers under Section 482 of the Code of Criminal Procedure, 1973."
)
BRIDGE_GUARD = (
    "The judgment was given under the earlier Code. This page does not say that it applies under BNSS s.528; a lawyer decides."
)
ONE_SENTENCE = "This page does not compare any category with your papers."
NOT_ASSESSED = (
    "Not assessed: whether any category applies to your papers; papers that were not added or could not be read; how the High Court "
    "will read the papers; whether a petition will succeed; anything not in the papers you added."
)
PAPERS_CAPTION = "They are listed, not read."
SCREENING_AID = "This is a screening aid, not legal advice. A lawyer decides."
DATA_STATEMENT = "Built and tested on invented text and public court orders only. No lawyer has used this on a live matter."
NO_PAPERS_CAPTION = "No papers added yet."
UNAVAILABLE = (
    "The agenda is not available: this service could not confirm that Amanullah v. State of Bihar is in its index, so the categories "
    "are not shown. A lawyer reads the papers."
)
REFUSAL = "No agenda for this relief yet. For now this page covers quashing of an FIR or complaint under BNSS s.528 only. A lawyer reads the papers."
STORE_OFF = "Stored papers are not available."


def papers_heading(n: int) -> str:
    return f"Papers you added ({n})"


# ---- words this page must never use (a test scans every emitted string; the Court's quoted words are exempt by pin) -----------
BARRED = (
    "ground", "grounds", "supports", "makes out", "made out", "strong", "weak", "will succeed", "likely", "probability", "complete",
    "all grounds", "good case", "met", "satisfied", "maintainable", "petition is available", "binds", "bound by", "settled law", "hopeless",
    "corroborated", "confirmed", "verified", "checked", "single-source", "other copies", "7 not assessed", "0 of 7", "production",
    "all categories", "every category",
)  # fmt: skip
_BARRED_RX = re.compile(
    r"(?<![A-Za-z])(?:" + "|".join(re.escape(b) for b in sorted(BARRED, key=len, reverse=True)) + r")(?![A-Za-z])", re.I
)


def barred_in(text: str) -> list[str]:
    """Barred words in one emitted string (never applied to the Court's quoted words)."""
    return [m.group(0).lower() for m in _BARRED_RX.finditer(text)]


# ---- the asset ---------------------------------------------------------------------------------------------------------------------


class AssetChanged(Exception):  # noqa: N818 - control flow inside the route, never leaves it
    """The shipped quotes are not what the attestation pinned: nothing is served from them."""


@dataclass(frozen=True)
class Quotes:
    year: int
    stem: str
    items: tuple[str, ...]
    caution: str
    pin_case_id: str
    pin_title: str
    pin_year: int


def _collapse(text: str) -> str:
    return " ".join(text.split())


def quote_block_sha256(stem: str, items: list[str], caution: str) -> str:
    return hashlib.sha256(("\n".join([stem, *items, caution]) + "\n").encode("utf-8")).hexdigest()


@lru_cache(maxsize=1)
def load_quotes() -> Quotes:
    """The shipped quotes, checked against the attestation committed beside them (no judgment text is read or needed): the hash of the
    concatenated quote_raw, seven items numbered 1 to 7, the pin equal to the attested row, no repairs. Any difference raises."""
    asset = json.loads(ASSET.read_text(encoding="utf-8"))
    att = json.loads(ATTESTATION.read_text(encoding="utf-8"))
    stem = str(asset["stem"]["quote_raw"])
    items = [str(i["quote_raw"]) for i in asset["items"]]
    caution = str(asset["caution"]["quote_raw"])
    if [i["n"] for i in asset["items"]] != [1, 2, 3, 4, 5, 6, 7]:
        raise AssetChanged("items are not numbered 1 to 7")
    if asset.get("repairs") != []:
        raise AssetChanged("the repair list is not empty")
    if quote_block_sha256(stem, items, caution) != att["quote_block_sha256"]:
        raise AssetChanged("the quotes do not match the attested hash")
    pin = asset["pin"]
    if pin["case_id"] != att["case_id"] or pin["title"] != att["row_title"] or pin["year"] != att["row_year"]:
        raise AssetChanged("the pin differs from the attestation")
    year = pin["year"]
    if isinstance(year, bool) or not isinstance(year, int):
        raise AssetChanged("the pinned year is not a whole number")
    return Quotes(year, stem, tuple(items), caution, str(pin["case_id"]), str(pin["title"]), year)


# ---- the runtime existence pin ---------------------------------------------------------------------------------------------------


def pin_status(index_path: Path | None, timeout_s: float = 2.0) -> str | None:
    """None when the pinned case exists in the case index with the pinned title and year (the ONLY runtime check: this service
    holds no judgment text); otherwise a fixed reason code that names no id or title. Never raises."""
    try:
        q = load_quotes()
    except Exception:  # noqa: BLE001
        return "asset_changed"
    if index_path is None or not Path(index_path).is_file():
        return "index_unavailable"
    deadline = time.monotonic() + timeout_s
    try:
        conn = sqlite3.connect(f"file:{Path(index_path).resolve()}?mode=ro", uri=True)
    except Exception:  # noqa: BLE001
        return "index_unavailable"
    try:
        conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
        rows = conn.execute("SELECT title, year FROM cases WHERE case_id = ?", (q.pin_case_id,)).fetchall()
    except Exception:  # noqa: BLE001
        return "index_unavailable"
    finally:
        conn.close()
    if not rows:
        return "case_missing"
    if len(rows) > 1:
        return "case_duplicate"  # more than one row for the pinned case id: its own code, never folded into "missing"
    title, year = rows[0]
    if year is None:
        return "year_missing"
    if title != q.pin_title or year != q.pin_year:
        return "case_changed"
    return None


_PIN_CACHE: dict[tuple[str, int, int], tuple[float, str | None]] = {}
PIN_CACHE_MAX = 16  # entries kept in all: a handful of index paths at most; the oldest are dropped first


def _cache_key(index_path: Path | None) -> tuple[str, int, int]:
    """Index path + the file's mtime and size: a swap of the file changes the key, so the old answer is never served for the new file."""
    if index_path is None:
        return ("", 0, 0)
    try:
        st = Path(index_path).stat()
        return (str(index_path), st.st_mtime_ns, st.st_size)
    except OSError:
        return (str(index_path), 0, 0)


def pin_status_cached(index_path: Path | None, now: float | None = None, ttl_s: float = PIN_CACHE_S) -> str | None:
    """`pin_status` with a short cache so a bounded read is not repeated on every request. The key is the index path + the file's mtime and
    size (a swap invalidates it at once); an entry older than `ttl_s` is re-read, so neither a PASS nor an UNAVAILABLE outlives the window."""
    key = _cache_key(index_path)
    t = time.monotonic() if now is None else now
    hit = _PIN_CACHE.get(key)
    if hit is not None and 0 <= t - hit[0] < ttl_s:
        return hit[1]
    result = pin_status(index_path)
    for k in [k for k in _PIN_CACHE if k[0] == key[0] and k != key]:
        del _PIN_CACHE[k]  # an index file that changed under the same path leaves no stale entry behind
    _PIN_CACHE[key] = (t, result)
    while len(_PIN_CACHE) > PIN_CACHE_MAX:
        del _PIN_CACHE[min(_PIN_CACHE, key=lambda k: _PIN_CACHE[k][0])]
    return result


# ---- the page ------------------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Doc:
    doc_id: str
    name: str
    n_pages: int
    pages_without_text: tuple[int, ...]


def build_page(docs: list[Doc]) -> dict[str, Any]:
    """The page: the Court's words (display fold = whitespace runs collapsed, nothing else), then the papers (names only)."""
    q = load_quotes()
    return {
        "standing_line": STANDING_LINE,
        "relief_line": RELIEF_LINE,
        "categories_line": CATEGORIES_LINE.format(year=q.year),
        "heading": HEADING,
        "stem": _collapse(q.stem),
        "caution": {
            "label": CAUTION_LABEL,
            "quote": _collapse(q.caution) + ELLIPSIS,
            "our_line": OUR_LINE,
        },
        "categories": [{"number": k, "text": _collapse(t)} for k, t in enumerate(q.items, 1)],
        "order_line": ORDER_LINE,
        "quotes_line": QUOTES_LINE,
        "code_numbers_line": CODE_NUMBERS_LINE,
        "fact_line": FACT_LINE,
        "bridge_guard": BRIDGE_GUARD,
        "one_sentence": ONE_SENTENCE,
        "not_assessed": NOT_ASSESSED,
        "papers": {
            "heading": papers_heading(len(docs)),
            "caption": PAPERS_CAPTION if docs else NO_PAPERS_CAPTION,
            "documents": [{"doc_id": d.doc_id, "name": d.name} for d in docs],
        },
        "screening_aid": SCREENING_AID,
        "data_statement": DATA_STATEMENT,
    }
