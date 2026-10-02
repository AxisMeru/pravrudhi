"""E1 (research/clef-decoder): ContractSchema -> Clef `noul` question records, the 64-question split, and
fail-closed decoding of a Clef reply. No model, no network: the transport is an injected callable.
Claim tier: unit-tested with constructed inputs only, never run live."""

from __future__ import annotations

import math

import pytest

from pravrudhi.application.nyaya_lean_registry import DescribedContract
from pravrudhi.application.typed.clef import (
    MAX_QUESTIONS,
    ClefDecodeError,
    build_records,
    judge_contract,
    noul_probability,
)
from pravrudhi.application.typed.lean_schema import contract_schema, element_field_name


def _cs(n: int):
    return contract_schema(DescribedContract("c", [f"element text {i}" for i in range(n)], ["a denial"]))


def _transport_for(p_by_q: dict[str, float]):
    def transport(record):
        out = {}
        for qid in record["questions"]:
            p = p_by_q[qid]
            out[qid] = {"false": math.log(1 - p), "true": math.log(p)}
        return out

    return transport


class TestBuildRecords:
    def test_one_noul_question_per_element_in_order_with_the_element_text(self) -> None:
        (rec,) = build_records(_cs(3), state="facts")
        assert rec["state"] == "facts"
        assert list(rec["questions"]) == [element_field_name(i) for i in range(3)]
        assert rec["questions"]["element_1"] == {"type": "noul", "instructions": "element text 1"}

    def test_denials_are_never_questions(self) -> None:
        (rec,) = build_records(_cs(2), state="s")
        assert all("denial" not in q["instructions"] for q in rec["questions"].values())

    def test_exactly_64_elements_is_one_record(self) -> None:
        assert len(build_records(_cs(MAX_QUESTIONS), state="s")) == 1

    def test_65_elements_split_into_two_records_covering_each_once_in_order(self) -> None:
        recs = build_records(_cs(MAX_QUESTIONS + 1), state="s")
        assert [len(r["questions"]) for r in recs] == [MAX_QUESTIONS, 1]
        names = [q for r in recs for q in r["questions"]]
        assert names == [element_field_name(i) for i in range(MAX_QUESTIONS + 1)]

    def test_empty_state_is_refused(self) -> None:
        with pytest.raises(ValueError):
            build_records(_cs(1), state="  ")


class TestNoulProbability:
    def test_is_the_softmax_of_true_over_both_options(self) -> None:
        assert noul_probability({"false": 0.0, "true": 0.0}) == pytest.approx(0.5)
        assert noul_probability({"false": 0.0, "true": math.log(3)}) == pytest.approx(0.75)

    @pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
    def test_non_finite_logit_raises(self, bad: float) -> None:
        for opts in ({"false": 0.0, "true": bad}, {"false": bad, "true": 0.0}):
            with pytest.raises(ClefDecodeError):
                noul_probability(opts)

    def test_missing_option_raises_never_guessed(self) -> None:
        with pytest.raises(ClefDecodeError):
            noul_probability({"true": 1.0})

    def test_extra_option_raises(self) -> None:
        with pytest.raises(ClefDecodeError):
            noul_probability({"false": 0.0, "true": 0.0, "maybe": 0.0})

    def test_non_numeric_raises(self) -> None:
        with pytest.raises(ClefDecodeError):
            noul_probability({"false": 0.0, "true": "high"})  # type: ignore[dict-item]


class TestJudgeContract:
    def test_scores_every_element_across_a_split(self) -> None:
        cs = _cs(MAX_QUESTIONS + 3)
        want = {element_field_name(i): 0.01 + 0.9 * i / (MAX_QUESTIONS + 3) for i in range(MAX_QUESTIONS + 3)}
        got = judge_contract(cs, state="s", transport=_transport_for(want))
        assert list(got) == list(want)
        assert all(got[k] == pytest.approx(want[k]) for k in want)

    def test_unanswered_question_raises(self) -> None:
        cs = _cs(2)

        def transport(record):
            return {"element_0": {"false": 0.0, "true": 0.0}}

        with pytest.raises(ClefDecodeError, match="element_1"):
            judge_contract(cs, state="s", transport=transport)

    def test_unknown_question_in_reply_raises(self) -> None:
        cs = _cs(1)

        def transport(record):
            return {"element_0": {"false": 0.0, "true": 0.0}, "element_9": {"false": 0.0, "true": 0.0}}

        with pytest.raises(ClefDecodeError, match="element_9"):
            judge_contract(cs, state="s", transport=transport)

    def test_malformed_reply_raises(self) -> None:
        for bad in (None, [], "x", {"element_0": None}, {"element_0": [1, 2]}):
            with pytest.raises(ClefDecodeError):
                judge_contract(_cs(1), state="s", transport=lambda r, b=bad: b)  # type: ignore[misc]

    def test_one_bad_chunk_fails_the_whole_contract(self) -> None:
        cs = _cs(MAX_QUESTIONS + 1)
        calls = []

        def transport(record):
            calls.append(len(record["questions"]))
            if len(calls) == 2:
                return {"element_64": {"false": 0.0, "true": math.nan}}
            return {q: {"false": 0.0, "true": 0.0} for q in record["questions"]}

        with pytest.raises(ClefDecodeError):
            judge_contract(cs, state="s", transport=transport)
        assert calls == [MAX_QUESTIONS, 1]

    def test_transport_errors_propagate_unchanged(self) -> None:
        def transport(record):
            raise TimeoutError("boom")

        with pytest.raises(TimeoutError):
            judge_contract(_cs(1), state="s", transport=transport)


class TestCli:
    def test_prints_records_for_a_list_of_elements(self, tmp_path, capsys) -> None:
        import json

        from pravrudhi.application.typed.clef import _main

        (tmp_path / "e.json").write_text(json.dumps(["a", "b", "c"]))
        (tmp_path / "s.txt").write_text("the facts")
        argv = ["--elements", str(tmp_path / "e.json"), "--state-file", str(tmp_path / "s.txt"), "--max-questions", "2"]
        assert _main(argv) == 0
        recs = json.loads(capsys.readouterr().out)
        assert [list(r["questions"]) for r in recs] == [["element_0", "element_1"], ["element_2"]]
        assert recs[0]["state"] == "the facts"
