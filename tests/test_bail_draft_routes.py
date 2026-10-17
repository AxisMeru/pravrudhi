# ruff: noqa: E501
"""A4 bail drafting route (POST /api/v1/bail-draft), registered only when wf_a4_bail_drafting is on. The drafting module (Track A's `bail_draft`) is
replaced by a stub here: these tests pin the route, the referrals, the framing hand-off, the completer and the day ceiling; its own tests pin the
drafting rules. Invented facts only."""

from __future__ import annotations

import logging
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pravrudhi.api import identity, roles
from pravrudhi.api.partner import PartnerApiConfig, build_partner_router
from pravrudhi.api.server import create_app
from pravrudhi.application import audit, tenancy
from pravrudhi.application import bail_draft_config as bdc
from pravrudhi.application.bail_draft_config import BailDraftConfig
from tests.flag_peek_util import peek_all_true
from tests.test_api_partner import _agent, _proof_script

USER = identity.User("u1", "user@example.test", "user")
CFG = PartnerApiConfig(rate_limit_per_minute=1000, max_concurrent=1, trust_proxy_header=False)
REPO = Path(__file__).resolve().parent.parent
PATH = "/api/v1/bail-draft"
MODULES = ("pravrudhi.application.bail_draft_route", "pravrudhi.application.bail_draft_input")
OTHER_SIDE = "The informant-marker-zq says the applicant was at the shop."
BODY: dict[str, Any] = {
    "consent": True, "kind": "bail", "forum": "trial_court", "offence_date": "2024-08-01", "fir_date": "2024-08-02",
    "applicant": "Applicant-marker-zq", "facts": [
        {"text": "The applicant has no antecedents.", "side": "applicant", "grounds": ["antecedents"]},
        {"text": OTHER_SIDE, "side": "other"},
    ],
}


@dataclass(frozen=True)
class Completion:
    text: str
    input_tokens: int
    output_tokens: int
    model: str
    stop_reason: str = ""


class StubDrafter:
    """Stands in for Track A's `bail_draft`: records what it was handed and answers the documented dict shape."""

    Completion = Completion

    def __init__(self, cost: float = 0.01) -> None:
        self.calls: list[dict[str, Any]] = []
        self.cost = cost

    def draft(self, case: Any, framing: Any, complete: Any, cfg: Any, run_id: str) -> dict[str, Any]:
        self.calls.append({"case": case, "framing": framing, "complete": complete, "cfg": cfg, "run_id": run_id})
        return {"refused": None, "state": "drafted" if complete is not None else "template_only", "sections": [], "grounds": [], "notes_for_lawyer": [],
                "fill_ins": [], "checks": {}, "unresolved_authorities": [], "calls": 1 if complete else 0, "tokens_in": 0, "tokens_out": 0,
                "cost_usd": self.cost if complete else 0.0, "regenerated": False, "incomplete": False, "framing_lines": framing.lines, "run_id": run_id, "version": "stub"}


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(identity, "PARTNER_KEY_ROUTES", identity.PARTNER_KEY_ROUTES)
    monkeypatch.setattr(identity, "PARTNER_KEY_PATHS", identity.PARTNER_KEY_PATHS)
    monkeypatch.setattr(roles, "USER_FACING", roles.USER_FACING)


def _flags(monkeypatch: pytest.MonkeyPatch, cfg: BailDraftConfig | None = None) -> StubDrafter:
    monkeypatch.setattr(bdc, "load_bail_draft_config", lambda _root: cfg)
    peek_all_true(monkeypatch)
    stub = StubDrafter()
    if cfg is not None:
        from pravrudhi.application import bail_draft_route as R

        monkeypatch.setattr(R, "_drafter", lambda: stub)
    return stub


def _client(tmp_path: Path, *, user: Any = USER) -> TestClient:
    app = FastAPI()
    agent = _agent(tmp_path, _proof_script())
    app.include_router(build_partner_router(tmp_path, agent_factory=lambda _r: agent, config=CFG))
    app.dependency_overrides[identity.current_user] = lambda: user
    return TestClient(app, raise_server_exceptions=False)


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


def test_with_the_flag_off_the_route_is_absent_nothing_is_imported_and_the_lists_are_the_released_ones(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _flags(monkeypatch, None)
    for m in (*MODULES, "pravrudhi.application.bail_draft", "pravrudhi.application.bail_draft_input"):
        monkeypatch.delitem(sys.modules, m, raising=False)
    before = (identity.PARTNER_KEY_ROUTES, identity.PARTNER_KEY_PATHS, roles.USER_FACING)
    c = _client(tmp_path)
    assert PATH not in _paths(c) and c.post(PATH, json=BODY).status_code == 404
    assert not any(m in sys.modules for m in (*MODULES, "pravrudhi.application.bail_draft"))
    assert before == (identity.PARTNER_KEY_ROUTES, identity.PARTNER_KEY_PATHS, roles.USER_FACING)


def test_with_the_flag_off_the_asset_is_never_opened_and_the_path_answers_the_bytes_of_an_unknown_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    opened: list[str] = []
    real_open, real_read_text = builtins.open, Path.read_text

    def spy_open(file: Any, *a: Any, **k: Any) -> Any:
        if "assets/bail_draft" in str(file):
            opened.append(str(file))
        return real_open(file, *a, **k)

    def spy_read_text(self: Path, *a: Any, **k: Any) -> str:
        if "assets/bail_draft" in str(self):
            opened.append(str(self))
        return real_read_text(self, *a, **k)

    _flags(monkeypatch, None)
    monkeypatch.setattr(builtins, "open", spy_open)
    monkeypatch.setattr(Path, "read_text", spy_read_text)
    c = _client(tmp_path)
    off, unknown = c.post(PATH, json=BODY), c.post("/api/v1/no-such-route", json=BODY)
    assert opened == [] and off.status_code == unknown.status_code == 404 and off.content == unknown.content


def test_with_the_flag_on_the_route_exists_opens_the_gate_lists_and_is_not_in_openapi(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _flags(monkeypatch, BailDraftConfig())
    c = _client(tmp_path)
    assert PATH in _paths(c) and ("POST", PATH) in identity.PARTNER_KEY_ROUTES and PATH in identity.PARTNER_KEY_PATHS
    assert PATH in roles.USER_FACING and PATH not in roles.ADMIN_ONLY and PATH not in roles.ADMIN_IN_BOTH_EDITIONS
    assert PATH not in c.get("/openapi.json").text


def test_with_the_flag_on_no_route_of_the_whole_app_is_unclassified_or_stale(monkeypatch: pytest.MonkeyPatch) -> None:
    _flags(monkeypatch, BailDraftConfig())
    paths = _paths(TestClient(create_app(REPO)))
    classified = roles.ADMIN_ONLY | roles.USER_FACING | roles.ADMIN_IN_BOTH_EDITIONS
    assert PATH in paths and not (paths - classified) and not (classified - paths)


def test_an_anonymous_caller_is_told_it_is_for_signed_in_users(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _flags(monkeypatch, BailDraftConfig())
    r = _client(tmp_path, user=None).post(PATH, json=BODY)
    assert r.status_code == 401 and "signed-in" in r.json()["detail"] and stub.calls == []


def test_without_consent_the_answer_is_400_and_nothing_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _flags(monkeypatch, BailDraftConfig())
    r = _client(tmp_path).post(PATH, json={**BODY, "consent": False})
    assert r.status_code == 400 and r.json() == {"error": "consent_required"} and stub.calls == []


def test_a_sensitive_matter_is_referred_with_the_fixed_text_no_draft_and_no_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pravrudhi.application import bail_draft_input as I

    stub = _flags(monkeypatch, BailDraftConfig())
    r = _client(tmp_path).post(PATH, json={**BODY, "tags": ["pocso"]})
    b = r.json()
    assert r.status_code == 200 and b["state"] == "referred" and b["draft"] is None and stub.calls == []
    assert b["referral"] == {"code": "sensitive_matter", "text": I.REFER_SENSITIVE} and b["preview"] is True and b["label"] == I.PREVIEW_LABEL
    assert "marker-zq" not in r.text and b["run_id"].startswith("bail-")


def test_dates_on_both_sides_of_the_cutoff_refer_and_show_both_framings_without_a_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _flags(monkeypatch, BailDraftConfig())
    r = _client(tmp_path).post(PATH, json={**BODY, "offence_date": "2024-05-01"})
    b = r.json()
    assert r.status_code == 200 and b["state"] == "referred" and b["referral"]["code"] == "dates_straddle" and stub.calls == [] and b["draft"] is None
    assert b["framing"]["status"] == "straddle" and [o["act"] for o in b["framing"]["options"]] == ["CrPC", "BNSS"]
    assert b["framing"]["confirmed_by_lawyer"] is False and b["framing"]["unconfirmed_line"]


def test_a_single_framing_hands_the_drafter_applicant_side_facts_only_and_wraps_its_answer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pravrudhi.application import bail_draft_input as I

    stub = _flags(monkeypatch, BailDraftConfig())
    r = _client(tmp_path).post(PATH, json=BODY)
    b = r.json()
    assert r.status_code == 200 and len(stub.calls) == 1, r.text
    call = stub.calls[0]
    case, fr = call["case"], call["framing"]
    assert [(f.fact_id, f.text) for f in case.facts] == [("F1", "The applicant has no antecedents.")] and case.n_facts_excluded == 1
    assert case.drafting_kind == "regular" and case.parties == {"applicant": "Applicant-marker-zq"} and case.matter_id == call["run_id"] == b["run_id"]
    assert fr.status == "single" and fr.lines == ["Under Section 480 of the Bharatiya Nagarik Suraksha Sanhita, 2023 (BNSS)."]
    assert "informant-marker-zq" not in repr(call["case"]) and "informant-marker-zq" not in r.text  # the other-side fact never leaves the route
    assert b["label"] == I.PREVIEW_LABEL and b["framing"]["options"][0]["section"] == "480" and b["facts_set_aside"] == 1
    assert b["state"] == "template_only" and b["draft"]["framing_lines"] == fr.lines  # no endpoint configured: the template only (complete is None)
    assert call["complete"] is None and call["cfg"] == BailDraftConfig()


def test_the_completer_is_built_only_when_all_five_endpoint_values_and_the_key_exist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = BailDraftConfig(base_url="https://llm.example.test/v1", model="pinned-model-1", api_key_env="A4_TEST_KEY", usd_per_mtok_in=2.0, usd_per_mtok_out=10.0)
    stub = _flags(monkeypatch, cfg)
    c = _client(tmp_path)
    monkeypatch.delenv("A4_TEST_KEY", raising=False)
    assert c.post(PATH, json=BODY).json()["state"] == "template_only" and stub.calls[-1]["complete"] is None  # the key is missing: no call possible
    monkeypatch.setenv("A4_TEST_KEY", "k-not-printed")
    from pravrudhi.models import openai_compat as oc

    seen: dict[str, Any] = {}

    def fake_chat(self: Any, messages: list[dict[str, str]], **kw: Any) -> Any:
        seen.update(messages=messages, kw=kw, model=self.model, key=self.api_key, thinking=self.thinking)
        return oc.ChatResult(text="{}", model=self.model, prompt_tokens=11, completion_tokens=7, wall_s=0.0, finish_reason="stop")

    monkeypatch.setattr(oc.ChatClient, "chat", fake_chat)
    r = c.post(PATH, json=BODY)
    assert r.json()["state"] == "drafted" and callable(stub.calls[-1]["complete"])
    comp = stub.calls[-1]["complete"]("SYS", "USER", 2500)
    assert comp == Completion("{}", 11, 7, "pinned-model-1", "stop") and seen["kw"]["max_tokens"] == 2500 and seen["model"] == "pinned-model-1"
    assert [m["role"] for m in seen["messages"]] == ["system", "user"] and seen["thinking"] is None and "k-not-printed" not in r.text


def test_over_the_day_ceiling_the_next_draft_is_the_template_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = BailDraftConfig(base_url="https://llm.example.test/v1", model="m", api_key_env="A4_TEST_KEY", usd_per_mtok_in=1.0, usd_per_mtok_out=1.0, usd_per_day=0.05)
    stub = _flags(monkeypatch, cfg)
    stub.cost = 0.06  # one draft spends more than the day ceiling
    monkeypatch.setenv("A4_TEST_KEY", "k")
    c = _client(tmp_path)
    assert c.post(PATH, json=BODY).json()["state"] == "drafted"
    second = c.post(PATH, json=BODY).json()
    assert second["state"] == "template_only" and stub.calls[-1]["complete"] is None and len(stub.calls) == 2


def test_the_day_ceiling_rolls_over_at_the_utc_day() -> None:
    from pravrudhi.application.bail_draft_route import DayCeiling

    t = [datetime(2026, 10, 17, 23, 0, tzinfo=UTC).timestamp()]
    d = DayCeiling(clock=lambda: t[0])
    assert d.room(1.0)
    d.add(1.5)
    assert not d.room(1.0)
    t[0] += 3600  # past midnight UTC
    assert d.room(1.0)


@pytest.mark.parametrize(
    "patch",
    [{"offence_date": "2024-13-45"}, {"consent": "yes"}, {"kind": "other"}, {"facts": []}, {"extra_key": 1}, {"tags": ["x"]}, {"facts": [{"text": "a", "side": "applicant", "x": 1}]}],
)
def test_every_malformed_body_gets_the_one_fixed_422_and_nothing_of_the_request_is_echoed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, patch: dict[str, Any]) -> None:
    stub = _flags(monkeypatch, BailDraftConfig())
    r = _client(tmp_path).post(PATH, json={**BODY, **patch})
    assert r.status_code == 422 and r.json() == {"error": "bad_request"} and stub.calls == []
    assert "marker-zq" not in r.text and "2024-13-45" not in r.text


def test_a_body_that_is_not_json_gets_the_same_fixed_422(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _flags(monkeypatch, BailDraftConfig())
    r = _client(tmp_path).post(PATH, content=b"{not json", headers={"content-type": "application/json"})
    assert r.status_code == 422 and r.json() == {"error": "bad_request"}


def test_the_override_is_applied_to_no_other_route(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pravrudhi.application.bail_draft_route import FixedValidationRoute

    _flags(monkeypatch, BailDraftConfig())
    c = _client(tmp_path)
    from fastapi.routing import APIRoute, APIRouter

    kinds: dict[str, type] = {}

    def walk(routes: Any) -> None:
        for r in routes:
            if isinstance(r, APIRoute):
                kinds[r.path] = type(r)
            else:
                sub = getattr(r, "original_router", None)
                if isinstance(sub, APIRouter):
                    walk(sub.routes)

    walk(c.app.routes)  # type: ignore[attr-defined]
    assert kinds[PATH] is FixedValidationRoute and [p for p, k in kinds.items() if k is FixedValidationRoute and p != PATH] == []


def test_no_fact_applicant_or_date_reaches_a_log_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    _flags(monkeypatch, BailDraftConfig())
    caplog.set_level(logging.DEBUG)
    _client(tmp_path).post(PATH, json=BODY)
    _client(tmp_path).post(PATH, json={**BODY, "tags": ["pocso"]})
    assert "marker-zq" not in caplog.text and "2024-08-01" not in caplog.text and "no antecedents" not in caplog.text


# ---- keys, orgs, the audit row -------------------------------------------------------------------------------------------------------


def _keyed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[TestClient, str, Path]:
    _flags(monkeypatch, BailDraftConfig())
    monkeypatch.setenv("PRAVRUDHI_AUTH", "required")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "s" * 40)
    monkeypatch.setenv("PRAVRUDHI_DISABLE_LOCAL_GUARD", "1")
    monkeypatch.setenv(tenancy.TENANCY_PROVISION_SECRET_ENV, "p" * 40)
    root = tmp_path / "root"
    shutil.copytree(REPO / "configs", root / "configs")
    conf = root / "configs" / "partner_api.yaml"
    conf.write_text(re.sub(r"(?m)^rate_limit_per_minute:.*$", "rate_limit_per_minute: 1000", conf.read_text()))
    tenancy.create_org(root, "acme", "Acme")
    key = tenancy.create_key(root, "acme", label="t")
    return TestClient(create_app(root), headers={"host": "127.0.0.1:8008"}, raise_server_exceptions=False), key.secret, root


def test_a_key_gets_a_draft_a_missing_key_does_not_and_every_status_leaves_a_bail_prefixed_audit_row_with_nothing_of_the_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    c, secret, root = _keyed(monkeypatch, tmp_path)
    h = {tenancy.API_KEY_HEADER: secret}
    assert c.post(PATH, json=BODY, headers=h).status_code == 200
    assert c.post(PATH, json={**BODY, "consent": False}, headers=h).status_code == 400
    assert c.post(PATH, json={**BODY, "tags": ["pocso"]}, headers=h).status_code == 200
    assert c.post(PATH, json=BODY).status_code in (401, 403)  # no key, no session
    key_id = tenancy.keys_for_org(root, "acme")[0].key_id
    rows, _n = audit.page(root, key_id, offset=0, limit=100, retention_s=3600, now=lambda: datetime.now(UTC))
    mine = [r for r in rows if str(r.get("run_id", "")).startswith("bail-")]
    assert len(mine) == 3 and {r["status_code"] for r in mine} == {200, 400} and all(r["mode"] == "intake" for r in mine)
    for r in mine:
        assert re.fullmatch(r"bail-[0-9a-f]{10}", r["run_id"]) and "marker" not in repr(r) and "2024" not in repr(r) and "antecedents" not in repr(r)
