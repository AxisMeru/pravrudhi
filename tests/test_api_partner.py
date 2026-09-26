"""The partner API's first endpoint: POST /api/v1/analyse-facts, wrapping application.nyaya_agent.NyayaAgent
over real FastAPI routing (house rule: no stub scorer as evidence -- but the AGENT here is exercised with the
same scripted Judge/Registry test doubles `test_nyaya_agent.py` itself uses, since this test file's job is the
HTTP wiring -- request parsing, response shape, error mapping, and the safety limits reviewer 1 required
(rate limit, concurrency cap, input caps, no path disclosure, clean 503 on a binary-sha mismatch) -- not
re-proving the agent loop's own decision logic, which is already covered there against the real pinned Lean
binary.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pravrudhi.api.partner import PartnerApiConfig, build_partner_router, load_partner_api_config
from pravrudhi.application import nyaya_lean_registry as reg
from pravrudhi.application.nyaya_agent import RETENTION_NOTICE, AgentConfig, BinaryShaMismatch, NyayaAgent
from pravrudhi.application.nyaya_judges import ElementJudgment, JudgeRequest

TOY_FACTS = [
    "TOY: Kiran was engaged to Lata and told her he would marry her in the spring.",
    "TOY: Kiran had already decided never to marry Lata when he made that promise.",
    "TOY: Relying on the promise, Lata had sexual intercourse with Kiran.",
]
BNS69_EL = [
    "induces the woman by deceitful means",
    "sexual intercourse with the woman actually occurs",
]
BNS69_DENY = "the sexual intercourse amounts to the offence of rape"


def _est(fid: str, quote: str, p: float = 0.97) -> ElementJudgment:
    return ElementJudgment("established", p, fid, quote, "model")


def _not(p: float = 0.03) -> ElementJudgment:
    return ElementJudgment("not_established", p)


class ScriptedJudge:
    def __init__(self, script: dict[str, list[ElementJudgment | Exception]]) -> None:
        self.script = script

    def judge(self, request: JudgeRequest) -> ElementJudgment:
        queue = self.script[request.element]
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        return item

    name = "scripted-test-judge"


class ScriptedRegistry:
    sha256 = "scripted-test-registry"

    def __init__(self) -> None:
        self.contracts = {"bns69": reg.DescribedContract("bns69", list(BNS69_EL), [BNS69_DENY])}
        self.sources = {"bns69": ["Bharatiya Nyaya Sanhita §69"]}

    def list_contracts(self) -> dict[str, list[str]]:
        return dict(self.sources)

    def describe(self, contract_id: str) -> reg.DescribedContract:
        return self.contracts[contract_id]

    def source_text(self, contract_id: str) -> str:
        return f"RETRIEVED statute text for {contract_id}"

    def check(self, assertions: dict, contract_id: str) -> dict[str, Any]:
        c = self.contracts[contract_id]
        met = {k for k, v in assertions.items() if v}
        denied = [e for e in c.denials if e in met]
        omitted = [e for e in c.elements if e not in met]
        flagged = denied or omitted
        return {
            "verdict": "flagged" if flagged else "grounded",
            "denied_claims": denied,
            "unlicensed_claims": [],
            "omitted_claims": omitted,
        }


def _proof_script() -> dict[str, list[ElementJudgment]]:
    return {
        BNS69_EL[0]: [_est("F2", "never to marry Lata")],
        BNS69_EL[1]: [_est("F3", "Lata had sexual intercourse with Kiran")],
        BNS69_DENY: [_not()],
    }


def _agent(tmp_path: Path, script: dict[str, list[ElementJudgment]]) -> NyayaAgent:
    config = AgentConfig(
        tau=0.74,
        refer_band=(0.5, 0.74),
        max_retries=2,
        audit_dir=tmp_path / "audit",
        judge_statute_text={"bns69": "TRAINING statute text for bns69"},
        validated_contracts=frozenset({"bns69"}),
    )
    return NyayaAgent(ScriptedJudge(script), ScriptedRegistry(), config)


#: Generous enough that the request-caps/shape tests below never trip the default rate limit by accident.
_NO_LIMIT_CONFIG = PartnerApiConfig(rate_limit_per_minute=1000, max_concurrent=100, trust_proxy_header=False)


def _client(
    tmp_path: Path,
    script: dict[str, list[ElementJudgment]] | None = None,
    *,
    config: PartnerApiConfig = _NO_LIMIT_CONFIG,
) -> TestClient:
    agent = _agent(tmp_path, script if script is not None else _proof_script())
    app = FastAPI()
    app.include_router(build_partner_router(tmp_path, agent_factory=lambda _root: agent, config=config))
    return TestClient(app)


def _req(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"facts": TOY_FACTS, "narrative": "TOY narrative.", "contract_ids": ["bns69"]}
    base.update(over)
    return base


def test_proof_response_shape_and_status(tmp_path: Path) -> None:
    c = _client(tmp_path)
    resp = c.post("/api/v1/analyse-facts", json=_req())
    assert resp.status_code == 200
    body = resp.json()
    assert body["judge"] == "scripted-test-judge"
    assert len(body["contracts"]) == 1
    contract = body["contracts"][0]
    assert contract["contract_id"] == "bns69"
    assert contract["outcome"] == "PROOF"
    assert contract["reason"] == "all_elements_established"


def test_quote_source_is_visible_per_element(tmp_path: Path) -> None:
    # Lead-2-assistant's explicit requirement: whole-fact claims need to be visible to the user, not just
    # the final verdict -- quote_source must survive the HTTP boundary, per element.
    c = _client(tmp_path)
    resp = c.post("/api/v1/analyse-facts", json=_req())
    body = resp.json()
    elements = body["contracts"][0]["elements"]
    established = [e for e in elements if e["status"] == "established"]
    assert established, "expected at least one established element in the PROOF fixture"
    for e in established:
        assert e["quote_source"] == "model"
        assert e["quote"] is not None
        assert e["fact_id"] is not None


def _unreachable_factory(_root: Path) -> NyayaAgent:
    raise AssertionError("the agent factory must not be called for a request that fails validation first")


def test_empty_facts_is_422() -> None:
    app = FastAPI()
    app.include_router(
        build_partner_router(Path("."), agent_factory=_unreachable_factory, config=_NO_LIMIT_CONFIG)
    )
    c = TestClient(app)
    resp = c.post("/api/v1/analyse-facts", json={"facts": [], "contract_ids": ["bns69"]})
    assert resp.status_code == 422


def test_denial_outcome(tmp_path: Path) -> None:
    script = {
        BNS69_EL[0]: [_est("F2", "never to marry Lata")],
        BNS69_EL[1]: [_est("F3", "Lata had sexual intercourse with Kiran")],
        BNS69_DENY: [_est("F3", "Lata had sexual intercourse with Kiran")],
    }
    c = _client(tmp_path, script)
    resp = c.post("/api/v1/analyse-facts", json=_req())
    body = resp.json()
    assert body["contracts"][0]["outcome"] == "DENIAL"


def test_unknown_contract_id_is_422(tmp_path: Path) -> None:
    c = _client(tmp_path)
    resp = c.post("/api/v1/analyse-facts", json=_req(contract_ids=["not_a_real_contract"]))
    assert resp.status_code == 422


# -- R1, 2026-09-25: an unreachable PRIMARY must be 503 judge_unavailable, never a 200 ABSTAIN -------------
# `NyayaAgent`'s own philosophy is that an exhausted-retries primary is a recorded, non-evidentiary
# ABSTAIN/judge_error outcome (module doc, "nothing here is evidence") -- correct for the engine's own
# testimony record, but an infrastructure failure must never look like a legal outcome to an HTTP caller or
# to monitoring. This maps ONLY that specific reason to 503; every other ABSTAIN/DENIAL/REFER_TO_LAWYER
# reason, including the second judge's own unavailable -> REFER_TO_LAWYER path, is untouched.


def test_primary_exhausting_retries_is_503_judge_unavailable(tmp_path: Path) -> None:
    """The primary raises on every attempt (max_retries=2 -> 3 total attempts, all failing): the request
    never reaches the caller as a 200 ABSTAIN."""
    script: dict[str, list[ElementJudgment | Exception]] = {
        BNS69_EL[0]: [ConnectionError("primary unreachable")],
        BNS69_EL[1]: [_est("F3", "Lata had sexual intercourse with Kiran")],
        BNS69_DENY: [_not()],
    }
    c = _client(tmp_path, script)
    resp = c.post("/api/v1/analyse-facts", json=_req())
    assert resp.status_code == 503
    assert resp.json() == {"error": "judge_unavailable"}


def test_a_transient_blip_that_the_retry_loop_rides_out_stays_200(tmp_path: Path) -> None:
    """Mid-run transient blips keep their existing retry behaviour: fewer failures than max_retries, then a
    real answer, must still read as a normal 200 -- the 503 mapping only fires once retries are exhausted,
    never on a blip the loop already recovered from."""
    script: dict[str, list[ElementJudgment | Exception]] = {
        BNS69_EL[0]: [ConnectionError("transient blip"), _est("F2", "never to marry Lata")],
        BNS69_EL[1]: [_est("F3", "Lata had sexual intercourse with Kiran")],
        BNS69_DENY: [_not()],
    }
    c = _client(tmp_path, script)
    resp = c.post("/api/v1/analyse-facts", json=_req())
    assert resp.status_code == 200
    assert resp.json()["contracts"][0]["outcome"] == "PROOF"


def test_second_judge_unavailable_stays_refer_not_503(tmp_path: Path) -> None:
    """The second judge's own unavailable path is untouched: still REFER_TO_LAWYER, a legitimate safety
    outcome, never 503 -- distinguishing the two matters exactly because both start from "a judge errored"."""
    from pravrudhi.application.nyaya_judges import AndGateJudge

    primary_script: dict[str, list[ElementJudgment | Exception]] = {
        BNS69_EL[0]: [_est("F2", "never to marry Lata")],
        BNS69_EL[1]: [_est("F3", "Lata had sexual intercourse with Kiran")],
        BNS69_DENY: [_not()],
    }
    second_script: dict[str, list[ElementJudgment | Exception]] = {
        BNS69_EL[0]: [ConnectionError("second judge unreachable")],
        BNS69_EL[1]: [_est("F3s", "Lata had sexual intercourse with Kiran")],
        BNS69_DENY: [_not()],
    }
    gate = AndGateJudge(ScriptedJudge(primary_script), ScriptedJudge(second_script), tau_primary=0.74, tau_second=0.97)
    config = AgentConfig(
        tau=0.74, refer_band=(0.5, 0.74), max_retries=2, audit_dir=tmp_path / "audit",
        judge_statute_text={"bns69": "TRAINING statute text for bns69"},
        validated_contracts=frozenset({"bns69"}),
    )
    agent = NyayaAgent(gate, ScriptedRegistry(), config)
    app = FastAPI()
    app.include_router(build_partner_router(tmp_path, agent_factory=lambda _root: agent, config=_NO_LIMIT_CONFIG))
    resp = TestClient(app).post("/api/v1/analyse-facts", json=_req())
    assert resp.status_code == 200
    assert resp.json()["contracts"][0]["outcome"] == "REFER_TO_LAWYER"
    assert resp.json()["contracts"][0]["reason"] == "second_judge_unavailable"


def _config_c_client(
    tmp_path: Path,
    *,
    primary_script: dict[str, list[ElementJudgment | Exception]],
    second_script: dict[str, list[ElementJudgment | Exception]],
    config: PartnerApiConfig,
) -> TestClient:
    from pravrudhi.application.nyaya_judges import AndGateJudge

    gate = AndGateJudge(ScriptedJudge(primary_script), ScriptedJudge(second_script), tau_primary=0.74, tau_second=0.97)
    agent_config = AgentConfig(
        tau=0.74, refer_band=(0.5, 0.74), max_retries=2, audit_dir=tmp_path / "audit",
        judge_statute_text={"bns69": "TRAINING statute text for bns69"},
        validated_contracts=frozenset({"bns69"}),
    )
    agent = NyayaAgent(gate, ScriptedRegistry(), agent_config)
    app = FastAPI()
    app.include_router(build_partner_router(tmp_path, agent_factory=lambda _root: agent, config=config))
    return TestClient(app)


class TestSecondJudgeDebugFields:
    """Lead-2's ask (2026-09-25, config-C smoke): the second judge's own p_established/tau/skip-reason/
    logit-distance/refer-band/unavailable fields are already computed by AndGateJudge and already survive
    onto `ElementResult` -- diagnosing a config-C disagreement needed a direct Python call because the HTTP
    response silently dropped every one of them. Two independent gates, both required: `configs/
    partner_api.yaml` (or NYAYA_DEBUG_SECOND_JUDGE_FIELDS) must opt the DEPLOYMENT in, and the caller must
    also pass `?debug_second_judge=true` on that specific request -- this is a public, unauthenticated
    partner endpoint (module docstring), so a caller alone flipping a query param can never expose these on
    a deployment that hasn't opted in."""

    def _scripts(self) -> tuple[dict, dict]:
        primary: dict[str, list[ElementJudgment | Exception]] = {
            BNS69_EL[0]: [_est("F2", "never to marry Lata", p=0.9998)],
            BNS69_EL[1]: [_est("F3", "Lata had sexual intercourse with Kiran", p=0.99999)],
            BNS69_DENY: [_not()],
        }
        second: dict[str, list[ElementJudgment | Exception]] = {
            BNS69_EL[0]: [_est("F2s", "never to marry Lata", p=0.65)],
            BNS69_EL[1]: [_est("F3s", "Lata had sexual intercourse with Kiran", p=0.84)],
            BNS69_DENY: [_not()],
        }
        return primary, second

    def test_fields_appear_when_both_the_deployment_and_the_request_opt_in(self, tmp_path: Path) -> None:
        primary, second = self._scripts()
        config = PartnerApiConfig(
            rate_limit_per_minute=1000, max_concurrent=100, trust_proxy_header=False,
            debug_second_judge_fields_enabled=True,
        )
        c = _config_c_client(tmp_path, primary_script=primary, second_script=second, config=config)
        resp = c.post("/api/v1/analyse-facts?debug_second_judge=true", json=_req())
        assert resp.status_code == 200
        elements = resp.json()["contracts"][0]["elements"]
        el0 = next(e for e in elements if e["element"] == BNS69_EL[0])
        assert el0["p_established_second"] == pytest.approx(0.65)
        assert el0["second_unavailable"] is False
        assert "tau_second" in el0 and "second_skip_reason" in el0 and "second_logit_distance" in el0
        assert "second_refer_band_fired" in el0

    def test_fields_absent_when_the_request_does_not_opt_in(self, tmp_path: Path) -> None:
        """Same deployment (opted in) but a caller that never asked -- today's exact response shape."""
        primary, second = self._scripts()
        config = PartnerApiConfig(
            rate_limit_per_minute=1000, max_concurrent=100, trust_proxy_header=False,
            debug_second_judge_fields_enabled=True,
        )
        c = _config_c_client(tmp_path, primary_script=primary, second_script=second, config=config)
        resp = c.post("/api/v1/analyse-facts", json=_req())
        el0 = resp.json()["contracts"][0]["elements"][0]
        for field in (
            "p_established_second", "tau_second", "second_skip_reason",
            "second_logit_distance", "second_refer_band_fired", "second_unavailable",
        ):
            assert field not in el0, f"{field} must not appear when the caller never asked for it"

    def test_fields_absent_when_the_deployment_has_not_opted_in_even_if_the_request_asks(self, tmp_path: Path) -> None:
        """The deployment-level gate wins: a caller alone cannot turn this on for a deployment that hasn't."""
        primary, second = self._scripts()
        config = PartnerApiConfig(rate_limit_per_minute=1000, max_concurrent=100, trust_proxy_header=False)
        assert config.debug_second_judge_fields_enabled is False
        c = _config_c_client(tmp_path, primary_script=primary, second_script=second, config=config)
        resp = c.post("/api/v1/analyse-facts?debug_second_judge=true", json=_req())
        el0 = resp.json()["contracts"][0]["elements"][0]
        assert "p_established_second" not in el0

    def test_default_response_shape_is_completely_unchanged_when_off(self, tmp_path: Path) -> None:
        """Byte-for-byte the same keys as before this feature existed, for the ordinary (no config C, no
        flags) case every existing test above already exercises."""
        c = _client(tmp_path)
        resp = c.post("/api/v1/analyse-facts", json=_req())
        el0 = resp.json()["contracts"][0]["elements"][0]
        assert set(el0) == {
            "element", "is_denial", "status", "claimed", "p_established", "fact_id", "quote", "start", "end",
            "quote_check", "attempts", "occurrences", "offsets_source", "quote_source", "error",
        }


def test_retention_notice_is_on_every_analyse_facts_response(tmp_path: Path) -> None:
    """Issue #39's own API-notice ask: a partner API caller who never sees the web UI still gets the exact
    retention notice text, verbatim, on every response -- not just in documentation."""
    c = _client(tmp_path)
    resp = c.post("/api/v1/analyse-facts", json=_req())
    assert resp.json()["retention_notice"] == RETENTION_NOTICE


def test_judge_misconfigured_still_503_unchanged(tmp_path: Path) -> None:
    """A 4xx config fault (JudgeMisconfigured) is untouched by this change -- still 503, via its own
    existing except clause, not the new judge_error check (there is no 401 mapping anywhere in this repo
    for JudgeMisconfigured; R1's "401 path" phrase does not match the code -- verified by grep before
    writing this test)."""
    from pravrudhi.models.openai_compat import HTTPStatusError

    class _ConfigFaultJudge:
        name = "fault-judge"

        def judge(self, request: JudgeRequest) -> ElementJudgment:
            try:
                raise HTTPStatusError(401, "bad key")
            except HTTPStatusError as inner:
                err = RuntimeError(f"judge backend 0 failed: {inner}")
                err.__cause__ = inner
                raise err from inner

    config = AgentConfig(
        tau=0.74, refer_band=(0.5, 0.74), max_retries=2, audit_dir=tmp_path / "audit",
        judge_statute_text={"bns69": "TRAINING statute text for bns69"},
        validated_contracts=frozenset({"bns69"}),
    )
    agent = NyayaAgent(_ConfigFaultJudge(), ScriptedRegistry(), config)
    app = FastAPI()
    app.include_router(build_partner_router(tmp_path, agent_factory=lambda _root: agent, config=_NO_LIMIT_CONFIG))
    resp = TestClient(app).post("/api/v1/analyse-facts", json=_req())
    assert resp.status_code == 503
    assert resp.json()["detail"].startswith("nyaya agent unavailable:")  # existing JudgeMisconfigured mapping, untouched


_REAL_SCORE_BIN = Path(os.environ.get("PRABHASA_NYAYA_SCORE_BIN", "prabhasa-nyaya-score-not-configured"))
_requires_score_bin = pytest.mark.skipif(
    not _REAL_SCORE_BIN.exists(),
    reason=f"the pinned prabhasa-nyaya score binary is not built on this host ({_REAL_SCORE_BIN})",
)


class TestRealDeadPortPrimary:
    """R1, 2026-09-25: "including a real dead-port primary test at the HTTP layer" -- a REAL HouseJudge
    pointed at an unreachable port (no ScriptedJudge double), through the real pinned Lean binary, through
    the real FastAPI TestClient. Proves the actual engine's exception (not a hand-simulated one) reaches
    partner.py's mapping."""

    SCORE_BIN = _REAL_SCORE_BIN
    requires_score_bin = _requires_score_bin

    @pytest.mark.requires_score_bin
    @requires_score_bin
    def test_dead_port_primary_is_503_judge_unavailable_over_real_http(self, tmp_path: Path) -> None:
        from pravrudhi.application.nyaya_agent import BinaryRegistry
        from pravrudhi.application.nyaya_judges import HouseJudge

        real_registry = BinaryRegistry(self.SCORE_BIN, pinned_sha256=None)
        primary = HouseJudge(base_url="http://127.0.0.1:1/v1", model=None, tau=0.74, statute_chars=600, timeout_s=2)
        config = AgentConfig(
            tau=0.74, refer_band=(0.5, 0.74), max_retries=1, audit_dir=tmp_path / "audit",
            judge_statute_text={"bns69": "Whoever, by deceitful means or by making promise to marry to a "
                                "woman without any intention of fulfilling the same, has sexual intercourse "
                                "with her, such sexual intercourse not amounting to the offence of rape, "
                                "shall be punished."},
            validated_contracts=frozenset({"bns69"}),
        )
        agent = NyayaAgent(primary, real_registry, config)
        app = FastAPI()
        app.include_router(build_partner_router(tmp_path, agent_factory=lambda _root: agent, config=_NO_LIMIT_CONFIG))
        resp = TestClient(app).post("/api/v1/analyse-facts", json=_req())
        assert resp.status_code == 503
        assert resp.json() == {"error": "judge_unavailable"}


# -- reviewer 1's requirements ---------------------------------------------------------------------------


def test_response_has_run_id_but_no_audit_path(tmp_path: Path) -> None:
    # (c): audit_path was an absolute server filesystem path disclosed to any anonymous caller. Dropped.
    c = _client(tmp_path)
    resp = c.post("/api/v1/analyse-facts", json=_req())
    body = resp.json()
    assert body["run_id"]
    assert "audit_path" not in body


def test_contract_ids_is_required(tmp_path: Path) -> None:
    # (b): omitting contract_ids used to mean "all 23 contracts" -- dozens of GPU calls from one public,
    # unauthenticated request. Now refused before the agent is ever touched.
    app = FastAPI()
    app.include_router(
        build_partner_router(tmp_path, agent_factory=_unreachable_factory, config=_NO_LIMIT_CONFIG)
    )
    c = TestClient(app)
    resp = c.post("/api/v1/analyse-facts", json={"facts": TOY_FACTS})
    assert resp.status_code == 422


def test_contract_ids_over_five_is_422(tmp_path: Path) -> None:
    app = FastAPI()
    app.include_router(
        build_partner_router(tmp_path, agent_factory=_unreachable_factory, config=_NO_LIMIT_CONFIG)
    )
    c = TestClient(app)
    resp = c.post("/api/v1/analyse-facts", json=_req(contract_ids=["a", "b", "c", "d", "e", "f"]))
    assert resp.status_code == 422


def test_more_than_eight_facts_is_422(tmp_path: Path) -> None:
    app = FastAPI()
    app.include_router(
        build_partner_router(tmp_path, agent_factory=_unreachable_factory, config=_NO_LIMIT_CONFIG)
    )
    c = TestClient(app)
    resp = c.post("/api/v1/analyse-facts", json=_req(facts=["fact"] * 9))
    assert resp.status_code == 422


def test_a_fact_over_four_thousand_chars_is_422(tmp_path: Path) -> None:
    app = FastAPI()
    app.include_router(
        build_partner_router(tmp_path, agent_factory=_unreachable_factory, config=_NO_LIMIT_CONFIG)
    )
    c = TestClient(app)
    resp = c.post("/api/v1/analyse-facts", json=_req(facts=["x" * 4001]))
    assert resp.status_code == 422


def test_eight_facts_and_five_contracts_are_accepted(tmp_path: Path) -> None:
    # The caps are a ceiling, not a trap -- exactly at the limit must still work. Registry only knows
    # "bns69", so this exercises validation acceptance via an unknown-contract 422 from the AGENT layer,
    # not the 422 Pydantic would raise for exceeding the cap.
    app = FastAPI()
    app.include_router(
        build_partner_router(tmp_path, agent_factory=_unreachable_factory, config=_NO_LIMIT_CONFIG)
    )
    # raise_server_exceptions=False: _unreachable_factory raises AssertionError once reached with valid
    # input; the point of this test is that Pydantic's own validation let the request through (a 500 from
    # the handler), not to assert anything about unhandled-exception behaviour in general.
    c = TestClient(app, raise_server_exceptions=False)
    resp = c.post(
        "/api/v1/analyse-facts",
        json=_req(facts=["fact"] * 8, contract_ids=["a", "b", "c", "d", "e"]),
    )
    assert resp.status_code == 500


def test_sixth_request_in_a_minute_is_429_with_retry_after(tmp_path: Path) -> None:
    config = PartnerApiConfig(rate_limit_per_minute=6, max_concurrent=100, trust_proxy_header=False)
    c = _client(tmp_path, config=config)
    statuses = [c.post("/api/v1/analyse-facts", json=_req()).status_code for _ in range(6)]
    assert statuses == [200] * 6
    resp = c.post("/api/v1/analyse-facts", json=_req())
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers
    # A rate-limited caller gets a real JSON body, not an empty 429 -- something a partner's client can
    # parse and log, not just a status code to notice.
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.json() == {"detail": "rate limit exceeded"}


def test_rate_limit_is_per_client_ip(tmp_path: Path) -> None:
    # TestClient always presents as the same peer address, so this exercises the counter keying logic
    # directly rather than simulating two real sockets.
    from pravrudhi.api.partner import RateLimiter

    limiter = RateLimiter(per_minute=2)
    assert limiter.allow("1.2.3.4") is True
    assert limiter.allow("1.2.3.4") is True
    assert limiter.allow("1.2.3.4") is False
    # A different IP has its own budget, untouched by the first.
    assert limiter.allow("5.6.7.8") is True


def test_trusted_proxy_header_used_only_when_configured(tmp_path: Path) -> None:
    config_untrusted = PartnerApiConfig(rate_limit_per_minute=1, max_concurrent=100, trust_proxy_header=False)
    c = _client(tmp_path, config=config_untrusted)
    # Two different spoofed X-Forwarded-For values from the SAME test client peer must share one budget,
    # since the header is not trusted.
    r1 = c.post("/api/v1/analyse-facts", json=_req(), headers={"X-Forwarded-For": "9.9.9.9"})
    r2 = c.post("/api/v1/analyse-facts", json=_req(), headers={"X-Forwarded-For": "8.8.8.8"})
    assert r1.status_code == 200
    assert r2.status_code == 429  # same underlying peer, budget of 1 already spent


def test_trust_proxy_header_without_a_trusted_proxy_allowlist_is_refused() -> None:
    # Reviewer 2: X-Forwarded-For is entirely client-controlled, so trust_proxy_header=true makes the rate
    # limit (and IP-keyed anything else) free to bypass unless the deployment also names which peers are
    # actually allowed to set that header truthfully.
    with pytest.raises(ValueError, match="trusted_proxies"):
        PartnerApiConfig(rate_limit_per_minute=1, max_concurrent=1, trust_proxy_header=True, trusted_proxies=())


_CLIENT_IP_SECRET = "s" * 32  # exactly MIN_CLIENT_IP_SECRET_LENGTH; a valid, if not realistic, value


def test_client_ip_header_with_correct_secret_keys_per_caller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pravrudhi.api.partner import CLIENT_IP_HEADER, CLIENT_IP_SECRET_ENV, CLIENT_IP_SECRET_HEADER

    monkeypatch.setenv(CLIENT_IP_SECRET_ENV, _CLIENT_IP_SECRET)
    config = PartnerApiConfig(rate_limit_per_minute=1, max_concurrent=100, trust_proxy_header=False)
    c = _client(tmp_path, config=config)
    # Same TestClient peer both times (no proxy in play at all), but two DIFFERENT proven client-IPs --
    # each gets its own budget of 1, exactly what a Worker→RunPod/tunnel deployment needs to stop one
    # caller's traffic from spending everyone else's rate-limit budget.
    r1 = c.post(
        "/api/v1/analyse-facts", json=_req(),
        headers={CLIENT_IP_HEADER: "203.0.113.1", CLIENT_IP_SECRET_HEADER: _CLIENT_IP_SECRET},
    )
    r2 = c.post(
        "/api/v1/analyse-facts", json=_req(),
        headers={CLIENT_IP_HEADER: "203.0.113.2", CLIENT_IP_SECRET_HEADER: _CLIENT_IP_SECRET},
    )
    assert r1.status_code == 200
    assert r2.status_code == 200
    # The SAME proven client-IP a second time spends the same budget as the first call.
    r3 = c.post(
        "/api/v1/analyse-facts", json=_req(),
        headers={CLIENT_IP_HEADER: "203.0.113.1", CLIENT_IP_SECRET_HEADER: _CLIENT_IP_SECRET},
    )
    assert r3.status_code == 429


def test_client_ip_header_without_the_secret_falls_back_to_the_shared_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pravrudhi.api.partner import CLIENT_IP_HEADER, CLIENT_IP_SECRET_ENV

    monkeypatch.delenv(CLIENT_IP_SECRET_ENV, raising=False)  # not configured: fail closed
    config = PartnerApiConfig(rate_limit_per_minute=1, max_concurrent=100, trust_proxy_header=False)
    c = _client(tmp_path, config=config)
    # Two different spoofed client-IP headers, no secret at all -- must NOT be believed; both calls share
    # the same underlying-peer budget, exactly as an untrusted X-Forwarded-For already does above.
    r1 = c.post("/api/v1/analyse-facts", json=_req(), headers={CLIENT_IP_HEADER: "203.0.113.1"})
    r2 = c.post("/api/v1/analyse-facts", json=_req(), headers={CLIENT_IP_HEADER: "203.0.113.2"})
    assert r1.status_code == 200
    assert r2.status_code == 429  # same underlying peer, budget of 1 already spent


def test_client_ip_header_with_wrong_secret_falls_back_to_the_shared_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pravrudhi.api.partner import CLIENT_IP_HEADER, CLIENT_IP_SECRET_ENV, CLIENT_IP_SECRET_HEADER

    monkeypatch.setenv(CLIENT_IP_SECRET_ENV, _CLIENT_IP_SECRET)
    config = PartnerApiConfig(rate_limit_per_minute=1, max_concurrent=100, trust_proxy_header=False)
    c = _client(tmp_path, config=config)
    r1 = c.post(
        "/api/v1/analyse-facts", json=_req(),
        headers={CLIENT_IP_HEADER: "203.0.113.1", CLIENT_IP_SECRET_HEADER: "wrong" * 8},
    )
    r2 = c.post(
        "/api/v1/analyse-facts", json=_req(),
        headers={CLIENT_IP_HEADER: "203.0.113.2", CLIENT_IP_SECRET_HEADER: "wrong" * 8},
    )
    assert r1.status_code == 200
    assert r2.status_code == 429  # a wrong secret is exactly as unbelievable as no secret at all


def test_client_ip_secret_too_short_is_treated_as_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pravrudhi.api.partner import CLIENT_IP_HEADER, CLIENT_IP_SECRET_ENV, CLIENT_IP_SECRET_HEADER

    short_secret = "too-short"
    monkeypatch.setenv(CLIENT_IP_SECRET_ENV, short_secret)
    config = PartnerApiConfig(rate_limit_per_minute=1, max_concurrent=100, trust_proxy_header=False)
    c = _client(tmp_path, config=config)
    r1 = c.post(
        "/api/v1/analyse-facts", json=_req(),
        headers={CLIENT_IP_HEADER: "203.0.113.1", CLIENT_IP_SECRET_HEADER: short_secret},
    )
    r2 = c.post(
        "/api/v1/analyse-facts", json=_req(),
        headers={CLIENT_IP_HEADER: "203.0.113.2", CLIENT_IP_SECRET_HEADER: short_secret},
    )
    assert r1.status_code == 200
    assert r2.status_code == 429  # a short configured secret is fail-closed, same as unset


def _blocking_agent_factory(release: threading.Event, entered: threading.Event) -> Any:
    class _BlockingAgent:
        def run(self, *_a: Any, **_kw: Any) -> Any:
            entered.set()
            release.wait(timeout=5)

            class _Result:
                #: analyse_facts_ep reads .contracts directly (judge_error -> 503 mapping) before calling
                #: .to_dict() -- this double must carry both, matching AgentRun's real shape.
                contracts: list[Any] = []

                def to_dict(self) -> dict[str, Any]:
                    return {
                        "run_id": "blocked-run",
                        "judge": "blocking",
                        "score_sha256": "x",
                        "facts": [],
                        "contracts": [],
                        "provenance": "agama",
                    }

            return _Result()

    return _BlockingAgent()


def test_third_concurrent_request_is_503_when_max_concurrent_is_two(tmp_path: Path) -> None:
    release = threading.Event()
    entered = threading.Event()
    agent = _blocking_agent_factory(release, entered)
    config = PartnerApiConfig(rate_limit_per_minute=1000, max_concurrent=2, trust_proxy_header=False)
    app = FastAPI()
    app.include_router(build_partner_router(tmp_path, agent_factory=lambda _r: agent, config=config))
    c = TestClient(app)

    results: dict[str, int] = {}

    def _call(name: str) -> None:
        results[name] = c.post("/api/v1/analyse-facts", json=_req()).status_code

    t1 = threading.Thread(target=_call, args=("a",))
    t2 = threading.Thread(target=_call, args=("b",))
    t1.start()
    t2.start()
    entered.wait(timeout=5)
    time.sleep(0.05)  # let both threads register as in-flight before the third fires

    resp3 = c.post("/api/v1/analyse-facts", json=_req())
    assert resp3.status_code == 503

    release.set()
    t1.join(timeout=5)
    t2.join(timeout=5)
    assert results == {"a": 200, "b": 200}


def test_rate_limiter_table_is_bounded_under_ip_rotation() -> None:
    # Reviewer 1's fix-before-merge finding: unbounded memory under real IP rotation (100k distinct callers
    # would otherwise mean 100k table entries forever). With no stale entries ever appearing in this test
    # (one fixed window throughout), the table caps out at max_keys and new keys past that are refused --
    # not evicted-and-replaced, which is exactly the bug reviewer 2 caught below.
    from pravrudhi.api.partner import RateLimiter

    limiter = RateLimiter(per_minute=6, max_keys=1000)
    for i in range(100_000):
        limiter.allow(f"10.0.{i // 256}.{i % 256}")
    assert len(limiter._windows) <= 1000


def test_eviction_never_resets_a_still_current_over_limit_key() -> None:
    # Reviewer 2's rejection of a50c3c1: the first version of this test asserted
    # `limiter.allow("victim") in (True, False)`, which is true of any bool and can never fail -- a
    # vacuous test that let a real exploit ship. Fixed to assert the actual guarantee: a target filled to
    # their limit, then a flood of new keys past the cap, must STILL be throttled afterward -- the hard
    # cap may never evict a key whose window is still current to make room for a new one.
    from pravrudhi.api.partner import RateLimiter

    limiter = RateLimiter(per_minute=2, max_keys=3)
    assert limiter.allow("victim") is True
    assert limiter.allow("victim") is True
    assert limiter.allow("victim") is False  # over limit, same window

    for i in range(10_000):
        limiter.allow(f"flood-{i}")

    assert limiter.allow("victim") is False  # still throttled -- eviction never let the attack reset it
    assert len(limiter._windows) <= 3


def test_reviewer_2s_exact_reproduction() -> None:
    # per_minute=5, max_keys=10: 5 allows + 1 denied for "attacker", then 50 flood keys in the same
    # window, then "attacker" must still be denied -- the exact scenario from reviewer 2's rejection of
    # a50c3c1.
    from pravrudhi.api.partner import RateLimiter

    limiter = RateLimiter(per_minute=5, max_keys=10)
    for _ in range(5):
        assert limiter.allow("attacker") is True
    assert limiter.allow("attacker") is False

    for i in range(50):
        limiter.allow(f"flood-{i}")

    assert limiter.allow("attacker") is False


def test_a_new_key_is_refused_outright_when_the_table_is_full_of_current_windows() -> None:
    # A brand-new caller arriving when the table is already at capacity, with every existing entry still
    # inside its current window (nothing stale to reclaim), must be refused -- never admitted by evicting
    # someone else's live entry.
    from pravrudhi.api.partner import RateLimiter

    limiter = RateLimiter(per_minute=10, max_keys=3)
    assert limiter.allow("a") is True
    assert limiter.allow("b") is True
    assert limiter.allow("c") is True
    assert limiter.allow("new-caller") is False
    assert set(limiter._windows.keys()) == {"a", "b", "c"}


def test_time_based_eviction_removes_only_stale_windows() -> None:
    from pravrudhi.api.partner import RateLimiter

    clock = [0.0]
    limiter = RateLimiter(per_minute=2, max_keys=1000, now=lambda: clock[0])
    limiter.allow("stale")  # window 0
    clock[0] = 3600.0  # 60 windows later -- "stale" is long expired
    for i in range(50):
        limiter.allow(f"fresh-{i}")
    assert "stale" not in limiter._windows


def test_binary_sha_mismatch_is_503_not_500(tmp_path: Path) -> None:
    def _factory(_root: Path) -> Any:
        raise BinaryShaMismatch("score binary sha256 does not match the pinned value")

    app = FastAPI()
    app.include_router(build_partner_router(tmp_path, agent_factory=_factory, config=_NO_LIMIT_CONFIG))
    c = TestClient(app)
    resp = c.post("/api/v1/analyse-facts", json=_req())
    assert resp.status_code == 503


class TestDebugSecondJudgeFieldsConfigLoading:
    def _write(self, tmp_path: Path, extra: str = "") -> None:
        (tmp_path / "configs").mkdir(exist_ok=True)
        (tmp_path / "configs" / "partner_api.yaml").write_text(
            "rate_limit_per_minute: 6\nmax_concurrent: 2\ntrust_proxy_header: false\n" + extra
        )

    def test_defaults_to_false_when_the_yaml_predates_this_field(self, tmp_path: Path) -> None:
        self._write(tmp_path)
        assert load_partner_api_config(tmp_path).debug_second_judge_fields_enabled is False

    def test_yaml_can_turn_it_on(self, tmp_path: Path) -> None:
        self._write(tmp_path, "debug_second_judge_fields_enabled: true\n")
        assert load_partner_api_config(tmp_path).debug_second_judge_fields_enabled is True

    def test_env_override_turns_it_on_over_a_yaml_that_predates_the_field(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._write(tmp_path)
        monkeypatch.setenv("NYAYA_DEBUG_SECOND_JUDGE_FIELDS", "true")
        assert load_partner_api_config(tmp_path).debug_second_judge_fields_enabled is True

    def test_env_override_recognizes_1_and_yes_too(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self._write(tmp_path)
        for value in ("1", "yes", "true"):
            monkeypatch.setenv("NYAYA_DEBUG_SECOND_JUDGE_FIELDS", value)
            assert load_partner_api_config(tmp_path).debug_second_judge_fields_enabled is True
        monkeypatch.setenv("NYAYA_DEBUG_SECOND_JUDGE_FIELDS", "0")
        assert load_partner_api_config(tmp_path).debug_second_judge_fields_enabled is False
