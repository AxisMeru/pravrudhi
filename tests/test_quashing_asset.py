# ruff: noqa: E501
"""S3: the shipped asset (the Court's words of paras 102-103, as quoted) against the attestation committed beside it. No judgment text
beyond the asset itself is in any test. These run in public CI; the byte-exact containment against the raw archive runs in the box
gate (`scripts/check_s3_quotes.py`)."""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from pravrudhi.application import quashing as G
from pravrudhi.application import quashing_gate as Q


def _asset() -> dict[str, Any]:
    return json.loads(G.ASSET.read_text(encoding="utf-8"))


def _att() -> dict[str, Any]:
    return json.loads(G.ATTESTATION.read_text(encoding="utf-8"))


def _units(asset: dict[str, Any]) -> Q.Units:
    return Q.Units(asset["stem"]["quote_raw"], tuple(i["quote_raw"] for i in asset["items"]), asset["caution"]["quote_raw"])


def test_the_asset_loads_and_matches_the_attested_quote_block() -> None:
    q = G.load_quotes()
    assert len(q.items) == 7 and q.year == 2016 and q.pin_case_id == _att()["case_id"]
    assert Q.quote_block_sha256(_units(_asset())) == _att()["quote_block_sha256"]


def test_the_asset_is_small_and_holds_only_the_courts_words_and_its_attribution() -> None:
    a = _asset()
    assert set(a) == {"schema", "attribution", "pin", "stem", "items", "caution", "repairs"}
    assert len(G.ASSET.read_bytes()) < 6000  # about 4 KB of the Court's words, no more
    assert a["repairs"] == [] and set(a["stem"]) == {"quote_raw"} and all(set(i) == {"n", "quote_raw"} for i in a["items"])
    att = a["attribution"]
    assert att["court"] == "Supreme Court of India" and att["para"] == "102 and 103 of State of Haryana v. Bhajan Lal, as quoted"
    assert att["case_title"] == "Amanullah Anr vs State Of Bihar Ors" and att["year"] == 2016
    import hashlib

    assert hashlib.sha256(att["source_note"].encode()).hexdigest()[:16] == "6483aaf59c010ce2"


def test_no_reporter_or_publisher_marker_is_in_the_asset_bytes_or_in_any_string_of_it() -> None:
    text = G.ASSET.read_text(encoding="utf-8")
    a = _asset()
    strings = [
        a["stem"]["quote_raw"],
        a["caution"]["quote_raw"],
        *[i["quote_raw"] for i in a["items"]],
        *a["attribution"].values(),
        *a["pin"].values(),
    ]
    for s in [text, *[str(x) for x in strings]]:
        assert Q.reporter_markers(s) == [], Q.reporter_markers(s)
    assert Q.clean_a(a["stem"]["quote_raw"]) == a["stem"]["quote_raw"]  # cleaner A finds nothing to remove in any quote
    assert all(Q.clean_a(i["quote_raw"]) == i["quote_raw"] for i in a["items"])


@pytest.mark.parametrize(
    "planted",
    [
        "[305D-H]",
        "[306A]",
        "[307B-C]",
        "SCR",
        "Headnote",
        "Headnotes",
        "HEADNOTE",
        "SCC",
        "AIR",
        "MANU",
        "Manupatra",
        "Equivalent citations",
        "Cited by",
        "Cites",
        "Held:",
        "Indian Kanoon",
        "indiankanoon",
        "http",
        "www",
    ],
)
def test_each_marker_in_the_list_is_found_when_planted(planted: str) -> None:
    assert Q.reporter_markers(f"words {planted} more words") != []


@pytest.mark.parametrize("innocent", ["scribe", "airline", "descriptive", "scrutiny", "chairman", "Where the airing", "[9]"])
def test_a_marker_inside_an_ordinary_word_does_not_trip_the_list(innocent: str) -> None:
    assert Q.reporter_markers(f"the {innocent} of it") == []


def test_the_page_shows_the_quotes_with_whitespace_collapsed_and_nothing_else_changed() -> None:
    q = G.load_quotes()
    page = G.build_page([])
    assert page["stem"] == " ".join(q.stem.split()) and [c["text"] for c in page["categories"]] == [
        " ".join(t.split()) for t in q.items
    ]
    assert page["caution"]["quote"] == " ".join(q.caution.split()) + " ..."
    assert "".join(page["stem"].split()) == "".join(q.stem.split())  # only whitespace differs


# ---- m1, m2, m9, m10 and friends: mutations that need only the asset itself, run in public CI ---------------------------------


def _load_with(monkeypatch: pytest.MonkeyPatch, tmp_path: Any, asset: dict[str, Any]) -> None:
    p = tmp_path / "asset.json"
    p.write_text(json.dumps(asset), encoding="utf-8")
    monkeypatch.setattr(G, "ASSET", p)
    G.load_quotes.cache_clear()


def _mutants() -> dict[str, Any]:
    base = _asset()

    def one(path: str, fn: Any) -> dict[str, Any]:
        m = copy.deepcopy(base)
        fn(m)
        return m

    def swap_char(s: str, ch: str, to: str) -> str:
        i = s.index(ch)
        return s[:i] + to + s[i + 1 :]

    out = {
        "m1_one_letter": one(
            "i", lambda m: m["items"][0].__setitem__("quote_raw", swap_char(m["items"][0]["quote_raw"], "a", "e"))
        ),
        "m1_punctuation_only": one(
            "p",
            lambda m: m["items"][3].__setitem__(
                "quote_raw", m["items"][3]["quote_raw"].replace("(4) Where, the", "(4) Where the")
            ),
        ),
        "m1_case_only": one(
            "c", lambda m: m["items"][1].__setitem__("quote_raw", m["items"][1]["quote_raw"].replace("Where", "where", 1))
        ),
        "m2_stem_char": one("s", lambda m: m["stem"].__setitem__("quote_raw", swap_char(m["stem"]["quote_raw"], "e", "a"))),
        "m6_item_dropped": one("d", lambda m: m["items"].pop(2)),
        "m6_items_reordered": one("r", lambda m: m["items"].__setitem__(slice(0, 2), [m["items"][1], m["items"][0]])),
        "m8_repair_added": one("a", lambda m: m.__setitem__("repairs", [{"kind": "x"}])),
        "m9_caution_extended": one(
            "e", lambda m: m["caution"].__setitem__("quote_raw", m["caution"]["quote_raw"] + " and that the extraordinary or")
        ),
        "m10_stem_shortened": one(
            "t",
            lambda m: m["stem"].__setitem__(
                "quote_raw", m["stem"]["quote_raw"].split(" and to give an exhaustive list")[0] + "."
            ),
        ),
        "m4_pin_title": one("t", lambda m: m["pin"].__setitem__("title", "Another vs Case")),
        "m4_pin_case_id": one("c", lambda m: m["pin"].__setitem__("case_id", "0" * 24)),
    }
    return out


@pytest.mark.parametrize("name", sorted(_mutants()))
def test_each_mutated_asset_is_refused_at_load(name: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    _load_with(monkeypatch, tmp_path, _mutants()[name])
    with pytest.raises(G.AssetChanged):
        G.load_quotes()
    G.load_quotes.cache_clear()


def test_the_unmutated_asset_still_loads_after_the_mutants() -> None:
    G.load_quotes.cache_clear()
    assert len(G.load_quotes().items) == 7


def test_the_attestation_states_the_pins_hashes_and_the_empty_citation_list() -> None:
    a = _att()
    assert a["dev_index_file_sha256"] == "a79df632459178476b123718e77584aaa484325cc4514449148680a7914c4d4a"
    assert a["archive_sha256"] == "8c094cdde4442a47badf7283b0ebf1c8ccd39044a146c29495f6f3d7d836359a" and a["archive_rows"] == 70
    assert a["text_sha256"] == "6c739205c7dd3a24e01a9a9b788526761985d91c3336684ba9ba174afbb07c0f"
    assert (
        a["case_citations_for_pin"] == []
        and a["fold"]["name"] == "s3_fold_v1"
        and a["second_copy_case_id"] == "c688a5684f046cc585159999"
    )
    assert (
        a["normaliser_sha256"] == Q.normaliser_hash()
        and a["fold"]["sha256"] == Q.fold_hash()
        and a["cleaner_a_sha256"] == Q.cleaner_hash()
    )  # m14: any change fails
    assert all(len(h) == 64 for h in (a["archive_sha256"], a["text_sha256"], a["quote_block_sha256"], a["builder_script_sha256"]))


def test_the_attested_statuses_follow_the_rule_and_item_six_is_a_conflict_by_design() -> None:
    by = {t["unit"]: t for t in _att()["corroboration"]}
    for t in by.values():  # recompute the derived status from the attested counts (m12: the rule cannot be edited unnoticed)
        res = Q.ItemResult(t["unit"], t["n_computable"], t["n_equal"])
        for v in t["variants"]:
            res.variants[v["hash"]] = v["n"]
            res.classes[v["hash"]] = v["class"]
        assert res.status() == t["status"], t["unit"]
    assert by["item6"]["status"] == "CONFLICT"  # the lawyer signs "concerned Act" vs "Act concerned"; shown in the PR record
    assert all(by[k]["status"] == "CORROBORATED" for k in ("item1", "item2", "item3", "item4", "item5", "item7", "stem"))
    assert by["caution"]["status"] == "INSUFFICIENT COPIES"
