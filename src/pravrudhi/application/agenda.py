# ruff: noqa: E501
"""A5, slice 1: the review agenda for cheque dishonour under NI Act s.138. Pure: plain document records in, a page out.

An agenda is a list of where to look, never a finding. Tier 1 holds ONLY the statute's own words (every quote is checked to be a
substring of the pinned s.138 text, whose sha256 is checked at load). Tier 2 is a lawyer's list and is hidden while
`lawyer_confirmed` is false. No paper is mapped to an ingredient. There is no "cited" column: no passage of any paper is
searched. Paper types are declared by the user; the file name is only a stated fallback.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

ASSETS = Path(__file__).resolve().parent.parent / "assets"
AGENDA_ASSET = ASSETS / "agenda" / "s138.json"
ROUTE = "/api/v1/review-agenda"
OFFENCE_ID = "s138_ni_act"

# ---- the fixed strings (R1's words, addenda 1 to 3) ---------------------------------------------------------------------------
STANDING_LINE = (
    "A review agenda, not a finding. It lists the ingredients of the offence and the papers to look in. It says nothing about how "
    "the case will turn out. It works only from the papers you added; for a paper it cannot read, it tells you which paper to check."
)
SOURCE_LINE = (
    "Checked against the Negotiable Instruments Act, 1881, s.138 text held by us (India Code copy captured 24 Sep 2026). "
    "The ingredients below are the section's own words."
)
DATA_STATEMENT = "Built and tested on invented text only. No lawyer has used this on a live matter."
OWNERSHIP_LINE = (
    "This page lists the ingredients and where to look. The dates in your papers are on the chronology page; "
    "the s.138 time steps are on the cheque checklist."
)
NOT_ASSESSED = (
    "Whether the papers show any ingredient.",
    "Papers that were not added or could not be read.",
    "How a court will read the facts.",
    "Anything not in the papers you added.",
)
HEADINGS = {
    "ingredients": "The ingredients, in the section's own words",
    "statute_names": "Papers the statute names for this offence",
    "lawyer_listed": "Papers a lawyer has listed for this offence",
    "not_added": "Papers not added yet that may matter",
}
TAGS = {"statute_text": "Named in the section", "lawyer_practice": "From a lawyer's list"}  # the source of each row, by its basis
QUOTES_LINE = (
    "Quotes are kept as printed in the India Code copy we hold, including any typing slips. "
    "A note in square brackets beside a quote is that copy's own footnote on an amendment."
)
ADDED, NOT_ADDED = "Added", "Not added yet"
NAME_ONLY = "A file whose name suggests this paper was added; its content was not checked."
HELD_NO_FILE = "Declared by you; no file added"
BASIS_DECLARED = "Paper type declared by you"
BASIS_FILE_NAME = "Paper type taken from the file name"
NONE_OUTSTANDING = (
    "No statute-named paper is outstanding among the paper types you declared. The complaint is named in s.142, which we do not hold."
)
REFUSAL = (
    "No agenda for this offence yet. For now this page covers cheque dishonour under s.138 only. "
    "For any other offence, a lawyer reads the papers."
)
NO_PAPERS = "There are no papers to read. Add papers first."
BAD_TYPE = "A paper type you declared names a paper that is not in this matter, or a type we do not list."
# ---- passage pointers (Track B's quality pass, 17 Oct): PENDING R1 for every string in this block --------------------------------
POINTER_HEADING = "Where the papers use words from this ingredient"
POINTER_LINE = (
    "These passages contain words that belong to this ingredient. They are places to start reading, not a finding that the papers show it."
)
NO_POINTER_LINE = "No passage with these words was found in the papers you added. That does not mean the papers lack it."
MAX_POINTERS = 5
POINTER_QUOTE = 300


def papers_read_heading(n: int) -> str:
    return f"Papers read ({n})"


def pages_note(blank: int, total: int) -> str | None:
    """R1's three notes (never "0 of M"): one page only, one of several, several of several."""
    if blank <= 0:
        return None
    if blank == 1 and total == 1:
        return "1 page had no text: check it yourself."
    if blank == 1:
        return f"1 of {total} pages had no text: check it yourself."
    return f"{blank} of {total} pages had no text: check them yourself."


def not_read_line(name: str) -> str:
    return f"Not added yet: a file named '{name}' was added but not read (scanned pages), so check it yourself."


# ---- the words this page must never use (a test scans every emitted string for each) -----------------------------------------
BARRED = (
    "missing", "gap", "lacking", "deficient", "defect", "weak", "strong", "fatal",
    "weak case", "strong case", "will fail", "likely quashed", "no offence made out", "probability", "score", "missing evidence",
    "inconsistent", "inconsistency", "contradiction", "contradict", "complete", "completeness", "all papers", "every paper",
    "usually settles", "would settle", "settle", "evidence of", "proves", "proved", "shows that the prosecution",
    "likelihood", "chance of", "nothing missing",
)  # fmt: skip
_BARRED_RX = re.compile(r"(?<![A-Za-z])(?:" + "|".join(re.escape(b) for b in sorted(BARRED, key=len, reverse=True)) + r")(?![A-Za-z])", re.I)


def barred_in(text: str) -> list[str]:
    """Barred words and phrases in one string (statute quotes are exempt by the caller: they are checked against the pinned text)."""
    return [m.group(0).lower() for m in _BARRED_RX.finditer(text)]


# ---- the asset and the pinned statute text ---------------------------------------------------------------------------------------


class AssetChanged(Exception):
    """The held s.138 text or the agenda asset is not what this code was pinned to: nothing is served from it."""


@lru_cache(maxsize=1)
def footnotes() -> dict[str, str]:
    """The held copy's own footnotes ("3 = Subs. by Act 55 of 2002 s.7 ..."), by number."""
    st = json.loads(AGENDA_ASSET.read_text(encoding="utf-8"))["statute"]
    held = json.loads((ASSETS / st["asset"]).read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for part in str(held["provenance"]["footnotes"]).split(";"):
        m = re.match(r"\s*(\d+)\s*=\s*(.+?)\s*$", part)
        if m:
            out[m.group(1)] = m.group(2)
    return out


_FOOTNOTED = re.compile(r"(\d+) \[([^\]]*)\]")


def show_statute(quote: str | None) -> str | None:
    """A quote as the page shows it: the held text's bracketed amendment ("3 [within thirty days]") is shown as the amended words
    with its source footnote marked explicitly; every other word is untouched. The raw quote is kept beside it."""
    if quote is None:
        return None
    notes = footnotes()
    return _FOOTNOTED.sub(lambda m: f"{m.group(2)} [footnote {m.group(1)}: {notes[m.group(1)]}]" if m.group(1) in notes else m.group(0), quote)


@dataclass(frozen=True)
class Passage:
    doc_id: str
    name: str
    page: int
    start: int
    end: int
    text: str


def _cue_regex(cue: str) -> re.Pattern[str]:
    return re.compile(r"(?<!\w)" + r"\s+".join(re.escape(w) for w in cue.casefold().split()) + r"(?!\w)")


def find_pointers(asset: dict[str, Any], passages: list[Passage]) -> dict[str, list[dict[str, Any]]]:
    """For each ingredient, up to MAX_POINTERS passages (in paper order) that contain one of its cue words or phrases. A pointer is a
    place to start reading: the passage text itself (cut to POINTER_QUOTE around the cue), its paper, page and offsets, and the cue."""
    out: dict[str, list[dict[str, Any]]] = {}
    for ing in asset["ingredients"]:
        rxs = [(c, _cue_regex(c)) for c in ing.get("cues", [])]
        found: list[dict[str, Any]] = []
        for p in passages:
            low = p.text.casefold()
            hit = next(((c, m) for c, rx in rxs if (m := rx.search(low)) is not None), None)
            if hit is None:
                continue
            cue, m = hit
            a = max(0, min(m.start() - POINTER_QUOTE // 2, len(p.text) - POINTER_QUOTE))
            b = min(len(p.text), a + POINTER_QUOTE)
            found.append(
                {
                    "doc_id": p.doc_id, "name": p.name, "page": p.page, "start": p.start + a, "end": p.start + b,
                    "cue": cue, "quote": p.text[a:b],
                }
            )
            if len(found) >= MAX_POINTERS:
                break
        out[str(ing["id"])] = found
    return out


@lru_cache(maxsize=1)
def load_asset() -> dict[str, Any]:
    asset: dict[str, Any] = json.loads(AGENDA_ASSET.read_text(encoding="utf-8"))
    st = asset["statute"]
    held = json.loads((ASSETS / st["asset"]).read_text(encoding="utf-8"))
    text = held[st["key"]]
    if hashlib.sha256(text.encode("utf-8")).hexdigest() != st["sha256"]:
        raise AssetChanged("the held s.138 text does not match the sha256 the agenda was pinned to")
    for q in [i["quote"] for i in asset["ingredients"]] + [t["quote"] for t in asset["tier_statute"] if t["quote"]]:
        if q not in text:
            raise AssetChanged(f"a statute quote is not in the pinned s.138 text: {q[:40]}")
    return asset


# ---- documents -------------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Doc:
    doc_id: str
    name: str
    n_pages: int
    pages_without_text: tuple[int, ...] = ()

    @property
    def read(self) -> bool:
        """A paper is read when at least one of its pages has text; a file with no pages, or whose every page is blank, was not read."""
        return self.n_pages > 0 and len(self.pages_without_text) < self.n_pages


def _name_type(name: str, asset: dict[str, Any]) -> str | None:
    """The type the FILE NAME suggests: whole words and phrases (the name split on non-alphanumerics), and only when exactly ONE
    type's words are in it. A name that fits two or more types ("Cheque_return_memo.pdf") is ambiguous and gets no type."""
    tokens = re.findall(r"[a-z0-9]+", name.lower())

    def has(phrase: str) -> bool:
        w = phrase.split()
        return any(tokens[i : i + len(w)] == w for i in range(len(tokens) - len(w) + 1))

    hits = [str(tid) for tid, phrases in asset["paper_types_from_file_name"].items() if any(has(p) for p in phrases)]
    if len(hits) != 1:
        return None
    others = {w for tid, words in asset["paper_types_hints"].items() if tid != hits[0] for w in words}
    return None if others & set(tokens) else hits[0]


def build_agenda(
    docs: list[Doc],
    declared: dict[str, str] | None = None,
    offence: str = OFFENCE_ID,
    ownership: bool = False,
    held_without_file: list[str] | None = None,
    passages: list[Passage] | None = None,
) -> dict[str, Any]:
    """The page. `declared` maps doc_id -> paper type id (the user's word); any doc without one gets the file-name fallback.
    `held_without_file`: paper types the user says they hold but have not added as a file (a scan has no text for A2 to keep).
    `ownership`: the line naming the chronology page and the cheque checklist is emitted only when both are ON in this deployment."""
    asset = load_asset()
    if offence != OFFENCE_ID:
        raise ValueError(REFUSAL)
    declared = declared or {}
    known = {t["id"] for t in asset["paper_types"]}
    by_id = {d.doc_id: d for d in docs}
    held = list(dict.fromkeys(held_without_file or []))
    if any(k not in by_id or v not in known for k, v in declared.items()) or any(h not in known or h == "other" for h in held):
        raise ValueError(BAD_TYPE)
    typed: list[dict[str, Any]] = []
    unread: list[str] = []
    for d in docs:
        if not d.read:
            unread.append(not_read_line(d.name))
            continue
        ptype: str | None
        if d.doc_id in declared:
            ptype, basis = declared[d.doc_id], BASIS_DECLARED
        else:
            ptype, basis = _name_type(d.name, asset), BASIS_FILE_NAME
        typed.append(
            {
                "doc_id": d.doc_id,
                "name": d.name,
                "pages": d.n_pages,
                "pages_without_text": len(d.pages_without_text),
                "pages_note": pages_note(len(d.pages_without_text), d.n_pages),
                "paper_type": ptype if ptype in known else None,
                "paper_type_basis": basis if ptype in known else None,
            }
        )
    have: dict[str, list[dict[str, Any]]] = {}
    for t in typed:
        if t["paper_type"]:
            have.setdefault(str(t["paper_type"]), []).append(t)

    def row(entry: dict[str, Any]) -> dict[str, Any]:
        ptype = entry["paper_type"]
        out: dict[str, Any] = {
            "id": entry["id"],
            "label": entry["label"],
            "proviso": entry.get("proviso"),
            "basis": entry["basis"],
            "section": entry.get("section"),
            "statute_words": show_statute(entry.get("quote")),
            "statute_words_raw": entry.get("quote"),
            "tag": TAGS[entry["basis"]],
        }
        if ptype is None:
            out["status"] = None
            return out
        hits = have.get(ptype, [])
        out["papers"] = [{"doc_id": h["doc_id"], "name": h["name"], "paper_type_basis": h["paper_type_basis"]} for h in hits]
        if any(h["paper_type_basis"] == BASIS_DECLARED for h in hits):
            out["status"] = ADDED
        elif ptype in held:
            out["status"] = HELD_NO_FILE  # the user's explicit declaration beats a file-name guess
        elif hits:
            out["status"] = NAME_ONLY  # only a file-name guess: never shown as plain "Added"
        else:
            out["status"] = NOT_ADDED
        return out

    pointers = None if passages is None else find_pointers(asset, passages)
    tier1 = [row(e) for e in asset["tier_statute"]]
    confirmed = bool(asset["lawyer_confirmed"])
    tier2 = [row(e) for e in asset["tier_lawyer"]] if confirmed else None
    outstanding = [r for r in tier1 + (tier2 or []) if r["status"] == NOT_ADDED]
    tier1_outstanding = [r for r in tier1 if r["status"] in (NOT_ADDED, NAME_ONLY)]  # a name-only guess is not a declaration
    return {
        "statement": DATA_STATEMENT,
        "standing_line": STANDING_LINE,
        "source_line": SOURCE_LINE,
        "ownership_line": OWNERSHIP_LINE if ownership else None,
        "quotes_line": QUOTES_LINE,
        "offence": dict(asset["offence"]),
        "headings": {k: v for k, v in HEADINGS.items() if confirmed or k != "lawyer_listed"},  # a hidden tier leaves no trace
        "ingredients": [
            {
                "id": i["id"], "proviso": i["proviso"], "section": asset["statute"]["section"], "statute_words": show_statute(i["quote"]),
                "statute_words_raw": i["quote"],
                "where_mentioned": (
                    None
                    if pointers is None
                    else {
                        "heading": POINTER_HEADING,
                        "line": POINTER_LINE if pointers[i["id"]] else NO_POINTER_LINE,
                        "pointers": pointers[i["id"]],
                    }
                ),
            }
            for i in asset["ingredients"]
        ],
        "statute_names": {"heading": HEADINGS["statute_names"], "rows": tier1},
        "lawyer_listed": (
            {"heading": HEADINGS["lawyer_listed"], "rows": tier2, "confirmed_by": asset["confirmed_by"], "confirmed_on": asset["confirmed_on"]}
            if tier2 is not None
            else None
        ),
        "lawyer_confirmed": confirmed,
        "papers_read": {"heading": papers_read_heading(len(typed)), "n": len(typed), "papers": typed},
        "not_added": {
            "heading": HEADINGS["not_added"] if outstanding or unread else None,  # no heading over an empty list
            "rows": [{"id": r["id"], "label": r["label"], "basis": r["basis"]} for r in outstanding] + [{"line": u} for u in unread],
            "none_line": NONE_OUTSTANDING if not tier1_outstanding and not unread else None,
        },
        "not_assessed": list(NOT_ASSESSED),
        "source": {"statute_sha256": asset["statute"]["sha256"], "asset_version": asset["version"]},
    }
