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

import pytest

from pravrudhi.application import heartbeat, requests
from pravrudhi.application.requests import Criterion, Evidence, capture, draft_criteria, get, triage


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

    def test_a_criterion_is_never_longer_than_the_cap_and_is_cut_at_a_boundary(self) -> None:
        from pravrudhi.application.requests import _CRITERION_CHARS

        drafted = draft_criteria("x" * 1500)
        assert len(drafted) == 1 and len(drafted[0].text) <= _CRITERION_CHARS + 3
        worded = draft_criteria(("`src/pravrudhi/x.py` holds the value. " * 60).strip())
        assert worded[0].text.count("`") % 2 == 0, "a clip never leaves a name half-quoted"


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

    def test_a_parked_request_does_not_hide_the_captured_asks_behind_it(self, tmp_path: Path) -> None:
        """Both loops chose the same parked request every hour for five hours with `looked_at: []` while
        ninety captured asks had no criteria: `parked_request` returned before triage was reached."""
        from pravrudhi.application import heartbeat

        parked = capture(tmp_path, "the parked one", criteria=[Criterion(text="a thing", source="operator")])
        for _ in range(heartbeat.MAX_CRITERION_ATTEMPTS):
            heartbeat.record_attempt(tmp_path, parked.id, 0)
        fresh = capture(tmp_path, "1)wire the sidebar 2)say what changed")
        chose, reason, result = heartbeat._beat_obligations(tmp_path, lambda _t: "")
        assert result is not None and result["kind"] == "triage" and chose == {"request": fresh.id}, reason
        assert len(get(tmp_path, fresh.id).criteria) == 2

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

    def test_an_unusable_answer_is_no_reading_rather_than_an_empty_one(self) -> None:
        """`None`, not `[]`: garbage is not a model saying the ask names nothing."""
        for answer in ("not json at all", "{}", '{"criteria": "not a list"}', ""):
            assert requests.decompose_ask("do the thing", complete=lambda _p, a=answer: a) is None

    def test_a_model_that_fails_is_no_reading_rather_than_raising(self) -> None:
        """An unreachable endpoint must not stop a beat; the verbatim fallback still applies."""
        def complete(_prompt: str) -> str:
            raise OSError("no chat model answered")

        assert requests.decompose_ask("do the thing", complete=complete) is None

    def test_an_ask_a_model_finds_non_actionable_is_declined_not_drafted_verbatim(self, tmp_path: Path) -> None:
        """A pasted bot-provisioning reply and a forwarded Telegram echo each became a criterion three agents
        were paid to attempt. When a model has read the ask and named nothing, the record says so."""
        r = capture(tmp_path, "Done! Congratulations on your new bot. You will find it at t.me/example_bot.")
        triaged = triage(tmp_path, r.id, complete=lambda _p: json.dumps({"criteria": ["be better"]}))
        assert triaged is not None and triaged.state == "declined" and triaged.criteria == []
        assert "names nothing" in triaged.notes[-1]["note"]
        assert requests.untriaged(tmp_path) == [], "a declined ask is no longer owed"

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

    def test_the_engine_builds_a_real_chat_seat_for_triage(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """`_triage_complete` returning `None` is a legitimate outcome, which meant a coding error inside it
        looked exactly like an unconfigured endpoint: a `NameError` for a missing import was swallowed and the
        fallback test passed for the wrong reason. So the seat itself is asserted, not only the fallback."""
        from pravrudhi.application import heartbeat
        from pravrudhi.models import openai_compat

        class _Reachable:
            def __init__(self, *a: object, **k: object) -> None:
                pass

            def chat(self, *a: object, **k: object) -> object:
                return type("R", (), {"text": '{"criteria": ["`x.py` exists"]}'})()

        # The seat is asked for a one-word reply when it is built, so an unconfigured or dead endpoint yields
        # `None` here (and the fleet, on an initialised workspace) rather than a callable that fails later.
        assert heartbeat._triage_complete(tmp_path) is None, "a bare directory has no seat and no fleet"
        monkeypatch.setattr(openai_compat, "ChatClient", _Reachable)
        built = heartbeat._triage_complete(tmp_path)
        assert built is not None and callable(built), "a suppressed exception must not hide a broken seat"
        assert "x.py" in built("anything")


class TestParkedIsNotDelivered:
    """The regression that took both loops down on 2026-09-10.

    Skipping a budget-exhausted criterion made `next_unmet` return None, and `next_obligation` read that as
    "nothing left to build" and emitted `advance_request`. `advance` then refused -- correctly, since a
    parked criterion is not a met one -- and the RequestError was unhandled, so the whole beat died. Both
    the studio and the product heartbeat had been failing every hour since:

        RequestError: r-5795501a still has 2 unmet criterion(s)
        RequestError: r-3981d7e0 cannot go from captured to delivered

    Two definitions of done disagreed. `next_obligation` counted a criterion whose attempts were spent as
    nothing left to build; `advance` counted it as unmet. The disagreement, not either definition, was the
    defect, and the crash is what made it urgent rather than merely wrong.
    """

    @staticmethod
    def _parked(root: Path) -> str:
        req = capture(root, "a thing that cannot be done")
        requests.add_criteria(root, req.id, [Criterion(text="never satisfiable", source="engine")])
        for _ in range(heartbeat.MAX_CRITERION_ATTEMPTS):
            heartbeat.record_attempt(root, req.id, 0)
        return req.id

    def test_it_is_reported_as_parked_rather_than_claimed_ready(self, tmp_path: Path) -> None:
        request_id = self._parked(tmp_path)
        assert requests.next_unmet(tmp_path) is None, "the budget is spent, so nothing is offered to work on"
        owed = requests.next_obligation(tmp_path)
        assert owed is not None
        assert owed["kind"] == "parked_request"
        assert owed["request"] == request_id

    def test_the_guard_that_refused_it_is_unchanged(self, tmp_path: Path) -> None:
        # The fix is upstream of `advance`. Delivering a request with an unmet criterion must still be
        # refused, and from `captured` the transition table refuses it even earlier. Both refusals are
        # correct; both were reached by a caller that should not have tried, and killed the beat.
        request_id = self._parked(tmp_path)
        with pytest.raises(requests.RequestError, match="cannot go from captured to delivered"):
            requests.advance(tmp_path, request_id, "delivered")
        requests.advance(tmp_path, request_id, "in_progress")
        with pytest.raises(requests.RequestError, match="unmet criterion"):
            requests.advance(tmp_path, request_id, "delivered")

    def test_the_obligations_beat_survives_a_parked_request(self, tmp_path: Path) -> None:
        # The crash itself, at the level it happened. `_beat_obligations` called `advance` on the strength of
        # `next_obligation` saying the request was ready; the refusal propagated out of the beat and the unit
        # exited 1 every hour. What it reports matters less than that it returns at all.
        request_id = self._parked(tmp_path)
        before = requests.get(tmp_path, request_id)
        assert before is not None
        chose, reason, extra = heartbeat._beat_obligations(tmp_path, None)
        after = requests.get(tmp_path, request_id)
        assert after is not None and after.state == before.state, "reporting a parked request changes nothing"
        assert request_id in json.dumps([chose, reason, extra], default=str)

    def test_a_genuinely_finished_request_still_advances(self, tmp_path: Path) -> None:
        # The fix must not close the path it was protecting.
        req = capture(tmp_path, "a thing that can be done")
        requests.add_criteria(tmp_path, req.id, [Criterion(text="satisfiable", source="engine")])
        requests.meet(tmp_path, req.id, 0, [Evidence(kind="commit", ref="abc1234")])
        owed = requests.next_obligation(tmp_path)
        assert owed is not None and owed["kind"] == "advance_request"

class TestDraftedMode:
    """r-9c8646fc: a criterion's `mode` is decided once at draft time (`heartbeat.dispatch_mode`'s own
    detection, applied here rather than only re-derived later at dispatch), so the persisted field already
    says what the loop will do with it."""

    def test_a_verbatim_prose_criterion_naming_a_recognised_path_is_drafted_as_build(self) -> None:
        drafted = draft_criteria("`src/pravrudhi/application/foo.py` gains a retry")
        assert len(drafted) == 1 and drafted[0].mode == "build"

    def test_a_verbatim_prose_criterion_naming_nothing_buildable_stays_proposal(self) -> None:
        drafted = draft_criteria("astra is back..you have to more actively monitor paid models")
        assert len(drafted) == 1 and drafted[0].mode == "proposal"

    def test_an_enumerated_item_naming_a_path_is_drafted_as_build_independently_of_its_siblings(self) -> None:
        drafted = draft_criteria(
            "1)`src/pravrudhi/application/foo.py` gains a retry 2)tell the operator when it is done"
        )
        assert [c.mode for c in drafted] == ["build", "proposal"]

    def test_a_model_decomposed_criterion_naming_a_path_is_drafted_as_build(self) -> None:
        drafted = requests.decompose_ask(
            "fix the retry logic",
            complete=lambda _p: json.dumps({"criteria": ["`src/pravrudhi/application/foo.py` retries once"]}),
        )
        assert drafted is not None and drafted[0].mode == "build"


class TestTriageDeclinesNonActionableDrafts:
    """r-9c8646fc's survey of the open backlog found a forwarded cross-session handoff, a plain instruction to
    reply rather than build, and other shapes accepted verbatim as "criteria" by the no-model fallback path -
    `decompose_ask` already declines an unbuildable MODEL answer (see `test_an_ask_a_model_finds_non_actionable_
    is_declined_not_drafted_verbatim` above); this is the same discipline for the path that never asks a model
    at all."""

    def test_a_forwarded_transcript_is_declined_without_a_model(self, tmp_path: Path) -> None:
        r = capture(
            tmp_path,
            '<cross-session-message from="peer" name="handoff">\nStatus update: nothing to report.\n'
            "</cross-session-message>",
        )
        triaged = triage(tmp_path, r.id)
        assert triaged is not None and triaged.state == "declined" and triaged.criteria == []
        assert "not something the engine can attempt" in triaged.notes[-1]["note"]

    def test_an_instruction_to_reply_is_declined_without_a_model(self, tmp_path: Path) -> None:
        r = capture(tmp_path, "Reply to me with the key lessons you learnt that I must carry forward")
        triaged = triage(tmp_path, r.id)
        assert triaged is not None and triaged.state == "declined"

    def test_a_bare_question_is_declined_without_a_model(self, tmp_path: Path) -> None:
        r = capture(tmp_path, "should we use approach A or approach B for the retry logic?")
        triaged = triage(tmp_path, r.id)
        assert triaged is not None and triaged.state == "declined"

    def test_an_ordinary_prose_ask_is_unaffected(self, tmp_path: Path) -> None:
        r = capture(tmp_path, "step up to bigger things")
        triaged = triage(tmp_path, r.id)
        assert triaged is not None and triaged.state != "declined" and len(triaged.criteria) == 1

    def test_an_enumerated_ask_keeps_only_its_actionable_items(self, tmp_path: Path) -> None:
        r = capture(tmp_path, "1)fix the retry bug 2)reply to me once it is done")
        triaged = triage(tmp_path, r.id)
        assert triaged is not None and [c.text for c in triaged.criteria] == ["fix the retry bug"]

    def test_a_forwarded_transcript_is_declined_even_when_the_model_is_unusable(self, tmp_path: Path) -> None:
        """An unreachable or unusable model falls back to the verbatim draft (`decompose_ask` returns `None`,
        not `[]`) - the new filter must still catch the pasted transcript on that fallback text, not only on
        the no-model path `test_a_forwarded_transcript_is_declined_without_a_model` already covers."""
        r = capture(tmp_path, '<cross-session-message from="peer">nothing actionable here</cross-session-message>')
        triaged = triage(tmp_path, r.id, complete=lambda _p: "not json")
        assert triaged is not None and triaged.state == "declined"


def test_the_decomposer_is_told_what_the_engine_may_not_change() -> None:
    """Of 327 open criteria on 2026-09-11, 77 named the kernel, research/ or gitignored docs: the decomposer
    proposed work the loop is forbidden to do, three dispatches each, until `heartbeat.unbuildable` learned to
    stall them. The upstream fix is to tell it."""
    from pravrudhi.application.requests import _decompose_prompt

    prompt = _decompose_prompt("make the kernel's controller smarter")
    for forbidden in ("pravrudhi_kernel/", "research/", "gates/", ".pravrudhi/", "docs/blueprint/"):
        assert forbidden in prompt
    assert "src/pravrudhi/" in prompt
    assert "make the kernel's controller smarter" in prompt, "the operator's words stay verbatim"


class TestClip:
    def test_a_long_criterion_keeps_its_last_backticked_name_whole(self) -> None:
        from pravrudhi.application.requests import _clip

        text = ("The messaging surfaces are reachable from `src/pravrudhi/cli/app.py`, reported by `pravrudhi doctor` "
                "and documented in `docs/usage.md` " + "with a sentence that runs on " * 40)
        clipped = _clip(text, limit=140)
        assert "`docs/usage.md`" in clipped or clipped.count("`") % 2 == 0
        assert len(clipped) <= 143

    def test_a_short_criterion_is_untouched(self) -> None:
        from pravrudhi.application.requests import _clip

        assert _clip("`src/x.py` sets VALUE = 2") == "`src/x.py` sets VALUE = 2"

    def test_drafted_criteria_are_no_longer_cut_at_three_hundred(self) -> None:
        from pravrudhi.application.requests import _CRITERION_CHARS

        assert _CRITERION_CHARS >= 1000
