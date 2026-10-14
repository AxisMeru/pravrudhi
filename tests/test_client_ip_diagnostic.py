"""The client-address diagnostic (labels only) and the failed-key budget knob. Constructed values only; no real secret or address.
Both arms of every conditional: proof valid / no_ip / mismatch / none / engine_unset / engine_short; source signed / proxy /
caller / peer; the knob default, the yaml key, the env override and every invalid value."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from pravrudhi.api import identity
from pravrudhi.api.partner import (
    BAD_KEY_BUDGET_ENV,
    CLIENT_IP_HEADER,
    CLIENT_IP_SECRET_ENV,
    CLIENT_IP_SECRET_HEADER,
    PartnerApiConfig,
    build_partner_router,
    client_ip_and_source,
    client_ip_proof,
    load_partner_api_config,
)
from pravrudhi.api.server import create_app
from pravrudhi.application import tenancy
from tests.test_api_partner import _agent, _proof_script
from tests.test_partner_key_metering import ADMIN, FakeClock, _req

SECRET = "s" * 32
OTHER = "t" * 32
ADMIN_SECRET = "p" * 40
ADDR = "198.51.100.7"
PROXIES = ("203.0.113.250",)
REPO = Path(__file__).resolve().parent.parent
SRC_HDR, PROOF_HDR = "X-Pravrudhi-Client-Ip-Source", "X-Pravrudhi-Client-Ip-Proof"


def req(headers: dict[str, str], peer: str = "203.0.113.250") -> Request:
    return Request(
        {"type": "http", "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()], "client": (peer, 5000)}
    )


# ---- the proof label -----------------------------------------------------------------------------------------------------------
def test_proof_labels_cover_every_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    signed = {CLIENT_IP_HEADER: ADDR, CLIENT_IP_SECRET_HEADER: SECRET}
    monkeypatch.delenv(CLIENT_IP_SECRET_ENV, raising=False)
    assert client_ip_proof(req(signed)) == "engine_unset"
    monkeypatch.setenv(CLIENT_IP_SECRET_ENV, "short")
    assert client_ip_proof(req(signed)) == "engine_short"
    monkeypatch.setenv(CLIENT_IP_SECRET_ENV, SECRET)
    assert client_ip_proof(req({})) == "none"  # the gateway sent no proof
    assert client_ip_proof(req({CLIENT_IP_HEADER: ADDR})) == "none"  # an address alone is no proof
    assert client_ip_proof(req({CLIENT_IP_HEADER: ADDR, CLIENT_IP_SECRET_HEADER: OTHER})) == "mismatch"
    assert client_ip_proof(req({CLIENT_IP_SECRET_HEADER: SECRET})) == "no_ip"
    assert client_ip_proof(req({CLIENT_IP_HEADER: "  ", CLIENT_IP_SECRET_HEADER: SECRET})) == "no_ip"
    assert client_ip_proof(req(signed)) == "valid"


# ---- the source label and the address, unchanged -------------------------------------------------------------------------------
def test_source_signed_proxy_caller_and_peer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CLIENT_IP_SECRET_ENV, SECRET)
    kw: dict[str, Any] = {"trust_proxy_header": True, "trusted_proxies": PROXIES}
    signed = {CLIENT_IP_HEADER: ADDR, CLIENT_IP_SECRET_HEADER: SECRET, "x-forwarded-for": "192.0.2.5"}
    assert client_ip_and_source(req(signed), **kw) == (ADDR, "signed")  # the proof wins over the proxy header
    xff = {"x-forwarded-for": "192.0.2.5"}
    assert client_ip_and_source(req(xff), **kw) == ("192.0.2.5", "proxy")
    assert client_ip_and_source(req(xff, peer="203.0.113.99"), **kw) == ("203.0.113.99", "peer")  # not a trusted proxy
    off: dict[str, Any] = {"trust_proxy_header": False, "trusted_proxies": ()}
    assert client_ip_and_source(req(xff), **off) == ("203.0.113.250", "peer")
    assert client_ip_and_source(req(xff), caller_key="key:k1", **off) == ("key:k1", "caller")


@pytest.mark.parametrize("secret", [None, "short"])
def test_without_a_usable_engine_secret_a_forged_address_is_never_the_source(
    monkeypatch: pytest.MonkeyPatch, secret: str | None
) -> None:
    if secret is None:
        monkeypatch.delenv(CLIENT_IP_SECRET_ENV, raising=False)
    else:
        monkeypatch.setenv(CLIENT_IP_SECRET_ENV, secret)
    forged = {CLIENT_IP_HEADER: ADDR, CLIENT_IP_SECRET_HEADER: SECRET}
    ip, source = client_ip_and_source(req(forged), trust_proxy_header=False, trusted_proxies=())
    assert (ip, source) == ("203.0.113.250", "peer")


def test_a_mismatched_or_missing_proof_falls_through_to_the_peer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CLIENT_IP_SECRET_ENV, SECRET)
    off: dict[str, Any] = {"trust_proxy_header": False, "trusted_proxies": ()}
    for headers in ({CLIENT_IP_HEADER: ADDR, CLIENT_IP_SECRET_HEADER: OTHER}, {CLIENT_IP_HEADER: ADDR}, {}):
        assert client_ip_and_source(req(headers), **off) == ("203.0.113.250", "peer")


# ---- the public health route carries the two labels ----------------------------------------------------------------
def health(tmp_path: Path, headers: dict[str, str] | None = None) -> Any:
    c = TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8008")
    return c.get("/api/health", headers=headers or {})


def test_health_reports_the_labels_and_stays_unauthenticated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CLIENT_IP_SECRET_ENV, SECRET)
    r = health(tmp_path)
    assert r.status_code == 200 and r.headers[SRC_HDR] == "peer"
    assert PROOF_HDR not in r.headers  # the reason a proof failed is not public
    assert set(r.json()) >= {"ok", "version", "kernel", "ledger"}  # the body is unchanged: the labels are headers only


@pytest.mark.parametrize(
    ("env", "headers", "proof", "source"),
    [
        (None, {CLIENT_IP_HEADER: ADDR, CLIENT_IP_SECRET_HEADER: SECRET}, "engine_unset", "peer"),
        ("short", {CLIENT_IP_HEADER: ADDR, CLIENT_IP_SECRET_HEADER: SECRET}, "engine_short", "peer"),
        (SECRET, {CLIENT_IP_HEADER: ADDR, CLIENT_IP_SECRET_HEADER: OTHER}, "mismatch", "peer"),
        (SECRET, {CLIENT_IP_SECRET_HEADER: SECRET}, "no_ip", "peer"),
        (SECRET, {CLIENT_IP_HEADER: ADDR, CLIENT_IP_SECRET_HEADER: SECRET}, "valid", "signed"),
    ],
)
def test_health_labels_follow_what_the_gateway_sent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, env: str | None, headers: dict[str, str], proof: str, source: str
) -> None:
    if env is None:
        monkeypatch.delenv(CLIENT_IP_SECRET_ENV, raising=False)
    else:
        monkeypatch.setenv(CLIENT_IP_SECRET_ENV, env)
    r = health(tmp_path, headers)
    assert r.headers[SRC_HDR] == source and PROOF_HDR not in r.headers  # anonymous: the source label only
    assert ADDR not in str(dict(r.headers)) and SECRET not in str(dict(r.headers))  # labels only: never the address or the secret
    monkeypatch.setenv(tenancy.TENANCY_PROVISION_SECRET_ENV, ADMIN_SECRET)
    admin = health(tmp_path, {**headers, tenancy.TENANCY_PROVISION_HEADER: ADMIN_SECRET})
    assert (admin.headers[PROOF_HDR], admin.headers[SRC_HDR]) == (proof, source)  # the provisioning-secret holder sees the reason
    assert ADMIN_SECRET not in str(dict(admin.headers))
    wrong = health(tmp_path, {**headers, tenancy.TENANCY_PROVISION_HEADER: "w" * 40})
    assert PROOF_HDR not in wrong.headers and wrong.status_code == 200  # a wrong secret is just an anonymous caller


def test_health_works_when_the_partner_config_is_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CLIENT_IP_SECRET_ENV, SECRET)
    (tmp_path / "configs").mkdir(exist_ok=True)
    r = health(tmp_path)
    assert r.status_code == 200 and SRC_HDR in r.headers


# ---- the failed-key budget knob ------------------------------------------------------------------------------------
def write_cfg(root: Path, **over: Any) -> Path:
    body = yaml.safe_load((REPO / "configs" / "partner_api.yaml").read_text())
    body.update(over)
    (root / "configs").mkdir(parents=True, exist_ok=True)
    (root / "configs" / "partner_api.yaml").write_text(yaml.safe_dump(body))
    return root


def test_the_default_is_unset_so_the_budget_is_the_rate_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(BAD_KEY_BUDGET_ENV, raising=False)
    cfg = load_partner_api_config(write_cfg(tmp_path))
    assert cfg.bad_key_budget_per_minute is None
    assert (cfg.bad_key_budget_per_minute or cfg.rate_limit_per_minute) == cfg.rate_limit_per_minute


def test_the_yaml_key_sets_it_and_the_env_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(BAD_KEY_BUDGET_ENV, raising=False)
    assert load_partner_api_config(write_cfg(tmp_path, bad_key_budget_per_minute=40)).bad_key_budget_per_minute == 40
    monkeypatch.setenv(BAD_KEY_BUDGET_ENV, "90")
    assert load_partner_api_config(write_cfg(tmp_path, bad_key_budget_per_minute=40)).bad_key_budget_per_minute == 90
    assert load_partner_api_config(write_cfg(tmp_path)).bad_key_budget_per_minute == 90  # env alone


@pytest.mark.parametrize("bad", ["0", "-1", "abc", "1.5", "1001", "٣"])
def test_an_invalid_env_value_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
    monkeypatch.setenv(BAD_KEY_BUDGET_ENV, bad)
    with pytest.raises(ValueError, match="bad_key_budget|BUDGET"):
        load_partner_api_config(write_cfg(tmp_path))


@pytest.mark.parametrize("bad", [0, -3, True, 1.5, "x", 10**9, 1001, [5]])
def test_an_invalid_yaml_value_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad: Any) -> None:
    monkeypatch.delenv(BAD_KEY_BUDGET_ENV, raising=False)
    with pytest.raises(ValueError, match="bad_key_budget|BUDGET"):
        load_partner_api_config(write_cfg(tmp_path, bad_key_budget_per_minute=bad))


def client_for(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **cfg_over: Any) -> TestClient:
    monkeypatch.setenv("PRAVRUDHI_ADMINS", ADMIN.id)
    monkeypatch.delenv(CLIENT_IP_SECRET_ENV, raising=False)
    cfg = PartnerApiConfig(rate_limit_per_minute=2, max_concurrent=100, trust_proxy_header=False, **cfg_over)
    app = FastAPI()
    app.include_router(
        build_partner_router(
            tmp_path, agent_factory=lambda _r: _agent(tmp_path, _proof_script()), config=cfg, rate_clock=FakeClock()
        )
    )
    app.dependency_overrides[identity.current_user] = lambda: None
    return TestClient(app)


GARBAGE = "pvk_invented_notakey"


def bad_key_call(c: TestClient) -> int:
    return c.post("/api/v1/analyse-facts", json=_req(), headers={tenancy.API_KEY_HEADER: GARBAGE}).status_code


def test_with_the_knob_unset_the_failed_key_budget_is_the_rate_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    c = client_for(tmp_path, monkeypatch)
    codes = [bad_key_call(c) for _ in range(4)]
    assert codes == [401, 401, 429, 429]


def test_with_the_knob_raised_more_failed_attempts_are_checked_before_the_throttle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = client_for(tmp_path, monkeypatch, bad_key_budget_per_minute=5)
    codes = [bad_key_call(c) for _ in range(7)]
    assert codes == [401] * 5 + [429, 429]
    # the anonymous per-address limit is untouched by the knob: 2 per minute (bad-key attempts use their own bucket)
    anon = [c.post("/api/v1/analyse-facts", json=_req()).status_code for _ in range(3)]
    assert anon[:2] != [429, 429] and anon[2] == 429


# ---- the pre-check's findings ------------------------------------------------------------------------------------
def test_a_non_ascii_proof_header_is_a_mismatch_not_a_500(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CLIENT_IP_SECRET_ENV, SECRET)
    assert client_ip_proof(req({CLIENT_IP_HEADER: ADDR, CLIENT_IP_SECRET_HEADER: "\u00e9" * 32})) == "mismatch"
    raw = TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8008")
    bad = [(CLIENT_IP_SECRET_HEADER.encode(), b"\xe9\xe9\xe9"), (CLIENT_IP_HEADER.encode(), b"x")]
    r = raw.get("/api/health", headers=bad)
    assert r.status_code == 200 and PROOF_HDR not in r.headers and r.headers[SRC_HDR] == "peer"
    bad_admin = [
        *bad,
        (tenancy.TENANCY_PROVISION_HEADER.encode(), b"\xe9\xe9"),
    ]  # a non-ASCII admin header is not an admin, not a 500
    assert raw.get("/api/health", headers=bad_admin).status_code == 200


def test_the_budget_object_refuses_an_invalid_value_even_when_built_directly() -> None:
    for bad in (0, -1, True, 1.5, "5", 1001):
        with pytest.raises(ValueError, match="bad_key_budget"):
            PartnerApiConfig(rate_limit_per_minute=2, max_concurrent=1, trust_proxy_header=False, bad_key_budget_per_minute=bad)  # type: ignore[arg-type]
    ok = PartnerApiConfig(rate_limit_per_minute=2, max_concurrent=1, trust_proxy_header=False, bad_key_budget_per_minute=1000)
    assert ok.bad_key_budget_per_minute == 1000


def test_health_proxy_source_with_a_real_config_and_a_trusted_proxy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(CLIENT_IP_SECRET_ENV, raising=False)
    write_cfg(tmp_path, trust_proxy_header=True, trusted_proxies=["testclient"])
    c = TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8008")
    r = c.get("/api/health", headers={"x-forwarded-for": "192.0.2.5"})
    assert r.headers[SRC_HDR] == "proxy" and "192.0.2.5" not in str(dict(r.headers))
    plain = TestClient(create_app(tmp_path / "other"), base_url="http://127.0.0.1:8008").get("/api/health")
    assert plain.headers[SRC_HDR] == "peer"  # no partner config there: the safe default (not trusted)


def test_a_broken_partner_config_reads_peer_and_is_not_cached(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(BAD_KEY_BUDGET_ENV, "abc")
    write_cfg(tmp_path, trust_proxy_header=True, trusted_proxies=["testclient"])
    c = TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8008")
    first = c.get("/api/health", headers={"x-forwarded-for": "192.0.2.5"})
    assert first.headers[SRC_HDR] == "peer"  # config refused: the safe default
    monkeypatch.delenv(BAD_KEY_BUDGET_ENV)
    assert c.get("/api/health", headers={"x-forwarded-for": "192.0.2.5"}).headers[SRC_HDR] == "proxy"  # healthy again: not stuck


def test_the_proxy_config_is_cached_until_the_file_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pravrudhi.api import server

    monkeypatch.delenv(BAD_KEY_BUDGET_ENV, raising=False)
    write_cfg(tmp_path)
    calls = []

    def load(root: Path) -> Any:
        calls.append(1)
        return load_partner_api_config(root)

    server._health_proxy_config(tmp_path, load)
    server._health_proxy_config(tmp_path, load)
    assert len(calls) == 1
    write_cfg(tmp_path, trust_proxy_header=True, trusted_proxies=["testclient"])
    assert server._health_proxy_config(tmp_path, load) == (True, ("testclient",)) and len(calls) == 2


def test_the_source_label_is_only_ever_signed_proxy_or_peer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CLIENT_IP_SECRET_ENV, SECRET)
    seen = {
        health(tmp_path, h).headers[SRC_HDR]
        for h in (
            {},
            {CLIENT_IP_HEADER: ADDR, CLIENT_IP_SECRET_HEADER: SECRET},
            {CLIENT_IP_HEADER: ADDR, CLIENT_IP_SECRET_HEADER: OTHER},
            {"x-forwarded-for": ADDR},
        )
    }
    assert seen <= {"signed", "proxy", "peer"} and {"signed", "peer"} <= seen


def test_the_proof_reason_goes_to_the_log_once_per_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from pravrudhi.api import server

    server._last_proof_label.clear()
    monkeypatch.delenv(CLIENT_IP_SECRET_ENV, raising=False)
    with caplog.at_level("WARNING"):
        health(tmp_path)
        health(tmp_path)
        assert [r.getMessage() for r in caplog.records if "client address proof" in r.getMessage()] == [
            "client address proof is not valid: engine_unset"
        ]
        monkeypatch.setenv(CLIENT_IP_SECRET_ENV, SECRET)
        health(tmp_path, {CLIENT_IP_HEADER: ADDR, CLIENT_IP_SECRET_HEADER: SECRET})  # valid: no line
        health(tmp_path, {CLIENT_IP_HEADER: ADDR, CLIENT_IP_SECRET_HEADER: OTHER})
    msgs = [r.getMessage() for r in caplog.records if "client address proof" in r.getMessage()]
    assert msgs[-1] == "client address proof is not valid: mismatch" and len(msgs) == 2
    assert ADDR not in " ".join(msgs) and SECRET not in " ".join(msgs)
