"""The registry (14-contract) HTTP surface: /api/nyaya/registry/contracts, /{id}/elements, /check.
Real compiled Lean binary, real FastAPI app -- no stub, no fake scorer (house rule).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pravrudhi.api.localguard import TOKEN_HEADER, app_token
from pravrudhi.application.app_serve import build_app
from pravrudhi.application.init import init_project

H = {"host": "127.0.0.1:8008"}

_SCORE_BIN = Path("/home/ss/projects/prabhasa-nyaya/lean/.lake/build/bin/score")
requires_registry_scorer = pytest.mark.skipif(
    not _SCORE_BIN.exists(), reason="prabhasa-nyaya's score binary is not built on this host"
)


@pytest.fixture(autouse=True)
def _point_at_real_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    if _SCORE_BIN.exists():
        monkeypatch.setenv("PRABHASA_NYAYA_SCORE_BIN", str(_SCORE_BIN))


def _client(tmp_path: Path) -> TestClient:
    init_project(tmp_path)
    return TestClient(build_app(tmp_path), headers=H)


def _token_header(tmp_path: Path) -> dict[str, str]:
    return {TOKEN_HEADER: app_token(tmp_path)}


def test_lists_all_fourteen_contracts(tmp_path: Path) -> None:
    c = _client(tmp_path)
    body = c.get("/api/nyaya/registry/contracts").json()
    assert len(body["contracts"]) == 14
    assert "ipc405_misappropriation" in body["contracts"]


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
