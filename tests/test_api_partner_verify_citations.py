"""#302 slice A: POST /api/v1/verify-citations exposes `verify` over HTTP. Constructed index and text only
(same toy rows as test_verify.py); no real judgment material, no network. NOT_IN_INDEX is "no evidence
either way", never "fake"."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pravrudhi.api.partner import PartnerApiConfig, build_partner_router
from pravrudhi.application.case_index import CaseRecord, insert_case, open_index

_CFG = PartnerApiConfig(rate_limit_per_minute=1000, max_concurrent=100, trust_proxy_header=False)
_CITER = "In Narandas Karsondas v. S.A. Kamtam and Anr. (1977) 3 SCC 247 the Court clarified the rule."
_BODY = (
    "In a suit for specific performance, time is not ordinarily of the essence of the contract "
    "for sale of immovable property unless the parties expressly intend it to be so."
)


def _index(tmp_path: Path) -> Path:
    p = tmp_path / "idx.sqlite3"
    conn = open_index(p)
    insert_case(conn, CaseRecord("c1", "Later Case v Other", "Supreme Court", 2005, "sc_pdf", "/x", _CITER))
    insert_case(
        conn, CaseRecord("r1", "Narandas Karsondas vs S A Kamtam and Anr", "Supreme Court", 1977, "sc_pdf", "/y", _BODY)
    )
    conn.commit()
    conn.close()
    return p


def _client(tmp_path: Path, index: Path | None) -> TestClient:
    app = FastAPI()
    app.include_router(build_partner_router(tmp_path, config=_CFG, citation_index_path=index))
    return TestClient(app)


def _post(c: TestClient, **over: str) -> dict:
    body = {"citation": "(1977) 3 SCC 247", "quote": "time is not ordinarily of the essence"}
    body.update(over)
    return c.post("/api/v1/verify-citations", json=body)


def test_verified(tmp_path: Path) -> None:
    r = _post(_client(tmp_path, _index(tmp_path)))
    assert r.status_code == 200 and r.json()["result"] == "VERIFIED"


def test_quote_not_found(tmp_path: Path) -> None:
    r = _post(_client(tmp_path, _index(tmp_path)), quote="the moon is made of cheese")
    assert r.json()["result"] == "EXISTS_QUOTE_NOT_FOUND"


def test_unresolvable_citation_is_not_in_index_with_no_evidence_note(tmp_path: Path) -> None:
    r = _post(_client(tmp_path, _index(tmp_path)), citation="AIR 1950 SC 27")
    j = r.json()
    assert r.status_code == 200 and j["result"] == "NOT_IN_INDEX"
    assert "no evidence" in j["note"].lower() and "fake" not in j["result"].lower()


def test_malformed_citation(tmp_path: Path) -> None:
    r = _post(_client(tmp_path, _index(tmp_path)), citation="not a citation")
    assert r.json()["result"] == "MALFORMED"


def test_no_index_configured_is_503_never_a_guessed_verdict(tmp_path: Path) -> None:
    r = _post(_client(tmp_path, None))
    assert r.status_code == 503 and r.json() == {"error": "citation_index_unavailable"}


def test_missing_index_file_is_503(tmp_path: Path) -> None:
    r = _post(_client(tmp_path, tmp_path / "absent.sqlite3"))
    assert r.status_code == 503 and r.json()["error"] == "citation_index_unavailable"


def test_the_index_is_opened_read_only(tmp_path: Path) -> None:
    p = _index(tmp_path)
    before = p.read_bytes()
    _post(_client(tmp_path, p))
    assert p.read_bytes() == before


@pytest.mark.parametrize("over", [{"citation": ""}, {"quote": ""}, {"citation": "x" * 501}, {"quote": "y" * 4001}])
def test_input_caps_422(tmp_path: Path, over: dict[str, str]) -> None:
    assert _post(_client(tmp_path, _index(tmp_path)), **over).status_code == 422


def test_rate_limited(tmp_path: Path) -> None:
    cfg = PartnerApiConfig(rate_limit_per_minute=1, max_concurrent=100, trust_proxy_header=False)
    app = FastAPI()
    app.include_router(build_partner_router(tmp_path, config=cfg, citation_index_path=_index(tmp_path)))
    c = TestClient(app)
    assert _post(c).status_code == 200
    assert _post(c).status_code == 429
