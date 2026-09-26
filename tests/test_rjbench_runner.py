"""RJ-Bench row -> JudgeRequest narrative pin (Track-C, 2026-09-26). Track-B's v2.2 prereg cites this test
as the code-level enforcement of "narrative = row.scrubbed_facts_section verbatim" -- see
`pravrudhi.application.rjbench_runner.build_rjbench_judge_request`.
"""

from __future__ import annotations

from pravrudhi.application.rjbench_runner import build_rjbench_judge_request

#: A representative RJ-Bench kept-instance row (rjbench-rules-v2.6-freeze@1b27e01 schema). Hand-written toy
#: text, not real case text -- this fixture tests the WIRING, not any real judgment.
ROW = {
    "instance_id": "toy-instance-0001",
    "source_path": "https://indiankanoon.org/doc/0000000",
    "contract_id": "bns85",
    "accused_identifier": "the accused",
    "unit": "original",
    "verdict": "convicted",
    "lower_court_verdict": "convicted",
    "lower_court_quote": "The accused is found guilty as charged.",
    "appeal_disposition": "dismissed",
    "appeal_quote": "The appeal is dismissed.",
    "scrubbed_facts_section": (
        "TOY: the accused, husband of the complainant, is alleged to have subjected her to cruelty over a "
        "period of several months, per the complainant's testimony and corroborating witness statements. "
        "[REDACTED: outcome sentence removed by the v2.7.2 leak scan]"
    ),
    "leak_scan_mechanical_clean": True,
    "attribution_path": "named",
}


def test_narrative_is_scrubbed_facts_section_verbatim() -> None:
    """The pinned rule, checked byte-for-byte -- not just `==` on stripped/normalized strings, which could
    pass even if the function silently re-encoded or trimmed whitespace."""
    req = build_rjbench_judge_request(
        ROW, contract_id="bns85", element="subjects the woman to cruelty", statute="TOY statute text",
    )
    assert req.narrative == ROW["scrubbed_facts_section"]
    assert req.narrative.encode("utf-8") == ROW["scrubbed_facts_section"].encode("utf-8")
    # A byte-for-byte pin must also survive a scrubbed section with leading/trailing whitespace and
    # embedded redaction markers -- neither stripped nor altered.
    assert req.narrative.startswith("TOY:")
    assert req.narrative.endswith("leak scan]")


def test_narrative_is_never_re_derived_from_other_row_fields() -> None:
    """A caller who accidentally builds narrative from appeal_quote/lower_court_quote instead (an easy
    mistake, since both are also free text on the row) must be caught -- confirms the pin points at
    scrubbed_facts_section specifically, not just "any string field on the row"."""
    req = build_rjbench_judge_request(
        ROW, contract_id="bns85", element="subjects the woman to cruelty", statute="TOY statute text",
    )
    assert req.narrative != ROW["appeal_quote"]
    assert req.narrative != ROW["lower_court_quote"]


def test_contract_element_statute_facts_pass_through_unpinned() -> None:
    """Only narrative is pinned by this module -- contract_id/element/statute/facts are the caller's own
    resolution and pass straight through, confirming this function does not silently reshape them."""
    facts = (("F1", "TOY fact one."), ("F2", "TOY fact two."))
    req = build_rjbench_judge_request(
        ROW, contract_id="bns85", element="the accused is the husband", statute="TOY statute text",
        facts=facts,
    )
    assert req.contract_id == "bns85"
    assert req.element == "the accused is the husband"
    assert req.statute == "TOY statute text"
    assert req.facts == facts
    assert req.is_denial is False


def test_empty_scrubbed_facts_section_is_still_passed_through_verbatim() -> None:
    """An empty string is a legitimate (if degenerate) scrubbed_facts_section value -- the pin applies
    regardless of content, never falling back to a placeholder of its own invention."""
    row = dict(ROW, scrubbed_facts_section="")
    req = build_rjbench_judge_request(row, contract_id="bns85", element="x", statute="y")
    assert req.narrative == ""
