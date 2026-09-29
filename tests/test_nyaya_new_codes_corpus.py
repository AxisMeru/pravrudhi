"""BNS / BNSS / BSA 2023 as shipped from India Code: section counts, provenance, and retrieval beside the IPC."""

from __future__ import annotations

import json
import re

import pytest

from pravrudhi.application import nyaya

EXPECTED = {"bns_sections.json": ("BNS", 358), "bnss_sections.json": ("BNSS", 531), "bsa_sections.json": ("BSA", 170)}


def _file(name: str) -> dict:  # type: ignore[type-arg]
    return json.loads((nyaya.ASSET_DIR / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", EXPECTED)
def test_each_act_has_every_section_once_and_records_its_provenance(name: str) -> None:
    prefix, count = EXPECTED[name]
    raw = _file(name)
    ids = [d["id"] for d in raw["documents"]]
    assert ids == [f"{prefix}/Section {n}" for n in range(1, count + 1)]
    assert all(d["text"].strip() and d["title"].strip() for d in raw["documents"])
    assert all(nyaya.CITE.fullmatch(f"[{i}]") for i in ids)
    src = raw["source"]
    assert src["site"] == "indiacode.gov.in" and src["fetched_at"] == "2026-09-29"
    assert src["sections_expected"] == src["sections_found"] == count
    assert re.fullmatch(r"[0-9a-f]{64}", src["raw_sha256"]) and src["act_page"].startswith("https://indiacode.gov.in/")


def test_the_ipc_documents_are_unchanged_by_the_new_acts() -> None:
    c = nyaya.load_corpus()
    ipc = [d for d in c.documents if d.id.startswith("IPC/")]
    assert len(ipc) == 100 and c.by_id["IPC/Section 302"].act == "Indian Penal Code, 1860"


def test_a_bns_provision_question_now_retrieves_it_above_the_relevance_floor() -> None:
    """The 2026-09-26 production finding: a BNS s.69 question found nothing because BNS wasn't shipped."""
    c = nyaya.load_corpus()
    q = "What is the punishment for sexual intercourse by deceitful means or a false promise to marry under BNS section 69?"
    hits = c.retrieve(q, k=3)
    assert hits[0][0].id == "BNS/Section 69" and hits[0][1] >= c.min_relevance_score
    plain = c.retrieve("punishment for promise to marry without intention of fulfilling it", k=1)
    assert plain[0][0].id == "BNS/Section 69" and plain[0][1] >= c.min_relevance_score
