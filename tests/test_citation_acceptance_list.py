"""The citation acceptance list, pinned against the shipped slim citation index (0.6.12 and later).

Opt-in: it runs only when PRAVRUDHI_CITATION_INDEX names the slim index file (the one the hosted image carries at
/opt/pravrudhi/citation/slim_all.sqlite3); with no index it is skipped, so a clean checkout and CI stay green.
Case citations only:
each row is a citation and the answer the existence check must give on that index. The invented-party shapes of the same rules
(spellings that fold, near names that never fold) are in tests/test_verify_alias_collapse.py and always run."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from pravrudhi.application.verify import exists_in_index

_INDEX = os.environ.get("PRAVRUDHI_CITATION_INDEX", "")

pytestmark = pytest.mark.skipif(
    not _INDEX or not Path(_INDEX).is_file(), reason="PRAVRUDHI_CITATION_INDEX does not name the slim index"
)

#: (citation as typed, the answer on the shipped slim index, why the row is here)
LIST = [
    ("(2014) 8 SCC 273", "IN_INDEX", "alias spellings of one case: the bare citation is found"),
    ("(2006) 6 SCC 736", "IN_INDEX", "an abbreviation spelling (Corpn./Corporation) of one case: found"),
    ("(1973) 4 SCC 225", "IN_INDEX", "spellings, a shortened rare name and prefix words of one case: found"),
    (
        "(2000) 4 SCC 168",
        "CONFLICT",
        "the held title differs from the cited parties in two words: stays a referral, never IN_INDEX",
    ),
    (
        "1992 Supp (1) SCC 335",
        "NOT_IN_INDEX",
        "the supplementary-volume form parses and is not held: no evidence, never MALFORMED",
    ),
    ("(1992) Supp (1) SCC 335", "NOT_IN_INDEX", "the same form with the year in brackets"),
    ("(2099) 1 SCC 1", "NOT_IN_INDEX", "a citation that is not in the index"),
    ("(2014) 8 SCC 273 and (2000) 4 SCC 168", "MALFORMED", "two citations in one entry"),
    ("(1992) Supp (2) SCC 10 and (2000) 4 SCC 168", "MALFORMED", "a supplementary citation and another: two citations, not one"),
    ("not a citation", "MALFORMED", "no citation at all"),
]


@pytest.fixture(scope="module")
def conn() -> Iterator[sqlite3.Connection]:
    c = sqlite3.connect(f"file:{Path(_INDEX).resolve()}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    yield c
    c.close()


def _has_own_supp(c: sqlite3.Connection) -> bool:
    return c.execute("SELECT 1 FROM sqlite_master WHERE name = 'case_citations'").fetchone() is not None


#: The rows that depend on the index build: with the own-supp table (schema_ext own-supp-v1) a supplementary-volume citation
#: resolves to the judgment whose own header prints it; without it the form is read and not held.
_SUPP_ROWS = {"1992 Supp (1) SCC 335", "(1992) Supp (1) SCC 335"}


@pytest.mark.parametrize(("citation", "want", "why"), LIST, ids=[row[0] for row in LIST])
def test_the_acceptance_list_on_the_shipped_index(conn: sqlite3.Connection, citation: str, want: str, why: str) -> None:
    if citation in _SUPP_ROWS and _has_own_supp(conn):
        want = "IN_INDEX"
    assert exists_in_index(conn, citation).value == want, why


_OWN_SUPP_ROWS = [
    ("1992 Supp (1) SCC 335", "IN_INDEX", "a judgment of 1990 reported in the 1992 supplement: the record that prints it"),
    ("1992 SCC (SUPP) 1 335", "IN_INDEX", "the header spelling of the same citation"),
    ("(1992) 1 SCC 335", "IN_INDEX", "the plain key of the same number is a different case and keeps its own answer"),
    ("1992 Supp (1) SCC 336", "NOT_IN_INDEX", "a neighbouring page is not held"),
]


@pytest.mark.parametrize(("citation", "want", "why"), _OWN_SUPP_ROWS, ids=[row[0] for row in _OWN_SUPP_ROWS])
def test_the_supplementary_rows_on_an_own_supp_index(conn: sqlite3.Connection, citation: str, want: str, why: str) -> None:
    if not _has_own_supp(conn):
        pytest.skip("the index has no case_citations table (not an own-supp build)")
    assert exists_in_index(conn, citation).value == want, why
