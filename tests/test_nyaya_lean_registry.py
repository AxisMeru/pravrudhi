"""T5b atomic wire: the Python consumer for `REG`, `--list-contracts`, and `--describe-contract`.

Done-when: `KNOWN_CONTRACT_IDS` matches the binary's own `--list-contracts` output exactly (the drift
test); a reading stating every required element is grounded; a reading missing one is flagged, naming it
in `omitted_claims`; an unknown contract_id is refused, never silently defaulted.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pravrudhi.application import nyaya_lean_registry as reg

# T5b's REG/--list-contracts/--describe-contract tags live on prabhasa-nyaya's
# assistant/trackA/t5b-atomic-review branch, not yet merged to main -- point at that worktree's freshly
# built binary until the merge lands, same pattern test_nyaya_lean.py already established for P1b.
_SCORE_BIN = Path("/home/ss/projects/prabhasa-nyaya/.worktrees/trackA-t5b-atomic/lean/.lake/build/bin/score")
requires_registry_scorer = pytest.mark.skipif(
    not _SCORE_BIN.exists(), reason="prabhasa-nyaya's T5b-atomic-review score binary is not built on this host"
)


class TestKnownContractIdsMatchesTheBinary:
    @requires_registry_scorer
    def test_drift_test_against_list_contracts(self) -> None:
        """The lead's explicit requirement: this reads the binary's OWN output, never trusts the hand-
        listed KNOWN_CONTRACT_IDS constant on its own -- if someone adds a twelfth registry Contract on
        the Lean side and forgets this file, this test catches it, not a human noticing later."""
        import subprocess

        proc = subprocess.run([str(_SCORE_BIN), "--list-contracts"], capture_output=True, text=True, check=True)
        ids_from_binary = frozenset(line for line in proc.stdout.strip().split("\n") if line)
        assert ids_from_binary == reg.KNOWN_CONTRACT_IDS


class TestDescribeContract:
    @requires_registry_scorer
    def test_reads_required_element_names_live_from_the_binary(self) -> None:
        names = reg.describe_contract("ipc405_misappropriation", score_bin=_SCORE_BIN)
        assert names == [
            "entrusted with property, or with dominion over property",
            "dishonestly misappropriates or converts the property to his own use",
        ]

    def test_refuses_an_unknown_contract_id_before_any_subprocess_call(self) -> None:
        with pytest.raises(reg.UnknownContractError):
            reg.describe_contract("not_a_real_id", score_bin=_SCORE_BIN)


@requires_registry_scorer
class TestCheckRegistry:
    def test_all_required_elements_met_is_grounded(self) -> None:
        result = reg.check_registry(
            {
                "entrusted with property, or with dominion over property": True,
                "dishonestly misappropriates or converts the property to his own use": True,
            },
            "ipc405_misappropriation",
            score_bin=_SCORE_BIN,
        )
        assert result["verdict"] == "grounded"
        assert result["omitted_claims"] == []
        assert result["unlicensed_claims"] == []
        assert result["denied_claims"] == []

    def test_a_missing_element_is_flagged_naming_it_as_omitted(self) -> None:
        result = reg.check_registry(
            {"entrusted with property, or with dominion over property": True},
            "ipc405_misappropriation",
            score_bin=_SCORE_BIN,
        )
        assert result["verdict"] == "flagged"
        assert len(result["omitted_claims"]) == 1
        assert "dishonestly misappropriates" in result["omitted_claims"][0]

    def test_an_element_marked_not_met_is_never_sent_and_reads_as_omitted(self) -> None:
        """A False entry is exactly as if the element had never been mentioned -- the module's own
        documented contract (no separate 'Not Met' wire signal, Lean decides omission from absence)."""
        result = reg.check_registry(
            {
                "entrusted with property, or with dominion over property": True,
                "dishonestly misappropriates or converts the property to his own use": False,
            },
            "ipc405_misappropriation",
            score_bin=_SCORE_BIN,
        )
        assert result["verdict"] == "flagged"
        assert len(result["omitted_claims"]) == 1

    def test_an_unlicensed_element_is_flagged(self) -> None:
        result = reg.check_registry(
            {
                "entrusted with property, or with dominion over property": True,
                "dishonestly misappropriates or converts the property to his own use": True,
                "an element this contract never required": True,
            },
            "ipc405_misappropriation",
            score_bin=_SCORE_BIN,
        )
        assert result["verdict"] == "flagged"
        assert len(result["unlicensed_claims"]) == 1

    def test_refuses_an_unknown_contract_id_before_any_subprocess_call(self) -> None:
        with pytest.raises(reg.UnknownContractError):
            reg.check_registry({"x": True}, "not_a_real_id", score_bin=_SCORE_BIN)

    def test_hostile_characters_in_an_element_name_round_trip(self) -> None:
        """The percent-escaping this module re-derives from Score.lean's escField must match exactly, or a
        real element name containing a comma/paren/percent would corrupt the wire line's field boundaries
        instead of round-tripping. Uses an unlicensed hostile element (never a real registry element) to
        prove exactly that: the wire line still parses into ONE unlicensed claim, not a malformed split
        across several -- `unlicensed_claims` carries the wire-encoded claim string (same convention
        `nyaya_lean.py`'s own claim-list fields already use), so the hostile name is checked for presence
        inside it (correctly escaped), not exact equality against the bare name."""
        result = reg.check_registry(
            {
                "entrusted with property, or with dominion over property": True,
                "dishonestly misappropriates or converts the property to his own use": True,
                "a(hostile), element%with,everything": True,
            },
            "ipc405_misappropriation",
            score_bin=_SCORE_BIN,
        )
        assert result["verdict"] == "flagged"
        assert len(result["unlicensed_claims"]) == 1
        assert result["unlicensed_claims"][0] == (
            "G_SATISFIES(E(the conduct in the facts,AC),"
            "E(a%28hostile%29%2C element%25with%2Ceverything,EL))"
        )
