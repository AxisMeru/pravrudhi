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


#: Real `score` output, captured verbatim (statute element text only -- public law). `cff3bee2_*` is
#: prabhasa-nyaya main @33dc40b's binary (sha256 cff3bee2...), which prints `DENY: ` lines under
#: `--describe-contract` and a tab-separated source column under `--list-contracts`; `9bff8f30_*` is
#: the older binary still pinned in the hosted image, kept so the parser never regresses on it.
_FIXTURES = Path(__file__).parent / "fixtures" / "nyaya_score"


def _fixture(name: str) -> str:
    return (_FIXTURES / name).read_text()


class TestParseDescribeOutput:
    def test_new_format_splits_denials_from_required_elements(self) -> None:
        described = reg.parse_describe_output(
            "bns316_misappropriation", _fixture("cff3bee2_describe_bns316_misappropriation.txt")
        )
        assert described.contract_id == "bns316_misappropriation"
        assert described.elements == [
            "entrusted with property, or with dominion over property",
            "dishonestly misappropriates or converts the property to his own use",
        ]
        assert described.denials == [
            "acted in good faith, believing the disobedience served the entrusting party's advantage, "
            "not dishonestly",
        ]

    def test_new_format_contract_without_a_deny_line_has_no_denials(self) -> None:
        described = reg.parse_describe_output(
            "bns316_wilfully_suffers", _fixture("cff3bee2_describe_bns316_wilfully_suffers.txt")
        )
        assert described.denials == []
        assert described.elements == [
            "entrusted with property, or with dominion over property",
            "wilfully suffers another person to do the misappropriation/conversion or the use/disposal "
            "described above",
        ]

    def test_old_format_still_parses_with_no_denials(self) -> None:
        described = reg.parse_describe_output(
            "ipc405_misappropriation", _fixture("9bff8f30_describe_ipc405_misappropriation.txt")
        )
        assert described.elements == [
            "entrusted with property, or with dominion over property",
            "dishonestly misappropriates or converts the property to his own use",
        ]
        assert described.denials == []

    def test_unknown_contract_output_raises(self) -> None:
        with pytest.raises(reg.UnknownContractError):
            reg.parse_describe_output("not_a_real_id", _fixture("cff3bee2_describe_unknown.txt"))

    def test_empty_output_is_refused_not_read_as_zero_elements(self) -> None:
        with pytest.raises(RuntimeError):
            reg.parse_describe_output("ipc405_misappropriation", "")


class TestParseListContracts:
    def test_new_format_reads_ids_and_sources(self) -> None:
        listed = reg.parse_list_contracts(_fixture("cff3bee2_list_contracts.txt"))
        assert len(listed) == 17
        assert listed["ipc405_misappropriation"] == ["Indian Penal Code \u00a7405"]
        assert listed["bns85"] == ["Bharatiya Nyaya Sanhita \u00a785", "Bharatiya Nyaya Sanhita \u00a786"]
        assert listed["bns316_wilfully_suffers"] == ["Bharatiya Nyaya Sanhita \u00a7316"]
        # The source column never leaks into an id (the pre-parser drift test compared whole lines).
        assert all("\t" not in cid and " " not in cid for cid in listed)

    def test_old_format_reads_ids_with_no_sources(self) -> None:
        listed = reg.parse_list_contracts(_fixture("9bff8f30_list_contracts.txt"))
        assert frozenset(listed) == reg.KNOWN_CONTRACT_IDS
        assert all(sources == [] for sources in listed.values())

    def test_new_format_ids_are_a_superset_of_the_old(self) -> None:
        old = reg.parse_list_contracts(_fixture("9bff8f30_list_contracts.txt"))
        new = reg.parse_list_contracts(_fixture("cff3bee2_list_contracts.txt"))
        assert set(old) <= set(new)


class TestKnownContractIdsMatchesTheBinary:
    @requires_registry_scorer
    def test_drift_test_against_list_contracts(self) -> None:
        """The lead's explicit requirement: this reads the binary's OWN output, never trusts the hand-
        listed KNOWN_CONTRACT_IDS constant on its own -- if someone adds a twelfth registry Contract on
        the Lean side and forgets this file, this test catches it, not a human noticing later."""
        import subprocess

        proc = subprocess.run([str(_SCORE_BIN), "--list-contracts"], capture_output=True, text=True, check=True)
        ids_from_binary = frozenset(reg.parse_list_contracts(proc.stdout))
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

    def test_a_bns46_route_scores_grounded(self) -> None:
        """Reachability for the three ids folded in by the T5b atomic wire pass (BNS 46's
        instigation/conspiracy/intentional-aid routes) -- proves they score through `check_registry`
        exactly like the original eleven, not just that `--list-contracts` names them."""
        result = reg.check_registry(
            {
                "instigates any person to do the thing (urges, incites or provokes it), including, "
                "per s.45 Explanation 1, wilfully misrepresenting or wilfully concealing a material "
                "fact one is bound to disclose so as to cause, procure, or attempt to cause or "
                "procure it": True,
                "the thing abetted is itself an offence, or is an act which would be an offence if "
                "committed by a person capable by law of committing an offence with the same "
                "intention or knowledge as the abettor's own": True,
            },
            "bns46_instigation",
            score_bin=_SCORE_BIN,
        )
        assert result["verdict"] == "grounded"
        assert result["omitted_claims"] == []

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


@requires_registry_scorer
class TestReachabilityForEveryKnownContractId:
    """The exit criterion (the lead, 2026-09-15): a recorded end-to-end run for every one of the
    fourteen ids, not just a sample -- reads each contract's own required elements live via
    `describe_contract` (never hand-copied) and drives one grounded run and one omission run
    through `check_registry` for each."""

    @pytest.mark.parametrize("contract_id", sorted(reg.KNOWN_CONTRACT_IDS))
    def test_all_required_elements_met_is_grounded(self, contract_id: str) -> None:
        names = reg.describe_contract(contract_id, score_bin=_SCORE_BIN)
        assert names, f"{contract_id} reported zero required elements -- describe_contract itself is broken"
        result = reg.check_registry(dict.fromkeys(names, True), contract_id, score_bin=_SCORE_BIN)
        assert result["verdict"] == "grounded", (contract_id, result)
        assert result["omitted_claims"] == []
        assert result["unlicensed_claims"] == []

    @pytest.mark.parametrize("contract_id", sorted(reg.KNOWN_CONTRACT_IDS))
    def test_dropping_the_first_required_element_is_flagged_naming_it_omitted(self, contract_id: str) -> None:
        names = reg.describe_contract(contract_id, score_bin=_SCORE_BIN)
        dropped, kept = names[0], names[1:]
        result = reg.check_registry(dict.fromkeys(kept, True), contract_id, score_bin=_SCORE_BIN)
        assert result["verdict"] == "flagged", (contract_id, result)
        assert len(result["omitted_claims"]) == 1, (contract_id, result)
        # omitted_claims carries the wire-encoded G_SATISFIES(...) string (nyaya_lean.py's own
        # claim-list convention, same as unlicensed_claims) -- compare against the escaped name,
        # not the bare one, since a dropped element containing a reserved character (e.g. bns47's
        # own "cheats (s.415: ...)") would otherwise never be found as a plain substring.
        assert reg._esc(dropped) in result["omitted_claims"][0], (contract_id, dropped, result["omitted_claims"])


@requires_registry_scorer
class TestRefutation:
    def test_a_denial_bearing_element_asserted_is_refuted(self) -> None:
        """`ipc405_misappropriation` carries the good-faith defeater (illustration (d)) IN its own
        `denials` -- asserting it (alongside the otherwise-complete required elements) must flag as
        a denial, distinct from an omission or an unlicensed claim, per `Adequacy.lean`'s own
        `Contract.Refuted` doc: refuted is stronger than merely unlicensed."""
        result = reg.check_registry(
            {
                "entrusted with property, or with dominion over property": True,
                "dishonestly misappropriates or converts the property to his own use": True,
                "acted in good faith, believing the disobedience served the entrusting party's "
                "advantage, not dishonestly": True,
            },
            "ipc405_misappropriation",
            score_bin=_SCORE_BIN,
        )
        assert result["verdict"] == "flagged"
        assert len(result["denied_claims"]) == 1
        assert "acted in good faith" in result["denied_claims"][0]
        assert result["omitted_claims"] == []
        # No double-encoding across buckets (Track B reviewer's finding and fix, 2026-09-15,
        # scoreREGLine): a denial-bearing claim is not in the Contract's own `axioms` (only in
        # `denials`), so it must land in `denied` exactly once and NEVER also in `unlicensed` --
        # that overlap was the exact bug this test was written to catch, reproduced against the
        # pre-fix binary before this assertion was corrected to match the actual fix.
        assert result["unlicensed_claims"] == []

    def test_a_second_denial_bearing_contract_mirrors_the_fix(self) -> None:
        """Track B reviewer's mirroring test (2026-09-15): the same denial/unlicensed
        double-encoding was reproduced on `bns69`'s `amountsToRape` denial, not only ipc405's --
        a second, independently-authored Contract proves the fix in `scoreREGLine` is general, not
        an artefact of one Contract's particular claim shape."""
        result = reg.check_registry(
            {
                "induces the woman by deceitful means (inducement for, or false promise of, "
                "employment or promotion, or marrying by suppressing identity), or by a promise "
                "to marry made without any intention of fulfilling it": True,
                "sexual intercourse with the woman actually occurs": True,
                "the sexual intercourse amounts to the offence of rape": True,
            },
            "bns69",
            score_bin=_SCORE_BIN,
        )
        assert result["verdict"] == "flagged"
        assert len(result["denied_claims"]) == 1
        assert "amounts to the offence of rape" in result["denied_claims"][0]
        assert result["omitted_claims"] == []
        assert result["unlicensed_claims"] == []
