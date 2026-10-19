# ruff: noqa: E501
"""A8 synopsis and list of dates route (POST /api/v1/synopsis-dates), registered only when wf_a8_synopsis_list_of_dates is on; it needs A2 ON and reads
the matter through A3's chronology as a function. Track A's real drafting module runs; a fake completer stands in for the model. Invented papers."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pravrudhi.api import identity, roles
from pravrudhi.api.partner import PartnerApiConfig, build_partner_router
from pravrudhi.api.server import create_app
from pravrudhi.application import bail_draft as REAL
from pravrudhi.application import bail_draft_input as I  # noqa: E741
from pravrudhi.application import bail_draft_route as R
from pravrudhi.application import matter_spans_config as msc
from pravrudhi.application import synopsis_dates_config as sdc
from pravrudhi.application.bail_draft_config import BailDraftConfig
from pravrudhi.application.matter_spans_config import MatterSpansConfig
from tests.flag_peek_util import peek_all_true
from tests.test_api_partner import _agent, _proof_script

USER = identity.User("u1", "user@example.test", "user")
USER2 = identity.User("u2", "two@example.test", "user")
CFG = PartnerApiConfig(rate_limit_per_minute=1000, max_concurrent=1, trust_proxy_header=False)
REPO = Path(__file__).resolve().parent.parent
MODULE = "pravrudhi.application.synopsis_dates_route"
PATH = "/api/v1/synopsis-dates"
A2 = "/api/v1/matter-spans"
PAPER = "The FIR was registered on 18 July 2021 at the police station. The petitioner was arrested on 12.06.2024 and remanded the next day."
MODEL_CFG = BailDraftConfig(base_url="https://llm.example.test/v1", model="pinned-model-1", api_key_env="A8_TEST_KEY", usd_per_mtok_in=2.0, usd_per_mtok_out=10.0)
FACTS = [{"text": "The petitioner has been in custody since the arrest.", "side": "applicant"}, {"text": "The informant-marker-zq says he saw the petitioner.", "side": "other"}]


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(msc.DIR_ENV, str(tmp_path / "store"))
    monkeypatch.setattr(R, "_SHARED_CEILING", R.DayCeiling())
    monkeypatch.setattr(identity, "PARTNER_KEY_ROUTES", identity.PARTNER_KEY_ROUTES)
    monkeypatch.setattr(identity, "PARTNER_KEY_PATHS", identity.PARTNER_KEY_PATHS)
    monkeypatch.setattr(roles, "USER_FACING", roles.USER_FACING)


def _flags(monkeypatch: pytest.MonkeyPatch, a8: BailDraftConfig | None = None, a2: MatterSpansConfig | None = None) -> None:
    monkeypatch.setattr(sdc, "load_synopsis_dates_config", lambda _root: a8)
    monkeypatch.setattr(msc, "load_matter_spans_config", lambda _root: a2)
    peek_all_true(monkeypatch)


def _on(monkeypatch: pytest.MonkeyPatch, cfg: BailDraftConfig | None = None) -> None:
    _flags(monkeypatch, cfg or BailDraftConfig(), MatterSpansConfig())


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


def _matter(c: TestClient, *names: str, text: str = PAPER) -> str:
    m = c.post(f"{A2}/matters", json={}).json()["matter_id"]
    for n in names:
        assert c.post(f"{A2}/documents", json={"matter_id": m, "text": text, "name": n}).status_code == 200
    return m


def _body(m: str, **kw: Any) -> dict[str, Any]:
    return {"consent": True, "sensitive_declaration": "none", "matter_id": m, "facts": FACTS, **kw}


def _fake_completer(monkeypatch: pytest.MonkeyPatch, answer_for: Any) -> list[tuple[str, str, int]]:
    calls: list[tuple[str, str, int]] = []

    def fake_make(_cfg: Any, drafter: Any, env: Any = None) -> Any:
        def complete(system: str, user: str, max_tokens: int) -> Any:
            calls.append((system, user, max_tokens))
            return REAL.Completion(answer_for(user), 1000, 300, "pinned-model-1", "end_turn")

        return complete

    monkeypatch.setattr(R, "make_completer", fake_make)
    return calls


def test_with_the_flag_off_the_route_is_absent_nothing_is_imported_and_the_lists_are_the_released_ones(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _flags(monkeypatch)
    for m in (MODULE, "pravrudhi.application.synopsis_dates"):
        monkeypatch.delitem(sys.modules, m, raising=False)
    before = (identity.PARTNER_KEY_ROUTES, identity.PARTNER_KEY_PATHS, roles.USER_FACING)
    c = _client(tmp_path)
    off, unknown = c.post(PATH, json={"matter_id": "a" * 32}), c.post("/api/v1/no-such-route", json={"matter_id": "a" * 32})
    assert PATH not in _paths(c) and off.status_code == unknown.status_code == 404 and off.content == unknown.content
    assert MODULE not in sys.modules and "pravrudhi.application.synopsis_dates" not in sys.modules
    assert before == (identity.PARTNER_KEY_ROUTES, identity.PARTNER_KEY_PATHS, roles.USER_FACING)


def test_with_the_flag_on_the_route_exists_opens_the_gate_lists_and_is_not_in_openapi(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    c = _client(tmp_path)
    assert PATH in _paths(c) and ("POST", PATH) in identity.PARTNER_KEY_ROUTES and PATH in identity.PARTNER_KEY_PATHS
    assert PATH in roles.USER_FACING and PATH not in roles.ADMIN_ONLY and PATH not in roles.ADMIN_IN_BOTH_EDITIONS
    assert PATH not in c.get("/openapi.json").text


def test_with_the_flag_on_no_route_of_the_whole_app_is_unclassified_or_stale(monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    paths = _paths(TestClient(create_app(REPO)))
    classified = roles.ADMIN_ONLY | roles.USER_FACING | roles.ADMIN_IN_BOTH_EDITIONS
    assert PATH in paths and not (paths - classified) and not (classified - paths)


def test_a2_off_409_anonymous_401_and_no_consent_400_with_nothing_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    m = _matter(_client(tmp_path), "complaint.pdf")
    _flags(monkeypatch, BailDraftConfig(), None)
    r = _client(tmp_path).post(PATH, json=_body(m))
    assert r.status_code == 409 and r.json() == {"detail": "Stored papers are not available.", "error": "store_off"}
    _on(monkeypatch)
    assert _client(tmp_path, user=None).post(PATH, json=_body(m)).status_code == 401
    r = _client(tmp_path).post(PATH, json=_body(m, consent=False))
    assert r.status_code == 400 and r.json()["error"] == "consent_required" and "informant" not in r.text


def test_a_sensitive_tag_or_section_refers_without_reading_the_matter_or_calling_a_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pravrudhi.application import bail_draft_input as I

    _on(monkeypatch, MODEL_CFG)
    calls = _fake_completer(monkeypatch, lambda _u: "{}")
    c = _client(tmp_path)
    for patch, code, text in (({"tags": ["pocso"]}, "sensitive_matter", I.REFER_SENSITIVE), ({"charged_sections": ["IPC 370"]}, "sensitive_section", I.REFER_SENSITIVE_SECTION)):
        r = c.post(PATH, json=_body("a" * 32, **patch))  # a matter that does not exist: the referral comes first, nothing is read
        b = r.json()
        assert r.status_code == 200 and b["state"] == "referred" and b["draft"] is None and b["referral"] == {"code": code, "text": text} and b["label"] == I.DRAFT_LABEL
    assert calls == []


def test_without_a_drafting_endpoint_the_page_is_the_table_of_dates_as_written_with_the_papers_own_sentences(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    c = _client(tmp_path)
    m = _matter(c, "complaint.pdf")
    r = c.post(PATH, json=_body(m))
    b = r.json()
    assert r.status_code == 200 and b["run_id"].startswith("synopsis-") and b["state"] == "table_only" and "preview" not in b
    rows = b["draft"]["list_of_dates"]["rows"]
    assert {x["date_as_written"] for x in rows} >= {"18 July 2021", "12.06.2024"} and all(x["event_quote"] and x["source"] for x in rows)
    assert b["facts_set_aside"] == 1 and "informant-marker-zq" not in r.text


def test_with_an_endpoint_one_checked_call_adds_lines_and_a_synopsis_and_the_spend_lands_on_the_shared_ceiling(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch, MODEL_CFG)
    c = _client(tmp_path)
    m = _matter(c, "complaint.pdf")
    first = c.post(PATH, json=_body(m)).json()  # a table-only run first to read the real refs the model must cite
    rows = first["draft"]["list_of_dates"]["rows"]
    ref_arrest = next(x["ref"] for x in rows if x["date_as_written"] == "12.06.2024")
    answer = json.dumps({"event_lines": [{"ref": ref_arrest, "text": "The petitioner was arrested."}], "synopsis": [{"text": f"The petitioner states that he has been in custody since the arrest [F1] [{ref_arrest}].", "facts": ["F1"], "rows": [ref_arrest]}], "notes_for_lawyer": []})
    calls = _fake_completer(monkeypatch, lambda _u: answer)
    out = c.post(PATH, json=_body(m)).json()
    assert out["state"] in ("drafted", "table_only") and len(calls) >= 1 and "informant-marker-zq" not in calls[0][1]
    assert out["draft"]["calls"] >= 1 and out["draft"]["cost_usd"] > 0
    assert R.shared_ceiling()._spent >= out["draft"]["cost_usd"] > 0  # bail drafting and the synopsis count against ONE ceiling


def test_over_the_shared_day_ceiling_the_next_synopsis_is_the_table_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = BailDraftConfig(base_url="https://llm.example.test/v1", model="pinned-model-1", api_key_env="K", usd_per_mtok_in=2.0, usd_per_mtok_out=10.0, usd_per_day=0.0001)
    _on(monkeypatch, cfg)
    calls = _fake_completer(monkeypatch, lambda _u: "{}")
    c = _client(tmp_path)
    m = _matter(c, "complaint.pdf")
    R.shared_ceiling().add(1.0)  # another drafting slice already spent more than the day ceiling
    b = c.post(PATH, json=_body(m)).json()
    assert b["state"] == "table_only" and calls == []


def test_another_tenants_matter_is_exactly_as_not_found_as_none_and_an_empty_matter_is_409(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    c = _client(tmp_path)
    m = _matter(c, "zebra-secret.pdf")
    foreign = _client(tmp_path, user=USER2).post(PATH, json=_body(m))
    none = _client(tmp_path, user=USER2).post(PATH, json=_body("b" * 32))
    assert foreign.status_code == none.status_code == 404 and foreign.content == none.content and "zebra" not in foreign.text
    empty = c.post(f"{A2}/matters", json={}).json()["matter_id"]
    r = c.post(PATH, json=_body(empty))
    assert r.status_code == 409 and r.json()["error"] == "no_papers"


@pytest.mark.parametrize(
    "patch",
    [{"matter_id": "x"}, {"extra": 1}, {"consent": "yes"}, {"facts": [{"text": "", "side": "applicant"}]}, {"facts": [{"text": "a", "side": "bad"}]}, {"include_row_ids": ["a"]},
     {"document_label": "x" * 81}, {"tags": ["other"]}, {"parties": {"judge": "x"}}],
)
def test_every_malformed_body_gets_the_one_fixed_422_and_nothing_of_the_request_is_echoed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, patch: dict[str, Any]) -> None:
    _on(monkeypatch)
    r = _client(tmp_path).post(PATH, json={**_body("a" * 32), **patch})
    assert r.status_code == 422 and r.json() == {"error": "bad_request"} and "informant-marker-zq" not in r.text


@pytest.mark.parametrize("value", [None, "yes", "NONE", True])
def test_the_synopsis_route_refuses_without_a_valid_declaration_and_declared_sensitive_is_the_referral(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: Any) -> None:
    _on(monkeypatch)
    calls = _fake_completer(monkeypatch, lambda _u: "{}")
    c = _client(tmp_path)
    m = _matter(c, "complaint.pdf")
    body = {k: v for k, v in _body(m).items() if k != "sensitive_declaration"}
    if value is not None:
        body["sensitive_declaration"] = value
    r = c.post(PATH, json={**body, "consent": False})  # checked before consent
    assert r.status_code == 422 and r.json()["error"] == "sensitive_declaration_required" and r.json()["detail"] == I.DECLARATION_REFUSAL
    assert calls == []
    s = c.post(PATH, json=_body(m, sensitive_declaration="sensitive"))
    assert s.status_code == 200 and s.json()["state"] == "referred" and s.json()["draft"] is None and calls == []
