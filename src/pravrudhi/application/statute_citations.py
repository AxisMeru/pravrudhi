"""Statute citations for a registry contract, derived from its own source column and resolved against the shipped
corpus. Deterministic: the only inputs are the contract's `--list-contracts` sources and the corpus, never a
judge's output, so a contract carries the same citations whatever its verdict."""

from __future__ import annotations

import re
from typing import Any

from pravrudhi.application.nyaya import Corpus

_ACTS: tuple[tuple[str, str], ...] = (
    ("bharatiya nagarik suraksha sanhita", "BNSS"),
    ("bharatiya nyaya sanhita", "BNS"),
    ("bharatiya sakshya adhiniyam", "BSA"),
    ("indian penal code", "IPC"),
    ("constitution of india", "COI"),
)
_REF = re.compile(r"^(?P<act>.*?)\s*(?:§|section|article)\s*(?P<num>\d+)", re.IGNORECASE)


def parse_source(source: str) -> tuple[str, str] | None:
    """`"Bharatiya Nyaya Sanhita §69"` -> `("BNS", "69")`. An act this module does not know keeps its own name;
    a source with no section number returns None."""
    m = _REF.match(source.strip())
    if not m:
        return None
    name = m.group("act").strip().rstrip(",")
    low = name.lower()
    abbr = next((a for prefix, a in _ACTS if low.startswith(prefix)), name)
    return abbr, m.group("num")


def _corpus_id(act: str, section: str) -> str:
    return f"{act}/{'Article' if act == 'COI' else 'Section'} {section}"


def contract_citations(sources: list[str], corpus: Corpus) -> list[dict[str, Any]]:
    docs = corpus.by_id
    out: list[dict[str, Any]] = []
    for src in sources:
        ref = parse_source(src)
        if ref is None:
            out.append({"act": src.strip(), "section": None, "corpus_id": None, "in_corpus": False, "title": None})
            continue
        act, section = ref
        doc = docs.get(_corpus_id(act, section))
        out.append({
            "act": act, "section": section,
            "corpus_id": doc.id if doc else None, "in_corpus": doc is not None,
            "title": doc.title if doc else None,
        })
    return out
