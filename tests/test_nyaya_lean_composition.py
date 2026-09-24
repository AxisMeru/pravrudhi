"""§9, the composed scoring wire tag: the Python consumer for `COMP` and `--list-compositions`.

Done-when: `KNOWN_COMPOSITION_IDS` matches the binary's own `--list-compositions` output exactly
(the drift test); a reading grounding one route scores grounded, naming that route as
`route_of_record`; a reading grounding two routes names both, comma-joined; a partial reading
scores flagged, naming the closest route; an unknown composition_id is refused, never silently
defaulted, before any subprocess call.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from pravrudhi.application import nyaya_lean_composition as comp

# No host path is committed here.  Set PRABHASA_NYAYA_SCORE_BIN to the built score binary; tests skip
# with a clear reason when the variable is unset.
_SCORE_BIN = Path(os.environ.get("PRABHASA_NYAYA_SCORE_BIN", "prabhasa-nyaya-score-not-configured"))
requires_composition_scorer = pytest.mark.skipif(
    not _SCORE_BIN.exists(),
    reason="PRABHASA_NYAYA_SCORE_BIN is not set or does not point to a built score binary (set it to run these tests)",
)

# ipc415_property's own three required elements (IPC415.lean), used across several tests below.
_DECEPTION = "deceives another person, whether by affirmative misrepresentation or by dishonest concealment of facts"
_FRAUD_OR_DISHONEST = "the deception fraudulently or dishonestly induces the deceived person"
_DELIVERY = "the inducement causes delivery of property to any person, or consent that any person shall retain property"
_INTENTIONAL_INDUCEMENT = (
    "the deception intentionally induces the deceived person to do or omit to do something they "
    "would not otherwise have done or omitted"
)
_DAMAGE_OR_HARM = (
    "the induced act or omission causes, or is likely to cause, damage or harm to the deceived "
    "person in body, mind, reputation, or property"
)
_CHEATS = "cheats (s.415: either the property-inducement limb or the damaging-act limb)"
_PERSONATION = (
    "pretending to be, substituting for, or misrepresenting the identity of, another person (real "
    "or imaginary)"
)


class TestKnownCompositionIdsMatchesTheBinary:
    @requires_composition_scorer
    def test_drift_test_against_list_compositions(self) -> None:
        proc = subprocess.run([str(_SCORE_BIN), "--list-compositions"], capture_output=True, text=True, check=True)
        ids_from_binary = frozenset(line for line in proc.stdout.strip().split("\n") if line)
        assert ids_from_binary == comp.KNOWN_COMPOSITION_IDS


class TestCheckComposition:
    def test_refuses_an_unknown_composition_id_before_any_subprocess_call(self) -> None:
        with pytest.raises(comp.UnknownCompositionError):
            comp.check_composition({"x": True}, "not_a_real_composition_id", score_bin=_SCORE_BIN)

    @requires_composition_scorer
    def test_grounded_by_one_route_names_it_route_of_record(self) -> None:
        result = comp.check_composition(
            {
                _CHEATS: True,
                _PERSONATION: True,
                _DECEPTION: True,
                _FRAUD_OR_DISHONEST: True,
                _DELIVERY: True,
            },
            "ipc416_composed",
            score_bin=_SCORE_BIN,
        )
        assert result["verdict"] == "grounded"
        assert result["route_of_record"] == "ipc415_property"
        assert result["omitted_claims"] == []
        assert result["refuted_claims"] == []

    @requires_composition_scorer
    def test_grounded_by_two_routes_comma_joins_route_of_record(self) -> None:
        result = comp.check_composition(
            {
                _CHEATS: True,
                _PERSONATION: True,
                _DECEPTION: True,
                _FRAUD_OR_DISHONEST: True,
                _DELIVERY: True,
                _INTENTIONAL_INDUCEMENT: True,
                _DAMAGE_OR_HARM: True,
            },
            "ipc416_composed",
            score_bin=_SCORE_BIN,
        )
        assert result["verdict"] == "grounded"
        assert result["route_of_record"] == "ipc415_property,ipc415_damaging_act"

    @requires_composition_scorer
    def test_partial_reading_is_flagged_naming_the_closest_route(self) -> None:
        # Limb A missing its mental element (fraudulentOrDishonestInducement) -- one element short.
        result = comp.check_composition(
            {_CHEATS: True, _PERSONATION: True, _DECEPTION: True, _DELIVERY: True},
            "ipc416_composed",
            score_bin=_SCORE_BIN,
        )
        assert result["verdict"] == "flagged"
        assert result["route_of_record"] == "ipc415_property"
        assert len(result["omitted_claims"]) == 1
        assert result["omitted_claims"][0]["source"] == "ipc415_property"
        assert "fraudulently or dishonestly" in result["omitted_claims"][0]["claim"]

    @requires_composition_scorer
    def test_silent_on_inner_family_is_flagged_with_no_route_of_record(self) -> None:
        result = comp.check_composition(
            {_PERSONATION: True}, "ipc416_composed", score_bin=_SCORE_BIN,
        )
        assert result["verdict"] == "flagged"
        assert result["route_of_record"] == "none"
        assert len(result["omitted_claims"]) == 1
        assert result["omitted_claims"][0]["source"] == "outer"

    @requires_composition_scorer
    def test_bare_bridge_assertion_is_labelled_bridge_not_outer(self) -> None:
        """Amendment A: the reading DID assert `cheats`, with no inner working shown -- the
        omission must be labelled `bridge` (a hallucinated conclusion), not `outer` (the same label
        `test_silent_on_inner_family_is_flagged_with_no_route_of_record` gets for a reading that
        never asserted it at all)."""
        result = comp.check_composition(
            {_CHEATS: True, _PERSONATION: True}, "ipc416_composed", score_bin=_SCORE_BIN,
        )
        assert result["verdict"] == "flagged"
        assert result["route_of_record"] == "none"
        assert len(result["omitted_claims"]) == 1
        assert result["omitted_claims"][0]["source"] == "bridge"

    @requires_composition_scorer
    def test_route_omission_counts_reports_every_declared_route(self) -> None:
        result = comp.check_composition(
            {_CHEATS: True, _PERSONATION: True, _DECEPTION: True, _DELIVERY: True},
            "ipc416_composed",
            score_bin=_SCORE_BIN,
        )
        assert set(result["route_omission_counts"]) == {"ipc415_property", "ipc415_damaging_act"}
        assert result["route_omission_counts"]["ipc415_property"] == 1
        assert result["tie_break_rule"] == "fewest-omissions-declared-order-v1"
