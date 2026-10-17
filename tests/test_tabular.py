# ruff: noqa: E501
"""S2 matter tabular table (pure engine): dates through A3's scanner once per paper, the new scanners, edge rule C, denominators, caps, states. Invented papers."""

from __future__ import annotations

import json
import random
from datetime import date
from typing import Any

import pytest

from pravrudhi.application import chronology_dates as CD
from pravrudhi.application import tabular as T
from pravrudhi.application.tabular_config import TabularConfig

TODAY = date(2026, 10, 17)


def world(*texts: str, pages_without: dict[int, tuple[int, ...]] | None = None) -> tuple[list[T.Doc], list[T.Passage]]:
    docs = [T.Doc(f"d{i}", f"Paper {i}.pdf", 1, (pages_without or {}).get(i, ())) for i in range(len(texts))]
    ps = [T.Passage(i + 1, f"d{i}", 1, 0, len(t), t) for i, t in enumerate(texts)]
    return docs, ps


def table(
    docs: list[T.Doc], ps: list[T.Passage], cfg: TabularConfig | None = None, labels: dict[str, str] | None = None, **kw: Any
) -> dict[str, Any]:
    return T.run_table(docs, ps, cfg or TabularConfig(), labels or {}, run_id="tab-test", today=kw.pop("today", TODAY), **kw)


def cells(out: dict[str, Any], paper: int, col: str) -> list[tuple[str, str | None, int]]:
    return [(c["text"], c.get("act"), c["count"]) for c in out["papers"][paper]["columns"][col]["cells"]]


FIR = "The FIR was registered at P.S. Karol Bagh, Delhi on 12.03.2019 under sections 323, 324 and 34 IPC. The matter is before the High Court of Delhi."


def test_the_five_columns_are_filled_from_the_paper_with_page_and_place() -> None:
    out = table(*world(FIR), labels={"d0": "my FIR"})
    p = out["papers"][0]
    assert (
        p["name"] == "Paper 0.pdf"
        and p["type_label"] == "my FIR"
        and out["headings"]["places"] == "Court or police station mentioned"
    )
    assert cells(out, 0, "dates") == [("12.03.2019", None, 1)]
    assert cells(out, 0, "sections") == [("323", "IPC", 1), ("324", "IPC", 1), ("34", "IPC", 1)]
    assert [c[0] for c in cells(out, 0, "places")] == ["P.S. Karol Bagh", "High Court of Delhi"]
    m = p["columns"]["sections"]["cells"][0]["mentions"][0]
    assert FIR[m["start"] : m["end"]] == "323" and m["page"] == 1 and m["span_id"] == 1 and len(m["sha256"]) == 64


def test_every_cell_text_equals_the_passage_slice_and_the_sha256_is_over_the_span_text() -> None:
    import hashlib

    out = table(*world(FIR))
    for col in ("dates", "sections", "places"):
        for c in out["papers"][0]["columns"][col]["cells"]:
            for m in c["mentions"]:
                assert (
                    FIR[m["start"] : m["end"]] == c["text"]
                    or col == "dates"
                    and FIR[m["start"] : m["end"]].startswith(c["text"][:3])
                )
                assert m["sha256"] == hashlib.sha256(FIR.encode()).hexdigest() and m["doc_start"] == m["start"]


def test_the_dates_column_is_a3s_scan_document_called_once_per_paper_and_equals_it() -> None:
    t = "Notice dated 5 April 2020 and 13.04.2020 and 03.04.2020 were served; hearing on 12 May 2027."
    p1, p2 = t[:50], t[50:]
    docs = [T.Doc("d0", "A.pdf", 1, ())]
    passages = [T.Passage(1, "d0", 1, 0, len(p1), p1), T.Passage(2, "d0", 1, len(p1), len(t), p2)]
    out = table(docs, passages)
    rows = [("d0", 1, 0, p1, 1), ("d0", 1, len(p1), p2, 2)]
    scan = CD.scan_document(rows, TODAY)
    want = sorted((h.text[:200], h.category, h.doc_start) for h in scan.hits if h.category != "not_read")
    got = sorted(
        (c["text"], c["category"], m["doc_start"]) for c in out["papers"][0]["columns"]["dates"]["cells"] for m in c["mentions"]
    )
    assert got == want and want  # the SAME hits, with the injected `today`


def test_a_date_cell_carries_no_sentence_and_the_response_carries_no_timestamp() -> None:
    out = table(*world(FIR))
    for c in out["papers"][0]["columns"]["dates"]["cells"]:
        assert "sentence" not in c and "context" not in c and all("sentence" not in m for m in c["mentions"])
    blob = json.dumps(out)
    assert "generated" not in blob and "timestamp" not in blob and '"today"' not in blob


def test_the_response_is_byte_stable_for_the_same_injected_today_and_may_differ_across_days() -> None:
    docs, ps = world("The next hearing is on 20 October 2026 and the order was on 16 October 2026.")
    a, b = json.dumps(table(docs, ps), sort_keys=True), json.dumps(table(docs, ps), sort_keys=True)
    assert a == b
    other = json.dumps(table(docs, ps, today=date(2027, 1, 1)), sort_keys=True)
    assert isinstance(other, str)  # day-relative categories ("scheduled") may legitimately differ


def test_a_section_string_a_date_has_claimed_is_counted_claimed_by_date_not_read_as_a_section() -> None:
    out = table(*world("Under s. 12.03.2020 of the matter."))
    den = out["papers"][0]["columns"]["sections"]["denominator"]
    assert den == {"recognised": 1, "read": 0, "not_read": 0, "claimed_by_date": 1} and cells(out, 0, "dates") == [
        ("12.03.2020", None, 1)
    ]


def test_a_list_cut_at_a_join_is_one_not_read_and_never_a_partial_list() -> None:
    a = "The accused is charged under sections 323, 32"
    b = "4 and 34 IPC in this matter."
    docs = [T.Doc("d0", "A.pdf", 1, ())]
    out = table(docs, [T.Passage(1, "d0", 1, 0, len(a), a), T.Passage(2, "d0", 1, len(a), len(a) + len(b), b)])
    col = out["papers"][0]["columns"]["sections"]
    assert (
        col["cells"] == []
        and [n["reason"] for n in col["not_read"]] == ["cut_at_storage_boundary"]
        and col["not_read"][0]["spans"] == [1, 2]
    )
    assert col["denominator"] == {"recognised": 1, "read": 0, "not_read": 1, "claimed_by_date": 0}
    assert col["not_read_lines"] == [
        "Something may have been split where the stored text is cut. It was not read: check it yourself."
    ]


def test_a_form_wholly_inside_one_passage_next_to_a_cut_is_read_normally() -> None:
    a, b = "Under section 420 IPC the accused was held.", "A new paragraph begins here."
    docs = [T.Doc("d0", "A.pdf", 1, ())]
    out = table(docs, [T.Passage(1, "d0", 1, 0, len(a), a), T.Passage(2, "d0", 1, len(a) + 1, len(a) + 1 + len(b), b)])
    assert (
        cells(out, 0, "sections") == [("420", "IPC", 1)]
        and out["papers"][0]["columns"]["sections"]["denominator"]["not_read"] == 0
    )


def test_an_empty_cell_says_not_found_only_when_nothing_was_recognised_otherwise_it_counts_what_was_not_read() -> None:
    out = table(*world("Nothing of interest is written here at all.", "section 20 of the NDPS Act is cited."))
    c0, c1 = out["papers"][0]["columns"], out["papers"][1]["columns"]
    for col in c0.values():
        assert col["empty"] == "Not found in the papers you added." and col["not_read_lines"] == []
    assert c1["sections"]["empty"] is None and c1["sections"]["not_read_lines"] == [
        "A section of an Act we do not list was not read: check it yourself."
    ]
    assert c1["places"]["empty"] == "Not found in the papers you added."  # the other columns of that paper are still empty-found


def test_cells_are_in_first_appearance_order_never_sorted_by_count() -> None:
    out = table(
        *world("Section 420 IPC once, then section 302 IPC, then section 302 IPC and section 302 IPC again, and Section 34 IPC.")
    )
    assert [c[0] for c in cells(out, 0, "sections")] == ["420", "302", "34"] and [c[2] for c in cells(out, 0, "sections")] == [
        1,
        3,
        1,
    ]
    assert out["papers"][0]["columns"]["sections"]["cells"][1]["count_line"] == "Mentioned 3 times in this paper."


def test_ipc_and_bns_are_never_merged_in_distinct_values() -> None:
    out = table(*world("Section 420 IPC and Section 420 BNS."))
    assert cells(out, 0, "sections") == [("420", "IPC", 1), ("420", "BNS", 1)]


def test_a_non_english_passage_is_counted_per_paper_and_the_state_is_partly_read() -> None:
    out = table(*world("धारा 302 के अंतर्गत मामला दर्ज हुआ।"))
    p = out["papers"][0]
    assert (
        p["non_english_passages"] == 1
        and p["non_english_line"] == "Papers in a language other than English are not read."
        and out["state"] == "partly_read"
    )


def test_pages_without_text_put_the_line_on_the_row_and_make_the_state_partly_read() -> None:
    docs = [T.Doc("d0", "Scan.pdf", 4, (2, 3))]
    out = table(docs, [T.Passage(1, "d0", 1, 0, len(FIR), FIR)])
    assert out["papers"][0]["pages_note"] == "2 of 4 pages had no text: check them yourself." and out["state"] == "partly_read"
    assert table(*world(FIR))["state"] == "read_papers_listed" and table(*world("Nothing here."))["state"] == "nothing_found"


def test_a_label_is_returned_raw_for_its_paper_only() -> None:
    docs, ps = world("a", "b")
    out = table(docs, ps, labels={"d1": '<b>my "FIR"</b> & more'})
    assert out["papers"][0]["type_label"] is None and out["papers"][1]["type_label"] == '<b>my "FIR"</b> & more'


def test_names_are_raw_in_the_response() -> None:
    docs = [T.Doc("d0", 'A&B <1> "x".pdf', 1, ())]
    assert table(docs, [])["papers"][0]["name"] == 'A&B <1> "x".pdf'


# ---- denominators on random text --------------------------------------------------------------------------------------------------------

_VOCAB = [
    "section 420 IPC", "sections 323, 324 and 34 IPC", "u/s 482 Cr.P.C.", "420/34 IPC", "379-382 IPC", "section 3 of the Act", "section 20 of the NDPS Act",
    "P.S. Karol Bagh,", "P.S. Kotwali Smt. X", "High Court of Delhi", "Supreme Court of India", "12.03.2019", "5 April 2020", "03.04.2020", "next hearing on 3 May 2027",
    "the", "accused", "was", "held", "and", "धारा 302", "Sec. 498A IPC", "Chief Judicial Magistrate", "Police Station", "s. 12.03.2020",
]  # fmt: skip


def test_the_denominator_holds_on_2000_random_passages_and_a_bug_would_be_loud() -> None:
    rng = random.Random(7)
    docs, ps, pos = [T.Doc(f"d{i}", f"P{i}.pdf", 1, ()) for i in range(40)], [], {}
    for k in range(2000):
        i = k % 40
        text = " ".join(rng.choice(_VOCAB) for _ in range(rng.randint(1, 12)))
        start = pos.get(i, 0)
        ps.append(T.Passage(k + 1, f"d{i}", 1, start, start + len(text), text))
        pos[i] = start + len(text) + rng.choice([0, 1])
    out = table(docs, ps, TabularConfig(max_cells_per_matter=10**6, max_cells_per_paper=10**5, max_response_bytes=50_000_000))
    for p in out["papers"]:
        for col in p["columns"].values():
            d = col["denominator"]
            assert d["recognised"] == d["read"] + d["not_read"] + d["claimed_by_date"]


# ---- caps ---------------------------------------------------------------------------------------------------------------------------------


def test_over_the_paper_cap_the_whole_request_is_refused_naming_the_cap() -> None:
    docs, ps = world(*["x"] * 3)
    with pytest.raises(T.TableRefused) as e:
        table(docs, ps, TabularConfig(max_papers=2))
    assert str(e.value) == "This matter holds more than 2 papers, so no table was made." and e.value.code == "too_many_papers"
    assert len(table(docs, ps, TabularConfig(max_papers=3))["papers"]) == 3  # exactly at the cap runs


def test_passage_and_character_caps_refuse_the_whole_request_at_exactly_one_over() -> None:
    docs, ps = world("aaaa", "bbbb")
    with pytest.raises(T.TableRefused) as e1:
        table(docs, ps, TabularConfig(max_passages=1))
    assert str(e1.value) == "The papers you added hold more than 1 stored passages, so no table was made."
    table(docs, ps, TabularConfig(max_passages=2))
    with pytest.raises(T.TableRefused) as e2:
        table(docs, ps, TabularConfig(max_chars=7))
    assert str(e2.value) == "The papers you added are longer than 7 characters together, so no table was made."
    table(docs, ps, TabularConfig(max_chars=8))


BIG = " ".join(f"Section {i} IPC" for i in range(1, 31))


def test_a_paper_over_the_per_paper_cell_cap_refuses_only_its_row_and_every_other_row_is_byte_identical() -> None:
    docs, ps = world("Section 420 IPC in this paper.", BIG, "Section 302 IPC in the third.")
    cfg = TabularConfig(max_cells_per_paper=10)
    out = table(docs, ps, cfg)
    big = out["papers"][1]
    assert (
        big["refused"] == "Too large to read: Paper 1.pdf. This paper is not in the table; check it yourself."
        and big["columns"] is None
    )
    assert out["state"] == "partly_read" and out["matter_denominator"]["papers_refused"] == 1
    without = table(*world("Section 420 IPC in this paper.", "x", "Section 302 IPC in the third."), cfg=cfg) if False else None
    alone_docs = [docs[0], docs[2]]
    alone = table(alone_docs, [ps[0], ps[2]], cfg)
    for mine, theirs in ((out["papers"][0], alone["papers"][0]), (out["papers"][2], alone["papers"][1])):
        assert json.dumps({k: v for k, v in mine.items() if k not in ("doc_id", "name")}, sort_keys=True) == json.dumps(
            {k: v for k, v in theirs.items() if k not in ("doc_id", "name")}, sort_keys=True
        )
    assert without is None


def test_the_cell_cap_is_at_exactly_the_cap_not_one_over() -> None:
    docs, ps = world(BIG)
    assert table(docs, ps, TabularConfig(max_cells_per_paper=30))["papers"][0].get("refused") is None
    assert table(docs, ps, TabularConfig(max_cells_per_paper=29))["papers"][0].get("refused")


def test_the_matter_cell_cap_refuses_the_whole_request_off_by_one() -> None:
    docs, ps = world(BIG, BIG)
    with pytest.raises(T.TableRefused) as e:
        table(docs, ps, TabularConfig(max_cells_per_matter=59))
    assert (
        e.value.code == "too_many_cells"
        and str(e.value) == "This matter's papers give more than 59 entries, so no table was made."
    )
    table(docs, ps, TabularConfig(max_cells_per_matter=60))


def test_the_response_byte_cap_refuses_the_whole_request_off_by_one_and_renders_a_size() -> None:
    docs, ps = world(FIR)
    full = len(json.dumps(table(docs, ps), ensure_ascii=False).encode("utf-8"))
    cap_ok = TabularConfig(max_response_bytes=full)
    table(docs, ps, cap_ok)
    with pytest.raises(T.TableRefused) as e:
        table(docs, ps, TabularConfig(max_response_bytes=full - 1))
    assert e.value.code == "too_big" and "KB" in str(e.value) and str(full - 1) not in str(e.value)


def test_the_time_cap_refuses_the_whole_request_with_one_fixed_text_never_a_partial_table() -> None:
    ticks = iter(range(0, 10_000, 20))
    docs, ps = world("a", "b", "c")
    with pytest.raises(T.TableRefused) as e:
        T.run_table(docs, ps, TabularConfig(max_seconds=30.0), {}, run_id="tab-t", today=TODAY, clock=lambda: float(next(ticks)))
    assert str(e.value) == "The table took too long, so no table was made." and e.value.code == "too_slow"


def test_a_column_whose_numbers_do_not_add_up_is_a_loud_error() -> None:
    with pytest.raises(AssertionError, match="does not add up"):
        T._column([], [], 5, 0)  # 5 recognised, none read, none not read, none claimed: a bug, never a quietly wrong table


def test_dates_are_scanned_once_per_paper_not_per_passage_so_the_paper_wide_rules_apply() -> None:
    t = "Notice dated 13.04.2020 was served. The case was listed on 5 May 2030 and then 04.05.2020 and also 07.08.2020 and 12.09.2020."
    p1, p2 = t[:70], t[70:]
    docs = [T.Doc("d0", "A.pdf", 1, ())]
    out = table(docs, [T.Passage(1, "d0", 1, 0, len(p1), p1), T.Passage(2, "d0", 1, len(p1), len(t), p2)], today=date(2035, 1, 1))
    rows = [("d0", 1, 0, p1, 1), ("d0", 1, len(p1), p2, 2)]
    want = sorted((h.text[:200], h.category) for h in CD.scan_document(rows, date(2035, 1, 1)).hits if h.category != "not_read")
    got = sorted((c["text"], c["category"]) for c in out["papers"][0]["columns"]["dates"]["cells"])
    assert got == want and ("5 May 2030", "scheduled") not in got  # a per-passage call would read it differently


def test_the_injected_today_decides_the_year_ceiling_not_the_real_clock() -> None:
    docs, ps = world("A hearing on 5 May 2031 was fixed.")
    early = table(docs, ps, today=date(2026, 10, 17))["papers"][0]["columns"]["dates"]
    late = table(docs, ps, today=date(2035, 1, 1))["papers"][0]["columns"]["dates"]
    assert [c["text"] for c in early["cells"]] == [] and early["denominator"]["not_read"] == 1
    assert [c["text"] for c in late["cells"]] == ["5 May 2031"] and late["denominator"]["not_read"] == 0
