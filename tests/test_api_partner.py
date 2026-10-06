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
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pravrudhi.api import identity
from pravrudhi.api.identity import User
from pravrudhi.api.partner import PartnerApiConfig, build_partner_router, load_partner_api_config
from pravrudhi.application import nyaya_lean_registry as reg
from pravrudhi.application import tenancy
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
        self.requests: list[JudgeRequest] = []

    def judge(self, request: JudgeRequest) -> ElementJudgment:
        self.requests.append(request)
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
        # The fixture's primary/second cite different fact_ids ("F2" vs "F2s") -- a genuine disagreement.
        assert el0["second_fact_id"] == "F2s"
        assert el0["fact_id_disagreement"] is True

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
            "second_fact_id", "fact_id_disagreement",
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
        """Byte-for-byte the same keys as before this feature existed for every CONFIG-C DEBUG field (the
        five `_SECOND_JUDGE_DEBUG_FIELDS`), for the ordinary (no config C, no flags) case every existing test
        above already exercises. `binding_leg` (issue #37) is a deliberate exception: unlike the debug
        fields, it's always present -- the truthful element status it supports is a first-class response
        field, never gated behind a debug flag."""
        c = _client(tmp_path)
        resp = c.post("/api/v1/analyse-facts", json=_req())
        el0 = resp.json()["contracts"][0]["elements"][0]
        assert set(el0) == {
            "element", "is_denial", "status", "claimed", "p_established", "fact_id", "quote", "start", "end",
            "quote_check", "attempts", "occurrences", "offsets_source", "quote_source", "error", "binding_leg",
        }


class TestSecondJudgeFieldsForAuthenticatedCallers:
    """QUEUE.md 2026-09-27 ("per-leg scores for authenticated callers only"): a verified Supabase session or
    a valid org API key must see the second judge's per-element fields even on a deployment that has never
    turned on the `debug_second_judge_fields_enabled` gate above -- and an anonymous caller must never see
    them, on that same deployment, even if it presents the `?debug_second_judge=true` query flag. Every test
    here uses `_no_debug_config` (the gate off) throughout, so any field that appears comes only from the
    authenticated path added by this fix, never from `TestSecondJudgeDebugFields`'s own mechanism."""

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

    def _no_debug_config(self) -> PartnerApiConfig:
        config = PartnerApiConfig(rate_limit_per_minute=1000, max_concurrent=100, trust_proxy_header=False)
        assert config.debug_second_judge_fields_enabled is False
        return config

    def _client(self, tmp_path: Path) -> TestClient:
        primary, second = self._scripts()
        return _config_c_client(
            tmp_path, primary_script=primary, second_script=second, config=self._no_debug_config()
        )

    def test_a_verified_supabase_session_sees_the_fields_with_the_debug_gate_off(self, tmp_path: Path) -> None:
        c = self._client(tmp_path)
        c.app.dependency_overrides[identity.current_user] = lambda: User(
            id="u-1", email="partner@example.com", role="authenticated"
        )
        resp = c.post("/api/v1/analyse-facts", json=_req())
        assert resp.status_code == 200
        el0 = resp.json()["contracts"][0]["elements"][0]
        assert el0["p_established_second"] == pytest.approx(0.65)
        assert "tau_second" in el0 and "second_skip_reason" in el0 and "second_logit_distance" in el0
        assert "second_fact_id" in el0 and "fact_id_disagreement" in el0

    def test_a_valid_org_api_key_sees_the_fields_with_the_debug_gate_off(self, tmp_path: Path) -> None:
        tenancy.create_org(tmp_path, "acme", "Acme")
        secret = tenancy.create_key(tmp_path, "acme", label="prod").secret
        c = self._client(tmp_path)
        resp = c.post("/api/v1/analyse-facts", json=_req(), headers={tenancy.API_KEY_HEADER: secret})
        assert resp.status_code == 200
        el0 = resp.json()["contracts"][0]["elements"][0]
        assert el0["p_established_second"] == pytest.approx(0.65)

    def test_a_revoked_org_api_key_is_401_not_a_silent_anonymous_fallback(self, tmp_path: Path) -> None:
        tenancy.create_org(tmp_path, "acme", "Acme")
        created = tenancy.create_key(tmp_path, "acme", label="prod")
        tenancy.revoke_key(tmp_path, created.record.key_id)
        c = self._client(tmp_path)
        resp = c.post("/api/v1/analyse-facts", json=_req(), headers={tenancy.API_KEY_HEADER: created.secret})
        assert resp.status_code == 401

    def test_an_anonymous_caller_never_sees_the_fields_even_with_the_debug_query_flag(self, tmp_path: Path) -> None:
        """No Supabase override, no API key header -- the identical anonymous caller `TestSecondJudgeDebugFields`
        covers, plus the debug query flag on a deployment that never opted the gate in. Neither the debug path
        nor the new authenticated path may expose these fields here."""
        c = self._client(tmp_path)
        resp = c.post("/api/v1/analyse-facts?debug_second_judge=true", json=_req())
        assert resp.status_code == 200
        el0 = resp.json()["contracts"][0]["elements"][0]
        for field in (
            "p_established_second", "tau_second", "second_skip_reason",
            "second_logit_distance", "second_refer_band_fired", "second_unavailable",
            "second_fact_id", "fact_id_disagreement",
        ):
            assert field not in el0, f"{field} leaked to an anonymous caller"


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
    assert resp.json()["error"] == "agent_unavailable"  # #318: a stable code and a fixed message, no exception text


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


# --- service window and /status ---------------------------------------------------------------------------

from datetime import UTC, datetime  # noqa: E402

from pravrudhi.application.service_window import ServiceWindow  # noqa: E402

_LONDON = ServiceWindow.from_config({"timezone": "Europe/London", "open": "09:00", "close": "21:00"})


def _window_client(tmp_path: Path, now: datetime, *, enforce: bool = True) -> TestClient:
    cfg = PartnerApiConfig(
        rate_limit_per_minute=1000, max_concurrent=100, trust_proxy_header=False,
        service_window=_LONDON, service_window_enforce=enforce,
    )
    agent = _agent(tmp_path, _proof_script())
    app = FastAPI()
    app.include_router(
        build_partner_router(tmp_path, agent_factory=lambda _r: agent, config=cfg, clock=lambda: now)
    )
    return TestClient(app)


def _utc(*a: int) -> datetime:
    return datetime(*a, tzinfo=UTC)


def test_status_reports_open_and_next_open(tmp_path: Path) -> None:
    body = _window_client(tmp_path, _utc(2026, 9, 30, 12, 0)).get("/api/v1/status").json()  # 13:00 BST
    w = body["service_window"]
    assert w["open_now"] is True and w["enforced"] is True and w["timezone"] == "Europe/London"
    assert body["judge"] == {"state": "unknown", "checked_at": None} and body["engine_version"]


def test_status_after_close_names_the_next_opening(tmp_path: Path) -> None:
    w = _window_client(tmp_path, _utc(2026, 9, 30, 21, 30)).get("/api/v1/status").json()["service_window"]  # 22:30 BST
    assert w["open_now"] is False and w["next_open_utc"] == "2026-10-01T08:00:00+00:00"  # 09:00 BST


def test_analyse_facts_outside_the_window_is_an_immediate_503_naming_it(tmp_path: Path) -> None:
    r = _window_client(tmp_path, _utc(2026, 9, 30, 23, 0)).post("/api/v1/analyse-facts", json=_req())  # 00:00 BST
    assert r.status_code == 503
    assert r.json()["error"] == "outside_service_window"
    assert r.json()["next_open_utc"] == "2026-10-01T08:00:00+00:00"
    assert r.json()["window"] == {"timezone": "Europe/London", "open": "09:00", "close": "21:00"}
    assert int(r.headers["Retry-After"]) == 9 * 3600


def test_analyse_facts_inside_the_window_runs(tmp_path: Path) -> None:
    r = _window_client(tmp_path, _utc(2026, 9, 30, 19, 15)).post("/api/v1/analyse-facts", json=_req())  # 20:15 BST
    assert r.status_code == 200


def test_the_window_edges_open_is_inclusive_close_is_exclusive() -> None:
    assert _LONDON.is_open(_utc(2026, 9, 30, 8, 0)) is True  # 09:00 BST
    assert _LONDON.is_open(_utc(2026, 9, 30, 7, 59)) is False
    assert _LONDON.is_open(_utc(2026, 9, 30, 19, 59)) is True
    assert _LONDON.is_open(_utc(2026, 9, 30, 20, 0)) is False  # 21:00 BST


def test_next_open_follows_the_wall_clock_across_a_dst_change() -> None:
    # 2026-10-25 01:00 UTC is the end of BST; 09:00 London that day is 09:00 UTC, not 08:00.
    assert _LONDON.next_open(_utc(2026, 10, 25, 3, 0)).isoformat() == "2026-10-25T09:00:00+00:00"


def test_window_that_wraps_midnight() -> None:
    w = ServiceWindow.from_config({"timezone": "UTC", "open": "22:00", "close": "02:00"})
    assert w.is_open(_utc(2026, 9, 30, 23, 0)) and w.is_open(_utc(2026, 10, 1, 1, 0))
    assert not w.is_open(_utc(2026, 10, 1, 3, 0))
    assert w.next_open(_utc(2026, 10, 1, 3, 0)).isoformat() == "2026-10-01T22:00:00+00:00"


def test_a_window_configured_but_not_enforced_reports_yet_never_refuses(tmp_path: Path) -> None:
    c = _window_client(tmp_path, _utc(2026, 9, 30, 23, 0), enforce=False)
    assert c.get("/api/v1/status").json()["service_window"]["enforced"] is False
    assert c.post("/api/v1/analyse-facts", json=_req()).status_code == 200


def test_status_reports_the_judge_last_seen_by_real_traffic_and_never_probes(tmp_path: Path) -> None:
    c = _window_client(tmp_path, _utc(2026, 9, 30, 12, 0))
    assert c.post("/api/v1/analyse-facts", json=_req()).status_code == 200
    assert c.get("/api/v1/status").json()["judge"]["state"] == "ready"


def test_no_window_configured_means_status_says_null_and_analyse_is_unchanged(tmp_path: Path) -> None:
    c = _client(tmp_path)
    assert c.get("/api/v1/status").json()["service_window"] is None
    assert c.post("/api/v1/analyse-facts", json=_req()).status_code == 200


def test_the_shipped_config_defines_the_hosted_window_but_does_not_enforce_it_by_default() -> None:
    cfg = load_partner_api_config(Path(__file__).resolve().parent.parent)
    assert cfg.service_window is not None and cfg.service_window.tz == "Europe/London"
    assert (cfg.service_window.open.hour, cfg.service_window.close.hour) == (9, 21)
    assert cfg.service_window_enforce is False


def test_env_turns_enforcement_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRAVRUDHI_SERVICE_WINDOW_ENFORCE", "1")
    assert load_partner_api_config(Path(__file__).resolve().parent.parent).service_window_enforce is True


def test_status_answers_on_a_root_with_no_partner_config(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from pravrudhi.api.server import create_app

    body = TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8008").get("/api/v1/status")
    assert body.status_code == 200
    assert body.json()["service_window"] is None
    assert body.json()["judge"]["state"] == "unknown"


# -- Issue #141: lean_attestation, judge_seen_ttl_s in config, fail closed with no config -----------------


def _wire_by_hand(contract_id: str, met: list[str]) -> str:
    """Independent of `reg_wire_line`: the wire grammar written out (no reserved characters in these names)."""
    claims = [f"G_SATISFIES(E(the conduct in the facts,AC),E({e},EL))" for e in met]
    return "\t".join(["REG", "live", contract_id, *claims])


def test_each_contract_carries_a_lean_attestation_a_caller_can_recompute(tmp_path: Path) -> None:
    import hashlib

    body = _client(tmp_path).post("/api/v1/analyse-facts", json=_req()).json()
    contract = body["contracts"][0]
    att = contract["lean_attestation"]
    assert set(att) == {"binary_sha256", "wire_sha256", "verdict"}
    assert att["binary_sha256"] == body["score_sha256"] == "scripted-test-registry"
    assert att["verdict"] == contract["lean"]["verdict"] == "grounded"
    met = [e for e, v in contract["assertions"].items() if v]
    expected = hashlib.sha256(_wire_by_hand("bns69", met).encode("utf-8")).hexdigest()
    assert att["wire_sha256"] == expected == reg.reg_wire_sha256(contract["assertions"], "bns69")


def test_same_assertions_same_wire_hash_and_one_changed_assertion_changes_it() -> None:
    a = {BNS69_EL[0]: True, BNS69_EL[1]: True, BNS69_DENY: False}
    assert reg.reg_wire_sha256(dict(a), "bns69") == reg.reg_wire_sha256(dict(a), "bns69")
    b = dict(a, **{BNS69_EL[1]: False})
    assert reg.reg_wire_sha256(b, "bns69") != reg.reg_wire_sha256(a, "bns69")
    assert reg.reg_wire_sha256(a, "bns69") != reg.reg_wire_sha256(a, "bns70")


def test_an_all_false_assertion_set_is_still_attested_with_its_flagged_verdict(tmp_path: Path) -> None:
    script = {BNS69_EL[0]: [_not()], BNS69_EL[1]: [_not()], BNS69_DENY: [_not()]}
    body = _client(tmp_path, script).post("/api/v1/analyse-facts", json=_req()).json()
    assert body["contracts"][0]["lean_attestation"]["verdict"] == "flagged"


def test_judge_seen_ttl_is_config_not_code(tmp_path: Path) -> None:
    assert load_partner_api_config(Path(__file__).resolve().parent.parent).judge_seen_ttl_s == 600.0
    now = [_utc(2026, 9, 30, 12, 0)]
    cfg = PartnerApiConfig(rate_limit_per_minute=1000, max_concurrent=100, trust_proxy_header=False,
                           judge_seen_ttl_s=30.0)
    agent = _agent(tmp_path, _proof_script())
    app = FastAPI()
    app.include_router(build_partner_router(tmp_path, agent_factory=lambda _r: agent, config=cfg, clock=lambda: now[0]))
    c = TestClient(app)
    assert c.post("/api/v1/analyse-facts", json=_req()).status_code == 200
    now[0] = _utc(2026, 9, 30, 12, 0, 20)
    assert c.get("/api/v1/status").json()["judge"]["state"] == "ready"
    now[0] = _utc(2026, 9, 30, 12, 1, 0)
    assert c.get("/api/v1/status").json()["judge"]["state"] == "unknown"


def test_enforce_on_with_no_partner_config_refuses_and_never_reaches_the_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PRAVRUDHI_SERVICE_WINDOW_ENFORCE", "1")
    assert not (tmp_path / "configs" / "partner_api.yaml").exists()
    app = FastAPI()
    app.include_router(build_partner_router(tmp_path, agent_factory=_unreachable_factory))
    resp = TestClient(app, raise_server_exceptions=False).post("/api/v1/analyse-facts", json=_req())
    assert resp.status_code == 503
    assert resp.json() == {"error": "service_config_missing"}


# -- #164: a cold judge is "warming", not a raw judge_unavailable -----------------------------------------


def _warming_client(tmp_path: Path, now: list[Any], script: dict[str, list[Any]], grace: float = 240.0) -> TestClient:
    cfg = PartnerApiConfig(rate_limit_per_minute=1000, max_concurrent=100, trust_proxy_header=False,
                           judge_warm_grace_s=grace, judge_warm_retry_s=30.0)
    agent = _agent(tmp_path, script)
    app = FastAPI()
    app.include_router(build_partner_router(tmp_path, agent_factory=lambda _r: agent, config=cfg, clock=lambda: now[0]))
    return TestClient(app)


def _dead_primary_script() -> dict[str, list[Any]]:
    return {
        BNS69_EL[0]: [ConnectionError("cold")],
        BNS69_EL[1]: [_est("F3", "Lata had sexual intercourse with Kiran")],
        BNS69_DENY: [_not()],
    }


class TestJudgesWarming:
    def test_first_failure_with_no_recent_ready_is_warming_with_retry_after(self, tmp_path: Path) -> None:
        now = [_utc(2026, 10, 2, 9, 0)]
        c = _warming_client(tmp_path, now, _dead_primary_script())
        resp = c.post("/api/v1/analyse-facts", json=_req())
        assert resp.status_code == 503
        assert resp.json() == {"error": "judge_unavailable", "reason": "judges_warming", "retry_after_s": 30}
        assert resp.headers["Retry-After"] == "30"

    def test_status_reports_warming_without_probing(self, tmp_path: Path) -> None:
        now = [_utc(2026, 10, 2, 9, 0)]
        c = _warming_client(tmp_path, now, _dead_primary_script())
        c.post("/api/v1/analyse-facts", json=_req())
        now[0] = _utc(2026, 10, 2, 9, 1)
        assert c.get("/api/v1/status").json()["judge"]["state"] == "warming"

    def test_still_failing_after_the_grace_is_a_plain_judge_unavailable(self, tmp_path: Path) -> None:
        now = [_utc(2026, 10, 2, 9, 0)]
        c = _warming_client(tmp_path, now, _dead_primary_script())
        c.post("/api/v1/analyse-facts", json=_req())
        now[0] = _utc(2026, 10, 2, 9, 5)
        resp = c.post("/api/v1/analyse-facts", json=_req())
        assert resp.status_code == 503
        assert resp.json() == {"error": "judge_unavailable"}
        assert "Retry-After" not in resp.headers
        assert c.get("/api/v1/status").json()["judge"]["state"] == "unavailable"

    def test_failure_right_after_a_ready_reading_is_a_fault_not_warming(self, tmp_path: Path) -> None:
        now = [_utc(2026, 10, 2, 9, 0)]
        script = _proof_script()
        c = _warming_client(tmp_path, now, script)
        assert c.post("/api/v1/analyse-facts", json=_req()).status_code == 200
        script[BNS69_EL[0]] = [ConnectionError("died")]
        now[0] = _utc(2026, 10, 2, 9, 1)
        resp = c.post("/api/v1/analyse-facts", json=_req())
        assert resp.status_code == 503
        assert resp.json() == {"error": "judge_unavailable"}

    def test_grace_zero_keeps_the_old_body(self, tmp_path: Path) -> None:
        now = [_utc(2026, 10, 2, 9, 0)]
        c = _warming_client(tmp_path, now, _dead_primary_script(), grace=0.0)
        assert c.post("/api/v1/analyse-facts", json=_req()).json() == {"error": "judge_unavailable"}

    def test_warm_grace_is_config_not_code(self) -> None:
        cfg = load_partner_api_config(Path(__file__).resolve().parent.parent)
        assert cfg.judge_warm_grace_s > 0 and cfg.judge_warm_retry_s > 0


def test_contracts_carry_citations_resolved_against_the_corpus(tmp_path: Path) -> None:
    """#142: the contract's own source column, checked against the shipped corpus; same whatever the verdict."""
    proof = _client(tmp_path).post("/api/v1/analyse-facts", json=_req()).json()["contracts"][0]
    assert proof["citations"] == [
        {"act": "BNS", "section": "69", "corpus_id": "BNS/Section 69", "in_corpus": True, "title": proof["citations"][0]["title"]}
    ]
    assert proof["citations"][0]["title"]
    no_proof = {BNS69_EL[0]: [_not()], BNS69_EL[1]: [_not()], BNS69_DENY: [_not()]}
    refer = _client(tmp_path, no_proof).post("/api/v1/analyse-facts", json=_req()).json()["contracts"][0]
    assert refer["outcome"] != proof["outcome"]
    assert refer["citations"] == proof["citations"]


def test_a_source_absent_from_the_corpus_is_reported_not_dropped(tmp_path: Path) -> None:
    agent = _agent(tmp_path, _proof_script())
    agent.registry.sources["bns69"] = ["Bharatiya Nyaya Sanhita §9999"]
    app = FastAPI()
    app.include_router(build_partner_router(tmp_path, agent_factory=lambda _root: agent, config=_NO_LIMIT_CONFIG))
    c = TestClient(app).post("/api/v1/analyse-facts", json=_req()).json()["contracts"][0]
    assert c["citations"] == [{"act": "BNS", "section": "9999", "corpus_id": None, "in_corpus": False, "title": None}]


def _citations_client(tmp_path: Path, agent: Any) -> TestClient:
    app = FastAPI()
    app.include_router(build_partner_router(tmp_path, agent_factory=lambda _root: agent, config=_NO_LIMIT_CONFIG))
    return TestClient(app)


def test_citations_reuse_the_sources_the_run_already_read_one_registry_call_per_request(tmp_path: Path) -> None:
    agent = _agent(tmp_path, _proof_script())
    real = agent.registry.list_contracts
    calls: list[int] = []

    def counting() -> dict[str, list[str]]:
        calls.append(1)
        return real()

    agent.registry.list_contracts = counting  # type: ignore[method-assign]
    r = _citations_client(tmp_path, agent).post("/api/v1/analyse-facts", json=_req())
    assert r.status_code == 200 and r.json()["contracts"][0]["citations"][0]["in_corpus"] is True
    assert calls == [1]
    assert "listed_sources" not in r.json()


@pytest.mark.parametrize(
    "error",
    [
        subprocess.CalledProcessError(1, ["score", "--list-contracts"]),
        subprocess.TimeoutExpired(["score", "--list-contracts"], 30),
        RuntimeError("binary gone"),
    ],
    ids=["CalledProcessError", "TimeoutExpired", "RuntimeError"],
)
def test_a_failing_sources_read_gives_null_citations_and_a_200_never_a_500(tmp_path: Path, error: Exception) -> None:
    """The fallback read (a run that carries no sources) fails: an already-completed analysis is still returned."""
    agent = _agent(tmp_path, _proof_script())
    real_run, real_list = agent.run, agent.registry.list_contracts
    calls: list[int] = []

    def run_without_sources(*a: Any, **k: Any) -> Any:
        out = real_run(*a, **k)
        out.listed_sources = None
        return out

    def flaky() -> dict[str, list[str]]:
        calls.append(1)
        if len(calls) > 1:  # the run's own selection read succeeds; the fallback read in the router fails
            raise error
        return real_list()

    agent.run = run_without_sources  # type: ignore[method-assign]
    agent.registry.list_contracts = flaky  # type: ignore[method-assign]
    r = _citations_client(tmp_path, agent).post("/api/v1/analyse-facts", json=_req())
    assert r.status_code == 200, r.text
    assert r.json()["contracts"][0]["citations"] is None and r.json()["contracts"][0]["outcome"]


class TestProceedingPosture:
    """#204: optional `proceeding_posture` on analyse-facts; 422 on an unknown value; no effect under legacy."""

    @staticmethod
    def _client_with_judge(tmp_path: Path) -> tuple[TestClient, Any]:
        agent = _agent(tmp_path, _proof_script())
        app = FastAPI()
        app.include_router(build_partner_router(tmp_path, agent_factory=lambda _root: agent, config=_NO_LIMIT_CONFIG))
        return TestClient(app), agent.judge

    @pytest.mark.parametrize("posture", ["quash", "discharge", "trial", "appeal"])
    def test_a_valid_posture_reaches_every_judge_request(self, tmp_path: Path, posture: str) -> None:
        c, judge = self._client_with_judge(tmp_path)
        resp = c.post("/api/v1/analyse-facts", json=_req(proceeding_posture=posture))
        assert resp.status_code == 200
        assert judge.requests and {r.proceeding_posture for r in judge.requests} == {posture}

    def test_an_absent_posture_stays_unset_for_the_engine_default(self, tmp_path: Path) -> None:
        c, judge = self._client_with_judge(tmp_path)
        assert c.post("/api/v1/analyse-facts", json=_req()).status_code == 200
        assert {r.proceeding_posture for r in judge.requests} == {None}

    @pytest.mark.parametrize("bad", ["bail", "", "TRIAL", 3, ["trial"]])
    def test_an_invalid_posture_is_a_422_before_any_judge_call(self, tmp_path: Path, bad: object) -> None:
        c, judge = self._client_with_judge(tmp_path)
        assert c.post("/api/v1/analyse-facts", json=_req(proceeding_posture=bad)).status_code == 422
        assert judge.requests == []

    def test_under_legacy_the_posture_changes_nothing_but_the_standard_object(self, tmp_path: Path) -> None:
        def body(posture: str | None) -> dict[str, Any]:
            c, _ = self._client_with_judge(tmp_path / str(posture))
            out = c.post("/api/v1/analyse-facts", json=_req() if posture is None else _req(proceeding_posture=posture)).json()
            out.pop("standard", None)
            for k in ("run_id", "audit_run_id", "audit_path", "wall_ms"):
                out.pop(k, None)
            return out  # type: ignore[no-any-return]

        assert body("quash") == body(None) == body("trial")

    def test_openapi_documents_the_enum_as_optional(self, tmp_path: Path) -> None:
        c, _ = self._client_with_judge(tmp_path)
        schema = c.get("/openapi.json").json()["components"]["schemas"]["AnalyseFactsRequest"]
        prop = schema["properties"]["proceeding_posture"]
        assert "proceeding_posture" not in schema.get("required", [])
        enums = [b["enum"] for b in prop.get("anyOf", [prop]) if "enum" in b]
        assert enums == [["quash", "discharge", "trial", "appeal"]]
        assert "stricter default" in prop["description"]


class _HouseTemplateJudge(ScriptedJudge):
    prompt_template = "house"


class TestStandardInResponse:
    """#220: response.standard is the resolved standard, equal to the run_start audit row's fields."""

    @staticmethod
    def _run(tmp_path: Path, posture: str | None, *, template: bool = True) -> tuple[dict[str, Any], dict[str, Any]]:
        agent = _agent(tmp_path, _proof_script())
        if template:
            agent.judge = _HouseTemplateJudge(_proof_script())
        app = FastAPI()
        app.include_router(build_partner_router(tmp_path, agent_factory=lambda _root: agent, config=_NO_LIMIT_CONFIG))
        body = TestClient(app).post(
            "/api/v1/analyse-facts", json=_req() if posture is None else _req(proceeding_posture=posture)
        ).json()
        import json as _json

        rows = [_json.loads(x) for x in (tmp_path / "audit" / f"{body['run_id']}.jsonl").read_text().splitlines()]
        start = next(r for r in rows if r["step"] == "run_start")
        return body, start.get("output", start)

    @pytest.mark.parametrize("posture", ["quash", "discharge", "trial", "appeal"])
    def test_standard_equals_the_audit_row(self, tmp_path: Path, posture: str) -> None:
        body, row = self._run(tmp_path, posture)
        std = body["standard"]
        assert std["requested"] == std["applied"] == row["standard"]
        assert std["in_judge_prompt"] is row["standard_in_judge_prompt"] is True
        assert std["proceeding_posture"] == row["proceeding_posture"] == posture
        assert (std["source"], row["standard_source"]) == ("proceeding_posture", "request")

    def test_absent_posture_is_proved_by_default(self, tmp_path: Path) -> None:
        body, row = self._run(tmp_path, None)
        assert body["standard"] == {
            "requested": "proved", "applied": "proved", "source": "default", "proceeding_posture": None,
            "in_judge_prompt": True,
        }
        assert row["standard"] == "proved" and row["standard_source"] == "default_proved"

    def test_legacy_template_records_the_basis_but_says_the_judge_never_saw_it(self, tmp_path: Path) -> None:
        body, row = self._run(tmp_path, "quash", template=False)
        assert body["standard"] == {
            "requested": "prima_facie_disclosed", "applied": None, "source": "proceeding_posture",
            "proceeding_posture": "quash", "in_judge_prompt": False,
        }
        assert row["standard"] == "prima_facie_disclosed" and row["standard_in_judge_prompt"] is False

    def test_openapi_documents_standard_as_optional_object(self, tmp_path: Path) -> None:
        c = _client(tmp_path)
        schemas = c.get("/openapi.json").json()["components"]["schemas"]
        resp = schemas["AnalyseFactsResponse"]
        assert "standard" not in resp.get("required", [])
        assert "StandardOut" in str(resp["properties"]["standard"])
        out = schemas["StandardOut"]
        assert set(out["properties"]) == {"requested", "applied", "source", "proceeding_posture", "in_judge_prompt"}
        assert set(out["required"]) == {"requested", "applied", "source", "in_judge_prompt"}
        assert {"type": "null"} in out["properties"]["applied"]["anyOf"]


def test_rule_text_fields_are_absent_unless_expose_rule_text_is_on(tmp_path: Path) -> None:
    """Licence hold (#308/#506): the shipped default withholds the provision text; the flag turns it on."""
    import dataclasses

    names = ("rule_text", "judge_rule_text", "rule_text_source")
    assert AgentConfig(tau=0.74, refer_band=(0.5, 0.74), max_retries=2, audit_dir=tmp_path).expose_rule_text is False
    off = _client(tmp_path).post("/api/v1/analyse-facts", json=_req()).json()["contracts"][0]
    assert not any(n in off for n in names)
    agent = _agent(tmp_path, _proof_script())
    agent.config = dataclasses.replace(agent.config, expose_rule_text=True)
    app = FastAPI()
    app.include_router(build_partner_router(tmp_path, agent_factory=lambda _root: agent, config=_NO_LIMIT_CONFIG))
    on = TestClient(app).post("/api/v1/analyse-facts", json=_req()).json()["contracts"][0]
    assert on["rule_text"] and on["rule_text_source"] == "lean_describe_source" and "judge_rule_text" in on


def test_the_shipped_config_exposes_rule_text_for_analysis_responses() -> None:
    """#506 decided (6 Oct): provision text may appear inside an analysis response that carries our element analysis."""
    from pravrudhi.application.nyaya_agent import load_agent_config

    assert load_agent_config(Path(__file__).resolve().parent.parent).expose_rule_text is True


RULE_NOTICE = "Unofficial text; the official version prevails."


def _rule_text_client(tmp_path: Path, *, expose: bool, **kw: Any) -> TestClient:
    import dataclasses

    agent = _agent(tmp_path, _proof_script())
    agent.config = dataclasses.replace(agent.config, expose_rule_text=expose)
    app = FastAPI()
    app.include_router(build_partner_router(tmp_path, agent_factory=lambda _root: agent, config=_NO_LIMIT_CONFIG, **kw))
    return TestClient(app)


def test_displayed_provision_text_carries_the_notice_and_an_india_code_source_url_beside_the_analysis(tmp_path: Path) -> None:
    contract = _rule_text_client(tmp_path, expose=True).post("/api/v1/analyse-facts", json=_req()).json()["contracts"][0]
    assert contract["rule_text"] and contract["elements"], "the text only ever comes with our element analysis"
    assert contract["rule_text_notice"] == RULE_NOTICE
    assert contract["rule_text_source_url"].startswith(("https://www.indiacode.nic.in", "https://www.indiacode.gov.in"))


def test_the_notice_and_url_are_present_exactly_when_provision_text_is_displayed(tmp_path: Path) -> None:
    off = _rule_text_client(tmp_path, expose=False).post("/api/v1/analyse-facts", json=_req()).json()["contracts"][0]
    assert not any(k in off for k in ("rule_text", "judge_rule_text", "rule_text_notice", "rule_text_source_url"))


def test_a_refused_request_carries_no_provision_text(tmp_path: Path) -> None:
    c = _rule_text_client(tmp_path, expose=True)
    for body in (_req(facts=["   "]), _req(contract_ids=["no-such-contract"])):
        resp = c.post("/api/v1/analyse-facts", json=body)
        assert resp.status_code in (422, 400) and "rule_text" not in resp.text and RULE_NOTICE not in resp.text


def test_provision_text_in_an_async_job_result_carries_the_notice_and_url(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import dataclasses

    from pravrudhi.api import identity
    from tests.test_partner_jobs import JOBS, H, _inline
    from tests.test_partner_key_metering import ADMIN, FakeClock, _key
    from tests.test_partner_key_metering import _req as _meter_req

    monkeypatch.setenv("PRAVRUDHI_ADMINS", ADMIN.id)
    agent = _agent(tmp_path, _proof_script())
    agent.config = dataclasses.replace(agent.config, expose_rule_text=True)
    app = FastAPI()
    app.include_router(
        build_partner_router(
            tmp_path, agent_factory=lambda _r: agent, config=_NO_LIMIT_CONFIG, job_executor=_inline, rate_clock=FakeClock()
        )
    )
    app.dependency_overrides[identity.current_user] = lambda: ADMIN
    c = TestClient(app)
    secret = _key(c, "acme")
    jid = c.post(JOBS, json=_meter_req(), headers={H: secret}).json()["job_id"]
    result = c.get(f"{JOBS}/{jid}", headers={H: secret}).json()["result"]["contracts"][0]
    assert result["rule_text"] and result["rule_text_notice"] == RULE_NOTICE and result["rule_text_source_url"]
