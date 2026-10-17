# ruff: noqa: E501
"""S3 build-time gate functions (`quashing_gate`) on a SYNTHETIC structural text: invented words with the same shape as the real
block (a stem, seven numbered items, a printed page header, a caution split by a header). No judgment text is in this file (the few
fixed markers the extractor keys on are the only phrases that match the Court's). The real-text checks run in the box gate."""

from __future__ import annotations

import pytest

from pravrudhi.application import quashing_gate as Q

HEADER = "Invented Party vs Other Party on 3 March, 2020\nIndian Kanoon - http://indiankanoon.org/doc/12345678/ 11\n"
ITEMS = [
    "(1) Where the first invented condition holds and nothing else is said about the matter at all.",
    "(2) Where the second invented condition holds and a further invented clause follows it here.",
    "(3) Where the third invented condition holds and some further invented words follow after it.",
    "(4) Where, the fourth invented condition holds and still more invented words are written here.",
    "(5) Where the fifth invented condition holds and the invented text continues for a while more.",
    "(6) Where there is a sixth invented bar written in the invented concerned Act and then more invented words follow it all the way to the end here.",
    "(7) Where the seventh invented condition holds and the last invented words end the list here.",
]
STEM = (
    Q.STEM_START
    + " of invented provisions and of invented principles, we list some invented kinds of cases wherein "
    + Q.STEM_END
)
CAUTION = Q.CAUTION_START + " to invented effect that something should be used sparingly, and that " + Q.CAUTION_END


def raw_text(items: list[str] | None = None, stem: str = STEM, caution: str = CAUTION, pad: str = "") -> str:
    its = ITEMS if items is None else items
    return f"Some invented preamble.\n{pad}{stem}\n" + "\n".join(its) + f"\n{caution} and that the invented rest.\nTail text.\n"


def units(raw: str | None = None) -> Q.Units:
    return Q.extract_units(raw or raw_text())


def test_the_units_are_exact_raw_substrings_in_order() -> None:
    raw = raw_text()
    u = units(raw)
    assert u.stem == STEM and len(u.items) == 7 and u.items[3].startswith("(4) Where, the") and u.caution == CAUTION
    assert Q.exact_failures(u, raw) == [] and not Q.block_between_has_artefact(raw, u)


@pytest.mark.parametrize(
    "bad",
    [
        raw_text(items=ITEMS[:6]),  # an item missing
        raw_text(items=[ITEMS[1], ITEMS[0], *ITEMS[2:]]),  # reordered
        raw_text(stem=STEM.replace(Q.STEM_START, "Another beginning")),  # stem start absent
        raw_text(caution="No caution here"),
    ],
)
def test_a_text_without_the_expected_shape_fails_extraction(bad: str) -> None:
    with pytest.raises(Q.ExtractError):
        Q.extract_units(bad)


def test_m1_a_punctuation_a_case_and_a_letter_change_each_fail_the_exact_check() -> None:
    raw = raw_text()
    u = units(raw)
    changes = {
        "punctuation": Q.Units(
            u.stem, (*u.items[:3], u.items[3].replace("(4) Where, the", "(4) Where the"), *u.items[4:]), u.caution
        ),
        "case": Q.Units(u.stem, (u.items[0], u.items[1].replace("Where", "where", 1), *u.items[2:]), u.caution),
        "letter": Q.Units(u.stem, (u.items[0].replace("first", "firsd"), *u.items[1:]), u.caution),
        "stem_letter": Q.Units(u.stem.replace("backdrop", "backdroq"), u.items, u.caution),
    }
    for name, mutated in changes.items():
        assert Q.exact_failures(mutated, raw) != [], name


def test_a_quote_that_is_not_found_once_fails_and_so_does_one_found_twice() -> None:
    raw = raw_text()
    u = units(raw)
    twice = raw + "\n" + u.items[0] + "\n"
    assert any("exactly once" in f for f in Q.exact_failures(u, twice))


def test_a_page_header_inside_the_block_is_found() -> None:
    raw = raw_text(items=[ITEMS[0], ITEMS[1] + "\n" + HEADER, *ITEMS[2:]])
    u = Q.extract_units(raw)
    assert Q.block_between_has_artefact(raw, u)


def test_m7_the_caution_extended_past_the_header_is_not_a_prefix_of_the_second_copy() -> None:
    u = units()
    second = raw_text()
    assert Q.exact_failures(u, raw_text(), second) == []
    longer = Q.Units(u.stem, u.items, u.caution + " and that the invented rest. Tail text and more words not in the second copy")
    assert any("prefix" in f or "once" in f for f in Q.exact_failures(longer, raw_text(caution=longer.caution), second))
    cut = raw_text(caution=CAUTION[:-12])
    assert any("prefix" in f for f in Q.exact_failures(u, raw_text(), cut))


def test_the_quote_block_hash_changes_with_any_one_character() -> None:
    u = units()
    base = Q.quote_block_sha256(u)
    assert Q.quote_block_sha256(Q.Units(u.stem + "x", u.items, u.caution)) != base  # m15: not recomputed would not notice
    assert Q.quote_block_sha256(Q.Units(u.stem, (*u.items[:-1], u.items[-1][:-1]), u.caution)) != base


def test_cleaner_a_removes_a_header_with_and_without_its_line_break_and_leaves_the_courts_sentence() -> None:
    two_line = f"words before {HEADER}words after"
    flat = "words before Invented Party vs Other Party on 3 March, 2020 Indian Kanoon - http://indiankanoon.org/doc/12345678/ 4 words after"
    assert Q.fold_v1(Q.clean_a(two_line)) == Q.fold_v1("words before words after")
    assert Q.fold_v1(Q.clean_a(flat)) == Q.fold_v1("words before words after")
    cut_at_edge = (
        "words before Indian Kanoon - http://indiankanoon.org/doc/12345678/ 11 words after"  # the title was cut by a window edge
    )
    assert Q.fold_v1(Q.clean_a(cut_at_edge)) == Q.fold_v1("words before words after")
    assert Q.clean_a("a note [9] here") == "a note   here" and Q.cleaner_hash() != ""


def test_the_fold_absorbs_hyphen_space_breaks_glyph_noise_and_spacing_but_not_words() -> None:
    assert Q.fold_v1("evi- dence of non€cognizable offence") == Q.fold_v1("evidence of non-cognizable offence")
    assert Q.fold_v1("Section 156(1)of the Code") == Q.fold_v1("section 156 (1) of the code")
    assert Q.fold_v1("and / or") == Q.fold_v1("and or")
    assert Q.fold_v1("concerned Act") != Q.fold_v1("Act concerned")


def _decisions(
    n_equal: int, n_c: int = 0, n_d: int = 0, d_text: str = "ACT concerned", dup: bool = False
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []

    def add(window: str, k: int) -> None:
        rows.append({"case_id": f"c{len(rows)}", "title": f"Invented Case {k}", "year": 2000 + (k % 25), "window": window})

    for k in range(n_equal):
        add(
            raw_text(pad=f"Preamble {k}. ")
            if k % 2
            else raw_text().replace("Some invented preamble.", HEADER + "Some invented preamble."),
            k,
        )
    for k in range(n_c):
        add(
            raw_text(
                items=[
                    ITEMS[0].replace("invented condition", "invented condition"),
                    ITEMS[1],
                    ITEMS[2],
                    ITEMS[3],
                    ITEMS[4],
                    ITEMS[5],
                    ITEMS[6],
                ],
                stem=STEM.replace("channelised", "channelised"),
            ),
            100 + k,
        )
    for k in range(n_d):
        add(raw_text(items=[*ITEMS[:5], ITEMS[5].replace("concerned Act", d_text), ITEMS[6]]), 200 + k)
    if dup:
        rows.append(dict(rows[0]))
    return rows


def test_m11_a_word_variant_at_three_decisions_is_a_conflict_by_the_d_rule_and_two_is_not() -> None:
    u = units()
    res3 = {r.name: r for r in Q.corroborate(u, _decisions(30, n_d=3))}
    res2 = {r.name: r for r in Q.corroborate(u, _decisions(30, n_d=2))}
    assert res3["item6"].status() == "CONFLICT" and res3["item6"].d_conflict()
    assert res2["item6"].status() == "CORROBORATED" and not res2["item6"].d_conflict()
    assert res3["item1"].status() == "CORROBORATED"  # the other items are untouched


def test_m11_the_five_times_rule_flips_an_item_when_the_variant_is_not_dominated() -> None:
    r = Q.ItemResult("x", n_computable=30, n_equal=14)
    r.variants["aa"], r.classes["aa"] = 3 - 1, "C"  # a C variant never conflicts alone ...
    assert r.status() == "CORROBORATED"
    r.variants["aa"] = 3  # ... until the pinned form is under five times the largest variant (14 < 15)
    assert r.status() == "CONFLICT"
    r.n_equal = 15
    assert r.status() == "CORROBORATED"


def test_m12_each_threshold_of_the_current_rule_matters() -> None:
    base = Q.ItemResult("x", n_computable=Q.MIN_COMPUTABLE, n_equal=Q.MIN_COMPUTABLE)
    assert base.status() == "CORROBORATED"
    assert Q.ItemResult("x", n_computable=Q.MIN_COMPUTABLE - 1, n_equal=Q.MIN_COMPUTABLE - 1).status() == "INSUFFICIENT COPIES"
    assert (Q.MIN_COMPUTABLE, Q.D_CONFLICT_N, Q.DOMINANCE) == (20, 3, 5)  # lowering any of them changes a status above or below
    d = Q.ItemResult("x", n_computable=40, n_equal=36)
    d.variants["bb"], d.classes["bb"] = Q.D_CONFLICT_N, "D"
    assert d.status() == "CONFLICT"
    d.variants["bb"] = Q.D_CONFLICT_N - 1
    assert d.status() == "CORROBORATED"


def test_m13_duplicate_rows_are_counted_once() -> None:
    u = units()
    a = {r.name: r for r in Q.corroborate(u, _decisions(25))}
    b = {r.name: r for r in Q.corroborate(u, _decisions(25, dup=True))}
    assert a["item1"].n_computable == b["item1"].n_computable == 25


def test_a_window_that_cuts_the_unit_is_not_computable() -> None:
    u = units()
    rows = [{"case_id": "x", "title": "Cut Case", "year": 2001, "window": raw_text()[: raw_text().index("(6)")]}]
    res = {r.name: r for r in Q.corroborate(u, rows)}
    assert res["item1"].n_computable == 1 and res["item6"].n_computable == 0 and res["caution"].n_computable == 0


def test_the_table_carries_counts_and_hashes_never_judgment_text() -> None:
    u = units()
    t = Q.table(Q.corroborate(u, _decisions(25, n_d=3)))
    blob = repr(t)
    assert (
        "concerned" not in blob
        and STEM[:30] not in blob
        and {"unit", "n_computable", "n_equal", "variants", "status"} <= set(t[0])
    )


def test_m14_the_fold_and_cleaner_and_normaliser_have_stable_hashes_that_change_with_their_definition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = (Q.fold_hash(), Q.cleaner_hash(), Q.normaliser_hash())
    assert all(len(x) == 64 for x in h)
    monkeypatch.setattr(Q, "SPELLING", {**Q.SPELLING, "licence": "license"})
    assert Q.fold_hash() != h[0]
    monkeypatch.setattr(Q, "CLEANER_A", (*Q.CLEANER_A, r"x"))
    assert Q.cleaner_hash() != h[1]


def test_m16_a_banned_marker_in_a_quote_is_found() -> None:
    assert (
        Q.reporter_markers("the Court said [305D-H] that") != [] and Q.reporter_markers("an ordinary sentence of the Court") == []
    )
