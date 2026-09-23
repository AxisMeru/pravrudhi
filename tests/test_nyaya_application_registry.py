"""application/nyaya.py's registry wrapper functions: registry_contract_ids, registry_elements,
registry_check. Thin wrappers over nyaya_lean_registry, tested here at the layer the API route calls,
against the REAL compiled Lean binary -- no stub, no fake scorer (house rule).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from pravrudhi.application import nyaya, nyaya_lean_registry

#: No hard-coded absolute path (this repo is public) -- set PRABHASA_NYAYA_SCORE_BIN to a real, built
#: `score` binary to run the tests below for real; skipped otherwise, same as this file's own env-var
#: convention (`nyaya_gold_score.score_bin_path`'s `SCORE_BIN_ENV`).
_SCORE_BIN = os.environ.get("PRABHASA_NYAYA_SCORE_BIN")
requires_registry_scorer = pytest.mark.skipif(
    not _SCORE_BIN or not Path(_SCORE_BIN).exists(),
    reason="set PRABHASA_NYAYA_SCORE_BIN to a built prabhasa-nyaya score binary to run these",
)


@pytest.fixture(autouse=True)
def _point_at_real_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test in this file resolves the Lean binary via the env override, not relative-path
    resolution from `root` -- correct in the real deployed engine (sibling checkout), but this test
    file may run from a worktree where the relative path does not land on a built binary."""
    if _SCORE_BIN:
        monkeypatch.setenv("PRABHASA_NYAYA_SCORE_BIN", _SCORE_BIN)


class TestRegistryContractIds:
    def test_lists_all_fourteen_sorted(self) -> None:
        ids = nyaya.registry_contract_ids()
        assert ids == sorted(nyaya_lean_registry.KNOWN_CONTRACT_IDS)
        assert len(ids) == 14

    def test_disjoint_from_the_citation_family(self) -> None:
        """The two contract families never share an id -- a real risk if someone later renumbers one
        to collide with the other; this pins the current, correct state."""
        from pravrudhi.application import nyaya_lean

        assert set(nyaya.registry_contract_ids()).isdisjoint(nyaya_lean.KNOWN_CONTRACT_IDS)


@requires_registry_scorer
class TestRegistryElements:
    def test_reads_required_elements_live(self, tmp_path: Path) -> None:
        names = nyaya.registry_elements(tmp_path, "ipc405_misappropriation")
        assert names == [
            "entrusted with property, or with dominion over property",
            "dishonestly misappropriates or converts the property to his own use",
        ]

    def test_refuses_an_unknown_contract_id(self, tmp_path: Path) -> None:
        with pytest.raises(nyaya_lean_registry.UnknownContractError):
            nyaya.registry_elements(tmp_path, "not_a_real_id")


@requires_registry_scorer
class TestRegistryCheck:
    def test_all_elements_met_is_grounded_and_persisted(self, tmp_path: Path) -> None:
        result = nyaya.registry_check(
            tmp_path,
            "ipc405_misappropriation",
            {
                "entrusted with property, or with dominion over property": True,
                "dishonestly misappropriates or converts the property to his own use": True,
            },
        )
        assert result["checker"] == "lean-registry"
        assert result["contract_id"] == "ipc405_misappropriation"
        assert result["verdict"] == "grounded"
        assert result["omitted_claims"] == []
        assert result["provenance"] == "agama"

        written = list((tmp_path / "research" / "nyaya" / "audits").glob("registry-*.json"))
        assert len(written) == 1
        on_disk = json.loads(written[0].read_text())
        assert on_disk == result

    def test_a_missing_element_is_flagged_omitted(self, tmp_path: Path) -> None:
        result = nyaya.registry_check(
            tmp_path,
            "ipc405_misappropriation",
            {"entrusted with property, or with dominion over property": True},
        )
        assert result["verdict"] == "flagged"
        assert len(result["omitted_claims"]) == 1

    def test_evidence_is_recorded_but_never_sent_to_the_scorer(self, tmp_path: Path) -> None:
        """Evidence spans are display-only metadata -- two calls that differ ONLY in evidence must
        score identically, proving evidence never reaches check_registry's scoring path."""
        assertions = {
            "entrusted with property, or with dominion over property": True,
            "dishonestly misappropriates or converts the property to his own use": True,
        }
        without_evidence = nyaya.registry_check(tmp_path, "ipc405_misappropriation", assertions)
        with_evidence = nyaya.registry_check(
            tmp_path,
            "ipc405_misappropriation",
            assertions,
            evidence={"entrusted with property, or with dominion over property": "the facts say so"},
        )
        assert without_evidence["verdict"] == with_evidence["verdict"]
        assert without_evidence["omitted_claims"] == with_evidence["omitted_claims"]
        assert with_evidence["evidence"] == {
            "entrusted with property, or with dominion over property": "the facts say so"
        }
        assert without_evidence["evidence"] == {}

    def test_refuses_an_unknown_contract_id_before_any_write(self, tmp_path: Path) -> None:
        with pytest.raises(nyaya_lean_registry.UnknownContractError):
            nyaya.registry_check(tmp_path, "not_a_real_id", {"x": True})
        assert not (tmp_path / "research" / "nyaya" / "audits").exists()


class TestMissingBinaryFailsClean:
    """Reproduces a real production incident (2026-09-23): the deployed container ships no compiled
    Lean `score` binary at all (root cause: not built into the Docker image -- a separate, larger
    infra gap flagged to the lead, not fixed here). The application-layer bug THIS class covers: that
    condition reached the API as an uncaught FileNotFoundError -> a raw, unhandled 500 with no CORS
    headers (browsers report it as a CORS failure, which is misleading -- the real cause is a server
    crash, not a CORS misconfiguration). registry_elements/registry_check must convert it to a
    RuntimeError, which api/nyaya.py already maps to a clean 503."""

    def test_registry_elements_raises_runtimeerror_not_filenotfounderror(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PRABHASA_NYAYA_SCORE_BIN", str(tmp_path / "no-such-binary"))
        with pytest.raises(RuntimeError):
            nyaya.registry_elements(tmp_path, "ipc405_misappropriation")

    def test_registry_check_raises_runtimeerror_not_filenotfounderror(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PRABHASA_NYAYA_SCORE_BIN", str(tmp_path / "no-such-binary"))
        with pytest.raises(RuntimeError):
            nyaya.registry_check(tmp_path, "ipc405_misappropriation", {"x": True})
        assert not (tmp_path / "research" / "nyaya" / "audits").exists()
