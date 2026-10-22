# ruff: noqa: E501
"""#1038 PR-4: the cold-start routes, end to end through the partner router with a fake RunPod health, a scripted agent and the test
acting as the box-side waker (signed polls and deliveries) and as several containers (apps sharing one FakeWakeDb). No job object:
the client reads /analyse-facts/wake/status, then submits the normal screening with the ticket. CPU only, no network. Invented ids."""

from __future__ import annotations

import json
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import HTTPConnection

from pravrudhi.api import identity
from pravrudhi.api import partner as partner_mod
from pravrudhi.api.partner import build_partner_router
from pravrudhi.application import cold_start_service as CS
from pravrudhi.application import judge_wake as W
from pravrudhi.application import tenancy as tenancy_mod
from tests.judge_wake_fakes import box_poll_headers, box_result_request
from tests.test_api_partner import _NO_LIMIT_CONFIG, _agent, _proof_script, _req
from tests.test_judges_offline import _health
from tests.wake_store_fakes import FakeWakeDb

URL1 = "https://api.runpod.ai/v2/ep111/openai/v1"  # the first judge (4B)
URL2 = "https://api.runpod.ai/v2/ep222/openai/v1"  # the second judge (32B)
SECRET = "test-secret-not-a-real-key"
WAKER = identity.User("u-waker", "waker@team.example.test", "user")
OTHER = identity.User("u-other", "other@team.example.test", "user")
FLAGS = {"cold_start_wake": True}
WAKE, STATUS, SCREEN = "/api/v1/analyse-facts/wake", "/api/v1/analyse-facts/wake/status", "/api/v1/analyse-facts"
REQS, RESULTS = "/api/v1/internal/wake-requests", "/api/v1/internal/wake-results"
TICKET = "X-Wake-Ticket"


class Rig:
    """One container. Two Rigs given the same `db` are two containers on one shared database."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, flags: dict[str, Any] | None = None,
                 secret: str | None = SECRET, wakers: str = "u-waker", user: identity.User | None = WAKER,
                 db: FakeWakeDb | None = None) -> None:
        monkeypatch.setattr(partner_mod, "_peek_flags", lambda _root: dict(FLAGS if flags is None else flags))
        monkeypatch.setenv("PRAVRUDHI_AUTH", "required")
        monkeypatch.setenv("PRAVRUDHI_WAKERS", wakers)
        if secret is None:
            monkeypatch.delenv("PRAVRUDHI_WAKER_SECRET", raising=False)
        else:
            monkeypatch.setenv("PRAVRUDHI_WAKER_SECRET", secret)
        monkeypatch.setattr(tenancy_mod, "wake_call_or_none", (lambda: db.call) if db is not None else (lambda: None))
        self.health: dict[str, dict[str, Any]] = {"ep111": _health(), "ep222": _health()}  # both parked
        self.partial_breakers: list[Any] = []
        self.runs: list[str] = []
        self.user = user

        def fetch(url: str, headers: Any) -> dict[str, Any]:
            return self.health[url.split("/v2/")[1].split("/")[0]]

        def agent() -> Any:
            a = _agent(tmp_path, _proof_script())
            a.config = replace(a.config, house_judge={"base_url": URL1}, second_judge={"base_url": URL2})
            real = a.run
            a.run = lambda *x, **k: (self.runs.append("run"), real(*x, **k))[1]  # type: ignore[method-assign]
            return a

        def partial(_root: Path, breaker: Any) -> Any:
            self.partial_breakers.append(breaker)
            return agent()

        app = FastAPI()
        app.include_router(build_partner_router(
            tmp_path, agent_factory=lambda _r: agent(), config=replace(_NO_LIMIT_CONFIG, judge_health_check=True, judge_health_ttl_s=0.0), judge_health_fetch=fetch,
            partial_agent_factory=partial,
        ))
        app.dependency_overrides[identity.current_user] = lambda: self.user
        self.c = TestClient(app, raise_server_exceptions=False)

    def up(self, *eps: str) -> None:
        for e in eps:
            self.health[e] = _health(ready=1)

    def box_poll(self) -> list[dict[str, Any]]:
        r = self.c.get(REQS, headers=box_poll_headers(SECRET))
        assert r.status_code == 200, r.text
        return [json.loads(x["body"]) for x in r.json()["requests"]]

    def box_deliver(self, results: list[dict[str, Any]]) -> int:
        body, h = box_result_request(SECRET, results)
        r = self.c.post(RESULTS, content=body, headers=h)
        assert r.status_code == 200, r.text
        return int(r.json()["applied"])


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Rig:
    return Rig(tmp_path, monkeypatch)


# ---- the feature is absent unless it is fully configured --------------------------------------------------------------------


def test_with_the_flag_off_none_of_the_routes_exist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    r = Rig(tmp_path, monkeypatch, flags={})
    assert r.c.post(WAKE).status_code == 404 and r.c.get(STATUS).status_code == 404
    assert r.c.get(REQS).status_code == 404 and r.c.post(RESULTS, content=b"{}").status_code == 404
    assert set(r.c.get("/api/v1/status").json()) == {"engine_version", "service_window", "judge", "workflows"}  # no `judges` key


def test_without_the_secret_or_with_a_bad_value_none_of_the_routes_exist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert Rig(tmp_path, monkeypatch, secret=None).c.post(WAKE).status_code == 404
    assert Rig(tmp_path, monkeypatch, secret="").c.get(REQS).status_code == 404
    assert Rig(tmp_path, monkeypatch, flags={**FLAGS, "cold_start_budget_s": 5}).c.post(WAKE).status_code == 404
    assert Rig(tmp_path, monkeypatch, flags={**FLAGS, "cold_start_ticket_ttl_s": 5}).c.post(WAKE).status_code == 404


def test_the_routes_are_not_in_the_openapi_document(rig: Rig) -> None:
    paths = rig.c.get("/openapi.json").json().get("paths", {}) if rig.c.get("/openapi.json").status_code == 200 else {}
    assert not [p for p in paths if "wake" in p]


def test_the_old_job_route_is_gone(rig: Rig) -> None:
    assert rig.c.get(WAKE + "/some-job-id").status_code == 404


# ---- who may wake (the same gate for the start and the status) ---------------------------------------------------------------


@pytest.mark.parametrize("user_id", ["u-other", None])
def test_anyone_who_is_not_a_waker_gets_the_one_marker_from_both_routes_and_nothing_is_asked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, user_id: str | None) -> None:
    r = Rig(tmp_path, monkeypatch, user=OTHER if user_id else None)
    for res in (r.c.post(WAKE), r.c.get(STATUS)):
        assert res.status_code == 503 and res.json() == {"error": "judges_offline", "wake": "not_allowed"}
    assert r.box_poll() == []  # nothing was asked of the waker, and the status told a non-waker nothing about the judges


def test_with_auth_disabled_nobody_wakes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    r = Rig(tmp_path, monkeypatch)
    monkeypatch.setenv("PRAVRUDHI_AUTH", "disabled")
    for res in (r.c.post(WAKE), r.c.get(STATUS)):
        assert res.status_code == 503 and res.json() == CS.NOT_ALLOWED


def test_an_empty_allowlist_means_nobody_and_a_body_authorises_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    r = Rig(tmp_path, monkeypatch, wakers="")
    for res in (r.c.post(WAKE), r.c.post(WAKE, json={"wake": True, "role": "admin"}), r.c.get(STATUS)):
        assert res.status_code == 503 and res.json() == CS.NOT_ALLOWED


# ---- the wake and the screening that follows ---------------------------------------------------------------------------------


def test_everything_already_up_answers_ready_with_a_full_ticket_and_nothing_stored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = FakeWakeDb(time.time)
    r = Rig(tmp_path, monkeypatch, db=db)
    r.up("ep111", "ep222")
    res = r.c.post(WAKE)
    assert res.status_code == 200 and res.json()["wake"]["state"] == "ready" and res.json()["ticket"]["mode"] == "full"
    assert db.calls == []


def test_a_cold_wake_then_the_client_submits_the_screening_with_its_ticket(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = FakeWakeDb(time.time)
    a, b = Rig(tmp_path, monkeypatch, db=db), Rig(tmp_path, monkeypatch, db=db)  # two containers, one database
    a.health = b.health  # the same judges
    res = a.c.post(WAKE)
    assert res.status_code == 202 and res.headers["Retry-After"] == "5" and res.json()["wake"]["state"] == "waking"
    assert [m["state"] for m in res.json()["wake"]["models"]] == ["waiting", "waiting"]
    asked = {q["label"]: q["nonce"] for q in b.box_poll()}  # the box polls the OTHER container
    assert b.box_deliver([{"nonce": n, "state": "granted", "lease_ref": f"l-{k}"} for k, n in asked.items()]) == 2
    assert [m["state"] for m in a.c.get(STATUS).json()["wake"]["models"]] == ["starting", "starting"]
    a.up("ep111", "ep222")
    done = b.c.get(STATUS).json()  # the status read on the other container
    assert done["wake"]["state"] == "ready" and done["ticket"]["mode"] == "full"
    shot = a.c.post(SCREEN, json=_req(), headers={TICKET: done["ticket"]["value"]})
    assert shot.status_code == 200 and a.runs == ["run"] and a.partial_breakers == []  # the ordinary screening, both judges


def test_the_partial_path_the_ticket_skips_only_the_second_judge_and_runs_with_a_tripped_breaker(rig: Rig) -> None:
    rig.c.post(WAKE)
    asked = {q["label"]: q["nonce"] for q in rig.box_poll()}
    rig.box_deliver([{"nonce": asked["first"], "state": "granted", "lease_ref": "l1"}, {"nonce": asked["second"], "state": "refused", "lease_ref": None}])
    rig.up("ep111")
    body = rig.c.get(STATUS).json()
    assert body["wake"]["state"] == "partial" and body["wake"]["reason"] == "second_judge_unavailable" and body["ticket"]["mode"] == "partial"
    assert rig.c.post(SCREEN, json=_req()).json() == {"error": "judges_offline"}  # no ticket: the ordinary refusal, nothing ran
    assert rig.runs == []
    shot = rig.c.post(SCREEN, json=_req(), headers={TICKET: body["ticket"]["value"]})
    assert shot.status_code == 200 and rig.runs == ["run"]
    assert len(rig.partial_breakers) == 1 and rig.partial_breakers[0].is_open() is True  # scoped to this run, already tripped


def test_a_partial_ticket_never_skips_the_first_judge(rig: Rig) -> None:
    rig.c.post(WAKE)
    asked = {q["label"]: q["nonce"] for q in rig.box_poll()}
    rig.box_deliver([{"nonce": asked["first"], "state": "granted", "lease_ref": "l1"}, {"nonce": asked["second"], "state": "refused", "lease_ref": None}])
    rig.up("ep111")
    ticket = rig.c.get(STATUS).json()["ticket"]["value"]
    rig.health["ep111"] = _health()  # the first judge went back to sleep
    res = rig.c.post(SCREEN, json=_req(), headers={TICKET: ticket})
    assert res.status_code == 503 and res.json() == {"error": "judges_offline"} and rig.runs == []


def test_a_bad_expired_or_other_users_ticket_is_ignored_and_never_a_new_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    r = Rig(tmp_path, monkeypatch, wakers="u-waker,u-other")
    r.up("ep111", "ep222")
    ticket = r.c.post(WAKE).json()["ticket"]["value"]
    r.health["ep222"] = _health()  # the second judge parked again
    r.user = OTHER
    for bad in (ticket, "garbage", "v1.1.partial." + "0" * 64, ""):  # another user's, malformed, expired-shaped, empty
        res = r.c.post(SCREEN, json=_req(), headers={TICKET: bad})
        assert res.status_code == 503 and res.json() == {"error": "judges_offline"}
    assert r.runs == []


def test_a_valid_ticket_of_a_user_no_longer_on_the_allowlist_is_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    r = Rig(tmp_path, monkeypatch)
    r.c.post(WAKE)
    asked = {q["label"]: q["nonce"] for q in r.box_poll()}
    r.box_deliver([{"nonce": asked["first"], "state": "granted", "lease_ref": "l1"}, {"nonce": asked["second"], "state": "refused", "lease_ref": None}])
    r.up("ep111")
    ticket = r.c.get(STATUS).json()["ticket"]["value"]
    monkeypatch.setenv("PRAVRUDHI_WAKERS", "someone-else")  # removed from the list after the ticket was issued
    res = r.c.post(SCREEN, json=_req(), headers={TICKET: ticket})
    assert res.status_code == 503 and r.runs == []


def test_a_screening_without_a_ticket_does_no_store_io(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = FakeWakeDb(time.time)
    r = Rig(tmp_path, monkeypatch, db=db)
    r.up("ep111", "ep222")
    assert r.c.post(SCREEN, json=_req()).status_code == 200  # an anonymous-style public screening with both judges up
    assert r.c.post(SCREEN, json=_req(), headers={TICKET: "v1.1.full." + "0" * 64}).status_code == 200
    assert db.calls == []  # not one database call: no extend rows, no reads


def test_a_valid_ticket_extends_the_judge_leases_once_in_a_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = FakeWakeDb(time.time)
    r = Rig(tmp_path, monkeypatch, db=db)
    r.c.post(WAKE)
    asked = {q["label"]: q["nonce"] for q in r.box_poll()}
    r.box_deliver([{"nonce": n, "state": "granted", "lease_ref": f"lease-{k}"} for k, n in asked.items()])
    r.up("ep111", "ep222")
    ticket = r.c.get(STATUS).json()["ticket"]["value"]
    assert r.c.post(SCREEN, json=_req(), headers={TICKET: ticket}).status_code == 200
    assert r.c.post(SCREEN, json=_req(), headers={TICKET: ticket}).status_code == 200
    extends = [q for q in r.box_poll() if q["kind"] == "extend"]
    assert sorted(q["lease_ref"] for q in extends) == ["lease-first", "lease-second"]  # one per judge, not one per screening


def test_a_wake_is_refused_with_the_marker_when_the_store_is_down(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = FakeWakeDb(time.time)
    r = Rig(tmp_path, monkeypatch, db=db)
    db.down = True
    for res in (r.c.post(WAKE), r.c.get(STATUS)):
        assert res.status_code == 503 and res.json() == CS.NOT_ALLOWED


def test_a_second_post_for_the_same_user_is_the_same_wake(rig: Rig) -> None:
    first = rig.c.post(WAKE)
    again = rig.c.post(WAKE)
    assert first.status_code == 202 and again.status_code == 202 and len(rig.box_poll()) == 2  # still one request per judge


# ---- the waker-facing routes ------------------------------------------------------------------------------------------------


def test_the_waker_routes_refuse_every_unsigned_forged_or_replayed_message(rig: Rig) -> None:
    rig.c.post(WAKE)
    assert rig.c.get(REQS).status_code == 401 and rig.c.post(RESULTS, content=b"{}").status_code == 401
    h = box_poll_headers(SECRET, nonce="a" * 32)
    assert rig.c.get(REQS, headers=h).status_code == 200
    assert rig.c.get(REQS, headers=h).status_code == 401  # replay
    assert rig.c.get(REQS, headers=box_poll_headers("another-secret")).status_code == 401
    assert rig.c.get(REQS, headers=box_poll_headers(SECRET, ts=int(time.time()) - 120)).status_code == 401
    body, hh = box_result_request(SECRET, [])
    assert rig.c.post(RESULTS, content=body + b" ", headers=hh).status_code == 401  # tampered
    assert rig.c.get(REQS, headers={**box_poll_headers(SECRET), "X-Waker-Signature": "v1=" + "0" * 64}).status_code == 401


def test_the_wake_requests_are_signed_fresh_and_valid_for_the_boxs_core(rig: Rig) -> None:
    rig.c.post(WAKE)
    r = rig.c.get(REQS, headers=box_poll_headers(SECRET))
    assert len(r.json()["requests"]) == 2
    for item in r.json()["requests"]:
        b = json.loads(item["body"])
        assert item["sig"] == "v1=" + W.sign(SECRET, b["ts"], b["nonce"], b["label"], b["kind"], b.get("lease_ref"))
        assert abs(b["ts"] - time.time()) < 5 and len(item["body"]) < 512


# ---- the identity gate lets ONLY these two signed requests through ---------------------------------------------------------


def _conn(method: str, path: str, headers: list[tuple[bytes, bytes]]) -> HTTPConnection:
    return HTTPConnection({"type": "http", "method": method, "path": path, "headers": headers, "query_string": b""})


@pytest.mark.parametrize(
    ("method", "path", "headers", "want"),
    [
        ("GET", "/api/v1/internal/wake-requests", [(b"x-waker-signature", b"v1=ab")], True),
        ("POST", "/api/v1/internal/wake-results", [(b"x-waker-signature", b"v1=ab")], True),
        ("GET", "/api/v1/internal/wake-requests", [], False),  # no signature header
        ("POST", "/api/v1/internal/wake-requests", [(b"x-waker-signature", b"v1=ab")], False),  # another method
        ("GET", "/api/v1/internal/wake-results", [(b"x-waker-signature", b"v1=ab")], False),
        ("GET", "/api/v1/internal/other", [(b"x-waker-signature", b"v1=ab")], False),  # another path
        ("GET", "/api/v1/analyse-facts/wake/x", [(b"x-waker-signature", b"v1=ab")], False),  # the user routes need a session
        ("POST", "/api/v1/analyse-facts/wake", [(b"x-waker-signature", b"v1=ab")], False),
        ("GET", "/api/v1/internal/wake-requests", [(b"x-waker-signature", b"a"), (b"x-waker-signature", b"b")], False),  # two headers
        ("GET", "/api/v1/internal/wake-requests", [(b"x-waker-signature", b"")], False),  # empty
    ],
)
def test_the_gate_passes_only_the_two_signed_waker_requests(method: str, path: str, headers: list[tuple[bytes, bytes]], want: bool) -> None:
    got = identity._partner_route_carries_its_own_credential(path, _conn(method, path, headers), "x-identity")
    assert got is want


def test_a_request_that_carries_an_identity_token_goes_through_the_ordinary_check() -> None:
    headers = [(b"x-waker-signature", b"v1=ab"), (b"x-identity", b"token")]
    conn = _conn("GET", "/api/v1/internal/wake-requests", headers)
    assert identity._partner_route_carries_its_own_credential("/api/v1/internal/wake-requests", conn, "x-identity") is False


# ---- R2 advises on #628 (B, C) and the status list ----------------------------------------------------------------------------


def test_an_oversize_or_undeclared_result_body_is_refused_before_it_is_read(rig: Rig) -> None:
    body, h = box_result_request(SECRET, [{"nonce": "x" * 32, "state": "granted", "lease_ref": "l"} for _ in range(100)])
    assert len(body) > 2048
    assert rig.c.post(RESULTS, content=body, headers=h).status_code == 413


def test_the_waker_throttle_is_per_client_address_and_the_body_names_the_cause(rig: Rig) -> None:
    for _ in range(120):  # a flood of unsigned calls from one address ...
        assert rig.c.get(REQS).status_code == 401
    flooded = rig.c.get(REQS)
    assert flooded.status_code == 429 and flooded.json() == {"detail": "rate limit exceeded"}
    assert rig.c.get(REQS, headers={"X-Forwarded-For": "203.0.113.9"}).status_code == 429  # an unproven header changes nothing


def test_status_lists_each_judge_only_when_cold_start_is_configured_and_never_wakes(rig: Rig) -> None:
    rig.up("ep111")
    body = rig.c.get("/api/v1/status").json()
    assert body["judges"] == {"first": "ready", "second": "parked"} and rig.box_poll() == []  # a read, no wake request queued
