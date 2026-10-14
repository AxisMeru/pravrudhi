"""IntakeDriver: stage order, binding refusals, small outputs, and equality with the plain deterministic bundle.
Invented fixtures only; in-process; no model, file or network."""

from __future__ import annotations

import pytest

from pravrudhi.application.a0_assist import intake as I  # noqa: E741
from pravrudhi.application.a0_assist.intake_driver import STAGES, IntakeDriver

RAW = (
    "1. The petitioner says the gate was open.\n2. He relied on (2014) 8 SCC 273 for the point.\n3. The guard did not open it.\n"
)


def run_all(d: IntakeDriver, facts=None):
    sha = d.call("read_input", {})["raw_sha256"]
    d.call("clean", {"raw_sha256": sha})
    d.call("split_facts", {})
    cits = d.call("extract_citations", {"raw_sha256": sha})["citations"]
    for c in cits:
        d.call("verify_citation", {"start": c["start"], "end": c["end"]})
    inp = {"raw_sha256": sha}
    if facts is not None:
        inp["facts"] = facts
    return d.call("bundle", inp)


def test_full_run_equals_the_plain_deterministic_bundle():
    vf = lambda t: "IN_INDEX"  # noqa: E731
    d = IntakeDriver(RAW, exists_fn=vf)
    out = run_all(d)
    assert d.complete() == [] and d.refusals == []
    plain = I.build_bundle(RAW, verify_fn=vf)
    assert d.result is not None and d.result["bundle_sha256"] == plain["bundle_sha256"]
    assert out["bundle_sha256"] == plain["bundle_sha256"] and out["facts"] == 3


def test_outputs_are_small_and_never_carry_the_text():
    d = IntakeDriver(RAW)
    run_all(d)
    for c in d.calls:
        assert "The petitioner says" not in str(c["output"])


@pytest.mark.parametrize("first", [t for t in STAGES if t != "read_input"])
def test_every_other_tool_before_read_input_is_refused(first):
    d = IntakeDriver(RAW)
    out = d.call(first, {"raw_sha256": "x", "start": 0, "end": 1})
    assert out["refused"] is True and d.refusals and d.complete()


def test_wrong_raw_sha_is_refused_and_the_run_is_incomplete():
    d = IntakeDriver(RAW)
    d.call("read_input", {})
    assert d.call("clean", {"raw_sha256": "0" * 64})["refused"] is True
    assert "a tool call was refused" in d.complete()


def test_read_input_twice_and_clean_twice_are_refused():
    d = IntakeDriver(RAW)
    sha = d.call("read_input", {})["raw_sha256"]
    assert d.call("read_input", {}).get("refused") is True
    d.call("clean", {"raw_sha256": sha})
    assert d.call("clean", {"raw_sha256": sha}).get("refused") is True


def test_bundle_before_the_other_stages_is_refused_and_unknown_tool_refused():
    d = IntakeDriver(RAW)
    sha = d.call("read_input", {})["raw_sha256"]
    assert d.call("bundle", {"raw_sha256": sha}).get("refused") is True
    assert d.call("write_file", {}).get("refused") is True
    assert d.result is None and "bundle was not called" in d.complete()


def test_verify_citation_must_name_an_extracted_citation():
    d = IntakeDriver(RAW)
    sha = d.call("read_input", {})["raw_sha256"]
    d.call("clean", {"raw_sha256": sha})
    d.call("split_facts", {})
    d.call("extract_citations", {"raw_sha256": sha})
    assert d.call("verify_citation", {"start": 1, "end": 5}).get("refused") is True


@pytest.mark.parametrize(
    "facts",
    [
        [{"cleaned_start": 0, "cleaned_end": 10, "text": "invented"}],  # text is not allowed at all
        [{"cleaned_start": 0}],
        "all of it",
        [["a", "b"]],
    ],
)
def test_agent_facts_must_be_index_spans_only(facts):
    d = IntakeDriver(RAW)
    out = run_all(d, facts=facts)
    assert out.get("refused") is True and d.result is None


def test_agent_may_merge_facts_by_indexes_and_bundle_revalidates():
    d = IntakeDriver(RAW)
    sha = d.call("read_input", {})["raw_sha256"]
    d.call("clean", {"raw_sha256": sha})
    cands = d.call("split_facts", {})["candidates"]
    d.call("extract_citations", {"raw_sha256": sha})
    merged = [{"cleaned_start": cands[0]["cleaned_start"], "cleaned_end": cands[1]["cleaned_end"]}, cands[2]]
    out = d.call("bundle", {"raw_sha256": sha, "facts": merged})
    assert out["facts"] == 2 and d.complete() == []


def test_agent_cannot_pass_cleaned_text_or_maps():
    d = IntakeDriver(RAW)
    sha = d.call("read_input", {})["raw_sha256"]
    d.call("clean", {"raw_sha256": sha, "cleaned": "x", "segments": []})  # extra keys are ignored: the driver uses its own state
    d.call("split_facts", {})
    d.call("extract_citations", {"raw_sha256": sha})
    d.call("bundle", {"raw_sha256": sha, "cleaned": "evil", "segments": [], "deletions": []})
    assert d.result is not None and "evil" not in d.result["cleaned"] and d.result["clean_status"] == "cleaned"


def test_verifier_fault_is_shown_not_guessed():
    def boom(t):
        raise RuntimeError("down")

    d = IntakeDriver(RAW, exists_fn=boom)
    run_all(d)
    assert d.result is not None and d.result["citations"][0]["verifier_status"] == "CHECK_UNAVAILABLE"


def test_empty_text_is_refused_at_read_input():
    d = IntakeDriver("   ")
    assert d.call("read_input", {}).get("refused") is True
