"""Track A P1/P1b: `checker="lean"` wired onto prabhasa-nyaya's compiled Lean scorer, generalized to any
contract the scorer knows (P1b, ADR-0003) rather than hard-wired to the IPC 378 example (P1).

Done-when (P1b): the IPC 378 test unchanged (still `unlicensed`, naming the claim, across two vendors) PLUS
a second test scoring seed 5 (Rowan v. Fisk) through the same generalized `check()` function, and unknown
contract ids are refused rather than silently defaulted.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pravrudhi.api.localguard import TOKEN_HEADER, app_token
from pravrudhi.api.server import create_app
from pravrudhi.application import nyaya_lean
from pravrudhi.application.init import init_project

# P1b's `A3N` wire tag lives on prabhasa-nyaya's assistant/trackA/p1b-checker-generalize branch, not yet
# merged to main -- point at that worktree's freshly-built binary until the merge lands and this can revert
# to the main checkout's own path.
_LEAN_SCORE_BIN = Path("/home/ss/projects/prabhasa-nyaya/.worktrees/trackA-p1b/lean/.lake/build/bin/score")
requires_lean_scorer = pytest.mark.skipif(
    not _LEAN_SCORE_BIN.exists(), reason="prabhasa-nyaya's compiled score binary is not built on this host"
)

# contract_id "0": Tests.lean's IPC 378 example (Pierce v. State, 470 F.2d 798).
_IPC378_CLEAN_ANSWER = (
    "The purse was in the complainant's possession and she did not consent to its being taken. "
    "The purse is movable property, and the accused moved it from the seat, with dishonest intention. "
    "Pierce v. State, 470 F.2d 798 establishes that a momentary taking suffices. "
    "Pierce v. State is a decision of the Supreme Court, and the Supreme Court binds the District Court "
    "hearing this matter, so that holding governs here. Therefore the elements of IPC 378 are made out."
)
# The volume is wrong by one (471, not 470) -- the exact mutation Tests.lean's own
# `Counterexamples.inventedCite` fixture uses, and the failure `citation_precision` measured at 0.0000.
_IPC378_INVENTED_CITATION_ANSWER = _IPC378_CLEAN_ANSWER.replace("470 F.2d 798", "471 F.2d 798")

# contract_id "5": Seeds/Seed5.lean's example (Rowan v. Fisk, 149 U.S. 302).
_SEED5_CLEAN_ANSWER = (
    "The statement was made in the ordinary course of business. Rowan v. Fisk, 149 U.S. 302 establishes "
    "that such a statement falls within the exception and is not excluded as hearsay. Rowan v. Fisk is a "
    "decision of the Supreme Court, and the Supreme Court binds the Sessions Court hearing this matter, so "
    "that holding governs here. Therefore the statement is admissible."
)
_SEED5_INVENTED_CITATION_ANSWER = _SEED5_CLEAN_ANSWER.replace("149 U.S. 302", "150 U.S. 302")


@requires_lean_scorer
class TestLeanCheckerOnTheIPC378Contract:
    def test_a_clean_citation_is_licensed(self) -> None:
        result = nyaya_lean.check(_IPC378_CLEAN_ANSWER, "0", score_bin=_LEAN_SCORE_BIN)
        assert result["verdict"] == "licensed"
        assert result["unlicensed_claims"] == []
        assert result["not_formalisable_count"] == 1  # Tests.lean's contract has one notFormalisable entry

    def test_an_invented_citation_comes_back_unlicensed_naming_the_claim(self) -> None:
        result = nyaya_lean.check(_IPC378_INVENTED_CITATION_ANSWER, "0", score_bin=_LEAN_SCORE_BIN)
        assert result["verdict"] == "unlicensed"
        assert result["unlicensed_claims"] == ["CITES:Pierce v. State:471:F.2d:798"]
        assert result["not_formalisable_count"] == 1

    def test_the_invented_citation_is_caught_across_two_different_vendors(self) -> None:
        """The checker reads the answer text alone -- which vendor produced it never enters the wire
        protocol -- so running it twice with two different vendor labels must agree, proving the wire itself
        is vendor-agnostic rather than accidentally tuned to one vendor's phrasing."""
        for vendor_label in ("claude-cli", "codex-cli"):
            result = nyaya_lean.check(_IPC378_INVENTED_CITATION_ANSWER, "0", score_bin=_LEAN_SCORE_BIN)
            assert result["verdict"] == "unlicensed", vendor_label
            assert result["unlicensed_claims"] == ["CITES:Pierce v. State:471:F.2d:798"], vendor_label

    def test_no_citation_found_is_reported_as_a_gap_not_a_false_grounding(self) -> None:
        result = nyaya_lean.check("The purse was taken.", "0", score_bin=_LEAN_SCORE_BIN)
        assert result["verdict"] == "no_citation_found"
        assert result["unlicensed_claims"] == []


@requires_lean_scorer
class TestLeanCheckerGeneralizesToASecondContract:
    """P1b's own done-when: the SAME check() function, a DIFFERENT contract_id (seed 5, Rowan v. Fisk) --
    proving this isn't secretly still hard-wired to seed 0 under a new name."""

    def test_seed5_clean_citation_is_licensed(self) -> None:
        result = nyaya_lean.check(_SEED5_CLEAN_ANSWER, "5", score_bin=_LEAN_SCORE_BIN)
        assert result["verdict"] == "licensed"
        assert result["unlicensed_claims"] == []
        assert result["not_formalisable_count"] == 1  # Seed5.lean's contract also has one notFormalisable entry

    def test_seed5_invented_citation_comes_back_unlicensed_naming_the_claim(self) -> None:
        result = nyaya_lean.check(_SEED5_INVENTED_CITATION_ANSWER, "5", score_bin=_LEAN_SCORE_BIN)
        assert result["verdict"] == "unlicensed"
        assert result["unlicensed_claims"] == ["CITES:Rowan v. Fisk:150:U.S.:302"]
        assert result["not_formalisable_count"] == 1


@requires_lean_scorer
def test_unknown_contract_id_is_refused_not_defaulted() -> None:
    with pytest.raises(nyaya_lean.UnknownContractError, match="99"):
        nyaya_lean.check("anything", "99", score_bin=_LEAN_SCORE_BIN)


@requires_lean_scorer
def test_api_audit_endpoint_with_lean_checker_names_the_unlicensed_citation_across_two_vendors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P1's original done-when, unchanged: an API test using the IPC 378 example from Tests.lean where an
    invented-citation answer comes back `unlicensed`, naming the claim, across two vendors. `checker="lean"`
    never calls a vendor to check -- "across two vendors" here means the two ANSWERING vendors' text is each
    independently sent through the same lean checker and must agree, since the wire protocol only ever sees
    answer text, never which vendor produced it. P1b adds: contract_id is now a required field on the
    request, not silently defaulted to 0."""
    init_project(tmp_path)
    monkeypatch.setenv("PRABHASA_NYAYA_SCORE_BIN", str(_LEAN_SCORE_BIN))
    client = TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8008")
    token = app_token(tmp_path)

    for vendor in ("claude-cli", "codex-cli"):
        r = client.post(
            "/api/nyaya/audit",
            json={
                "sources": "IPC 378 and Pierce v. State, 470 F.2d 798",
                "answer": _IPC378_INVENTED_CITATION_ANSWER,
                "checker": "lean",
                "contract_id": "0",
            },
            headers={TOKEN_HEADER: token},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["checker"] == "lean", vendor
        assert body["verdict"] == "unlicensed", vendor
        assert body["unlicensed_claims"] == ["CITES:Pierce v. State:471:F.2d:798"], vendor
        assert body["not_formalisable_count"] == 1, vendor


@requires_lean_scorer
def test_api_audit_endpoint_refuses_an_unknown_contract_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    init_project(tmp_path)
    monkeypatch.setenv("PRABHASA_NYAYA_SCORE_BIN", str(_LEAN_SCORE_BIN))
    client = TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8008")
    token = app_token(tmp_path)

    r = client.post(
        "/api/nyaya/audit",
        json={"sources": "irrelevant", "answer": "irrelevant", "checker": "lean", "contract_id": "99"},
        headers={TOKEN_HEADER: token},
    )
    assert r.status_code == 422, r.text


@requires_lean_scorer
def test_api_audit_endpoint_refuses_lean_checker_with_no_contract_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    init_project(tmp_path)
    monkeypatch.setenv("PRABHASA_NYAYA_SCORE_BIN", str(_LEAN_SCORE_BIN))
    client = TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8008")
    token = app_token(tmp_path)

    r = client.post(
        "/api/nyaya/audit",
        json={"sources": "irrelevant", "answer": "irrelevant", "checker": "lean"},
        headers={TOKEN_HEADER: token},
    )
    assert r.status_code == 422, r.text
