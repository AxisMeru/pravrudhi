"""Merge plan for rows of the dev case index that carry one judgment twice (#508).

The dev index holds some judgments more than once: an exact double ingest, or the same judgment from two sources (`sc_pdf` and
`injudgements`). Two rows with one title and one year answer CONFLICT in the existence check, so a real citation to such a
judgment is reported as ambiguous. The slim-index builder (`case_index_slim.build_slim_index`, `merge_map=`) keeps ONE row per
judgment; this module decides which rows are one judgment. The rule (Lead-2, 16 Oct 2026, option 2):

* rows whose full text is byte-identical (equal SHA-256) are one judgment, whatever their title metadata says;
* two rows that share a decision date and whose normalised text is at least 0.98 alike (word-level 2*LCS/(n1+n2), measured by
  Track A, not recomputed here) are one judgment UNLESS their party names differ after a CLOSED normalisation (`parties_agree`);
* nothing else merges: the line is not lowered, and no title or party alone ever decides a merge.

The text similarity is an input (Track A's handoff files); this module holds the part that is code: the party-name test, the
grouping, the choice of the row kept, and the list of every pair the party-name test refused.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

#: A date the injudgements copy of a title appends to its second party: "... on 4 May, 1950", also "on 13 December 1955 (1) 1".
_TRAILING_DATE = re.compile(r"\s+on\s+\d{1,2}\s+[^\W\d_]+\.?,?\s+\d{4}.*$", re.IGNORECASE | re.DOTALL)
_MARKUP = re.compile(r"<[^>]*>")
_ELLIPSIS = re.compile(r"\.{3,}|…")
_WORD = re.compile(r"[^\W_]+")
#: Words that carry no party identity: articles, "and", the "others" family. Closed list, whole words only.
NOISE_WORDS = frozenset({"the", "and", "ors", "others", "other", "anr", "another", "an", "etc"})

#: How the kept row of a merge group is chosen: the row with a recorded year, then the `sc_pdf` copy, then the lowest rowid.
KEPT_SOURCE = "sc_pdf"


def normalise_party(raw: str) -> tuple[str, bool]:
    """`(compact, truncated)` for one party string of a title. The trailing " on <date>" an injudgements title carries is
    dropped, markup and the ellipsis a truncated title ends with are removed (`truncated` records the ellipsis), case,
    punctuation and spacing are ignored (`U.P` = `U P`, `J&K` = `J K`, `Mohd.` = `Mohd`), and NOISE_WORDS are dropped."""
    s = _MARKUP.sub("", raw or "")
    s = _TRAILING_DATE.sub("", s)
    truncated = _ELLIPSIS.search(s) is not None
    s = _ELLIPSIS.sub(" ", s).lower()
    words = [w for w in _WORD.findall(s) if w not in NOISE_WORDS]
    return "".join(words), truncated


def _agree(a: str, b: str) -> bool:
    (ca, ta), (cb, tb) = normalise_party(a), normalise_party(b)
    if not ca or not cb:
        return False
    if ca == cb:
        return True
    # a title cut short ("Commissioner Of Income-Tax, ...") is a prefix of the full name; only a cut title may be a prefix
    return bool((ta or tb) and (ca.startswith(cb) or cb.startswith(ca)))


def parties_agree(a: tuple[str, str], b: tuple[str, str]) -> bool:
    """True when both parties of title `a` and title `b` name the same party after the closed normalisation of
    `normalise_party`: equal compact strings, or one a prefix of the other where one of the two titles was cut short
    (ellipsis)."""
    return _agree(a[0], b[0]) and _agree(a[1], b[1])


@dataclass(frozen=True)
class Row:
    rowid: int
    case_id: str
    title: str
    year: int | None
    source: str
    party_1: str
    party_2: str

    @staticmethod
    def from_handoff(d: Mapping[str, Any]) -> Row:
        y = d.get("year")
        return Row(
            rowid=int(d["rowid"]),
            case_id=str(d["case_id"]),
            title=str(d.get("title", "")),
            year=None if y in (None, "") else int(y),
            source=str(d.get("source", "")),
            party_1=str(d.get("party_1_raw", "")),
            party_2=str(d.get("party_2_raw", "")),
        )


@dataclass
class MergePlan:
    #: rowid of every DROPPED row -> rowid of the row kept for its judgment
    dropped_to_kept: dict[int, int] = field(default_factory=dict)
    rows: dict[int, Row] = field(default_factory=dict)
    #: ratio-only pairs refused by the party-name test: both rows and the compact names compared
    refused_pairs: list[dict[str, Any]] = field(default_factory=list)
    #: refused pairs whose rows are nevertheless one group, joined through other links
    refused_but_joined: list[dict[str, Any]] = field(default_factory=list)
    n_hash_groups: int = 0
    n_ratio_pairs: int = 0
    n_ratio_kept: int = 0
    n_groups: int = 0


def kept_rank(r: Row) -> tuple[bool, bool, int]:
    """Sort key of the row kept for a judgment: a recorded year first, then the `sc_pdf` copy, then the lowest rowid."""
    return (r.year is None, r.source != KEPT_SOURCE, r.rowid)


class _Groups:
    def __init__(self) -> None:
        self._p: dict[int, int] = {}

    def find(self, x: int) -> int:
        self._p.setdefault(x, x)
        root = x
        while self._p[root] != root:
            root = self._p[root]
        while self._p[x] != root:
            self._p[x], x = root, self._p[x]
        return root

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._p[max(ra, rb)] = min(ra, rb)

    def members(self) -> dict[int, list[int]]:
        out: dict[int, list[int]] = {}
        for x in list(self._p):
            out.setdefault(self.find(x), []).append(x)
        return out


def plan_merges(hash_groups: Iterable[Iterable[Row]], ratio_pairs: Iterable[tuple[Row, Row, float]]) -> MergePlan:
    """Group the rows. Every hash-equal group is one judgment (its rows are linked unconditionally). A ratio-only pair
    (same decision date, text >= 0.98 alike, different text hashes) links its two rows only when `parties_agree`; every
    refused pair is listed in `refused_pairs`. A group is a connected component of the links; one row of it is kept
    (`kept_rank`) and the others are dropped."""
    plan = MergePlan()
    g = _Groups()
    for grp in hash_groups:
        rows = list(grp)
        plan.n_hash_groups += 1
        for r in rows:
            plan.rows[r.rowid] = r
            g.find(r.rowid)
        for r in rows[1:]:
            g.union(rows[0].rowid, r.rowid)
    refused: list[tuple[Row, Row, float]] = []
    for a, b, ratio in ratio_pairs:
        plan.n_ratio_pairs += 1
        plan.rows[a.rowid], plan.rows[b.rowid] = a, b
        if parties_agree((a.party_1, a.party_2), (b.party_1, b.party_2)):
            plan.n_ratio_kept += 1
            g.union(a.rowid, b.rowid)
        else:
            refused.append((a, b, ratio))
            g.find(a.rowid)
            g.find(b.rowid)
    for a, b, ratio in refused:
        rec = {
            "ratio": ratio,
            "a": _describe(a),
            "b": _describe(b),
            "reason": "party names differ after the closed normalisation",
        }
        plan.refused_pairs.append(rec)
        if g.find(a.rowid) == g.find(b.rowid):
            plan.refused_but_joined.append(rec)
    for members in g.members().values():
        if len(members) < 2:
            continue
        plan.n_groups += 1
        kept = min((plan.rows[m] for m in members), key=kept_rank)
        for m in members:
            if m != kept.rowid:
                plan.dropped_to_kept[m] = kept.rowid
    return plan


def _describe(r: Row) -> dict[str, Any]:
    return {
        "rowid": r.rowid,
        "case_id": r.case_id,
        "title": r.title,
        "source": r.source,
        "year": r.year,
        "compact_party_1": normalise_party(r.party_1)[0],
        "compact_party_2": normalise_party(r.party_2)[0],
    }
