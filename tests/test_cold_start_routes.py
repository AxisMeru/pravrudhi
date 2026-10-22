# ruff: noqa: E501
"""#1038 PR-3: the cold-start routes, end to end through the partner router with a fake RunPod health, a scripted agent and the
test acting as the box-side waker (signed polls and deliveries). CPU only, no network. Invented ids."""

from __future__ import annotations

import json
import threading
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
from tests.judge_wake_fakes import box_poll_headers, box_result_request
from tests.test_api_partner import _NO_LIMIT_CONFIG, _agent, _proof_script, _req
from tests.test_judges_offline import _health

URL1 = "https://api.runpod.ai/v2/ep111/openai/v1"  # the first judge (4B)
URL2 = "https://api.runpod.ai/v2/ep222/openai/v1"  # the second judge (32B)
SECRET = "test-secret-not-a-real-key"
WAKER = identity.User("u-waker", "waker@team.example.test", "user")
OTHER = identity.User("u-other", "other@team.example.test", "user")
FLAGS = {"cold_start_wake": True}
WAKE, POLL = "/api/v1/analyse-facts/wake", "/api/v1/analyse-facts/wake/"
REQS, RESULTS = "/api/v1/internal/wake-requests", "/api/v1/internal/wake-results"


class Rig:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, flags: dict[str, Any] | None = None,
                 secret: str | None = SECRET, wakers: str = "u-waker", user: identity.User | None = WAKER) -> None:
        monkeypatch.setattr(partner_mod, "_peek_flags", lambda _root: dict(FLAGS if flags is None else flags))
        monkeypatch.setattr(CS, "CHECK_EVERY_S", 0.01)
        monkeypatch.setenv("PRAVRUDHI_AUTH", "required")
        monkeypatch.setenv("PRAVRUDHI_WAKERS", wakers)
        if secret is None:
            monkeypatch.delenv("PRAVRUDHI_WAKER_SECRET", raising=False)
        else:
            monkeypatch.setenv("PRAVRUDHI_WAKER_SECRET", secret)
        self.health: dict[str, dict[str, Any]] = {"ep111": _health(), "ep222": _health()}  # both parked
        self.partial_breakers: list[Any] = []
        self.user = user
        self.threads: list[threading.Thread] = []

        def fetch(url: str, headers: Any) -> dict[str, Any]:
            return self.health[url.split("/v2/")[1].split("/")[0]]

        def agent() -> Any:
            a = _agent(tmp_path, _proof_script())
            a.config = replace(a.config, house_judge={"base_url": URL1}, second_judge={"base_url": URL2})
            return a

        def run_in_thread(f: Any) -> None:
            t = threading.Thread(target=f, daemon=True)
            self.threads.append(t)
            t.start()

        def partial(_root: Path, breaker: Any) -> Any:
            self.partial_breakers.append(breaker)
            return agent()

        app = FastAPI()
        app.include_router(build_partner_router(
            tmp_path, agent_factory=lambda _r: agent(), config=_NO_LIMIT_CONFIG, judge_health_fetch=fetch,
            job_executor=run_in_thread, partial_agent_factory=partial,
        ))
        app.dependency_overrides[identity.current_user] = lambda: self.user
        self.c = TestClient(app, raise_server_exceptions=False)

    def up(self, *eps: str) -> None:
        for e in eps:
            self.health[e] = _health(ready=1)

    def box_poll(self) -> list[dict[str, Any]]:
        h = box_poll_headers(SECRET)
        r = self.c.get(REQS, headers=h)
        assert r.status_code == 200, r.text
        return [json.loads(x["body"]) for x in r.json()["requests"]]

    def box_deliver(self, results: list[dict[str, Any]]) -> int:
        body, h = box_result_request(SECRET, results)
        r = self.c.post(RESULTS, content=body, headers=h)
        assert r.status_code == 200, r.text
        return int(r.json()["applied"])

    def wait_job(self, job_id: str, timeout: float = 10.0) -> dict[str, Any]:
        end = time.time() + timeout
        out: dict[str, Any] = {}
        while time.time() < end:
            out = self.c.get(POLL + job_id).json()
            if out.get("status") in ("done", "failed"):
                return out
            time.sleep(0.02)
        raise AssertionError(f"the job did not finish: {out}")


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Rig:
    return Rig(tmp_path, monkeypatch)


# ---- the feature is absent unless it is fully configured --------------------------------------------------------------------


def test_with_the_flag_off_none_of_the_routes_exist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    r = Rig(tmp_path, monkeypatch, flags={})
    assert r.c.post(WAKE, json=_req()).status_code == 404
    assert r.c.get(POLL + "x").status_code == 404 and r.c.get(REQS).status_code == 404 and r.c.post(RESULTS, content=b"{}").status_code == 404
    assert set(r.c.get("/api/v1/status").json()) == {"engine_version", "service_window", "judge", "workflows"}  # no `judges` key


def test_without_the_secret_or_with_a_bad_value_none_of_the_routes_exist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert Rig(tmp_path, monkeypatch, secret=None).c.post(WAKE, json=_req()).status_code == 404
    assert Rig(tmp_path, monkeypatch, secret="").c.get(REQS).status_code == 404
    bad = Rig(tmp_path, monkeypatch, flags={**FLAGS, "cold_start_budget_s": 5})
    assert bad.c.post(WAKE, json=_req()).status_code == 404


def test_the_routes_are_not_in_the_openapi_document(rig: Rig) -> None:
    paths = rig.c.get("/openapi.json").json().get("paths", {}) if rig.c.get("/openapi.json").status_code == 200 else {}
    assert not [p for p in paths if "wake" in p]


# ---- who may wake -----------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("user_id", ["u-other", None])
def test_anyone_who_is_not_a_waker_gets_the_one_marker_and_no_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, user_id: str | None) -> None:
    r = Rig(tmp_path, monkeypatch, user=OTHER if user_id else None)
    res = r.c.post(WAKE, json=_req())
    assert res.status_code == 503 and res.json() == {"error": "judges_offline", "wake": "not_allowed"}
    assert r.box_poll() == []  # nothing was asked of the waker


def test_with_auth_disabled_nobody_wakes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    r = Rig(tmp_path, monkeypatch)
    monkeypatch.setenv("PRAVRUDHI_AUTH", "disabled")
    res = r.c.post(WAKE, json=_req())
    assert res.status_code == 503 and res.json() == CS.NOT_ALLOWED


def test_an_empty_allowlist_means_nobody_and_wake_true_in_the_body_authorises_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    r = Rig(tmp_path, monkeypatch, wakers="")
    for body in (_req(), {**_req(), "wake": True, "role": "admin"}):
        res = r.c.post(WAKE, json=body)
        assert res.status_code == 503 and res.json() == CS.NOT_ALLOWED


# ---- the wake itself --------------------------------------------------------------------------------------------------------


def test_everything_already_up_is_just_the_normal_screening(rig: Rig) -> None:
    rig.up("ep111", "ep222")
    res = rig.c.post(WAKE, json=_req())
    assert res.status_code == 200
    body = res.json()
    assert body["second_judge_ran"] is True and body["judges"]["first"] == {"ran": True}
    assert body["judges"]["second"]["ran"] is True and "contracts" in body


def test_a_cold_wake_runs_the_screening_after_both_judges_come_up(rig: Rig) -> None:
    res = rig.c.post(WAKE, json=_req())
    assert res.status_code == 202 and res.headers["Retry-After"] == "5"
    job = res.json()
    assert set(job) == {"job_id", "status", "wake"} and job["status"] == "pending"
    assert [m["state"] for m in job["wake"]["models"]] in (["waiting", "waiting"], ["starting", "starting"])
    listed = rig.box_poll()
    assert sorted(b["label"] for b in listed) == ["first", "second"] and all(b["kind"] == "wake" for b in listed)
    nonces = {b["label"]: b["nonce"] for b in listed}
    rig.box_deliver([{"nonce": nonces["first"], "state": "granted", "lease_ref": "lease-1"},
                     {"nonce": nonces["second"], "state": "granted", "lease_ref": "lease-2"}])
    rig.up("ep111", "ep222")
    out = rig.wait_job(job["job_id"])
    assert out["status"] == "done" and out["wake"]["state"] == "ready" and out["result"]["second_judge_ran"] is True
    assert set(out["wake"]) == {"state", "models", "reason", "deadline_s"} and "lease" not in json.dumps(out["wake"])
    assert rig.partial_breakers == []  # a full run never gets the partial breaker
    ext = sorted((b["label"], b["kind"], b.get("lease_ref")) for b in rig.box_poll())
    assert ext == [("first", "extend", "lease-1"), ("second", "extend", "lease-2")]  # at start and end, for the leases granted here


def test_the_partial_load_runs_with_a_request_scoped_tripped_breaker_and_refers(rig: Rig) -> None:
    job = rig.c.post(WAKE, json=_req()).json()
    nonces = {b["label"]: b["nonce"] for b in rig.box_poll()}
    rig.box_deliver([{"nonce": nonces["first"], "state": "granted", "lease_ref": "lease-1"},
                     {"nonce": nonces["second"], "state": "refused", "lease_ref": None}])
    rig.up("ep111")
    out = rig.wait_job(job["job_id"])
    assert out["status"] == "done" and out["wake"]["state"] == "partial" and out["wake"]["reason"] == "second_judge_unavailable"
    assert out["result"]["second_judge_ran"] is False and out["result"]["judges"]["second"] == {"ran": False, "used": False}
    assert len(rig.partial_breakers) == 1 and rig.partial_breakers[0].is_open() is True  # scoped to this run, already tripped


def test_a_refused_first_judge_fails_the_job_with_nothing_scored_and_the_user_cools_down(rig: Rig) -> None:
    job = rig.c.post(WAKE, json=_req()).json()
    nonces = {b["label"]: b["nonce"] for b in rig.box_poll()}
    rig.box_deliver([{"nonce": nonces["first"], "state": "refused", "lease_ref": None}])
    out = rig.wait_job(job["job_id"])
    assert out["status"] == "failed" and out["wake"]["state"] == "failed" and out["wake"]["reason"] == "first_judge_unavailable"
    assert "result" not in out and out["error"]["status_code"] == 503
    again = rig.c.post(WAKE, json=_req())
    assert again.status_code == 503 and again.json() == CS.NOT_ALLOWED  # the cooldown answers with the one marker


def test_a_second_post_for_the_same_user_is_the_same_job(rig: Rig) -> None:
    a = rig.c.post(WAKE, json=_req()).json()
    b = rig.c.post(WAKE, json=_req()).json()
    assert a["job_id"] == b["job_id"] and len(rig.box_poll()) == 2


def test_only_the_owner_can_poll_a_wake_job(rig: Rig) -> None:
    job = rig.c.post(WAKE, json=_req()).json()
    assert rig.c.get(POLL + job["job_id"]).status_code == 200
    rig.user = OTHER
    assert rig.c.get(POLL + job["job_id"]).status_code == 404
    rig.user = None
    assert rig.c.get(POLL + job["job_id"]).status_code == 404
    rig.user = WAKER
    assert rig.c.get(POLL + "no-such-job").status_code == 404
    assert rig.c.get("/api/v1/analyse-facts/jobs/" + job["job_id"]).status_code in (401, 404)  # the key-owned route never serves it


# ---- the waker-facing routes ------------------------------------------------------------------------------------------------


def test_the_waker_routes_refuse_every_unsigned_forged_or_replayed_message(rig: Rig) -> None:
    rig.c.post(WAKE, json=_req())
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
    rig.c.post(WAKE, json=_req())
    r = rig.c.get(REQS, headers=box_poll_headers(SECRET))
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


# ---- R2 advises on #628 (A, B, C) ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("kind", "reasons", "want"),
    [("full", ["certain"], True), ("full", ["certain", "second_judge_unavailable"], False), ("partial", ["certain"], False)],
)
def test_second_judge_ran_is_false_when_the_second_judge_was_unavailable_even_on_a_full_run(kind: str, reasons: list[str], want: bool) -> None:
    from pravrudhi.application import cold_start_service as cs

    out = cs.decorate_result({"contracts": [{"reason": r, "elements": []} for r in reasons]}, kind)
    assert out["second_judge_ran"] is want and out["judges"]["second"]["ran"] is want
    assert out["judges"]["second"]["used"] is False


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
