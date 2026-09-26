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
from pravrudhi.application.nyaya_judges import AndGateJudge, ElementJudgment
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

#: The served taus of the shipped AND gate: `AND(4B >= 0.74, 32B >= 0.97)`.
TAU_PRIMARY = 0.74
TAU_SECOND = 0.97

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


#: name -> (script factory, expected status, expected binding_leg). The four declared statuses are all
#: reachable through these six; `test_every_declared_status_is_covered_by_a_scenario` enforces that.
SCENARIOS: dict[str, tuple[Callable[[], tuple[Script, Script]], str, str | None]] = {
    "established": (_sc_established, "established", None),
    "not_confirmed_second_leg": (_sc_not_confirmed_second, "not_confirmed", "second"),
    "not_established_second_leg": (_sc_not_established_second, "not_established", "second"),
    "not_confirmed_primary_leg": (_sc_not_confirmed_primary, "not_confirmed", "primary"),
    "not_established_primary_leg": (_sc_not_established_primary, "not_established", "primary"),
    "second_unavailable": (_sc_second_unavailable, "not_evaluated_second_unavailable", None),
}


# -- harness -----------------------------------------------------------------------------------------------


def _and_gate_agent(tmp_path: Path, primary: Script, second: Script) -> NyayaAgent:
    gate = AndGateJudge(
        ScriptedJudge(primary), ScriptedJudge(second), tau_primary=TAU_PRIMARY, tau_second=TAU_SECOND,
    )
    return NyayaAgent(gate, _registry(), _config(tmp_path, second_judge={"tau": TAU_SECOND}))


def _computed(tmp_path: Path, scenario: str) -> ElementResult:
    """The element under test as the ENGINE computes it (the `ElementResult` dataclass)."""
    factory, _status, _leg = SCENARIOS[scenario]
    primary, second = factory()
    run = _and_gate_agent(tmp_path, primary, second).run(
        TOY_FACTS, narrative="TOY narrative.", contract_ids=["bns69"],
    )
    return run.contracts[0].elements[0]


def _serialised(tmp_path: Path, scenario: str, *, debug: bool = False) -> dict[str, Any]:
    """The whole contract as the PARTNER API serialises it, through the real route. `debug=False` is the
    shipped default, where the five config-C debug fields are stripped -- i.e. what a real caller sees."""
    factory, _status, _leg = SCENARIOS[scenario]
    primary, second = factory()
    agent = _and_gate_agent(tmp_path, primary, second)
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
    @pytest.mark.parametrize("scenario", list(SCENARIOS))
    def test_computed_status_and_leg(self, tmp_path: Path, scenario: str) -> None:
        _factory, status, leg = SCENARIOS[scenario]
        el = _computed(tmp_path, scenario)
        assert el.status == status
        assert el.binding_leg == leg

    @pytest.mark.parametrize("scenario", list(SCENARIOS))
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

    def test_the_element_is_not_presented_as_a_definite_boolean_finding(self, tmp_path: Path) -> None:
        """`assertions` is part of the SAME response (`ContractResultOut.assertions`) and is a plain
        `dict[str, bool]`: an element nothing was ever evaluated for appears there as `false`, i.e. exactly
        "the judge concluded this element is not met" -- the one place in the shipped output where "couldn't
        evaluate" and "concluded fail" still carry the same label. `status` and `outcome` above distinguish
        them; this field does not."""
        c = _serialised(tmp_path, "second_unavailable")
        el = c["elements"][0]
        assert el["status"] == "not_evaluated_second_unavailable"
        assert c["assertions"] is not None
        assert c["assertions"].get(el["element"]) is not False, (
            "an element that was never evaluated is asserted false in the response's own assertions map"
        )


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
        """`established` (nothing failed) and `not_evaluated_second_unavailable` (nothing was measured against
        a tau) must carry no leg at all -- a leg there would invite a split that counts non-misses."""
        for scenario in ("established", "second_unavailable"):
            assert _computed(tmp_path, scenario).binding_leg is None, scenario

    def test_a_tau_miss_always_names_the_leg_that_bound_it(self, tmp_path: Path) -> None:
        """`ElementResult.binding_leg`'s own docstring says it is None ONLY when the element IS established,
        or the reason is not a tau miss at all (`not_evaluated_second_unavailable`, or a Gate 1 veto). A
        single-judge deployment -- the shipped `configs/nyaya_agent.yaml` default, where the `second_judge:`
        block is commented out -- is a tau miss on the primary and nothing else, so it must name "primary".
        Without a leg, Lead-2's "not_confirmed split by binding leg, with n" cannot be computed at all on the
        default config."""
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

    def test_the_response_model_constrains_status_to_the_declared_set(self) -> None:
        """The serialisation boundary is the only presentation layer this repo ships, so it is where a status
        the code does not know must be refused. `ElementResultOut.status` is a bare `str`, so an unknown
        status is serialised to a caller verbatim and unflagged -- and `AnalyseFactsResponse` is the response
        model for the whole route, so nothing downstream of it re-checks."""
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

    def test_an_empty_status_is_refused(self) -> None:
        """The blank case specifically: `status: ""` renders as nothing at all."""
        with pytest.raises(Exception):  # noqa: B017 -- pydantic.ValidationError
            ElementResultOut(**_out_kwargs(status=""))

    def test_an_unknown_status_is_never_treated_as_established(self) -> None:
        """Whatever a build does with a status it does not know, it must not be the favourable reading. This
        is the invariant every outcome check in `nyaya_agent` relies on (`status == "established"`)."""
        out = ElementResultOut(**_out_kwargs(status="a_status_this_build_does_not_know"))
        assert out.status != "established"
