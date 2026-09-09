"""A captured ask must be able to become work the loop can pick up.

On 2026-09-09 twenty-eight of thirty-five requests sat in `captured` with zero criteria. `next_obligation`
filters on `r.open and r.criteria`, the only automatic `add_criteria` caller is the completion gate's
`_criterion_from_finding` (which needs a request that already has criteria), and no CLI command adds one. A
captured ask therefore could not become actionable by any path in the code, and the loop reported "every
captured request is verified; nothing is owed" while twenty-eight unverified asks waited behind that sentence.

Triage is the missing step: the engine reads the ask and writes acceptance criteria for it. The operator
delegated drafting and approval, so a drafted criterion is `source="engine"` and needs no sign-off — the
completion gate still reviews delivered work against the operator's verbatim ask, so self-drafting cannot
lower the bar, only decide what to attempt first.
"""

from __future__ import annotations

import json
from pathlib import Path

from pravrudhi.application import requests
from pravrudhi.application.requests import capture, draft_criteria, get, triage


class TestDraftCriteria:
    def test_an_enumerated_ask_becomes_one_criterion_per_item(self) -> None:
        drafted = draft_criteria(
            "1)research and setup multiple agent orchestration systems 2)pick the best one 3)wire it in"
        )
        assert [c.text for c in drafted] == [
            "research and setup multiple agent orchestration systems",
            "pick the best one",
            "wire it in",
        ]
        assert all(c.source == "engine" for c in drafted), "the engine drafted these, and the record says so"
        assert all(not c.met for c in drafted), "drafting is not evidence that anything was done"

    def test_other_enumeration_styles_are_recognised(self) -> None:
        assert len(draft_criteria("1. do the first thing\n2. do the second thing")) == 2
        assert len(draft_criteria("1) alpha\n2) beta\n3) gamma")) == 3

    def test_a_prose_ask_becomes_a_single_criterion_carrying_the_whole_ask(self) -> None:
        """Nothing is invented from prose. Splitting a sentence on guesswork produced criteria that named
        nothing, and three agents that could only guess at them."""
        ask = "astra is back..you have to more actively monitor paid models"
        drafted = draft_criteria(ask)
        assert len(drafted) == 1
        assert drafted[0].text == ask, "the ask is quoted, not paraphrased"

    def test_an_empty_ask_drafts_nothing(self) -> None:
        assert draft_criteria("   ") == []

    def test_a_criterion_is_never_longer_than_the_ledger_column(self) -> None:
        drafted = draft_criteria("x" * 900)
        assert len(drafted) == 1 and len(drafted[0].text) <= 300


class TestTriage:
    def test_a_captured_ask_gains_criteria_and_becomes_visible_to_the_loop(self, tmp_path: Path) -> None:
        r = capture(tmp_path, "1)unblock the studio loop 2)report what it chose")
        assert requests.next_unmet(tmp_path) is None, "with no criteria there is nothing to pick up"

        triaged = triage(tmp_path, r.id)
        assert triaged is not None and len(triaged.criteria) == 2

        picked = requests.next_unmet(tmp_path)
        assert picked is not None and picked[0].id == r.id, "triage is what puts the ask in front of the loop"

    def test_triage_does_not_touch_a_request_that_already_has_criteria(self, tmp_path: Path) -> None:
        """Re-drafting hourly would bury an ask under its own restatements."""
        r = capture(tmp_path, "1)one 2)two")
        triage(tmp_path, r.id)
        again = triage(tmp_path, r.id)
        assert again is None
        assert len(get(tmp_path, r.id).criteria) == 2  # type: ignore[union-attr]

    def test_an_ask_that_states_nothing_is_left_alone(self, tmp_path: Path) -> None:
        r = capture(tmp_path, "   ")
        assert triage(tmp_path, r.id) is None
        assert get(tmp_path, r.id).criteria == []  # type: ignore[union-attr]

    def test_untriaged_names_the_captured_asks_with_no_criteria_oldest_first(self, tmp_path: Path) -> None:
        first = capture(tmp_path, "asked first", asked_at="2026-09-01T00:00:00Z")
        second = capture(tmp_path, "asked second", asked_at="2026-09-02T00:00:00Z")
        done = capture(tmp_path, "1)already triaged")
        triage(tmp_path, done.id)

        pending = requests.untriaged(tmp_path)
        assert [r.id for r in pending] == [first.id, second.id], "oldest first, and only the undrafted ones"


class TestTheBeatTriages:
    """The loop must be able to reach a captured ask on its own, and must not misdescribe what it sees."""

    def test_a_beat_with_nothing_actionable_triages_the_oldest_captured_ask(self, tmp_path: Path) -> None:
        from pravrudhi.application import heartbeat

        r = capture(tmp_path, "1)unblock the studio loop 2)say what it chose")
        chose, reason, result = heartbeat._beat_obligations(tmp_path, lambda _t: "")

        assert result is not None and result["kind"] == "triage", "a beat with no criteria to work has triage to do"
        assert chose == {"request": r.id}
        assert r.id in reason
        triaged = get(tmp_path, r.id)
        assert triaged is not None and len(triaged.criteria) == 2
        assert requests.next_unmet(tmp_path) is not None, "the next beat can now pick this up"

    def test_a_workspace_with_no_requests_still_reports_nothing_owed(self, tmp_path: Path) -> None:
        from pravrudhi.application import heartbeat

        _chose, reason, result = heartbeat._beat_obligations(tmp_path, lambda _t: "")
        assert result is None and "nothing is owed" in reason

    def test_the_beat_never_calls_an_untriaged_ask_verified(self, tmp_path: Path) -> None:
        """`captured` is not `verified`. Saying so was a statement the ledger did not support."""
        from pravrudhi.application import heartbeat

        capture(tmp_path, "   ")  # states nothing, so triage declines it and there is still nothing actionable
        _chose, reason, _result = heartbeat._beat_obligations(tmp_path, lambda _t: "")
        assert "verified" not in reason, "one unverified captured ask makes that claim false"


class TestDecomposeAsk:
    """A prose ask names nothing a builder can act on, so a model is asked to say what would satisfy it.

    `draft_criteria` alone turned "step up to bigger things, let pravrudhi rsi handle most of its growth" into
    one criterion repeating those words. Three agents were once handed a criterion like that and could only
    guess, producing twenty-odd files apiece before the attempt budget parked the request
    (`heartbeat._names_something`). Decomposition is what makes such an ask actionable at all — but only if what
    comes back names something, so anything that does not is dropped.
    """

    def test_a_model_answer_becomes_criteria_that_name_things(self) -> None:
        def complete(_prompt: str) -> str:
            return json.dumps({"criteria": [
                "`kshudha.py` measures a growth drive from the ledger rather than a constant",
                "heartbeat.py dispatches that drive without the operator naming a card",
            ]})

        drafted = requests.decompose_ask("step up to bigger things, let pravrudhi rsi handle its own growth",
                                         complete=complete)
        assert len(drafted) == 2
        assert all(c.source == "engine" for c in drafted)
        assert all(not c.met for c in drafted), "a drafted criterion is not evidence"
        assert "kshudha.py" in drafted[0].text

    def test_a_criterion_that_names_nothing_is_dropped(self) -> None:
        """The reviewer's own test for an actionable finding, applied to the engine's own drafting."""
        def complete(_prompt: str) -> str:
            return json.dumps({"criteria": [
                "The engine should be better at handling its own growth over time",
                "`heartbeat.py` chooses the growth drive",
            ]})

        drafted = requests.decompose_ask("do the growth thing", complete=complete)
        assert [c.text for c in drafted] == ["`heartbeat.py` chooses the growth drive"]

    def test_an_answer_naming_nothing_at_all_drafts_nothing(self) -> None:
        """`[]` hands the decision back to the caller rather than inventing a criterion from a bad answer."""
        def complete(_prompt: str) -> str:
            return json.dumps({"criteria": ["be better", "improve things generally"]})

        assert requests.decompose_ask("do the thing", complete=complete) == []

    def test_an_unusable_answer_drafts_nothing_rather_than_raising(self) -> None:
        for answer in ("not json at all", "{}", '{"criteria": "not a list"}', ""):
            assert requests.decompose_ask("do the thing", complete=lambda _p, a=answer: a) == []

    def test_a_model_that_fails_drafts_nothing_rather_than_raising(self) -> None:
        """An unreachable endpoint must not stop a beat; the verbatim fallback still applies."""
        def complete(_prompt: str) -> str:
            raise OSError("no chat model answered")

        assert requests.decompose_ask("do the thing", complete=complete) == []

    def test_the_ask_is_quoted_to_the_model_verbatim(self) -> None:
        seen: list[str] = []

        def complete(prompt: str) -> str:
            seen.append(prompt)
            return json.dumps({"criteria": ["`x.py` does the thing"]})

        ask = "astra is back..you have to more actively monitor paid models"
        requests.decompose_ask(ask, complete=complete)
        assert ask in seen[0], "the operator's words reach the model unrewritten"


class TestTriageUsesTheModelOnlyForProse:
    def test_an_enumerated_ask_never_reaches_the_model(self, tmp_path: Path) -> None:
        """The operator's own numbering is better structure than a model's reading of it, and free."""
        called: list[str] = []

        def complete(prompt: str) -> str:
            called.append(prompt)
            return json.dumps({"criteria": ["`a.py` something"]})

        r = capture(tmp_path, "1)first thing 2)second thing")
        triaged = triage(tmp_path, r.id, complete=complete)
        assert triaged is not None and len(triaged.criteria) == 2
        assert called == [], "no dispatch is paid for an ask that already states its parts"

    def test_a_prose_ask_is_decomposed(self, tmp_path: Path) -> None:
        def complete(_prompt: str) -> str:
            return json.dumps({"criteria": ["`watchdog.py` reports a parked criterion",
                                            "`requests.py` skips a parked criterion"]})

        r = capture(tmp_path, "the loop keeps choosing the thing it gave up on")
        triaged = triage(tmp_path, r.id, complete=complete)
        assert triaged is not None and len(triaged.criteria) == 2
        assert "watchdog.py" in triaged.criteria[0].text

    def test_a_prose_ask_falls_back_to_the_verbatim_criterion(self, tmp_path: Path) -> None:
        """Decomposition failing must not leave the ask invisible again; one criterion beats none."""
        ask = "step up to bigger things"
        r = capture(tmp_path, ask)
        triaged = triage(tmp_path, r.id, complete=lambda _p: "garbage")
        assert triaged is not None and len(triaged.criteria) == 1
        assert triaged.criteria[0].text == ask

    def test_triage_without_a_model_is_still_the_deterministic_drafter(self, tmp_path: Path) -> None:
        ask = "step up to bigger things"
        r = capture(tmp_path, ask)
        triaged = triage(tmp_path, r.id)
        assert triaged is not None and [c.text for c in triaged.criteria] == [ask]


class TestTheBeatUsesAModelForProse:
    def test_the_beat_passes_its_model_through_to_triage(self, tmp_path: Path) -> None:
        from pravrudhi.application import heartbeat

        def complete(_prompt: str) -> str:
            return json.dumps({"criteria": ["`heartbeat.py` chooses the growth drive"]})

        r = capture(tmp_path, "step up to bigger things and handle your own growth")
        _chose, _reason, result = heartbeat._beat_triage(tmp_path, complete=complete)

        assert result is not None and result["kind"] == "triage"
        assert result["criteria"] == ["`heartbeat.py` chooses the growth drive"]
        triaged = get(tmp_path, r.id)
        assert triaged is not None and triaged.criteria[0].source == "engine"

    def test_a_beat_with_no_reachable_model_still_triages(self, tmp_path: Path) -> None:
        """The deterministic drafter is the floor: an unreachable endpoint must not re-hide the ask."""
        from pravrudhi.application import heartbeat

        ask = "step up to bigger things"
        capture(tmp_path, ask)
        _chose, _reason, result = heartbeat._beat_triage(tmp_path, complete=None)
        assert result is not None and result["criteria"] == [ask]

    def test_the_engine_builds_a_real_chat_seat_for_triage(self, tmp_path: Path) -> None:
        """`_triage_complete` returning `None` is a legitimate outcome, which meant a coding error inside it
        looked exactly like an unconfigured endpoint: a `NameError` for a missing import was swallowed and the
        fallback test passed for the wrong reason. So the seat itself is asserted, not only the fallback."""
        from pravrudhi.application import heartbeat

        built = heartbeat._triage_complete(tmp_path)
        assert built is not None and callable(built), "a suppressed exception must not hide a broken seat"
