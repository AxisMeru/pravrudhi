"""Issue #51's own calibration set (tests/fixtures/nyaya_relevance_calibration.json), run against the shipped
retrieve(). This is the artefact of the issue's own required process: the set was hand-built and sealed
BEFORE any candidate length-aware rule was measured against it (see the fixture's own `_provenance` and the
commits that introduced it), and the selection rule (minimise false citations at recall >= the current
recall) was written down before the comparison ran. `min_relevance_norm`'s own value (`configs/nyaya_corpus.
yaml`) is the winner of that comparison; this test is what keeps it honest against regression -- a future
change to the corpus, the BM25 shape, or the norm threshold that quietly drops calibration recall or lets
more off-topic noise through fails here first.

**v2 (2026-09-26, Tag/Lead-2 decision):** the BNS/BNSS statute text v1 measured against was withdrawn
(pravrudhi PR #55 -- fetched from indiacode.gov.in, whose Terms of Use/Copyright Policy conflict with
shipping it as a package asset; held pending lawyer's review). The set was recalibrated honestly against the
IPC+COI-only corpus that actually ships -- see the fixture's own `_provenance` for the full relabeling
record. Two genuine, real recall misses survive this relabeling (`KNOWN_ON_TOPIC_MISSES` below): removing
~800 BNS/BNSS documents shifted this corpus's idf statistics enough that two otherwise-correct IPC/COI
answers now marginally miss `min_relevance_norm`'s floor, even though neither the doctrine nor the threshold
changed. Reported, not hidden by loosening a floor or re-labeling ground truth to match current behaviour --
that would defeat the point of a calibration set built independently of the implementation it measures.

Every case also carries a `split`: `"tune"|"report"` field (stratified by kind, seeded), addressing R1's
in-sample-measurement concern: `test_the_held_out_report_split_recall_and_false_citation_rate` computes the
PR's own headline numbers from the `report` half alone, which was not used to decide any label by eyeballing
retrieval scores.

The three off_topic_verbose cases this file names as `KNOWN_RESIDUAL_LEAKS` are a real, measured gap, not
swept under an aggregate pass: at the tightest norm threshold that still finds every on_topic case, those
three verbose questions' single best-matching document still clears both gates (see MIN_RELEVANCE_NORM's own
comment in nyaya.py for the exact 3/12 vs. 9/12 vs. 11/12 comparison across all three candidates -- this one
was the strict winner, not a perfect fix). Naming them here means a change that fixes one of them is a
welcome surprise this test will catch (strict equality, not "at most 3"), and a change that adds a FOURTH
leak is caught as a real regression rather than hiding inside a loosened aggregate threshold."""

from __future__ import annotations

import json
from pathlib import Path

from pravrudhi.application import nyaya

CALIBRATION_PATH = Path(__file__).parent / "fixtures" / "nyaya_relevance_calibration.json"

#: The exact off_topic_verbose cases whose top hit still clears both gates today -- see this module's own
#: docstring for why these three are named rather than folded into an aggregate "false citation rate" number.
KNOWN_RESIDUAL_LEAKS = frozenset(
    {
        "verbose-off-topic-bns69-more-constitutional-terms",
        "verbose-off-topic-uk-parliament-with-coi-vocab",
        "verbose-off-topic-holiday-planning-with-conspiracy-vocab",
    }
)

#: v2 (2026-09-26): the exact on_topic cases that now genuinely miss -- a real, measured side effect of
#: removing ~800 BNS/BNSS documents (idf shift), not a doctrine change and not a threshold change. Named
#: explicitly, exact-match (not "at most 2"), same discipline as KNOWN_RESIDUAL_LEAKS above: a newly-missing
#: THIRD case is a real regression, and a fixed one silently staying tolerated forever is also caught.
KNOWN_ON_TOPIC_MISSES = frozenset(
    {
        "long-on-topic-conspiracy-story",
        "medium-on-topic-remedies-fundamental-rights",
    }
)


def _cases() -> list[dict]:
    return json.loads(CALIBRATION_PATH.read_text())["cases"]


def _top_id(c: nyaya.Corpus, question: str) -> str | None:
    hits = c.retrieve(question, k=8)
    return hits[0][0].id if hits else None


def test_every_on_topic_calibration_case_still_finds_its_match() -> None:
    """Recall must not regress beyond the two known, real misses this file names explicitly -- the selection
    rule issue #51 fixed before measuring any candidate: minimise false citations at recall >= current."""
    c = nyaya.load_corpus()
    misses = set()
    for case in _cases():
        if case["kind"] != "on_topic":
            continue
        top_id = _top_id(c, case["question"])
        if top_id not in case["expected_ids"]:
            misses.add(case["id"])
    assert misses == KNOWN_ON_TOPIC_MISSES, f"on_topic recall changed: {sorted(misses)}"


def test_on_topic_uncovered_cases_return_nothing() -> None:
    """`kind: on_topic_uncovered` (v2): a real, in-domain legal question this specific shipped corpus
    genuinely has no text for (no CrPC/BNSS shipped at all; no textual IPC clause covers the deceit-marriage
    question) -- unlike `KNOWN_RESIDUAL_LEAKS` above, there is no tolerated leak here at all: retrieve() must
    return nothing for every one of these, full stop. This is the exact 'don't cite what isn't there'
    behaviour the withdrawn BNS/BNSS package asset itself violated."""
    c = nyaya.load_corpus()
    leaking = [case["id"] for case in _cases() if case["kind"] == "on_topic_uncovered" and c.retrieve(case["question"], k=8)]
    assert leaking == []


def test_off_topic_calibration_cases_are_cut_except_the_named_residual_gap() -> None:
    """The false-citation rate this length-aware gate actually achieves: every off_topic/off_topic_verbose
    calibration case returns nothing EXCEPT the three named in KNOWN_RESIDUAL_LEAKS. A newly-leaking case
    (fourth+) fails this test; so does a fixed one silently staying in the tolerated set forever, since the
    set comparison below is exact, not "at most"."""
    c = nyaya.load_corpus()
    leaking = set()
    for case in _cases():
        if case["kind"] == "on_topic":
            continue
        if c.retrieve(case["question"], k=8):
            leaking.add(case["id"])
    assert leaking == KNOWN_RESIDUAL_LEAKS


def test_the_held_out_report_split_recall_and_false_citation_rate() -> None:
    """The PR's own headline numbers, computed on the `report` half alone (Lead-2, 2026-09-26, addressing
    R1's in-sample-measurement concern): this half was not used to decide any label by eyeballing a retrieval
    score -- every expected_ids entry was set from the shipped document's own TEXT (see the fixture's
    `_provenance`), and the tune/report split was assigned afterwards, stratified by kind, seeded, before this
    test was ever run. If these numbers ever need to change, they must change here, not just in a PR
    description someone forgot to update."""
    c = nyaya.load_corpus()
    report = [case for case in _cases() if case["split"] == "report"]

    on_topic = [case for case in report if case["kind"] == "on_topic"]
    on_topic_misses = {case["id"] for case in on_topic if _top_id(c, case["question"]) not in case["expected_ids"]}
    assert on_topic_misses == KNOWN_ON_TOPIC_MISSES  # both known misses landed in the report split
    assert len(on_topic) == 5 and len(on_topic_misses) == 2  # 3/5 recall on the held-out on_topic report cases

    uncovered = [case for case in report if case["kind"] == "on_topic_uncovered"]
    assert not any(c.retrieve(case["question"], k=8) for case in uncovered)
    assert len(uncovered) == 1

    noisy = [case for case in report if case["kind"] in ("off_topic", "off_topic_verbose")]
    leaks = {case["id"] for case in noisy if c.retrieve(case["question"], k=8)}
    assert leaks == (KNOWN_RESIDUAL_LEAKS & {case["id"] for case in noisy})
    assert len(noisy) == 5 and len(leaks) == 1  # 1/5 false citations on the held-out noise report cases
