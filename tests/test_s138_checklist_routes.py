"""B3 s.138 checklist: the route (POST /api/v1/s138-checklist), registered only when the flag is on.

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
from pravrudhi.application import s138_checklist_config as scc
from pravrudhi.application.s138_checklist_config import S138ChecklistConfig
from tests.flag_peek_util import peek_all_true
from tests.test_api_partner import _agent, _proof_script

BODY = {
    "cheque_date": {"value": "01.02.2013", "source": "typed", "confirmed": True, "confirmed_as": "2013-02-01"},
    "validity_months": 3,
    "presentations": [{"value": "10.03.2013", "source": "typed", "confirmed": True, "confirmed_as": "2013-03-10"}],
    "return_memos": [{"value": "12.03.2013", "source": "typed", "confirmed": True, "confirmed_as": "2013-03-12"}],
    "information_received": [{"value": "12.03.2013", "source": "typed", "confirmed": True, "confirmed_as": "2013-03-12"}],
    "notices": [{"value": "20.03.2013", "source": "typed", "confirmed": True, "confirmed_as": "2013-03-20"}],
    "notice_receipts": [{"value": "21.03.2013", "source": "typed", "confirmed": True, "confirmed_as": "2013-03-21"}],
}
USER = identity.User("u1", "user@example.test", "user")
CFG = PartnerApiConfig(rate_limit_per_minute=1000, max_concurrent=1, trust_proxy_header=False)
PATH = "/api/v1/s138-checklist"
REPO = Path(__file__).resolve().parent.parent
MODULE = "pravrudhi.application.s138_checklist"


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PRAVRUDHI_CITATION_INDEX", raising=False)
    # the gate lists are rebound while a route exists: restore them after every test
    monkeypatch.setattr(identity, "PARTNER_KEY_ROUTES", identity.PARTNER_KEY_ROUTES)
    monkeypatch.setattr(identity, "PARTNER_KEY_PATHS", identity.PARTNER_KEY_PATHS)
    monkeypatch.setattr(roles, "USER_FACING", roles.USER_FACING)


def _on(monkeypatch: pytest.MonkeyPatch, cfg: S138ChecklistConfig | None = None) -> None:
    monkeypatch.setattr(scc, "load_s138_checklist_config", lambda _root: cfg or S138ChecklistConfig())
    peek_all_true(monkeypatch)


def _client(tmp_path: Path, *, user: Any = USER, cfg: PartnerApiConfig = CFG) -> TestClient:
    app = FastAPI()
    agent = _agent(tmp_path, _proof_script())
    app.include_router(build_partner_router(tmp_path, agent_factory=lambda _r: agent, config=cfg))
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
    assert c.post(PATH, json=BODY).status_code == 404
    assert MODULE not in sys.modules, "the checklist must not be imported while the flag is off"


def test_with_the_flag_off_the_gate_lists_are_the_released_ones(tmp_path: Path) -> None:
    before = (identity.PARTNER_KEY_ROUTES, identity.PARTNER_KEY_PATHS)
    _client(tmp_path)
    assert before == (identity.PARTNER_KEY_ROUTES, identity.PARTNER_KEY_PATHS)
    assert ("POST", PATH) not in identity.PARTNER_KEY_ROUTES and PATH not in identity.PARTNER_KEY_PATHS


def _break_module(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make `from pravrudhi.application import s138_checklist` raise ImportError (the module entry and the package attribute)."""
    import pravrudhi.application as pkg

    monkeypatch.setitem(sys.modules, MODULE, None)
    monkeypatch.delattr(pkg, "s138_checklist", raising=False)


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
    assert scc.load_s138_checklist_config(REPO) is None
    text = (REPO / "configs" / "partner_api.yaml").read_text()
    assert "wf_b3_s138_checklist" not in text or "wf_b3_s138_checklist: false" in text


# ---- sign-in and the answers ----------------------------------------------------------------------------------------------


def test_an_anonymous_caller_is_told_the_checklist_is_for_signed_in_users(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _on(monkeypatch)
    r = _client(tmp_path, user=None).post(PATH, json=BODY)
    assert r.status_code == 401 and "signed-in" in r.json()["detail"]


def test_a_signed_in_caller_gets_the_checklist_with_the_fixed_lines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    r = _client(tmp_path).post(PATH, json=BODY)
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["run_id"].startswith("s138-checklist-") and b["statement"] == "A checklist, not a decision"
    assert [s["step"] for s in b["steps"]] == ["presentation", "notice", "payment", "complaint_window"]
    assert [s["status"] for s in b["steps"]] == ["INSIDE", "INSIDE", "COMPUTED", "REFER"]
    assert (
        len(b["not_assessed"]) == 8
        and b["lawyer_confirmed"] is False
        and b["description"].startswith("Built and tested on court judgment recitals only")
    )


def test_an_unconfirmed_date_comes_back_refer_through_the_route(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    body = {**BODY, "notices": [{"value": "20.03.2013", "source": "typed", "confirmed": False}]}
    b = _client(tmp_path).post(PATH, json=body).json()
    assert [s["status"] for s in b["steps"]][:2] == ["INSIDE", "REFER"]


@pytest.mark.parametrize(
    "body",
    [
        {"text": "The cheque dated 1.2.2013"},  # no free text: nothing picks "the first date"
        {"notices": [{"value": "1.1.2013"}]},  # a source is required
        {"notices": [{"value": "1.1.2013", "source": "x", "confirmed": "yes"}]},
        {"validity_months": 0},
        {"validity_months": "3"},
        {"notice_mode": "pigeon"},
        {"notices": "1.1.2013"},
    ],
)
def test_a_bad_body_is_422_and_nothing_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: dict[str, Any]) -> None:
    _on(monkeypatch)
    assert _client(tmp_path).post(PATH, json=body).status_code == 422


def test_an_empty_body_is_all_refer_not_an_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    b = _client(tmp_path).post(PATH, json={}).json()
    assert [s["status"] for s in b["steps"]] == ["REFER"] * 4


def test_too_many_dates_for_one_step_is_413_with_a_stable_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch, S138ChecklistConfig(max_dates_per_role=2))
    body = {"notices": [{"value": "1.1.2013", "source": "x", "confirmed": True}] * 3}
    r = _client(tmp_path).post(PATH, json=body)
    assert r.status_code == 413 and r.json()["error"] == "too_many_dates" and set(r.json()) == {"detail", "error"}


# ---- the route's own budgets -----------------------------------------------------------------------------------------------


def test_the_route_has_its_own_rate_budget_and_spends_none_of_intakes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    cfg = PartnerApiConfig(rate_limit_per_minute=2, max_concurrent=1, trust_proxy_header=False)
    c = _client(tmp_path, cfg=cfg)
    assert [c.post(PATH, json=BODY).status_code for _ in range(3)] == [200, 200, 429]
    assert [c.post("/api/v1/intake", json={"text": "1. A fact."}).status_code for _ in range(2)] == [200, 200]


def test_exhausting_intake_does_not_spend_the_checklists_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    cfg = PartnerApiConfig(rate_limit_per_minute=2, max_concurrent=1, trust_proxy_header=False)
    c = _client(tmp_path, cfg=cfg)
    assert [c.post("/api/v1/intake", json={"text": "1. A fact."}).status_code for _ in range(3)] == [200, 200, 429]
    assert c.post(PATH, json=BODY).status_code == 200


def test_the_route_has_its_own_slots_and_a_full_slot_is_a_coded_503(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch, S138ChecklistConfig(max_concurrent=1))
    from pravrudhi.application import s138_checklist as B

    gate, released = threading.Event(), threading.Event()
    real = B.check

    def slow(*a: Any, **k: Any) -> Any:
        gate.set()
        released.wait(5)
        return real(*a, **k)

    monkeypatch.setattr(B, "check", slow)
    c = _client(tmp_path)
    results: list[int] = []
    t = threading.Thread(target=lambda: results.append(c.post(PATH, json=BODY).status_code))
    t.start()
    assert gate.wait(5)
    second = c.post(PATH, json=BODY)
    assert second.status_code == 503 and second.json().get("error") == "agent_at_capacity"
    assert c.post("/api/v1/intake", json={"text": "1. A fact."}).status_code == 200  # an existing route's slot is untouched
    released.set()
    t.join(10)
    assert results == [200]


def test_the_checklist_still_runs_while_intake_holds_its_only_slot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pravrudhi.application.a0_assist import intake

    _on(monkeypatch, S138ChecklistConfig(max_concurrent=1))
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
    assert c.post(PATH, json=BODY).status_code == 200
    released.set()
    t.join(10)


def test_a_second_request_runs_after_the_slot_is_released(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch, S138ChecklistConfig(max_concurrent=1))
    c = _client(tmp_path)
    assert [c.post(PATH, json=BODY).status_code for _ in range(3)] == [200, 200, 200]


# ---- keys, the whole-surface gate and the audit row -------------------------------------------------------------------------


def _keyed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, cfg: S138ChecklistConfig | None = None
) -> tuple[TestClient, str, Path]:
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


def test_a_key_only_caller_reaches_the_route_and_leaves_one_audit_row_without_the_dates(keyed: Any) -> None:
    c, secret, root = keyed
    r = c.post(PATH, json=BODY, headers={tenancy.API_KEY_HEADER: secret})
    assert r.status_code == 200, r.text
    rows = _rows(root)
    assert len(rows) == 1 and rows[0]["status_code"] == 200 and rows[0]["mode"] == "intake"
    assert rows[0]["run_id"].startswith("s138-checklist-")
    # never what the user typed (the bare year is not asserted: it can sit in a timestamp or a run id)
    items = [d for v in BODY.values() for d in (v if isinstance(v, list) else [v]) if isinstance(d, dict)]
    typed = {str(d[k]) for d in items for k in ("value", "confirmed_as")}
    assert typed and not any(t in str(rows) for t in typed)


def test_a_refused_request_leaves_an_audit_row_with_the_checklist_run_id(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    c, secret, root = _keyed(monkeypatch, tmp_path, S138ChecklistConfig(max_dates_per_role=1))
    body = {"notices": [{"value": "1.1.2013", "source": "x", "confirmed": True}] * 2}
    r = c.post(PATH, json=body, headers={tenancy.API_KEY_HEADER: secret})
    assert r.status_code == 413
    rows = _rows(root)
    assert len(rows) == 1 and rows[0]["status_code"] == 413 and rows[0]["run_id"].startswith("s138-checklist-")


def test_a_failure_inside_the_checklist_leaves_a_500_audit_row(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from pravrudhi.application import s138_checklist as B

    def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("internal")

    c, secret, root = _keyed(monkeypatch, tmp_path)
    monkeypatch.setattr(B, "check", boom)
    r = c.post(PATH, json=BODY, headers={tenancy.API_KEY_HEADER: secret})
    assert r.status_code == 500
    rows = _rows(root)
    assert len(rows) == 1 and rows[0]["status_code"] == 500 and rows[0]["run_id"].startswith("s138-checklist-")


def test_a_full_slot_leaves_a_503_audit_row_with_the_checklist_run_id(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from pravrudhi.application import s138_checklist as B

    gate, released = threading.Event(), threading.Event()
    real = B.check

    def slow(*a: Any, **k: Any) -> Any:
        gate.set()
        released.wait(5)
        return real(*a, **k)

    c, secret, root = _keyed(monkeypatch, tmp_path, S138ChecklistConfig(max_concurrent=1))
    monkeypatch.setattr(B, "check", slow)
    hdr = {tenancy.API_KEY_HEADER: secret}
    t = threading.Thread(target=lambda: c.post(PATH, json=BODY, headers=hdr))
    t.start()
    assert gate.wait(5)
    r = c.post(PATH, json=BODY, headers=hdr)
    released.set()
    t.join(10)
    assert r.status_code == 503
    rows = _rows(root)
    assert sorted(x["status_code"] for x in rows) == [200, 503]
    assert all(x["run_id"].startswith("s138-checklist-") for x in rows) and len({x["run_id"] for x in rows}) == 2


def test_the_gate_refuses_the_route_without_a_credential_and_a_bad_key_is_401(keyed: Any) -> None:
    c, _secret, _root = keyed
    r = c.post(PATH, json=BODY)
    assert r.status_code == 401 and r.json()["detail"] == "Missing bearer token"
    r2 = c.post(PATH, json=BODY, headers={tenancy.API_KEY_HEADER: "pnk_aaaaaaaaaaaaaaaa_notthesecret"})
    assert r2.status_code == 401 and r2.json()["detail"] == "Invalid or revoked API key"


def test_a_valid_key_does_not_excuse_a_bad_identity_token(keyed: Any) -> None:
    c, secret, _root = keyed
    r = c.post(PATH, json=BODY, headers={tenancy.API_KEY_HEADER: secret, "Authorization": "Bearer not-a-token"})
    assert r.status_code == 401 and "Invalid token" in r.json()["detail"]


def test_the_error_shape_carries_no_path_and_no_stack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch, S138ChecklistConfig(max_dates_per_role=1))
    body = {"notices": [{"value": "1.1.2013", "source": "x", "confirmed": True}] * 2}
    r = _client(tmp_path).post(PATH, json=body)
    assert set(r.json()) == {"detail", "error"} and "/" not in r.json()["detail"] and "Traceback" not in r.text


def test_no_answer_through_the_route_says_barred_premature_invalid_or_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _on(monkeypatch)
    c = _client(tmp_path)
    late = {**BODY, "notices": [{"value": "20.04.2013", "source": "typed", "confirmed": True, "confirmed_as": "2013-04-20"}]}
    for body in (BODY, late, {}):
        low = c.post(PATH, json=body).text.lower()
        assert not any(w in low for w in ("barred", "premature", "invalid", "fatal", "within limitation"))


def test_a_confirmation_without_the_reading_and_an_impossible_order_are_refer_through_the_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _on(monkeypatch)
    c = _client(tmp_path)
    blind = {**BODY, "notices": [{"value": "20.03.2013", "source": "typed", "confirmed": True}]}  # no confirmed_as
    b = c.post(PATH, json=blind).json()
    assert [s["status"] for s in b["steps"]][:2] == ["INSIDE", "REFER"] and "confirmed_as" in b["steps"][1]["result"]
    wrong = {**BODY, "notices": [{"value": "03/04/2015", "source": "typed", "confirmed": True, "confirmed_as": "2015-03-04"}]}
    assert c.post(PATH, json=wrong).json()["steps"][1]["status"] == "REFER"
    iso = {**BODY, "notices": [{"value": "2013-03-20", "source": "typed", "confirmed": True, "confirmed_as": "2013-03-20"}]}
    assert c.post(PATH, json=iso).json()["steps"][1]["status"] == "INSIDE"  # an ISO date is read
    swapped = {
        **BODY,
        "return_memos": [{"value": "10.03.2013", "source": "typed", "confirmed": True, "confirmed_as": "2013-03-10"}],
    }
    swapped["presentations"] = [{"value": "20.03.2013", "source": "typed", "confirmed": True, "confirmed_as": "2013-03-20"}]
    out = c.post(PATH, json=swapped).json()
    assert all(s["status"] == "REFER" for s in out["steps"][:3])
    assert "not in the order the steps follow" in out["steps"][1]["result"]
