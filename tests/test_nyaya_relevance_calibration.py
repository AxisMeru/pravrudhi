"""Issue #51's calibration TUNING set (tests/fixtures/nyaya_relevance_calibration.json), run against the shipped
retrieve(). It is a tuning set, not an evaluation set: `min_relevance_norm` and the choice of rule were fit on it, so
every number below is IN-SAMPLE and is a regression guard for that tuned threshold, not evidence about retrieval quality
on new questions. Its provenance (including that five expected_ids were widened 2m20s after the first commit, b4553b48
vs 55f88914) is in the fixture's own `_provenance`. A fresh evaluation set, written before any run, is a backlog item.
This test keeps `min_relevance_norm` honest against regression: a change to the corpus, the BM25 shape or the
threshold that quietly drops recall or lets more off-topic noise through fails here first.

**v3 (2026-10-05, measured on main @3402450b):** the fixture is again the ORIGINAL 26-question set (widened version).
The v2 relabel (kept beside it as `nyaya_relevance_calibration_v2_ipc_coi_only.json`, not run) existed only because the
BNS/BNSS text was withdrawn from the package for a licence review; it is shipped again (1,609 provisions), so v2's labels
no longer describe the corpus. The `split` assignment of v2 is read from the v2 file by case id (the split is NOT a
held-out evaluation: the threshold was fit on all 26 cases).

Measured on today's ranking (scripts/nyaya_relevance_calibration_run.py), 14 on_topic / 12 off_topic cases, IN-SAMPLE:

| rule | recall | false citations |
|---|---|---|
| absolute floor only (main) | 13/14 | 12/12 |
| score / query self-score >= 0.28 (shipped, tuned here) | 13/14 | 5/12 |
| matched-distinct-term coverage (best, >= 0.36..0.3833) | 13/14 | 5/12 |
| top-1/top-2 margin (best, > 0) | 13/14 | 12/12 |

Self-score normalisation and coverage TIE at 5/12 (on 2026-09-26 it was 3/12 vs 9/12; main's ranking has since
changed); the shipped rule stays as the incumbent. Its plateau on this set is 0.24-0.3047 (recall 13/14, 5/12); 0.28
sits inside it, and at 0.31 recall drops to 12/14. The baseline's one on_topic miss (`medium-on-topic-bns-deceit-marriage`)
is a ranking miss, not a floor effect. Of the 5 residual leaks, THREE are `bns69` questions that name a section the corpus
now has, so the named-section boost (+100) returns it and no length gate can cut it; the other two are real BM25 noise.

Named, not hidden: `KNOWN_RESIDUAL_LEAKS` and `KNOWN_ON_TOPIC_MISSES` are exact-match, so a newly leaking case
fails and so does a fixed one silently staying in the tolerated set."""

from __future__ import annotations

import json
from pathlib import Path

from pravrudhi.application import nyaya

FIXTURES = Path(__file__).parent / "fixtures"
CALIBRATION_PATH = FIXTURES / "nyaya_relevance_calibration.json"
SPLIT_PATH = FIXTURES / "nyaya_relevance_calibration_v2_ipc_coi_only.json"  # only its `split` field is read

#: The off_topic / off_topic_verbose cases whose top hit still clears both gates today (see the module docstring).
KNOWN_RESIDUAL_LEAKS = frozenset(
    {
        "short-off-topic-bns69-bare",
        "verbose-off-topic-bns69-governor-president",
        "verbose-off-topic-bns69-more-constitutional-terms",
        "verbose-off-topic-uk-parliament-with-coi-vocab",
        "verbose-off-topic-holiday-planning-with-conspiracy-vocab",
    }
)

#: The on_topic cases that miss on today's ranking, with or without the norm gate.
KNOWN_ON_TOPIC_MISSES = frozenset({"medium-on-topic-bns-deceit-marriage"})


def _cases() -> list[dict]:
    return json.loads(CALIBRATION_PATH.read_text())["cases"]


def _splits() -> dict[str, str]:
    return {c["id"]: c["split"] for c in json.loads(SPLIT_PATH.read_text())["cases"]}


def _top_id(c: nyaya.Corpus, question: str) -> str | None:
    hits = c.retrieve(question, k=8)
    return hits[0][0].id if hits else None


def test_the_calibration_set_is_the_26_case_tuning_set() -> None:
    kinds = {}
    for case in _cases():
        kinds[case["kind"]] = kinds.get(case["kind"], 0) + 1
    assert kinds == {"on_topic": 14, "off_topic": 5, "off_topic_verbose": 7}
    assert set(_splits()) == {case["id"] for case in _cases()}


def test_every_on_topic_calibration_case_still_finds_its_match_except_the_named_miss() -> None:
    c = nyaya.load_corpus()
    misses = {
        case["id"] for case in _cases() if case["kind"] == "on_topic" and _top_id(c, case["question"]) not in case["expected_ids"]
    }
    assert misses == KNOWN_ON_TOPIC_MISSES, f"on_topic recall changed: {sorted(misses)}"


def test_off_topic_calibration_cases_are_cut_except_the_named_residual_gap() -> None:
    c = nyaya.load_corpus()
    leaking = {case["id"] for case in _cases() if case["kind"] != "on_topic" and c.retrieve(case["question"], k=8)}
    assert leaking == KNOWN_RESIDUAL_LEAKS


def test_the_norm_gate_costs_no_recall_against_the_absolute_floor_alone() -> None:
    """The selection rule's constraint: recall >= the current recall. The same corpus with the norm gate off is
    the baseline; the shipped gate must not lose a single on_topic hit that the baseline finds."""
    gated, base = nyaya.load_corpus(), nyaya.load_corpus()
    base.min_relevance_norm = -1.0
    on_topic = [case for case in _cases() if case["kind"] == "on_topic"]
    found = lambda c: {x["id"] for x in on_topic if _top_id(c, x["question"]) in x["expected_ids"]}  # noqa: E731
    assert found(gated) == found(base) and len(found(base)) == 13


def test_the_norm_gate_removes_most_of_the_false_citations_the_absolute_floor_lets_through() -> None:
    gated, base = nyaya.load_corpus(), nyaya.load_corpus()
    base.min_relevance_norm = -1.0
    noisy = [case for case in _cases() if case["kind"] != "on_topic"]
    leaks = lambda c: {x["id"] for x in noisy if c.retrieve(x["question"], k=8)}  # noqa: E731
    assert len(leaks(base)) == 12 and len(leaks(gated)) == 5


def test_the_shipped_threshold_sits_inside_the_recall_plateau() -> None:
    """0.28 is inside the 0.24-0.3047 plateau; just above it (0.31) one more on_topic case is lost, so the
    threshold is not parked on a cliff edge."""
    on_topic = [case for case in _cases() if case["kind"] == "on_topic"]

    def recall(norm: float) -> int:
        c = nyaya.load_corpus()
        c.min_relevance_norm = norm
        return sum(1 for x in on_topic if _top_id(c, x["question"]) in x["expected_ids"])

    assert recall(0.24) == recall(0.28) == recall(0.30) == 13
    assert recall(0.31) == 12


def test_the_report_split_numbers_in_sample() -> None:
    """The `report` half of the v2 split, IN-SAMPLE (the threshold was fit on all 26 cases, so this is not a held-out
    measurement): 6/6 on_topic hits (the named miss is in the other half) and 2 false citations out of 5 noisy cases.
    No threshold was retuned in this pass: 0.28 is unchanged."""
    c = nyaya.load_corpus()
    splits = _splits()
    report = [case for case in _cases() if splits[case["id"]] == "report"]
    on_topic = [x for x in report if x["kind"] == "on_topic"]
    assert len(on_topic) == 6 and all(_top_id(c, x["question"]) in x["expected_ids"] for x in on_topic)
    noisy = [x for x in report if x["kind"] != "on_topic"]
    leaks = {x["id"] for x in noisy if c.retrieve(x["question"], k=8)}
    assert len(noisy) == 5 and leaks == {"short-off-topic-bns69-bare", "verbose-off-topic-uk-parliament-with-coi-vocab"}
