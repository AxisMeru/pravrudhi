# ruff: noqa: E501
"""S1 matter citation sweep (pure engine): memoised lookups, edge rule C, caps and denominators, R1's templates and tokens. Invented papers."""

from __future__ import annotations

import json
from typing import Any

import pytest

from pravrudhi.application import draft_check as A1
from pravrudhi.application import matter_sweep as S
from pravrudhi.application.matter_sweep_config import SweepConfig

CITE = "(2014) 8 SCC 273"
NAMED = "Ravi Kumar v. State of Zorbland " + CITE
OTHER = "(2015) 3 SCC 11"


class Recorder:
    def __init__(
        self, answers: dict[str, tuple[str, bool]] | None = None, default: tuple[str, bool] = ("IN_INDEX", False)
    ) -> None:
        self.calls: list[str] = []
        self.answers, self.default = answers or {}, default

    def __call__(self, text: str) -> tuple[str, bool]:
        self.calls.append(text)
        return self.answers.get(text, self.default)


def one_passage_docs(*texts: str) -> tuple[list[S.Doc], list[S.Passage]]:
    docs = [S.Doc(f"d{i}", f"Paper {i}.pdf", 1, ()) for i in range(len(texts))]
    passages = [S.Passage(i + 1, f"d{i}", 1, 0, len(t), t) for i, t in enumerate(texts)]
    return docs, passages


def sweep(
    docs: list[S.Doc], passages: list[S.Passage], lookup: Any = None, cfg: SweepConfig | None = None, **kw: Any
) -> dict[str, Any]:
    return S.run_sweep(docs, passages, cfg or SweepConfig(), lookup or Recorder(), run_id="sweep-test", **kw)


def split_passages(doc_id: str, parts: list[str], *, gap: int = 0, pages: list[int] | None = None) -> list[S.Passage]:
    out, pos = [], 0
    for i, t in enumerate(parts):
        out.append(S.Passage(100 * len(doc_id) + i + 1, doc_id, (pages or [1] * len(parts))[i], pos, pos + len(t), t))
        pos += len(t) + gap
    return out


# ---- memo: one lookup per distinct lookup text --------------------------------------------------------------------------------


def test_the_same_case_cited_in_two_papers_is_one_lookup_two_citations_one_distinct_row() -> None:
    docs, ps = one_passage_docs(f"As held in {CITE} the court said so.", f"See {CITE} also.")
    rec = Recorder()
    out = sweep(docs, ps, rec)
    assert rec.calls == [CITE] and out["matter_denominator"]["lookups_made"] == 1
    assert out["matter_denominator"]["read"] == 2 and out["matter_denominator"]["distinct"] == 1
    row = out["distinct_citations"][0]
    assert [p["count"] for p in row["papers"]] == [1, 1] and len(row["occurrences"]) == 2
    assert "2 citations listed in 2 papers; 1 distinct citation." in out["summary"]


def test_a_different_typed_name_is_its_own_lookup_and_a_name_mismatch_cannot_hide_behind_a_found_row() -> None:
    docs, ps = one_passage_docs(
        f"As in {NAMED} the court said.", f"As in Another Person v. State of Elsewhere {CITE} the court said."
    )
    rec = Recorder({f"Another Person v. State of Elsewhere {CITE}": ("NAME_MISMATCH", True)})
    out = sweep(docs, ps, rec)
    assert len(rec.calls) == 2 and out["matter_denominator"]["name_mismatch"] == 1 and out["matter_denominator"]["distinct"] == 1
    row = out["distinct_citations"][0]
    assert row["statuses"] == ["name_mismatch", "found"]  # worst first
    assert {o["status"] for o in row["occurrences"]} == {"found", "name_mismatch"}


def test_a_lookup_that_raises_is_one_not_checked_and_the_sweep_continues_and_it_is_not_retried() -> None:
    def boom(text: str) -> tuple[str, bool]:
        if "SCC 273" in text:
            raise RuntimeError("index down")
        return ("IN_INDEX", False)

    docs, ps = one_passage_docs(f"See {CITE}.", f"See {CITE} again and {OTHER}.")
    out = sweep(docs, ps, boom)
    rows = [r for p in out["papers_read"] for r in p["citations"]]
    assert [r["reason"] for r in rows if r["status"] == "not_checked"] == ["lookup_failed", "lookup_failed"]
    assert out["matter_denominator"]["found"] == 1 and out["state"] == "unread_or_unchecked_remain"


# ---- the denominator, state and R1's summary --------------------------------------------------------------------------------


def test_the_summary_follows_r1s_template_with_not_read_and_not_checked_first_and_no_printed_zero() -> None:
    docs, ps = one_passage_docs(f"See {CITE} and AIR 2012 SC 100 and {OTHER}.")
    out = sweep(docs, ps, Recorder({f"{OTHER}": ("NOT_IN_INDEX", False)}))
    assert out["summary"] == (
        "Existence check only, in the papers you added. 1 not checked. 3 citations listed in 1 paper; 3 distinct citations. "
        "1 found in our index, 1 not found in our index. This does not say what any case held."
    )
    assert "0 not read" not in out["summary"] and "0 not checked" not in out["summary"]


def test_a_matter_with_no_citation_says_what_a1_says_and_is_none_listed() -> None:
    docs, ps = one_passage_docs("Nothing is cited here at all.")
    out = sweep(docs, ps)
    assert out["state"] == "none_listed" and out["summary"].startswith("No citation in a form this tool recognises was found")
    assert "Existence check only" not in out["summary"] or out["summary"]  # A1's own sentence, reused unchanged


@pytest.mark.parametrize(
    "n,m,d,expect",
    [
        (1, 1, 1, "1 citation listed in 1 paper; 1 distinct citation."),
        (5, 3, 2, "5 citations listed in 3 papers; 2 distinct citations."),
    ],
)
def test_the_count_line_gives_both_numbers_never_merged(n: int, m: int, d: int, expect: str) -> None:
    assert S.count_line(n, m, d) == expect


def test_the_state_tokens_are_the_ruled_four_and_the_listed_strings_token_is_a_counts_statement() -> None:
    assert S.STATES == ("none_listed", "nothing_checked", "unread_or_unchecked_remain", "listed_strings_looked_up")
    docs, ps = one_passage_docs(f"See {CITE}.")
    assert sweep(docs, ps)["state"] == "listed_strings_looked_up"
    docs, ps = one_passage_docs("AIR 2012 SC 100 only.")
    assert sweep(docs, ps)["state"] == "nothing_checked"
    docs2 = [S.Doc("d0", "Scan.pdf", 4, (2, 3))]
    ps2 = [S.Passage(1, "d0", 1, 0, 20, f"See {CITE}.")]
    out = sweep(docs2, ps2)
    assert (
        out["state"] == "unread_or_unchecked_remain"
        and out["papers_read"][0]["pages_note"] == "2 of 4 pages had no text: check them yourself."
    )


def _strings(obj: Any) -> list[str]:
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [s for k, v in obj.items() for s in [*([k] if isinstance(k, str) else []), *_strings(v)]]
    if isinstance(obj, list):
        return [s for v in obj for s in _strings(v)]
    return []


def test_no_string_or_token_of_the_report_holds_a_barred_word_outside_a1s_reused_lines() -> None:
    docs, ps = one_passage_docs(f"See {NAMED} and AIR 2012 SC 100.")
    out = sweep(docs, ps)
    reused = set(A1.NOT_ASSESSED) | {out["wording"]}
    own = {
        out["summary"], out["statement"], out["heading"], out["heading_line"], out["order_label"], out["cut_line"], out["snapshot_line"],
        out["duplicates_line"], out["state"],
        *S.STATES, S.NO_PAPERS, S.INDEX_OFF, S.TOO_MANY.format(cap=500), S.STORE_OFF,
    }  # fmt: skip
    for s in own - reused:
        assert S.barred_in(s) == [], (s, S.barred_in(s))
    assert reused  # A1's lines and wording are scanned by A1's own tests
    for token in S.STATES:
        assert "complete" not in token and "all" not in token.split("_")


@pytest.mark.parametrize("word", S.BARRED)
def test_each_barred_phrase_is_found_by_the_scan(word: str) -> None:
    assert S.barred_in(f"The page says {word} here.") == [word.lower()]


# ---- edge rule C ---------------------------------------------------------------------------------------------------------------


def _cut_case(parts: list[str], *, gap: int = 0, pages: list[int] | None = None) -> tuple[dict[str, Any], Recorder]:
    rec = Recorder()
    docs = [S.Doc("d0", "Cut.pdf", max(pages or [1]), ())]
    out = sweep(docs, split_passages("d0", parts, gap=gap, pages=pages), rec)
    return out, rec


@pytest.mark.parametrize(
    ("parts", "gap", "pages"),
    [
        (["As held in (2014) 8 SCC 27", "3 the court said so."], 0, None),  # a hard cut inside the digits
        (["As held in (2014) 8 SCC", "273 the court said so."], 1, None),  # a cut at a line end (whitespace gap)
        (["As held in (2014) 8 SCC", "273 the court said so."], 1, [1, 2]),  # a page change between "SCC" and the page number
    ],
)
def test_a_citation_split_by_a_passage_cut_is_not_read_not_looked_up_and_listed_with_both_passages(
    parts: list[str], gap: int, pages: list[int] | None
) -> None:
    out, rec = _cut_case(parts, gap=gap, pages=pages)
    assert rec.calls == []  # neither half was looked up
    den = out["matter_denominator"]
    assert (
        den["read"] == 0
        and den["not_read"] >= 1
        and out["papers_read"][0]["denominator"]["not_read_cut_at_storage_boundary"] >= 1
    )
    cut = out["papers_read"][0]["cut_at_storage_boundary"][0]
    assert cut["reason"] == "cut_at_storage_boundary" and len(cut["passages"]) == 2 and len(cut["pages"]) == 2
    assert out["state"] == "nothing_checked" and out["cut_line"] == S.CUT_LINE


def test_a_citation_wholly_inside_one_passage_next_to_a_cut_is_read_normally() -> None:
    out, rec = _cut_case([f"As held in {CITE} the court said.", "A new paragraph begins here."], gap=1)
    assert rec.calls == [CITE] and out["matter_denominator"]["read"] == 1 and out["matter_denominator"]["not_read"] == 0


def test_the_matter_denominator_counts_a_cut_string_once_as_not_read_and_the_invariant_holds() -> None:
    out, _ = _cut_case(["See (2014) 8 SCC 27", f"3 and also {OTHER}."])
    d = out["matter_denominator"]
    assert (
        d["like_strings"] == d["read"] + d["not_read"]
        and d["read"] == d["checked"] + d["not_checked"]
        and d["distinct"] <= d["read"]
    )
    assert d["read"] == 1 and d["not_read"] == 1


def test_a_name_in_the_previous_passage_reaches_the_citation_through_the_masked_context() -> None:
    parts = ["The court relied on Ravi Kumar v. State of Zorbland", f"{CITE} for this."]
    out, rec = _cut_case(parts, gap=1)
    assert rec.calls == [f"Ravi Kumar v. State of Zorbland {CITE}"]
    assert out["papers_read"][0]["citations"][0]["name"] == "Ravi Kumar v. State of Zorbland"


def test_the_context_is_never_read_as_a_citation_of_the_next_passage() -> None:
    parts = [f"First {CITE} here.", "Second passage has nothing."]
    out, rec = _cut_case(parts, gap=1)
    assert rec.calls == [CITE] and out["matter_denominator"]["read"] == 1


def test_rows_take_their_page_and_offsets_from_the_passage() -> None:
    p1 = S.Passage(1, "d0", 1, 0, 30, "Nothing here to see at all.....")
    text = f"Held in {CITE} indeed."
    p2 = S.Passage(2, "d0", 3, 31, 31 + len(text), text)
    out = sweep([S.Doc("d0", "A.pdf", 3, ())], [p1, p2])
    row = out["papers_read"][0]["citations"][0]
    assert row["page"] == 3 and row["span"][0] == 31 + text.index("(2014)") and row["doc_id"] == "d0" and row["passage"] == 2


# ---- caps ------------------------------------------------------------------------------------------------------------------------


def test_over_the_distinct_citation_cap_the_sweep_refuses_before_any_lookup_with_r1s_line() -> None:
    texts = [f"See ({2000 + i}) {i + 1} SCC {100 + i}." for i in range(6)]
    docs, ps = one_passage_docs(*texts)
    rec = Recorder()
    with pytest.raises(S.SweepRefused) as e:
        sweep(docs, ps, rec, SweepConfig(max_distinct_citations=5))
    assert e.value.code == "too_many_citations" and e.value.status_code == 422 and rec.calls == []
    assert (
        str(e.value)
        == "This matter lists more than 5 different citations, so the sweep does not run on it. Check them in smaller sets, or refer to a lawyer."
    )
    assert (
        sweep(docs, ps, Recorder(), SweepConfig(max_distinct_citations=6))["matter_denominator"]["read"] == 6
    )  # exactly at the cap runs


def test_papers_and_passages_over_their_caps_are_counted_not_searched_and_the_state_is_not_listed_strings() -> None:
    docs, ps = one_passage_docs(*[f"See ({2000 + i}) {i + 1} SCC {100 + i}." for i in range(4)])
    out = sweep(docs, ps, cfg=SweepConfig(max_papers=2))
    d = out["matter_denominator"]
    assert (
        d["papers"] == 2
        and d["papers_not_searched"] == 2
        and d["passages_not_searched"] == 2
        and out["state"] == "unread_or_unchecked_remain"
    )
    assert out["notes"] == ["The sweep reached its limit: 2 papers and 2 passages were not searched."]
    out2 = sweep(docs, ps, cfg=SweepConfig(max_passages=1))
    assert out2["matter_denominator"]["passages_not_searched"] == 3 and out2["matter_denominator"]["read"] == 1
    out3 = sweep(docs, ps, cfg=SweepConfig(max_chars=40))
    assert out3["matter_denominator"]["passages_not_searched"] >= 1


def test_the_time_cap_labels_the_rest_not_checked_and_the_state_is_never_listed_strings() -> None:
    ticks = iter(range(0, 1000, 20))
    docs, ps = one_passage_docs(*[f"See ({2000 + i}) {i + 1} SCC {100 + i}." for i in range(4)])
    out = S.run_sweep(docs, ps, SweepConfig(max_seconds=30.0), Recorder(), run_id="sweep-t", clock=lambda: float(next(ticks)))
    reasons = [r["reason"] for p in out["papers_read"] for r in p["citations"] if r["status"] == "not_checked"]
    assert (
        reasons
        and set(reasons) <= {"time_cap", "cap_reached"}
        and out["state"] in ("unread_or_unchecked_remain", "nothing_checked")
    )
    d = out["matter_denominator"]
    assert d["read"] == d["checked"] + d["not_checked"] and "complete" not in json.dumps(out).lower().replace("incomplete", "")


def test_the_row_cap_and_the_bytes_cap_keep_the_counts_exact_and_set_the_flag() -> None:
    texts = [" ".join(f"({2000 + i}) {i + 1} SCC {100 + i}." for i in range(40))]
    docs, ps = one_passage_docs(*texts)
    full = sweep(docs, ps)
    assert full["matter_denominator"]["read"] == 40 and not full["listing_truncated"]
    capped = sweep(docs, ps, cfg=SweepConfig(max_rows=10))
    assert (
        capped["listing_truncated"]
        and len(capped["papers_read"][0]["citations"]) == 10
        and capped["matter_denominator"]["read"] == 40
    )
    small = sweep(docs, ps, cfg=SweepConfig(max_bytes=10_000))
    assert small["listing_truncated"] and small["matter_denominator"]["read"] == 40
    assert len(json.dumps(small["papers_read"])) + len(json.dumps(small["distinct_citations"])) <= 10_000 + 1


# ---- non-SCC citations: the distinct key is the normalised text ----------------------------------------------------------------


def test_five_non_scc_citations_of_one_year_are_five_distinct_rows_never_one_bucket() -> None:
    docs, ps = one_passage_docs("See AIR 2012 SC 100, AIR 2012 SC 101, AIR 2012 SC 102, AIR 2012 SC 103 and AIR 2012 SC 104.")
    out = sweep(docs, ps)
    assert out["matter_denominator"]["read"] == 5 and out["matter_denominator"]["distinct"] == 5
    assert out["matter_denominator"]["checked"] == 0 and out["state"] == "nothing_checked"


def test_the_same_scc_citation_written_two_ways_is_one_distinct_row() -> None:
    docs, ps = one_passage_docs("See (2014) 8 SCC 273 and later (2014) 8 SCC\n273 again.")
    out = sweep(docs, ps)
    assert out["matter_denominator"]["distinct"] == 1 and out["matter_denominator"]["read"] == 2


# ---- the differential against A1 on the same text -------------------------------------------------------------------------------


def test_one_passage_per_paper_the_sweep_equals_a1_on_the_same_text() -> None:
    texts = [
        f"As in {NAMED} the court held; see also {OTHER} and AIR 2012 SC 100.",
        "(2016)10SCC386 compact, and (2014) 8 SCC 273.",
    ]
    docs, ps = one_passage_docs(*texts)
    rec = Recorder()
    out = sweep(docs, ps, rec)
    for text, paper in zip(texts, out["papers_read"], strict=True):
        a1 = A1.check_draft(text, A1.DraftCheckConfig(), Recorder())["draft_check"]
        assert [(r["text"], r["status"], r["reason"], r["name"], r["name_state"], r["quoted"]) for r in paper["citations"]] == [
            (r["text"], r["status"], r["reason"], r["name"], r["name_state"], r["quoted"]) for r in a1["citations"]
        ]
        assert (
            paper["denominator"]["read"] == a1["denominator"]["read"]
            and paper["denominator"]["not_read"] == a1["denominator"]["not_read"]
        )


def test_duplicates_are_counted_per_paper_and_in_the_matter_with_the_standing_note() -> None:
    docs, ps = one_passage_docs(f"See {CITE}.", f"See {CITE}.")
    out = sweep(docs, ps)
    assert [p["denominator"]["read"] for p in out["papers_read"]] == [1, 1] and out["matter_denominator"]["read"] == 2
    assert out["duplicates_line"] == S.DUPLICATES_LINE


def test_a_paper_not_ready_is_counted_beside_the_heading_and_the_state_is_not_listed_strings() -> None:
    docs, ps = one_passage_docs(f"See {CITE}.")
    out = sweep(docs, ps, not_ready=2)
    assert out["matter_denominator"]["papers_excluded_not_ready"] == 2 and "2 not ready and not included." in out["notes"]
    assert out["state"] == "unread_or_unchecked_remain"


def test_names_are_returned_raw_and_the_report_holds_no_text_of_another_matter() -> None:
    docs = [S.Doc("d0", 'A&B <1> "x".pdf', 1, ())]
    ps = [S.Passage(1, "d0", 1, 0, 30, f"See {CITE}.")]
    out = sweep(docs, ps)
    assert out["papers_read"][0]["name"] == 'A&B <1> "x".pdf'
