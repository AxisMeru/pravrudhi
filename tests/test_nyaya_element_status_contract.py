"""The four-state element status contract and `binding_leg` (issue #37, PR #45), tested from OUTSIDE the
change: an adversarial review suite written against the contract as issue #37 and its own comment thread
state it, not against what `_truthful_status` happens to do.

The contract under test, in the issue's words:
  * "'judge concluded fail' and 'couldn't evaluate' are never the same label anywhere downstream";
  * `not_confirmed` is shown as "not confirmed at the required confidence", NEVER as "fails";
  * `binding_leg` names "which judge's tau it failed", so a `not_confirmed` split by leg tells Lead-2
    whether the 32B's tau=0.97 or the 4B's tau=0.74 is the binding constraint.

Reuses `test_nyaya_agent.py`'s own scripted doubles rather than inventing a second set (this repo already
imports across test modules -- see `test_cli_gate.py`'s `from tests.test_gate import ...`), and drives the
REAL `AndGateJudge` over two `ScriptedJudge`s so the gate's own cost-saving early return (primary rejects ->
second never asked) is exercised exactly as production wires it, never re-implemented here. The serialisation
half goes through the REAL FastAPI route, the same discipline `test_api_partner.py` uses.

Every status/leg set is read from the code's own declarations (`get_args`), never hardcoded, so adding a
fifth status or a fourth leg without extending the coverage here trips this file instead of shipping silently.

THE FIFTH STATUS (operator decision, 2026-09-26). `not_evaluated_gate1_unavailable`: Gate 1, the entailment
check, could not evaluate the element at all -- distinct from `not_established` (a judge scored it and it
failed) and from `not_evaluated_second_unavailable` (the SECOND judge never answered). The engine does not
emit it yet: `_truthful_status`'s `vetoed_by == "gate1"` early return collapses every Gate 1 veto, including
the model-unavailable one, into plain `not_established` (`nyaya_agent.py:844-845`), while the contract
OUTCOME is already REFER_TO_LAWYER / `gate1_unavailable` (`nyaya_agent.py:1248-1249`). So the outcome is
fail-closed and truthful; the element label is fail-closed but not truthful. The operator sequenced the
frontend first (`pravrudhi-app` PR #8 renders all five, unknown still to the error state), the engine after,
so the three assertions that pin the new label cannot hold on this branch yet. The assertions that already
hold today -- the outcome-level refusal, and the element's own `gate1_unavailable` flag that a later engine
change derives the label from -- are ordinary PASSING tests here, so a refactor cannot quietly remove the
fail-closed half while the truthful-label half is still in flight.

CONTRACTS THE CODE HAS NOT ADOPTED YET are marked `xfail(strict=True)` rather than left red (`main` is
branch-protected, and a deliberately-red suite cannot merge into it). Seven markers, four reasons, listed at
`XFAIL_FIFTH_STATUS` and below. This is NOT a skip and NOT a quarantine: every marked test runs on every
CI run and executes every one of its assertions, pinning exactly what the code does today; only the
reporting of its known failure changes. And it cannot rot -- `strict=True` turns the XPASS into a hard
FAILURE the moment the contract IS adopted, so the marker has to be deleted by whoever lands that change.
The flip side of strict is that an XPASS for the WRONG reason (a fixture that stopped exercising the thing)
would also read as adoption, which a red test could not do, so each marked test either keeps its own
in-test guard ahead of the marked assertion or has a plain, never-marked companion test that fails if its
fixture drifts; both are called out in the docstrings below.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, get_args, get_type_hints

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pravrudhi.api.partner import ElementResultOut, build_partner_router
from pravrudhi.application.nyaya_agent import ElementResult, ElementStatus, NyayaAgent
from pravrudhi.application.nyaya_judges import AndGateJudge, ElementJudgment, Gate1Judge
from tests.test_api_partner import _NO_LIMIT_CONFIG
from tests.test_nyaya_agent import (
    BNS69_EL,
    TOY_FACTS,
    ScriptedJudge,
    _config,
    _not,
    _proof_script,
    _registry,
    _second,
)
from tests.test_nyaya_gate1 import _StubModel

#: The served taus of the shipped AND gate: `AND(4B >= 0.74, 32B >= 0.97)`.
TAU_PRIMARY = 0.74
TAU_SECOND = 0.97

#: The fifth element status, as the project operator specified it on 2026-09-26 (name and user label both).
#: NOT in the engine's `ElementStatus` literal yet -- deliberately spelled out here rather than read from
#: `get_args`, because this file's whole job for the fifth case is to say what the engine SHOULD emit before
#: it emits it. `DECLARED_STATUSES` below stays the engine's own four, so the exhaustiveness tests keep
#: measuring the engine, not this constant.
GATE1_UNAVAILABLE_STATUS = "not_evaluated_gate1_unavailable"

#: The seven contracts this file asserts that the engine/API has NOT adopted yet, each marked
#: `xfail(strict=True)` with the reason verbatim as the project set it (2026-09-26). `strict=True` on every
#: one, deliberately: the day a contract IS adopted the test XPASSes, and a strict xfail reports an XPASS as
#: a FAILURE -- so the marker cannot rot in place, whoever lands the change has to delete it. A non-strict
#: marker would absorb the xpass silently, the same fail-open shape this suite exists to remove. Note what
#: this is NOT: nothing is skipped or quarantined. Every marked test still runs, still executes every
#: assertion, and still pins today's behaviour -- only the reporting of its known failure changes.
XFAIL_FIFTH_STATUS = "awaits fifth-status engine change (not_evaluated_gate1_unavailable)"
XFAIL_TRI_STATE_ASSERTIONS = "awaits #56 tri-state assertions (PR #60)"
XFAIL_BINDING_LEG = "awaits #57 binding_leg fix (PR #63)"
#: Issue #78, "API response model must constrain element status to the declared set and refuse empty or
#: unknown values" -- filed 2026-09-26 off this suite's own findings 3 and 4, and covering both of them.
XFAIL_RESPONSE_MODEL = "awaits #78: API response model must constrain status to the declared set and refuse empty"

Script = dict[str, list[ElementJudgment | Exception]]

#: Every status the engine declares, read from the engine's own `Literal` -- the single source of truth.
DECLARED_STATUSES: tuple[str, ...] = tuple(get_args(ElementStatus))


def _declared_legs() -> tuple[str, ...]:
    """Every `binding_leg` value `ElementResult` declares, read from its own annotation (a
    `Literal[...] | None`), so a fourth leg added to the field trips the exhaustiveness tests below."""
    legs: list[str] = []
    for arm in get_args(get_type_hints(ElementResult)["binding_leg"]):
        legs.extend(a for a in get_args(arm) if isinstance(a, str))
    return tuple(legs)


DECLARED_LEGS: tuple[str, ...] = _declared_legs()

#: The two statuses that mean "a verdict was reached" -- what an element must NEVER read as when nothing was
#: actually evaluated (issue #37's whole point).
VERDICT_STATUSES = ("established", "not_established")


# -- scenarios ---------------------------------------------------------------------------------------------
# Each builds a FRESH pair of scripts (the `ScriptedJudge` queues are consumed, so a shared dict would leak
# between the computed run and the serialised run of the same scenario). EL0 is the element under test; EL1
# and the denial always behave, so the contract's outcome turns only on EL0.


def _second_passes() -> Script:
    return {
        BNS69_EL[0]: [_second("established", 0.99)],
        BNS69_EL[1]: [_second("established", 0.99)],
    }


def _sc_established() -> tuple[Script, Script]:
    """Both judges clear their own tau."""
    return _proof_script(TOY_FACTS), _second_passes()


def _sc_not_confirmed_second() -> tuple[Script, Script]:
    """Lead-2's own fixture: the 4B passes, the 32B leans established (0.8 >= 0.5) but misses its tau 0.97."""
    second = _second_passes()
    second[BNS69_EL[0]] = [_second("not_established", 0.8)]
    return _proof_script(TOY_FACTS), second


def _sc_not_established_second() -> tuple[Script, Script]:
    """The 4B passes; the 32B actually scores the element unmet (0.3 < 0.5) -- a real negative."""
    second = _second_passes()
    second[BNS69_EL[0]] = [_second("not_established", 0.3)]
    return _proof_script(TOY_FACTS), second


def _sc_not_confirmed_primary() -> tuple[Script, Script]:
    """The 4B leans established (0.6 >= 0.5) but misses its own tau 0.74, so the 32B is never asked."""
    primary = _proof_script(TOY_FACTS)
    primary[BNS69_EL[0]] = [_not(0.6)]
    return primary, _second_passes()


def _sc_not_established_primary() -> tuple[Script, Script]:
    """The 4B actually scores the element unmet (0.2 < 0.5)."""
    primary = _proof_script(TOY_FACTS)
    primary[BNS69_EL[0]] = [_not(0.2)]
    return primary, _second_passes()


def _sc_second_unavailable() -> tuple[Script, Script]:
    """The 32B never answers at all -- the original #37 case."""
    second = _second_passes()
    second[BNS69_EL[0]] = [ConnectionError("second judge unreachable")]
    return _proof_script(TOY_FACTS), second


def _sc_gate1_unavailable() -> tuple[Script, Script]:
    """Both judges clear their own tau, so Gate 1 is asked -- and the Gate 1 model is down (see `GATE1_DOWN`).
    The operator's fifth case: the entailment check could not evaluate the element at all."""
    return _proof_script(TOY_FACTS), _second_passes()


#: Scenarios that additionally wrap the AND gate in the REAL `Gate1Judge` over a `_StubModel` that errors on
#: every call -- the Gate 1 model failing to load, which is what `gate1_unavailable` means in production
#: (`nyaya_judges.Gate1Judge`'s own fail-closed path, `nyaya_judges.py:929-933`). A whole-model failure, not
#: a per-element contrivance: Gate 1 is only asked about an element the judge(s) already established, so EL1
#: comes back unevaluable too, while the denial (never established) is never sent to Gate 1 at all. The
#: contract outcome still turns on EL0, the element under test.
GATE1_DOWN: frozenset[str] = frozenset({"gate1_unavailable"})

#: name -> (script factory, expected status, expected binding_leg). The four declared statuses are all
#: reachable through the first six; `test_every_declared_status_is_covered_by_a_scenario` enforces that. The
#: seventh expects the fifth status the engine does not emit yet (see the module docstring): its two
#: round-trip entries below are intended failures until the engine change lands, and they are the reason the
#: scenario lives in this table rather than only in its own class -- the day the engine emits the label, the
#: computed AND serialised coverage is already here, rather than being a fifth status with no scenario.
SCENARIOS: dict[str, tuple[Callable[[], tuple[Script, Script]], str, str | None]] = {
    "established": (_sc_established, "established", None),
    "not_confirmed_second_leg": (_sc_not_confirmed_second, "not_confirmed", "second"),
    "not_established_second_leg": (_sc_not_established_second, "not_established", "second"),
    "not_confirmed_primary_leg": (_sc_not_confirmed_primary, "not_confirmed", "primary"),
    "not_established_primary_leg": (_sc_not_established_primary, "not_established", "primary"),
    "second_unavailable": (_sc_second_unavailable, "not_evaluated_second_unavailable", None),
    "gate1_unavailable": (_sc_gate1_unavailable, GATE1_UNAVAILABLE_STATUS, None),
}

#: Scenarios whose EXPECTED status the engine does not emit yet. Deliberately a separate set from
#: `GATE1_DOWN` above even though the two hold the same one name today: `GATE1_DOWN` says how the agent is
#: wired for a scenario, this says which expectation is still awaiting an engine change. They stop agreeing
#: the moment either changes, and conflating them would silently mis-mark whichever moved.
AWAITS_FIFTH_STATUS: frozenset[str] = frozenset({"gate1_unavailable"})


def _scenario_params() -> list[Any]:
    """The scenario table as `parametrize` arguments, with the strict xfail on the fifth-status scenario's
    CASE ONLY (`pytest.param(..., marks=...)`) rather than on the test. Marking the test would xfail the six
    scenarios that pass today too, and a regression in any of those would then be reported as an expected
    failure -- exactly the blindness this file was written to prevent."""
    return [
        pytest.param(name, marks=pytest.mark.xfail(strict=True, reason=XFAIL_FIFTH_STATUS))
        if name in AWAITS_FIFTH_STATUS
        else name
        for name in SCENARIOS
    ]


# -- harness -----------------------------------------------------------------------------------------------


def _and_gate_agent(tmp_path: Path, primary: Script, second: Script, *, gate1_down: bool = False) -> NyayaAgent:
    judge: Any = AndGateJudge(
        ScriptedJudge(primary), ScriptedJudge(second), tau_primary=TAU_PRIMARY, tau_second=TAU_SECOND,
    )
    if gate1_down:
        # The shipped composition order (`nyaya_agent._build`: Gate 1 wraps the AND gate, never the reverse)
        # over a model double that errors -- never a hand-built `ElementJudgment` with Gate 1 fields set.
        judge = Gate1Judge(judge, _StubModel(error=RuntimeError("gate 1 model not loaded")))
    return NyayaAgent(judge, _registry(), _config(tmp_path, second_judge={"tau": TAU_SECOND}))


def _agent(tmp_path: Path, scenario: str) -> NyayaAgent:
    """The agent a scenario needs, wired as production wires it."""
    factory, _status, _leg = SCENARIOS[scenario]
    primary, second = factory()
    return _and_gate_agent(tmp_path, primary, second, gate1_down=scenario in GATE1_DOWN)


def _computed(tmp_path: Path, scenario: str) -> ElementResult:
    """The element under test as the ENGINE computes it (the `ElementResult` dataclass)."""
    run = _agent(tmp_path, scenario).run(
        TOY_FACTS, narrative="TOY narrative.", contract_ids=["bns69"],
    )
    return run.contracts[0].elements[0]


def _computed_contract(tmp_path: Path, scenario: str) -> Any:
    """The whole `ContractResult` as the engine computes it -- outcome and reason included."""
    return _agent(tmp_path, scenario).run(
        TOY_FACTS, narrative="TOY narrative.", contract_ids=["bns69"],
    ).contracts[0]


def _serialised(tmp_path: Path, scenario: str, *, debug: bool = False) -> dict[str, Any]:
    """The whole contract as the PARTNER API serialises it, through the real route. `debug=False` is the
    shipped default, where the five config-C debug fields are stripped -- i.e. what a real caller sees."""
    agent = _agent(tmp_path, scenario)
    app = FastAPI()
    app.include_router(
        build_partner_router(tmp_path, agent_factory=lambda _root: agent, config=_NO_LIMIT_CONFIG),
    )
    path = "/api/v1/analyse-facts" + ("?debug_second_judge=true" if debug else "")
    resp = TestClient(app).post(
        path, json={"facts": TOY_FACTS, "narrative": "TOY narrative.", "contract_ids": ["bns69"]},
    )
    assert resp.status_code == 200, resp.text
    contract: dict[str, Any] = resp.json()["contracts"][0]
    return contract


def _out_kwargs(**over: Any) -> dict[str, Any]:
    """A complete `ElementResultOut` payload, so a test can vary exactly one field."""
    base: dict[str, Any] = {
        "element": BNS69_EL[0], "is_denial": False, "status": "not_confirmed", "claimed": False,
        "p_established": 0.6, "fact_id": None, "quote": None, "start": None, "end": None,
        "quote_check": None, "attempts": 1, "occurrences": 0, "offsets_source": None,
        "quote_source": None, "error": None, "binding_leg": "primary",
    }
    base.update(over)
    return base


# -- 1. each of the four states round-trips, with its own distinct label ------------------------------------


class TestEachStateRoundTrips:
    @pytest.mark.parametrize("scenario", _scenario_params())
    def test_computed_status_and_leg(self, tmp_path: Path, scenario: str) -> None:
        _factory, status, leg = SCENARIOS[scenario]
        el = _computed(tmp_path, scenario)
        assert el.status == status
        assert el.binding_leg == leg

    @pytest.mark.parametrize("scenario", _scenario_params())
    def test_serialised_status_and_leg_survive_the_api(self, tmp_path: Path, scenario: str) -> None:
        """Both fields reach a real caller on the DEFAULT response (no `?debug_second_judge`): issue #37 asked
        for this carried through "the API response, not just an internal field"."""
        _factory, status, leg = SCENARIOS[scenario]
        el = _serialised(tmp_path, scenario)["elements"][0]
        assert el["status"] == status
        assert el["binding_leg"] == leg

    def test_not_confirmed_and_not_established_are_not_the_same_output(self, tmp_path: Path) -> None:
        """The failure mode worth catching: "the second judge did not confirm" vs "the element was positively
        found absent". Same fixture either side of the 0.5 presentation cutoff, on the DEFAULT response where
        the debug fields (including `p_established_second`, the only other place the difference shows) are
        stripped -- so if `status` collapsed, the two elements would be byte-identical to a caller."""
        nc = _serialised(tmp_path, "not_confirmed_second_leg")["elements"][0]
        ne = _serialised(tmp_path, "not_established_second_leg")["elements"][0]
        assert nc != ne, "not_confirmed and not_established must not serialise identically"
        assert nc["status"] != ne["status"]
        assert {k: v for k, v in nc.items() if k != "status"} == {k: v for k, v in ne.items() if k != "status"}, (
            "status must be the field carrying the difference, not an incidental one"
        )

    @pytest.mark.parametrize("status", DECLARED_STATUSES)
    def test_every_declared_status_is_distinct_from_every_other(self, status: str) -> None:
        """Trivial but load-bearing: the four labels are four different strings, so no two states can be
        aliased to the same label in a future edit."""
        assert len([s for s in DECLARED_STATUSES if s == status]) == 1


# -- 2. not_evaluated_second_unavailable is never presentable as a finding ----------------------------------


class TestSecondUnavailableIsNeverAFinding:
    def test_the_status_itself_is_neither_verdict(self, tmp_path: Path) -> None:
        el = _serialised(tmp_path, "second_unavailable")["elements"][0]
        assert el["status"] == "not_evaluated_second_unavailable"
        assert el["status"] not in VERDICT_STATUSES

    def test_no_string_in_the_element_reads_as_a_verdict(self, tmp_path: Path) -> None:
        """Not just the enum: every string the element carries to a caller. A `quote_check`, `error` or
        `binding_leg` that said "not_established" would re-create the conflation one field over."""
        el = _serialised(tmp_path, "second_unavailable")["elements"][0]
        for key, value in el.items():
            if key == "status" or not isinstance(value, str):
                continue
            assert value not in VERDICT_STATUSES, f"{key}={value!r} reads as a verdict on an unevaluated element"

    def test_the_contract_outcome_flags_it_rather_than_deciding_it(self, tmp_path: Path) -> None:
        """The structural distinguishability issue #37 asked for, at contract level: REFER_TO_LAWYER with
        `second_judge_unavailable`, never an ordinary DENIAL/ABSTAIN a caller would read as a decision."""
        c = _serialised(tmp_path, "second_unavailable")
        assert c["outcome"] == "REFER_TO_LAWYER"
        assert c["reason"] == "second_judge_unavailable"

    @pytest.mark.xfail(strict=True, reason=XFAIL_TRI_STATE_ASSERTIONS)
    def test_the_element_is_not_presented_as_a_definite_boolean_finding(self, tmp_path: Path) -> None:
        """`assertions` is part of the SAME response (`ContractResultOut.assertions`) and is a plain
        `dict[str, bool]`: an element nothing was ever evaluated for appears there as `false`, i.e. exactly
        "the judge concluded this element is not met" -- the one place in the shipped output where "couldn't
        evaluate" and "concluded fail" still carry the same label. `status` and `outcome` above distinguish
        them; this field does not.

        The three assertions before the marked one are the non-vacuity guard, and they must stay ahead of
        it: with `strict=True` an XPASS is a hard failure, so the only acceptable way for this to pass is the
        assertions map becoming truthful about an unevaluated element -- NOT the element under test drifting
        away from `not_evaluated_second_unavailable`, and not the map arriving empty or absent (a `.get` on
        an element that simply is not there returns None, which would pass this assertion while telling a
        caller nothing). `BNS69_EL[1]`, the element that WAS established, is asserted present and True to
        prove the map is really populated for this contract."""
        c = _serialised(tmp_path, "second_unavailable")
        el = c["elements"][0]
        assert el["status"] == "not_evaluated_second_unavailable"
        assert c["assertions"] is not None
        assert c["assertions"].get(BNS69_EL[1]) is True, "the assertions map is not populated for this contract"
        assert c["assertions"].get(el["element"]) is not False, (
            "an element that was never evaluated is asserted false in the response's own assertions map"
        )


# -- 2b. the fifth status: "could not evaluate" is not "concluded fail", one gate over --------------------


class TestGate1UnevaluableIsItsOwnStatus:
    """The operator's fifth status, `not_evaluated_gate1_unavailable` (decided 2026-09-26; user label "Not
    evaluated: entailment check unavailable"). The same conflation this file exists to catch, one gate over:
    an element Gate 1 could not evaluate must not be spelled the way an element a judge scored unmet is.
    Three tests here PASS today -- the fail-closed outcome, the element flag a later engine change derives
    the label from, and the half of the operator's distinctness requirement that already holds. One is
    `xfail(strict=True)` until the engine change lands, per the operator's app-first sequencing."""

    def test_the_fixture_really_is_a_gate1_unevaluable_element(self, tmp_path: Path) -> None:
        """PASSES today, and is deliberately NEVER `xfail`-marked: it is the non-vacuity guard for all three
        strict-xfail fifth-status tests (the one below, plus the two `gate1_unavailable` round-trip cases in
        `TestEachStateRoundTrips`). If this scenario ever stopped producing a genuine Gate-1-unavailable
        element (Gate 1 never asked, the model answering after all, a quote check rejecting first), those
        three could XPASS with nothing having been adopted -- and under `strict=True` an XPASS reads as
        adoption. This test fails first and says why instead. Every field asserted here is one the engine
        ALREADY carries, which is the point -- the information needed to emit the fifth status exists; only
        the label is missing."""
        el = _computed(tmp_path, "gate1_unavailable")
        assert el.gate1_unavailable is True
        assert el.gate1_score is None, "the model never answered, so there is no score to report"
        assert el.gate1_not_entailed is False and el.gate1_contradiction is False, (
            "a model failure is neither of the two POSSIBLE veto reasons -- nothing was scored at all"
        )
        assert el.second_unavailable is False, "the 32B answered fine; Gate 1 is what could not evaluate"
        assert el.claimed is False, (
            "the veto happens inside the judge, so `claimed` is already False -- `gate1_unavailable` is the "
            "only field left saying nothing was evaluated"
        )

    def test_the_contract_outcome_already_refuses_rather_than_deciding(self, tmp_path: Path) -> None:
        """PASSES today, and is the half worth locking in: the OUTCOME level is already truthful and
        fail-closed (`nyaya_agent.py:1248-1249`) -- REFER_TO_LAWYER under its own reason, distinct from the
        second judge's. A refactor that folded `gate1_unavailable` into `second_judge_unavailable`, or into
        an ordinary DENIAL/ABSTAIN a caller would read as a decision, would remove the only place the
        distinction survives on this branch."""
        c = _serialised(tmp_path, "gate1_unavailable")
        assert c["outcome"] == "REFER_TO_LAWYER"
        assert c["reason"] == "gate1_unavailable"
        assert c["reason"] != _serialised(tmp_path, "second_unavailable")["reason"]
        assert _computed_contract(tmp_path, "gate1_unavailable").outcome == "REFER_TO_LAWYER", (
            "the engine's own ContractResult, not just the serialised view"
        )

    def test_it_is_already_distinct_from_second_judge_unavailable(self, tmp_path: Path) -> None:
        """PASSES today, for a shallow reason worth stating out loud: the two labels differ only because
        Gate 1's case is currently borrowing `not_established`. Asserted apart from the intended failure
        below so that once the engine emits the fifth status this test goes on checking the operator's OTHER
        distinctness requirement -- `not_evaluated_gate1_unavailable` and `not_evaluated_second_unavailable`
        are never the same label -- rather than being satisfied by the collapse it is meant to rule out."""
        g = _serialised(tmp_path, "gate1_unavailable")["elements"][0]
        s = _serialised(tmp_path, "second_unavailable")["elements"][0]
        assert g["status"] != s["status"]

    @pytest.mark.xfail(strict=True, reason=XFAIL_FIFTH_STATUS)
    def test_it_must_not_collapse_into_a_scored_and_failed_element(self, tmp_path: Path) -> None:
        """XFAIL (strict), engine change not yet landed -- NOT a defect introduced here. Today
        `_truthful_status` returns plain `not_established` for every Gate 1 veto, the model-unavailable one
        included (`nyaya_agent.py:844-845`), so this element carries exactly the label of one a judge scored
        unmet: the first assertion fails with both sides reading "not_established". That is the failure a
        consumer cannot detect -- `ElementResultOut` declares no `gate1_*` field at all
        (`partner.py:244-279`), so nothing else in the default response reveals that nothing was evaluated.
        Goes green when the engine emits `not_evaluated_gate1_unavailable`, sequenced after `pravrudhi-app`
        PR #8 per the operator; Gate 1 is off by default in production (`AgentConfig.gate1_enabled=False`),
        so nothing emits this status today and no caller is seeing the collapse yet."""
        g = _serialised(tmp_path, "gate1_unavailable")["elements"][0]
        ne = _serialised(tmp_path, "not_established_second_leg")["elements"][0]
        assert g["status"] != ne["status"], (
            f"a Gate-1-unevaluable element reads as {g['status']!r}, the label of one a judge scored unmet"
        )
        assert g["status"] == GATE1_UNAVAILABLE_STATUS
        assert g["status"] not in VERDICT_STATUSES


# -- 3. binding_leg agrees with the state ------------------------------------------------------------------


class TestBindingLegAgreesWithTheState:
    def test_a_primary_only_state_is_never_labelled_second_or_both(self, tmp_path: Path) -> None:
        """The 4B rejected outright, so the 32B was never asked at all: nothing the second judge did could
        possibly be the binding constraint."""
        for scenario in ("not_confirmed_primary_leg", "not_established_primary_leg"):
            el = _computed(tmp_path, scenario)
            assert el.second_skip_reason == "primary_not_established"
            assert el.p_established_second is None
            assert el.binding_leg == "primary", f"{scenario}: {el.binding_leg!r}"
            assert el.binding_leg not in ("second", "both")

    def test_a_second_only_state_is_never_labelled_primary(self, tmp_path: Path) -> None:
        """The 4B cleared its own tau; only the 32B's tau was missed."""
        for scenario in ("not_confirmed_second_leg", "not_established_second_leg"):
            el = _computed(tmp_path, scenario)
            assert el.p_established is not None and el.p_established >= TAU_PRIMARY
            assert el.p_established_second is not None
            assert el.binding_leg not in ("primary", "both")
            assert el.binding_leg == "second", f"{scenario}: {el.binding_leg!r}"

    def test_states_with_no_tau_miss_name_no_leg(self, tmp_path: Path) -> None:
        """`established` (nothing failed) and the two not-evaluated states (nothing was measured against a
        tau at all) must carry no leg -- a leg there would invite a split that counts non-misses. Passes for
        the Gate 1 case today too: `_truthful_status` returns no leg for a Gate 1 veto, and the operator's
        fifth status does not change that, so the engine change must leave this third case as it is."""
        for scenario in ("established", "second_unavailable", "gate1_unavailable"):
            assert _computed(tmp_path, scenario).binding_leg is None, scenario

    @pytest.mark.xfail(strict=True, reason=XFAIL_BINDING_LEG)
    def test_a_tau_miss_always_names_the_leg_that_bound_it(self, tmp_path: Path) -> None:
        """`ElementResult.binding_leg`'s own docstring says it is None ONLY when the element IS established,
        or the reason is not a tau miss at all (`not_evaluated_second_unavailable`, or a Gate 1 veto). A
        single-judge deployment -- the shipped `configs/nyaya_agent.yaml` default, where the `second_judge:`
        block is commented out -- is a tau miss on the primary and nothing else, so it must name "primary".
        Without a leg, Lead-2's "not_confirmed split by binding leg, with n" cannot be computed at all on the
        default config.

        XFAIL (strict): a real defect, awaiting #57's fix. Its own non-vacuity guard is in-test and ahead of
        the marked assertion -- `status == "not_confirmed"` must still hold, so the only way to XPASS is a
        leg appearing on a genuine tau miss, not the fixture drifting into some other state."""
        script = _proof_script(TOY_FACTS)
        script[BNS69_EL[0]] = [_not(0.6)]
        agent = NyayaAgent(ScriptedJudge(script), _registry(), _config(tmp_path))
        el = agent.run(TOY_FACTS, narrative="TOY narrative.", contract_ids=["bns69"]).contracts[0].elements[0]
        assert el.status == "not_confirmed"
        assert el.binding_leg is not None, "a single-judge tau miss names no binding leg"
        assert el.binding_leg == "primary"


# -- 4. exhaustiveness: drift trips the suite --------------------------------------------------------------


class TestExhaustiveness:
    def test_every_declared_status_is_covered_by_a_scenario(self, tmp_path: Path) -> None:
        """Parametrised over the engine's own `ElementStatus`, never over four hardcoded cases: a FIFTH status
        added to the Literal with no scenario producing it fails here, instead of shipping unexercised."""
        produced = {_computed(tmp_path, name).status for name in SCENARIOS}
        assert produced == set(DECLARED_STATUSES), (
            f"declared but never produced: {sorted(set(DECLARED_STATUSES) - produced)}; "
            f"produced but not declared: {sorted(produced - set(DECLARED_STATUSES))}"
        )

    def test_every_produced_leg_is_a_declared_leg(self, tmp_path: Path) -> None:
        produced = {_computed(tmp_path, name).binding_leg for name in SCENARIOS}
        assert produced - {None} <= set(DECLARED_LEGS), f"undeclared leg produced: {produced - {None}}"

    def test_the_only_unreachable_declared_leg_is_the_reserved_one(self, tmp_path: Path) -> None:
        """PR #45 reserves "both" for a future mode where both judges are asked and scored independently, and
        says it is unreachable under today's AND gate. That is the ONLY declared leg allowed to have no
        scenario: a fourth leg added to the field trips here until it is either produced or documented."""
        produced = {_computed(tmp_path, name).binding_leg for name in SCENARIOS} - {None}
        assert set(DECLARED_LEGS) - produced == {"both"}, (
            f"declared legs with no scenario: {sorted(set(DECLARED_LEGS) - produced)}"
        )

    @pytest.mark.parametrize("status", DECLARED_STATUSES)
    def test_every_declared_status_survives_the_response_model(self, status: str) -> None:
        """Fail-closed must not become fail-everything: each of the four real statuses validates through."""
        assert ElementResultOut(**_out_kwargs(status=status)).status == status

    @pytest.mark.xfail(strict=True, reason=XFAIL_RESPONSE_MODEL)
    def test_the_response_model_constrains_status_to_the_declared_set(self) -> None:
        """The serialisation boundary is the only presentation layer this repo ships, so it is where a status
        the code does not know must be refused. `ElementResultOut.status` is a bare `str`, so an unknown
        status is serialised to a caller verbatim and unflagged -- and `AnalyseFactsResponse` is the response
        model for the whole route, so nothing downstream of it re-checks.

        XFAIL (strict), awaiting #78. The non-vacuity guard is the never-marked
        `test_every_declared_status_survives_the_response_model` above: a `pytest.raises(Exception)` test
        XPASSes on ANY exception, so a `_out_kwargs` payload gone stale against the model (a field added or
        removed) would raise about something else entirely and read exactly like adoption. That test
        constructs the same payload with each known status and fails first if it ever stops validating."""
        with pytest.raises(Exception):  # noqa: B017 -- pydantic.ValidationError is what SHOULD be raised
            ElementResultOut(**_out_kwargs(status="a_status_this_build_does_not_know"))


# -- 5. an unknown or absent status fails closed -----------------------------------------------------------


class TestUnknownStatusFailsClosed:
    def test_an_absent_status_is_refused_rather_than_defaulted(self) -> None:
        """No neutral/blank fallback: a payload with no `status` at all must not validate into a response
        where the field is simply missing (and, with `response_model_exclude_unset=True` on the route, would
        then vanish from the JSON entirely -- a reader seeing no status reads "nothing found")."""
        kwargs = _out_kwargs()
        del kwargs["status"]
        with pytest.raises(Exception):  # noqa: B017 -- pydantic.ValidationError
            ElementResultOut(**kwargs)

    @pytest.mark.xfail(strict=True, reason=XFAIL_RESPONSE_MODEL)
    def test_an_empty_status_is_refused(self) -> None:
        """The blank case specifically: `status: ""` renders as nothing at all. XFAIL (strict), awaiting #78
        with the test above, and guarded against a stale-payload XPASS the same way it is."""
        with pytest.raises(Exception):  # noqa: B017 -- pydantic.ValidationError
            ElementResultOut(**_out_kwargs(status=""))

    def test_an_unknown_status_is_never_treated_as_established(self) -> None:
        """Whatever a build does with a status it does not know, it must not be the favourable reading. This
        is the invariant every outcome check in `nyaya_agent` relies on (`status == "established"`)."""
        out = ElementResultOut(**_out_kwargs(status="a_status_this_build_does_not_know"))
        assert out.status != "established"
