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
    assert j["note"].startswith("The case was not found in our index.") and "fake" not in j["note"].lower()


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


# -- R1 review fixes (#181): bounded, gated, metered and audited ---------------------------------------------------

import sqlite3  # noqa: E402
from typing import Any  # noqa: E402

from pravrudhi.api import identity  # noqa: E402
from pravrudhi.api import partner as partner_module  # noqa: E402
from pravrudhi.api.identity import User  # noqa: E402
from pravrudhi.application import tenancy  # noqa: E402
from pravrudhi.application import verify as verify_module  # noqa: E402

_ADMIN = User(id="op-1", email="op@example.com", role="authenticated")
_H = tenancy.API_KEY_HEADER


def _keyed_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cfg: PartnerApiConfig = _CFG) -> tuple[TestClient, str]:
    monkeypatch.setenv("PRAVRUDHI_ADMINS", _ADMIN.id)
    app = FastAPI()
    app.include_router(build_partner_router(tmp_path, config=cfg, citation_index_path=_index(tmp_path)))
    app.dependency_overrides[identity.current_user] = lambda: _ADMIN
    c = TestClient(app)
    c.post("/api/v1/orgs", json={"org_id": "acme", "name": "acme"})
    secret = str(c.post("/api/v1/orgs/acme/keys", json={"rate_limit_per_minute": 3}).json()["secret"])
    return c, secret


def test_a_keyed_lookup_is_metered_audited_and_has_rate_limit_headers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    c, secret = _keyed_client(tmp_path, monkeypatch)
    body = {"citation": "(1977) 3 SCC 247", "quote": "time is not ordinarily of the essence"}
    r = c.post("/api/v1/verify-citations", json=body, headers={_H: secret})
    assert r.status_code == 200 and r.json()["result"] == "VERIFIED"
    assert {"x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset"} <= set(r.headers)
    assert c.get("/api/v1/orgs/acme/usage", headers={_H: secret}).json()["calls"] == 1
    rows = c.get("/api/v1/audit", headers={_H: secret}).json()["rows"]
    assert [(x["mode"], x["status_code"], x["outcomes"]) for x in rows] == [("verify", 200, {"verify": "VERIFIED"})]
    assert "SCC 247" not in str(rows) and "essence" not in str(rows)  # no citation or quote text in the audit


def test_a_keyed_lookup_is_rate_limited_per_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    c, secret = _keyed_client(tmp_path, monkeypatch)
    body = {"citation": "(1977) 3 SCC 247", "quote": "time is not ordinarily"}
    codes = [c.post("/api/v1/verify-citations", json=body, headers={_H: secret}).status_code for _ in range(5)]
    assert codes[:3] == [200, 200, 200] and set(codes[3:]) == {429}


def test_an_invalid_key_is_401_never_anonymous(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    c, _secret = _keyed_client(tmp_path, monkeypatch)
    r = c.post("/api/v1/verify-citations", json={"citation": "(1977) 3 SCC 247", "quote": "x"}, headers={_H: "not-a-real-key"})
    assert r.status_code == 401


def test_lookups_are_capped_by_a_non_blocking_gate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = PartnerApiConfig(rate_limit_per_minute=1000, max_concurrent=100, trust_proxy_header=False, verify_max_concurrent=1)
    c = _client_with(tmp_path, cfg)
    seen: dict[str, Any] = {}

    def slow_verify(conn: sqlite3.Connection, citation: str, quote: str) -> Any:
        seen["inner"] = _post(c)  # a second lookup while the first holds the only slot
        return verify_module.VerifyResult.NOT_IN_INDEX

    monkeypatch.setattr(partner_module, "verify_citation", slow_verify)
    outer = _post(c)
    assert outer.status_code == 200
    assert seen["inner"].status_code == 503 and seen["inner"].json() == {"error": "verify_at_capacity"}
    assert _post(c).status_code == 200  # the slot was released


def test_a_lookup_that_runs_past_the_time_bound_is_aborted_with_503(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = PartnerApiConfig(rate_limit_per_minute=1000, max_concurrent=100, trust_proxy_header=False, verify_timeout_s=0.05)
    c = _client_with(tmp_path, cfg)

    def forever(conn: sqlite3.Connection, citation: str, quote: str) -> Any:
        conn.execute("WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n) SELECT count(*) FROM n").fetchone()

    monkeypatch.setattr(partner_module, "verify_citation", forever)
    r = _post(c)
    assert r.status_code == 503 and r.json() == {"error": "verify_timeout"}


def _client_with(tmp_path: Path, cfg: PartnerApiConfig) -> TestClient:
    app = FastAPI()
    app.include_router(build_partner_router(tmp_path, config=cfg, citation_index_path=_index(tmp_path)))
    return TestClient(app)


def test_the_fuzzy_title_scan_never_reads_the_text_column_for_every_case(tmp_path: Path) -> None:
    conn = open_index(tmp_path / "big.sqlite3")
    for i in range(50):
        title = f"Unrelated Party {i} v Other {i}"
        insert_case(conn, CaseRecord(f"c{i}", title, "Supreme Court", 2000, "sc_pdf", "/x", "T" * 1000))
    hit = CaseRecord("hit", "Narandas Karsondas vs S A Kamtam", "Supreme Court", 1977, "sc_pdf", "/y", "the real text")
    insert_case(conn, hit)
    conn.commit()
    conn.row_factory = sqlite3.Row
    statements: list[str] = []
    conn.set_trace_callback(statements.append)
    rows = verify_module._fuzzy_confirm(conn, "Narandas Karsondas", "S A Kamtam")
    assert [r["case_id"] for r in rows] == ["hit"] and rows[0]["text"] == "the real text"
    scans = [s for s in statements if "FROM cases" in s and "WHERE" not in s]
    assert scans and all("text" not in s.lower() for s in scans), scans


def test_no_verify_note_uses_the_word_fake_and_not_in_index_says_what_it_means() -> None:
    """A public API string (R1's wording): never calls a citation fake; absence from the index shows nothing either way."""
    from pravrudhi.api.partner import _VERIFY_NOTES

    assert set(_VERIFY_NOTES) == {"VERIFIED", "EXISTS_QUOTE_NOT_FOUND", "NOT_IN_INDEX", "MALFORMED", "CONFLICT"}
    assert not any("fake" in note.lower() for note in _VERIFY_NOTES.values())
    assert _VERIFY_NOTES["NOT_IN_INDEX"] == (
        "The case was not found in our index. "
        "That does not show whether the citation is real: the index does not hold every judgment."
    )
