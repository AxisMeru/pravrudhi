"""The registry (26-contract) HTTP surface: /api/nyaya/registry/contracts, /{id}/elements, /check.
Real compiled Lean binary, real FastAPI app -- no stub, no fake scorer (house rule).
BNSS 187 contracts are excluded due to Lean-side defects and return UnknownContractError.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pravrudhi.api.localguard import TOKEN_HEADER, app_token
from pravrudhi.application.app_serve import build_app
from pravrudhi.application.init import init_project

H = {"host": "127.0.0.1:8008"}

#: No hard-coded absolute path (this repo is public) -- set PRABHASA_NYAYA_SCORE_BIN to a real, built
#: `score` binary to run the tests below for real; skipped otherwise.
_SCORE_BIN = os.environ.get("PRABHASA_NYAYA_SCORE_BIN")
requires_registry_scorer = pytest.mark.skipif(
    not _SCORE_BIN or not Path(_SCORE_BIN).exists(),
    reason="set PRABHASA_NYAYA_SCORE_BIN to a built prabhasa-nyaya score binary to run these",
)


@pytest.fixture(autouse=True)
def _point_at_real_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    if _SCORE_BIN:
        monkeypatch.setenv("PRABHASA_NYAYA_SCORE_BIN", _SCORE_BIN)


def _client(tmp_path: Path) -> TestClient:
    init_project(tmp_path)
    return TestClient(build_app(tmp_path), headers=H)


def _token_header(tmp_path: Path) -> dict[str, str]:
    return {TOKEN_HEADER: app_token(tmp_path)}


def test_lists_all_known_contracts(tmp_path: Path) -> None:
    c = _client(tmp_path)
    body = c.get("/api/nyaya/registry/contracts").json()
    assert len(body["contracts"]) == 37
    assert {"ipc405_misappropriation", "bnss187_extended_serious", "ni138", "bnss528", "bsa63"} <= set(
        body["contracts"]
    )


@pytest.mark.requires_score_bin
@requires_registry_scorer
def test_elements_reads_live_from_the_binary(tmp_path: Path) -> None:
    c = _client(tmp_path)
    body = c.get("/api/nyaya/registry/ipc405_misappropriation/elements").json()
    assert body["contract_id"] == "ipc405_misappropriation"
    assert body["elements"] == [
        "entrusted with property, or with dominion over property",
        "dishonestly misappropriates or converts the property to his own use",
    ]


def test_elements_422s_an_unknown_contract_id(tmp_path: Path) -> None:
    c = _client(tmp_path)
    resp = c.get("/api/nyaya/registry/not_a_real_id/elements")
    assert resp.status_code == 422


@pytest.mark.requires_score_bin
@requires_registry_scorer
def test_check_all_met_is_grounded(tmp_path: Path) -> None:
    c = _client(tmp_path)
    resp = c.post(
        "/api/nyaya/registry/check",
        json={
            "contract_id": "ipc405_misappropriation",
            "assertions": {
                "entrusted with property, or with dominion over property": True,
                "dishonestly misappropriates or converts the property to his own use": True,
            },
        },
        headers=_token_header(tmp_path),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["checker"] == "lean-registry"
    assert body["verdict"] == "grounded"
    assert body["omitted_claims"] == []
    assert body["provenance"] == "agama"


@pytest.mark.requires_score_bin
@requires_registry_scorer
def test_check_a_missing_element_is_flagged(tmp_path: Path) -> None:
    c = _client(tmp_path)
    resp = c.post(
        "/api/nyaya/registry/check",
        json={
            "contract_id": "ipc405_misappropriation",
            "assertions": {"entrusted with property, or with dominion over property": True},
        },
        headers=_token_header(tmp_path),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["verdict"] == "flagged"
    assert len(body["omitted_claims"]) == 1


def test_check_422s_an_unknown_contract_id(tmp_path: Path) -> None:
    c = _client(tmp_path)
    resp = c.post(
        "/api/nyaya/registry/check", json={"contract_id": "not_a_real_id", "assertions": {"x": True}},
        headers=_token_header(tmp_path),
    )
    assert resp.status_code == 422


def test_check_422s_empty_assertions(tmp_path: Path) -> None:
    c = _client(tmp_path)
    resp = c.post(
        "/api/nyaya/registry/check",
        json={"contract_id": "ipc405_misappropriation", "assertions": {}},
        headers=_token_header(tmp_path),
    )
    assert resp.status_code == 422


@pytest.mark.requires_score_bin
@requires_registry_scorer
def test_check_carries_evidence_through_without_affecting_the_verdict(tmp_path: Path) -> None:
    c = _client(tmp_path)
    resp = c.post(
        "/api/nyaya/registry/check",
        json={
            "contract_id": "ipc405_misappropriation",
            "assertions": {
                "entrusted with property, or with dominion over property": True,
                "dishonestly misappropriates or converts the property to his own use": True,
            },
            "evidence": {"entrusted with property, or with dominion over property": "para 3 of the facts"},
        },
        headers=_token_header(tmp_path),
    )
    body = resp.json()
    assert body["verdict"] == "grounded"
    assert body["evidence"] == {
        "entrusted with property, or with dominion over property": "para 3 of the facts"
    }


def test_elements_503s_cleanly_when_the_lean_binary_is_unreachable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Real production incident (2026-09-23): a missing score binary reached the browser as an
    uncaught 500 with no CORS headers (misreported as a CORS failure). Must be a clean 503 now."""
    monkeypatch.setenv("PRABHASA_NYAYA_SCORE_BIN", str(tmp_path / "no-such-binary"))
    c = _client(tmp_path)
    resp = c.get("/api/nyaya/registry/ipc405_misappropriation/elements")
    assert resp.status_code == 503


def test_check_503s_cleanly_when_the_lean_binary_is_unreachable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRABHASA_NYAYA_SCORE_BIN", str(tmp_path / "no-such-binary"))
    c = _client(tmp_path)
    resp = c.post(
        "/api/nyaya/registry/check",
        json={"contract_id": "ipc405_misappropriation", "assertions": {"x": True}},
        headers=_token_header(tmp_path),
    )
    assert resp.status_code == 503
