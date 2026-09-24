"""Tests for `application.case_index.verify` (P3): resolve a citation against the index, then check a
quoted claim against the resolved case's own text.

Scope, stated up front rather than silently: `verify` currently resolves only SCC-family citations
("(year) N SCC page"), because `mine_aliases` (case_index.py) only mines that one "party v. party, (year) N
SCC page" shape from judgment text. AIR/SCC OnLine/INSC/SCR/HC-neutral citations correctly come back
NOT_IN_INDEX today -- an honest "we have no alias evidence for this", never "fake" -- rather than a silent
false negative dressed up as a real check. Widening the alias miner to the other reporter families is
tracked as follow-up, not done here.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from pravrudhi.application.case_index import CaseRecord, insert_case, open_index
from pravrudhi.application.verify import VerifyResult, verify

# MINED: opennyaiorg/InJudgements shard0 `Text` column (same source as test_case_index.py's alias fixture).
_CITING_TEXT = (
    "This Court in Narandas Karsondas v. S.A. Kamtam and Anr. (1977) 3 SCC 247 clarified the "
    "position on specific performance and time being of the essence in a contract for sale."
)

# A real-shaped SC judgment document: title from the corpus's own filename convention
# (`<Parties>_on_<day>_<Month>_<year>_<n>.PDF` -> "Narandas Karsondas vs S A Kamtam ..."), body text
# containing the actual holding a citing document might quote.
_RESOLVED_CASE_TEXT = (
    "In a suit for specific performance, time is not ordinarily of the essence of the contract "
    "for sale of immovable property unless the parties expressly intend it to be so."
)


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = open_index(tmp_path / "verify_index.sqlite3")
    # The citing document -- this is what populates citation_aliases via insert_case's mine_aliases call.
    insert_case(
        conn,
        CaseRecord(
            case_id="citer1",
            title="Some Later Case v Someone Else",
            court="Supreme Court",
            year=2005,
            source="sc_pdf",
            path_or_url="/fake/citer.pdf",
            text=_CITING_TEXT,
        ),
    )
    # The resolved case itself -- title matches the party names from the alias, as it would for a real SC
    # PDF (filename-derived title "Narandas Karsondas vs S A Kamtam ...").
    insert_case(
        conn,
        CaseRecord(
            case_id="resolved1",
            title="Narandas Karsondas vs S A Kamtam and Anr",
            court="Supreme Court",
            year=1977,
            source="sc_pdf",
            path_or_url="/fake/narandas.pdf",
            text=_RESOLVED_CASE_TEXT,
        ),
    )
    conn.commit()
    yield conn
    conn.close()


def test_verified_when_case_resolves_and_quote_matches(db: sqlite3.Connection) -> None:
    result = verify(db, "(1977) 3 SCC 247", "time is not ordinarily of the essence of the contract")
    assert result == VerifyResult.VERIFIED


def test_verified_despite_pdf_line_wrap_hyphenation_in_the_indexed_text(db: sqlite3.Connection) -> None:
    # Real noise seen extracting the SC PDF corpus (P3 measurement): pdftotext/pypdf breaks a word across a
    # line with a hyphen at the wrap point. The quote is checked against the SAME text either way -- this
    # pins that a hyphen-broken word in the indexed document doesn't defeat a real match.
    db.execute(
        "UPDATE cases SET text = ? WHERE case_id = 'resolved1'",
        (
            "In a suit for specific perfor-\nmance, time is not ordinarily of the es-\nsence of the "
            "contract for sale of immovable property unless the parties expressly intend it to be so.",
        ),
    )
    db.commit()
    result = verify(db, "(1977) 3 SCC 247", "time is not ordinarily of the essence of the contract")
    assert result == VerifyResult.VERIFIED


def test_verified_despite_a_ligature_in_the_indexed_text(db: sqlite3.Connection) -> None:
    # pypdf sometimes extracts the "fi"/"fl" ligature glyph as a single unicode codepoint rather than two
    # ASCII letters -- real behaviour, not a hypothetical.
    db.execute("UPDATE cases SET text = ? WHERE case_id = 'resolved1'", ("A suit for speciﬁc performance.",))
    db.commit()
    result = verify(db, "(1977) 3 SCC 247", "A suit for specific performance.")
    assert result == VerifyResult.VERIFIED


def test_exists_quote_not_found_when_case_resolves_but_quote_absent(db: sqlite3.Connection) -> None:
    result = verify(db, "(1977) 3 SCC 247", "this sentence never appears in that judgment at all")
    assert result == VerifyResult.EXISTS_QUOTE_NOT_FOUND


def test_not_in_index_when_citation_has_no_alias_evidence(db: sqlite3.Connection) -> None:
    result = verify(db, "(1994) 3 SCC 569", "any proposition")
    assert result == VerifyResult.NOT_IN_INDEX


def test_not_in_index_for_reporter_family_alias_mining_does_not_cover(db: sqlite3.Connection) -> None:
    # AIR is a real, parseable citation (citations.py handles it) but mine_aliases only covers SCC-shaped
    # cites -- honestly NOT_IN_INDEX, never "fake", for a real gap in coverage.
    result = verify(db, "AIR 1958 SC 398", "any proposition")
    assert result == VerifyResult.NOT_IN_INDEX


def test_malformed_when_citation_does_not_parse(db: sqlite3.Connection) -> None:
    result = verify(db, "not a citation at all", "any proposition")
    assert result == VerifyResult.MALFORMED


def test_malformed_when_multiple_citations_in_one_string(db: sqlite3.Connection) -> None:
    # verify() takes exactly one citation -- an input carrying two is a caller error, not a NOT_IN_INDEX.
    result = verify(db, "AIR 1958 SC 398 and (1977) 3 SCC 247", "any proposition")
    assert result == VerifyResult.MALFORMED


def test_near_duplicate_party_names_are_not_a_false_conflict(db: sqlite3.Connection) -> None:
    # Real bug found measuring resolution precision on 100 real citations (LEG-PLAN P3): two citing
    # documents naming the SAME real case with trivially different text -- OCR line-break noise or a
    # leading filler word the citations.py fix didn't fully catch -- inflated CONFLICT far past real
    # ambiguity. "Narandas Karsondas" vs "In Narandas  Karsondas" (leading filler + double space) is the
    # same party, not a second case.
    insert_case(
        db,
        CaseRecord(
            case_id="citer2",
            title="A Different Later Case",
            court="Supreme Court",
            year=2010,
            source="sc_pdf",
            path_or_url="/fake/citer2.pdf",
            text="This Court in In Narandas  Karsondas v. S. A.  Kamtam (1977) 3 SCC 247 held that",
        ),
    )
    db.commit()
    result = verify(db, "(1977) 3 SCC 247", "time is not ordinarily of the essence of the contract")
    assert result == VerifyResult.VERIFIED


def test_fuzzy_confirmation_resolves_a_real_spelling_variant(db: sqlite3.Connection) -> None:
    # Real verify() miss (P3 audit, 2026-09-24, reviewer 2, idx 69): "Ramchandran" (as mined from a citing
    # document) vs "Ramachandran" (the actual corpus title) is a genuine spelling variant, not a different
    # party -- exact FTS token match misses it. A conservative fuzzy confirmation (token-set-ratio >= 0.9)
    # must resolve it, but ONLY as a confirmation of a candidate the citation key already pointed to, never
    # to invent a match with no citation evidence at all.
    insert_case(
        db,
        CaseRecord(
            case_id="citer3",
            title="A Later Case",
            court="Supreme Court",
            year=2011,
            source="sc_pdf",
            path_or_url="/fake/citer3.pdf",
            text="This Court in Ramchandran & Ors. v. State of Kerala (2011) 9 SCC 257 held that",
        ),
    )
    insert_case(
        db,
        CaseRecord(
            case_id="ramachandran-kerala",
            title="Ramachandran Ors vs State Of Kerala",
            court="Supreme Court",
            year=2011,
            source="sc_pdf",
            path_or_url="/fake/ramachandran.pdf",
            text="The appellant's real name is spelled Ramachandran throughout this judgment, correctly.",
        ),
    )
    db.commit()
    result = verify(db, "(2011) 9 SCC 257", "The appellant's real name is spelled Ramachandran throughout")
    assert result == VerifyResult.VERIFIED


def test_fuzzy_confirmation_never_invents_a_match_with_no_citation_evidence(db: sqlite3.Connection) -> None:
    # No alias at all for this citation -- fuzzy matching must never kick in without a citation-key hit
    # first, even if a case with a similar-looking title happens to exist in the index.
    insert_case(
        db,
        CaseRecord(
            case_id="unrelated",
            title="Ramachandran Ors vs State Of Kerala",
            court="Supreme Court",
            year=2011,
            source="sc_pdf",
            path_or_url="/fake/unrelated.pdf",
            text="unrelated text",
        ),
    )
    db.commit()
    result = verify(db, "(2011) 9 SCC 257", "anything")
    assert result == VerifyResult.NOT_IN_INDEX


def test_ampersand_honorific_normalizes_the_same_as_and(db: sqlite3.Connection) -> None:
    # Real bug found by hand-inspecting the 26 CONFLICTs still left after the first normalization pass
    # (LEG-PLAN P3 re-measurement): "State of Bihar & Ors." vs "State of Bihar" was reported as a
    # CONFLICT because _TRAILING_HONORIFIC only recognized "and Ors.", not "& Ors." -- the same real party,
    # a different conjunction spelling.
    from pravrudhi.application.verify import normalize_party_name

    assert normalize_party_name("State of Bihar & Ors.") == normalize_party_name("State of Bihar")


def test_conflict_when_alias_maps_citation_to_two_different_party_pairs(db: sqlite3.Connection) -> None:
    # A second, different citing document attributes the SAME citation to different parties -- real
    # data-integrity signal, not silently resolved to either one.
    insert_case(
        db,
        CaseRecord(
            case_id="citer2",
            title="A Different Later Case",
            court="Supreme Court",
            year=2010,
            source="sc_pdf",
            path_or_url="/fake/citer2.pdf",
            text="This Court in Totally Different Party v. Another Stranger (1977) 3 SCC 247 held that",
        ),
    )
    db.commit()
    result = verify(db, "(1977) 3 SCC 247", "any proposition")
    assert result == VerifyResult.CONFLICT
