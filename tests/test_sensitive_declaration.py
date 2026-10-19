"""#558: no A4 or A8 draft without the lawyer's declaration; both routes; no model call, no read, nothing written on a refusal."""

from __future__ import annotations

import hashlib

import pytest

from pravrudhi.api import identity, roles
from pravrudhi.application import bail_draft_input as I  # noqa: E741
from pravrudhi.application.bail_draft_config import BailDraftConfig
from tests.test_bail_draft_routes import BODY as BAIL_BODY
from tests.test_bail_draft_routes import PATH as BAIL_PATH
from tests.test_bail_draft_routes import _client as bail_client
from tests.test_bail_draft_routes import _flags as bail_flags


@pytest.fixture(autouse=True)
def _restore_process_wide_lists(monkeypatch: pytest.MonkeyPatch) -> None:
    """Building an app adds routes to the gate and role lists; restore them after each test so no other test file sees them."""
    monkeypatch.setattr(identity, "PARTNER_KEY_ROUTES", identity.PARTNER_KEY_ROUTES)
    monkeypatch.setattr(identity, "PARTNER_KEY_PATHS", identity.PARTNER_KEY_PATHS)
    monkeypatch.setattr(roles, "USER_FACING", roles.USER_FACING)


def test_the_refusal_line_is_r1s_exactly_and_the_values_are_closed():
    assert hashlib.sha256(I.DECLARATION_REFUSAL.encode()).hexdigest()[:16] == "4c30a82379c58f15"
    assert I.DECLARATION_REQUIRED == "sensitive_declaration_required" and I.DECLARATION_VALUES == ("none", "sensitive")
    for ok in ("none", "sensitive"):
        assert I.check_declaration(ok) == ok
    for bad in (None, "", "None", "NONE", "yes", "no", True, False, 0, 1, [], {}, "none "):
        with pytest.raises(ValueError):
            I.check_declaration(bad)


@pytest.mark.parametrize("value", [None, "", "yes", "NONE", True, 1, "unknown"])
def test_the_bail_route_refuses_without_a_valid_declaration_before_anything_else(tmp_path, monkeypatch, value):
    stub = bail_flags(monkeypatch, BailDraftConfig())
    c = bail_client(tmp_path)
    body = {k: v for k, v in BAIL_BODY.items() if k != "sensitive_declaration"}
    if value is not None:
        body["sensitive_declaration"] = value
    r = c.post(BAIL_PATH, json={**body, "consent": False})  # consent is missing too: the declaration is checked FIRST
    assert r.status_code == 422 and r.json()["error"] == "sensitive_declaration_required"
    assert r.json()["detail"] == I.DECLARATION_REFUSAL
    assert stub.calls == []  # no drafter, no model call


def test_the_bail_route_declared_sensitive_is_the_referral_and_none_goes_on(tmp_path, monkeypatch):
    stub = bail_flags(monkeypatch, BailDraftConfig())
    c = bail_client(tmp_path)
    r = c.post(BAIL_PATH, json={**BAIL_BODY, "sensitive_declaration": "sensitive"})
    j = r.json()
    assert r.status_code == 200 and j["state"] == "referred" and j["referral"]["code"] == "sensitive_matter"
    assert j["draft"] is None and stub.calls == []
    ok = c.post(BAIL_PATH, json={**BAIL_BODY, "sensitive_declaration": "none"})
    assert ok.status_code == 200 and ok.json()["state"] != "referred"
    declared = {**BAIL_BODY, "sensitive_declaration": "none"}
    tagged = c.post(BAIL_PATH, json={**declared, "tags": ["pocso"]})  # "none" never overrides a hit
    assert tagged.json()["state"] == "referred"
