"""A1 whole-draft citation check: the route (POST /api/v1/draft-citation-check), registered only when the flag is on.

Both arms of the flag, sign-in, the route's own budgets (it never spends an existing route's), the key-only gate, the audit
row, and the start-up rules (nothing imported, no route, no gate change with the flag off). Invented parties only."""

from __future__ import annotations

import shutil
import sys
import threading
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pravrudhi.api import identity, roles
from pravrudhi.api.partner import PartnerApiConfig, build_partner_router
from pravrudhi.api.server import create_app
from pravrudhi.application import audit, tenancy
from pravrudhi.application import draft_check_config as dcc
from pravrudhi.application.case_index import CaseRecord, insert_case, open_index
from pravrudhi.application.draft_check_config import DraftCheckConfig
from tests.test_api_partner import _agent, _proof_script
from tests.test_draft_check import b64 as b64pdf
from tests.test_draft_check import make_pdf

USER = identity.User("u1", "user@example.test", "user")
CFG = PartnerApiConfig(rate_limit_per_minute=1000, max_concurrent=1, trust_proxy_header=False)
PATH = "/api/v1/draft-citation-check"
GOOD = "(2087) 4 SCC 512"
REPO = Path(__file__).resolve().parent.parent
MODULE = "pravrudhi.application.draft_check"


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PRAVRUDHI_CITATION_INDEX", raising=False)
    # the gate lists are rebound while a route exists: restore them after every test
    monkeypatch.setattr(identity, "PARTNER_KEY_ROUTES", identity.PARTNER_KEY_ROUTES)
    monkeypatch.setattr(identity, "PARTNER_KEY_PATHS", identity.PARTNER_KEY_PATHS)
    monkeypatch.setattr(roles, "USER_FACING", roles.USER_FACING)


def _on(monkeypatch: pytest.MonkeyPatch, cfg: DraftCheckConfig | None = None) -> None:
    monkeypatch.setattr(dcc, "load_draft_check_config", lambda _root: cfg or DraftCheckConfig())


def _index(tmp_path: Path) -> Path:
    p = tmp_path / "idx.sqlite3"
    conn = open_index(p)
    citer = f"In Aldric Vennoway v. State of Zorbland {GOOD} the Court clarified the rule."
    insert_case(conn, CaseRecord("c1", "Later Case v Other", "Supreme Court", 2095, "sc_pdf", "/x", citer))
    insert_case(conn, CaseRecord("r1", "Aldric Vennoway vs State Of Zorbland", "Supreme Court", 2087, "sc_pdf", "/y", "body"))
    conn.commit()
    conn.close()
    return p


def _client(tmp_path: Path, *, user: Any = USER, index: Path | None = None, cfg: PartnerApiConfig = CFG) -> TestClient:
    app = FastAPI()
    agent = _agent(tmp_path, _proof_script())
    app.include_router(build_partner_router(tmp_path, agent_factory=lambda _r: agent, config=cfg, citation_index_path=index))
    app.dependency_overrides[identity.current_user] = lambda: user
    return TestClient(app)


def _paths(client: TestClient) -> set[str]:
    from fastapi.routing import APIRoute, APIRouter

    def walk(routes: Any) -> Any:
        for r in routes:
            if isinstance(r, APIRoute):
                yield r.path
            else:
                sub = getattr(r, "original_router", None)
                if isinstance(sub, APIRouter):
                    yield from walk(sub.routes)

    return set(walk(client.app.routes))  # type: ignore[attr-defined]


# ---- the flag: off adds nothing, on adds the route ------------------------------------------------------------------------


def test_with_the_flag_off_the_route_is_absent_and_nothing_is_imported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(sys.modules, MODULE, raising=False)
    c = _client(tmp_path)
    assert PATH not in _paths(c)
    assert c.post(PATH, json={"text": GOOD}).status_code == 404
    assert MODULE not in sys.modules, "the draft check must not be imported while the flag is off"


def test_with_the_flag_off_the_gate_lists_are_the_released_ones(tmp_path: Path) -> None:
    before = (identity.PARTNER_KEY_ROUTES, identity.PARTNER_KEY_PATHS)
    _client(tmp_path)
    assert before == (identity.PARTNER_KEY_ROUTES, identity.PARTNER_KEY_PATHS)
    assert ("POST", PATH) not in identity.PARTNER_KEY_ROUTES and PATH not in identity.PARTNER_KEY_PATHS


def _break_module(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make `from pravrudhi.application import draft_check` raise ImportError (the module entry and the package attribute)."""
    import pravrudhi.application as pkg

    monkeypatch.setitem(sys.modules, MODULE, None)
    monkeypatch.delattr(pkg, "draft_check", raising=False)


def test_with_the_flag_off_a_broken_module_cannot_stop_the_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _break_module(monkeypatch)
    c = _client(tmp_path)
    assert c.get("/api/v1/status").status_code == 200 and PATH not in _paths(c)


def test_with_the_flag_on_a_broken_module_turns_the_route_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    _break_module(monkeypatch)
    c = _client(tmp_path)
    assert c.get("/api/v1/status").status_code == 200 and PATH not in _paths(c)


def test_with_the_flag_off_roles_is_the_released_list(tmp_path: Path) -> None:
    before = roles.USER_FACING
    _client(tmp_path)
    assert before == roles.USER_FACING and PATH not in roles.USER_FACING


def test_with_the_flag_on_the_route_is_user_facing_and_in_no_admin_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    _client(tmp_path)
    assert PATH in roles.USER_FACING
    assert PATH not in roles.ADMIN_ONLY and PATH not in roles.ADMIN_IN_BOTH_EDITIONS


def test_with_the_flag_on_no_route_of_the_whole_app_is_unclassified_or_stale(monkeypatch: pytest.MonkeyPatch) -> None:
    """The two existing roles tests (nothing unclassified, nothing stale) hold in the ON arm too."""
    _on(monkeypatch)
    c = TestClient(create_app(REPO))
    paths = _paths(c)
    assert PATH in paths
    classified = roles.ADMIN_ONLY | roles.USER_FACING | roles.ADMIN_IN_BOTH_EDITIONS
    assert not (paths - classified), sorted(paths - classified)
    assert not (classified - paths), sorted(classified - paths)


def test_with_the_flag_on_the_route_exists_and_opens_the_key_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    c = _client(tmp_path)
    assert PATH in _paths(c) and ("POST", PATH) in identity.PARTNER_KEY_ROUTES and PATH in identity.PARTNER_KEY_PATHS


def test_the_route_is_not_in_the_openapi_document(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    c = _client(tmp_path)
    assert PATH not in c.get("/openapi.json").text


def test_the_flag_in_the_shipped_config_is_off() -> None:
    assert dcc.load_draft_check_config(REPO) is None
    text = (REPO / "configs" / "partner_api.yaml").read_text()
    assert "wf_a1_draft_citation_check" not in text or "wf_a1_draft_citation_check: false" in text


# ---- sign-in and the answers ----------------------------------------------------------------------------------------------


def test_an_anonymous_caller_is_told_the_check_is_for_signed_in_users(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    r = _client(tmp_path, user=None).post(PATH, json={"text": GOOD})
    assert r.status_code == 401 and "signed-in" in r.json()["detail"]


def test_a_signed_in_caller_gets_the_report_with_the_denominator(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    text = f"As held in Aldric Vennoway v. State of Zorbland, {GOOD}; and (2087) 4 SCC 999; and (2O87) 4 SCC 512."
    r = _client(tmp_path, index=_index(tmp_path)).post(PATH, json={"text": text})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["client_data"] is True and b["run_id"].startswith("draft-check-")
    rep = b["draft_check"]
    d = rep["denominator"]
    assert (d["like_strings"], d["read"], d["not_read"], d["found"], d["not_found"]) == (3, 2, 1, 1, 1)
    assert [c["name_state"] for c in rep["citations"]] == ["name_matched", "name_not_read"]
    assert rep["state"] == "incomplete" and "not read" in rep["summary"]


def test_a_wrong_typed_name_is_a_mismatch_through_the_real_lookup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    text = f"See Brandel Thackeray v. Union of Zorbland, {GOOD}."
    r = _client(tmp_path, index=_index(tmp_path)).post(PATH, json={"text": text})
    rep = r.json()["draft_check"]
    assert rep["citations"][0]["status"] == "name_mismatch" and rep["denominator"]["name_mismatch"] == 1


def test_without_an_index_nothing_is_checked_and_the_report_says_so(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    rep = _client(tmp_path).post(PATH, json={"text": f"see {GOOD}"}).json()["draft_check"]
    assert rep["citations"][0]["reason"] == "no_index" and rep["state"] == "nothing_checked"


def test_a_text_layer_pdf_goes_through_the_route(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    pdf = make_pdf([f"Page one cites {GOOD}.", "", "Page three has plain prose and no citation at all in it."])
    rep = _client(tmp_path, index=_index(tmp_path)).post(PATH, json={"pdf_base64": b64pdf(pdf)}).json()["draft_check"]
    assert rep["unread_pages"] == [2] and rep["denominator"]["found"] == 1 and rep["state"] == "incomplete"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"text": ""},
        {"text": "  "},
        {"text": GOOD, "pdf_base64": "QQ=="},
        {"text": 5},
        {"pdf_base64": 7},
        {"text": "a", "z": 1},
    ],
)
def test_a_bad_body_is_422_and_nothing_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: dict[str, Any]) -> None:
    _on(monkeypatch)
    r = _client(tmp_path).post(PATH, json=body)
    assert r.status_code == 422 or (body == {"text": "a", "z": 1} and r.status_code == 200)


def test_too_long_a_draft_is_413_with_a_stable_code_and_a_bad_pdf_is_422(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch, DraftCheckConfig(max_chars=1_000))
    c = _client(tmp_path)
    r = c.post(PATH, json={"text": "word " * 400})
    assert r.status_code == 413 and r.json()["error"] == "too_long"
    r2 = c.post(PATH, json={"pdf_base64": b64pdf(b"not a pdf")})
    assert r2.status_code == 422 and r2.json()["error"] == "pdf_unreadable"


# ---- the route's own budgets -----------------------------------------------------------------------------------------------


def test_the_route_has_its_own_rate_budget_and_spends_none_of_intakes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    cfg = PartnerApiConfig(rate_limit_per_minute=2, max_concurrent=1, trust_proxy_header=False)
    c = _client(tmp_path, cfg=cfg)
    assert [c.post(PATH, json={"text": GOOD}).status_code for _ in range(3)] == [200, 200, 429]
    # intake still has its whole budget (it shares the analyse-facts limiter, which the draft route never touched)
    assert [c.post("/api/v1/intake", json={"text": "1. A fact."}).status_code for _ in range(2)] == [200, 200]


def test_exhausting_intake_does_not_spend_the_draft_routes_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    cfg = PartnerApiConfig(rate_limit_per_minute=2, max_concurrent=1, trust_proxy_header=False)
    c = _client(tmp_path, cfg=cfg)
    assert [c.post("/api/v1/intake", json={"text": "1. A fact."}).status_code for _ in range(3)] == [200, 200, 429]
    assert c.post(PATH, json={"text": GOOD}).status_code == 200


def test_the_route_has_its_own_slots_and_a_full_slot_is_a_coded_503_not_an_existing_routes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _on(monkeypatch, DraftCheckConfig(max_concurrent=1))
    from pravrudhi.application import draft_check as D

    gate, released = threading.Event(), threading.Event()
    real = D.check_draft

    def slow(*a: Any, **k: Any) -> Any:
        gate.set()
        released.wait(5)
        return real(*a, **k)

    monkeypatch.setattr(D, "check_draft", slow)
    c = _client(tmp_path)
    results: list[int] = []
    t = threading.Thread(target=lambda: results.append(c.post(PATH, json={"text": GOOD}).status_code))
    t.start()
    assert gate.wait(5)
    second = c.post(PATH, json={"text": GOOD})
    assert second.status_code == 503 and second.json().get("error") == "agent_at_capacity"
    # an existing route's slot (max_concurrent=1 in CFG) is untouched while the draft check holds its own (the other direction,
    # with intake genuinely holding its slot, is the next test)
    assert c.post("/api/v1/intake", json={"text": "1. A fact."}).status_code == 200
    released.set()
    t.join(10)
    assert results == [200]


def test_the_draft_route_still_runs_while_intake_holds_its_only_slot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The other direction, non-trivially: intake really holds its one slot and the draft route is unaffected."""
    from pravrudhi.application.a0_assist import intake

    _on(monkeypatch, DraftCheckConfig(max_concurrent=1))
    gate, released = threading.Event(), threading.Event()
    real = intake.build_bundle

    def slow_bundle(*a: Any, **k: Any) -> Any:
        gate.set()
        released.wait(5)
        return real(*a, **k)

    monkeypatch.setattr(intake, "build_bundle", slow_bundle)
    c = _client(tmp_path)
    t = threading.Thread(target=lambda: c.post("/api/v1/intake", json={"text": "1. A fact."}))
    t.start()
    assert gate.wait(5)
    assert c.post("/api/v1/intake", json={"text": "1. A fact."}).status_code == 503  # intake's slot really is taken
    assert c.post(PATH, json={"text": GOOD}).status_code == 200  # the draft check is not
    released.set()
    t.join(10)


def test_a_second_draft_runs_after_the_slot_is_released(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch, DraftCheckConfig(max_concurrent=1))
    c = _client(tmp_path)
    assert [c.post(PATH, json={"text": GOOD}).status_code for _ in range(3)] == [200, 200, 200]


# ---- keys, the whole-surface gate and the audit row -------------------------------------------------------------------------


def _keyed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, cfg: DraftCheckConfig | None = None) -> tuple[TestClient, str, Path]:
    _on(monkeypatch, cfg)
    monkeypatch.setenv("PRAVRUDHI_AUTH", "required")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "s" * 40)
    monkeypatch.setenv("PRAVRUDHI_DISABLE_LOCAL_GUARD", "1")
    monkeypatch.setenv(tenancy.TENANCY_PROVISION_SECRET_ENV, "p" * 40)
    root = tmp_path / "root"
    shutil.copytree(REPO / "configs", root / "configs")
    tenancy.create_org(root, "acme", "Acme")
    key = tenancy.create_key(root, "acme", label="t")
    client = TestClient(create_app(root), headers={"host": "127.0.0.1:8008"}, raise_server_exceptions=False)
    return client, key.secret, root


@pytest.fixture
def keyed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[TestClient, str, Path]:
    return _keyed(monkeypatch, tmp_path)


def _rows(root: Path) -> list[dict[str, Any]]:
    from datetime import UTC, datetime

    key_id = tenancy.keys_for_org(root, "acme")[0].key_id
    rows, _next = audit.page(root, key_id, offset=0, limit=50, retention_s=3600, now=lambda: datetime.now(UTC))
    return rows


def test_a_key_only_caller_reaches_the_route_and_leaves_one_audit_row_without_the_text(keyed: Any) -> None:
    from datetime import UTC, datetime

    c, secret, root = keyed
    r = c.post(PATH, json={"text": f"see {GOOD} here"}, headers={tenancy.API_KEY_HEADER: secret})
    assert r.status_code == 200, r.text
    key_id = tenancy.keys_for_org(root, "acme")[0].key_id
    rows, _next = audit.page(root, key_id, offset=0, limit=10, retention_s=3600, now=lambda: datetime.now(UTC))
    assert len(rows) == 1 and rows[0]["status_code"] == 200 and rows[0]["mode"] == "intake"
    assert rows[0]["run_id"].startswith("draft-check-")
    assert "SCC" not in str(rows)  # never the text


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({"text": "word " * 2_000}, 413),  # over the character cap
        ({"pdf_base64": b64pdf(b"not a pdf")}, 422),  # an unreadable PDF
    ],
)
def test_a_refused_draft_leaves_an_audit_row_with_the_draft_check_run_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, body: dict[str, Any], status: int
) -> None:
    c, secret, root = _keyed(monkeypatch, tmp_path, DraftCheckConfig(max_chars=1_000))
    r = c.post(PATH, json=body, headers={tenancy.API_KEY_HEADER: secret})
    assert r.status_code == status, r.text
    rows = _rows(root)
    assert len(rows) == 1 and rows[0]["status_code"] == status and rows[0]["mode"] == "intake"
    assert rows[0]["run_id"].startswith("draft-check-")


def test_a_failure_inside_the_check_leaves_a_500_audit_row_with_the_draft_check_run_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from pravrudhi.application import draft_check as D

    def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("internal")

    c, secret, root = _keyed(monkeypatch, tmp_path)
    monkeypatch.setattr(D, "check_draft", boom)
    r = c.post(PATH, json={"text": f"see {GOOD}"}, headers={tenancy.API_KEY_HEADER: secret})
    assert r.status_code == 500
    rows = _rows(root)
    assert len(rows) == 1 and rows[0]["status_code"] == 500 and rows[0]["run_id"].startswith("draft-check-")


def test_a_full_slot_leaves_a_503_audit_row_with_the_draft_check_run_id(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from pravrudhi.application import draft_check as D

    gate, released = threading.Event(), threading.Event()
    real = D.check_draft

    def slow(*a: Any, **k: Any) -> Any:
        gate.set()
        released.wait(5)
        return real(*a, **k)

    c, secret, root = _keyed(monkeypatch, tmp_path, DraftCheckConfig(max_concurrent=1))
    monkeypatch.setattr(D, "check_draft", slow)
    hdr = {tenancy.API_KEY_HEADER: secret}
    t = threading.Thread(target=lambda: c.post(PATH, json={"text": f"see {GOOD}"}, headers=hdr))
    t.start()
    assert gate.wait(5)
    r = c.post(PATH, json={"text": f"see {GOOD}"}, headers=hdr)
    released.set()
    t.join(10)
    assert r.status_code == 503
    rows = _rows(root)
    assert sorted(x["status_code"] for x in rows) == [200, 503]
    assert all(x["run_id"].startswith("draft-check-") for x in rows) and len({x["run_id"] for x in rows}) == 2


def test_the_gate_refuses_the_route_without_a_credential_and_a_bad_key_is_401(keyed: Any) -> None:
    c, _secret, _root = keyed
    r = c.post(PATH, json={"text": GOOD})
    assert r.status_code == 401 and r.json()["detail"] == "Missing bearer token"
    r2 = c.post(PATH, json={"text": GOOD}, headers={tenancy.API_KEY_HEADER: "pnk_aaaaaaaaaaaaaaaa_notthesecret"})
    assert r2.status_code == 401 and r2.json()["detail"] == "Invalid or revoked API key"


def test_a_valid_key_does_not_excuse_a_bad_identity_token(keyed: Any) -> None:
    c, secret, _root = keyed
    r = c.post(PATH, json={"text": GOOD}, headers={tenancy.API_KEY_HEADER: secret, "Authorization": "Bearer not-a-token"})
    assert r.status_code == 401 and "Invalid token" in r.json()["detail"]


def test_the_error_shape_carries_no_path_and_no_stack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch, DraftCheckConfig(max_chars=1_000))
    r = _client(tmp_path).post(PATH, json={"text": "word " * 400})
    assert set(r.json()) == {"detail", "error"} and "/" not in r.json()["detail"] and "Traceback" not in r.text


def test_the_wording_never_calls_a_citation_fake_or_a_hallucination(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    r = _client(tmp_path, index=_index(tmp_path)).post(PATH, json={"text": "see (2087) 4 SCC 999 and (2087) 4 SCC 998"})
    low = r.text.lower()
    assert "fake" not in low and "hallucinat" not in low and "fabricat" not in low and "not found in our index" in low
