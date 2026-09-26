"""Issue #51's own calibration set (tests/fixtures/nyaya_relevance_calibration.json), run against the shipped
retrieve(). This is the artefact of the issue's own required process: the set was hand-built and sealed
BEFORE any candidate length-aware rule was measured against it (see the fixture's own `_provenance` and the
commits that introduced it), and the selection rule (minimise false citations at recall >= the current
recall) was written down before the comparison ran. `min_relevance_norm`'s own value (`configs/nyaya_corpus.
yaml`) is the winner of that comparison; this test is what keeps it honest against regression -- a future
change to the corpus, the BM25 shape, or the norm threshold that quietly drops calibration recall or lets
more off-topic noise through fails here first.

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


def _cases() -> list[dict]:
    return json.loads(CALIBRATION_PATH.read_text())["cases"]


def test_every_on_topic_calibration_case_still_finds_its_match() -> None:
    """Recall must not regress below what the absolute-only floor already achieved (14/14) -- the selection
    rule issue #51 fixed before measuring any candidate: minimise false citations at recall >= current."""
    c = nyaya.load_corpus()
    misses = []
    for case in _cases():
        if case["kind"] != "on_topic":
            continue
        hits = c.retrieve(case["question"], k=8)
        top_id = hits[0][0].id if hits else None
        if top_id not in case["expected_ids"]:
            misses.append((case["id"], top_id, case["expected_ids"]))
    assert not misses, f"calibration recall regressed: {misses}"


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
