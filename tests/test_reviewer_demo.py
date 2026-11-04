"""The reviewer demo page's API over the REAL pinned Lean binary (skipped unless PRABHASA_NYAYA_SCORE_BIN points at it):
only a reviewer-confirmed, quote-checked ingredient is sent as Met, and the binary's own verdict comes back unaltered."""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
BIN = os.environ.get("PRABHASA_NYAYA_SCORE_BIN")
pytestmark = pytest.mark.skipif(not BIN or not Path(BIN).is_file(), reason="needs the real pinned Lean score binary")


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("REVIEWER_DEMO_RECORD", str(tmp_path / "record.jsonl"))
    spec = importlib.util.spec_from_file_location("reviewer_demo_app", ROOT / "scripts" / "reviewer_demo" / "app.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, mod)  # pydantic resolves the request model's annotations through it
    spec.loader.exec_module(mod)
    return TestClient(mod.create_app(mod._registry()))


QUOTES = [
    ("F3", "by representing themselves to be established traders, assuring payment within one month and promising to pay "
           "above the prevailing market price"),
    ("F3", "the petitioners induced him to part with goods worth ₹77,92,720/-"),
    ("F4", "after obtaining delivery of the goods"),
]


def _check(client: TestClient, confirmed: list[bool], quotes: list[tuple[str, str]] = QUOTES) -> dict:
    els = client.get("/api/contract/ipc415_property").json()["elements"]
    maps = [{"element": e, "fact_id": f, "quote": q, "confirmed": c} for e, (f, q), c in zip(els, quotes, confirmed, strict=True)]
    r = client.post("/api/check", json={"contract_id": "ipc415_property", "reviewer": "t", "mappings": maps})
    assert r.status_code == 200, r.text
    return r.json()


def test_complete_confirmed_mapping_is_grounded(client: TestClient) -> None:
    d = _check(client, [True, True, True])
    assert d["lean"] == {"verdict": "grounded", "denied_claims": [], "unlicensed_claims": [], "omitted_claims": []}
    assert d["cross_check_agrees"] and all(r["asserted_met"] for r in d["rows"])


def test_an_unconfirmed_ingredient_is_never_sent_and_lean_names_it(client: TestClient) -> None:
    d = _check(client, [True, False, True])
    assert d["lean"]["verdict"] == "flagged"
    assert len(d["lean"]["omitted_claims"]) == 1 and "induces the deceived person" in d["lean"]["omitted_claims"][0]
    assert "induces" not in d["wire"]


def test_a_confirmed_quote_not_in_the_fact_is_not_sent(client: TestClient) -> None:
    bad = [*QUOTES[:2], ("F4", "after receiving the goods in full")]
    d = _check(client, [True, True, True], bad)
    assert d["rows"][2]["quote_check"]["reason"] == "quote_not_found" and not d["rows"][2]["asserted_met"]
    assert d["lean"]["verdict"] == "flagged"
