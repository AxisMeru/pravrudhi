"""Track A P1: `checker="lean"` wired onto the IPC 378 example from prabhasa-nyaya's own
`lean/PrabhasaNyaya/Tests.lean` (seed index 0 in `lean/Score.lean`'s `contractFor`), via the compiled `score`
binary -- no Python import of prabhasa-nyaya, per ADR-0001's independence rule.

Done-when (ADR-0003's P1 plan): an invented-citation answer comes back `unlicensed`, naming the claim,
across two vendors.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pravrudhi.api.localguard import TOKEN_HEADER, app_token
from pravrudhi.api.server import create_app
from pravrudhi.application import nyaya_lean
from pravrudhi.application.init import init_project

_LEAN_SCORE_BIN = Path("/home/ss/projects/prabhasa-nyaya/lean/.lake/build/bin/score")
requires_lean_scorer = pytest.mark.skipif(
    not _LEAN_SCORE_BIN.exists(), reason="prabhasa-nyaya's compiled score binary is not built on this host"
)

_CLEAN_ANSWER = (
    "The purse was in the complainant's possession and she did not consent to its being taken. "
    "The purse is movable property, and the accused moved it from the seat, with dishonest intention. "
    "Pierce v. State, 470 F.2d 798 establishes that a momentary taking suffices. "
    "Pierce v. State is a decision of the Supreme Court, and the Supreme Court binds the District Court "
    "hearing this matter, so that holding governs here. Therefore the elements of IPC 378 are made out."
)

# The volume is wrong by one (471, not 470) -- the exact mutation Tests.lean's own
# `Counterexamples.inventedCite` fixture uses, and the failure `citation_precision` measured at 0.0000.
_INVENTED_CITATION_ANSWER = _CLEAN_ANSWER.replace("470 F.2d 798", "471 F.2d 798")


@requires_lean_scorer
class TestLeanCheckerOnTheIPC378Example:
    def test_a_clean_citation_is_licensed(self) -> None:
        result = nyaya_lean.check_ipc378(_CLEAN_ANSWER, score_bin=_LEAN_SCORE_BIN)
        assert result["verdict"] == "licensed"
        assert result["unlicensed_claims"] == []
        assert result["not_formalisable_count"] == 1  # Tests.lean's contract has one notFormalisable entry

    def test_an_invented_citation_comes_back_unlicensed_naming_the_claim(self) -> None:
        result = nyaya_lean.check_ipc378(_INVENTED_CITATION_ANSWER, score_bin=_LEAN_SCORE_BIN)
        assert result["verdict"] == "unlicensed"
        assert result["unlicensed_claims"] == ["CITES:Pierce v. State:471:F.2d:798"]
        assert result["not_formalisable_count"] == 1

    def test_the_invented_citation_is_caught_across_two_different_vendors(self) -> None:
        """The checker reads the answer text alone -- which vendor produced it never enters the wire
        protocol -- so running it twice with two different vendor labels must agree, proving the wire itself
        is vendor-agnostic rather than accidentally tuned to one vendor's phrasing."""
        for vendor_label in ("claude-cli", "codex-cli"):
            result = nyaya_lean.check_ipc378(_INVENTED_CITATION_ANSWER, score_bin=_LEAN_SCORE_BIN)
            assert result["verdict"] == "unlicensed", vendor_label
            assert result["unlicensed_claims"] == ["CITES:Pierce v. State:471:F.2d:798"], vendor_label

    def test_no_citation_found_is_reported_as_a_gap_not_a_false_grounding(self) -> None:
        result = nyaya_lean.check_ipc378("The purse was taken.", score_bin=_LEAN_SCORE_BIN)
        assert result["verdict"] == "no_citation_found"
        assert result["unlicensed_claims"] == []


@requires_lean_scorer
def test_api_audit_endpoint_with_lean_checker_names_the_unlicensed_citation_across_two_vendors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The done-when this slice answers to (ADR-0003's P1 plan): an API test using the IPC 378 example from
    Tests.lean where an invented-citation answer comes back `unlicensed`, naming the claim, across two
    vendors. `checker="lean"` never calls a vendor to check -- "across two vendors" here means the two
    ANSWERING vendors' text is each independently sent through the same lean checker and must agree, since
    the wire protocol only ever sees answer text, never which vendor produced it."""
    init_project(tmp_path)
    monkeypatch.setenv("PRABHASA_NYAYA_SCORE_BIN", str(_LEAN_SCORE_BIN))
    client = TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8008")
    token = app_token(tmp_path)

    for vendor in ("claude-cli", "codex-cli"):
        r = client.post(
            "/api/nyaya/audit",
            json={"sources": "IPC 378 and Pierce v. State, 470 F.2d 798", "answer": _INVENTED_CITATION_ANSWER, "checker": "lean"},
            headers={TOKEN_HEADER: token},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["checker"] == "lean", vendor
        assert body["verdict"] == "unlicensed", vendor
        assert body["unlicensed_claims"] == ["CITES:Pierce v. State:471:F.2d:798"], vendor
        assert body["not_formalisable_count"] == 1, vendor
