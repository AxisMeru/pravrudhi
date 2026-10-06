"""pravrudhi#318: every 503 on these routes carries a stable code and a FIXED message; exception text never reaches a response,
a streamed event or a stored job (it is logged server-side only). A marker string is placed in each raised exception and must
appear nowhere in what the caller can read."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pravrudhi.api import identity
from pravrudhi.api import nyaya as nyaya_api
from pravrudhi.api.chat import build_chat_router
from pravrudhi.api.nyaya import build_nyaya_router
from pravrudhi.api.partner import PartnerApiConfig, build_partner_router
from pravrudhi.application.chat import ChatEndpointUnreachable
from pravrudhi.application.nyaya_agent import BinaryShaMismatch, JudgeMisconfigured
from pravrudhi.application.nyaya_lean_registry import UnknownContractError
from tests.test_api_partner import _NO_LIMIT_CONFIG, _req
from tests.test_partner_jobs import JOBS, H
from tests.test_partner_key_metering import ADMIN, FakeClock, _key
from tests.test_partner_key_metering import _req as _meter_req

MARK = "SECRET-MARKER-9f3a /srv/internal/path http://10.1.2.3:8110"


def _partner_app(tmp_path: Path, factory: Callable[[Path], Any], executor: Any = None) -> FastAPI:
    app = FastAPI()
    kw: dict[str, Any] = {"job_executor": executor, "rate_clock": FakeClock()} if executor else {}
    cfg = (
        _NO_LIMIT_CONFIG
        if not executor
        else PartnerApiConfig(rate_limit_per_minute=1000, max_concurrent=100, trust_proxy_header=False)
    )
    app.include_router(build_partner_router(tmp_path, agent_factory=factory, config=cfg, **kw))
    if executor:
        app.dependency_overrides[identity.current_user] = lambda: ADMIN
    return app


def _raiser(exc: BaseException) -> Callable[[Path], Any]:
    def factory(_root: Path) -> Any:
        raise exc

    return factory


AGENT_FAULTS = [JudgeMisconfigured(MARK), BinaryShaMismatch(MARK), FileNotFoundError(MARK), OSError(MARK)]


@pytest.mark.parametrize("exc", AGENT_FAULTS, ids=lambda e: type(e).__name__)
def test_partner_sync_agent_fault_is_a_coded_503_with_no_exception_text(tmp_path: Path, exc: BaseException) -> None:
    resp = TestClient(_partner_app(tmp_path, _raiser(exc))).post("/api/v1/analyse-facts", json=_req())
    assert resp.status_code == 503
    assert resp.json()["error"] == "agent_unavailable" and "MARKER" not in resp.text and "10.1.2.3" not in resp.text
    assert resp.json()["detail"] == "the nyaya agent is unavailable; retry later"  # fixed


def test_partner_capacity_is_a_coded_503(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from pravrudhi.api import partner

    class _Full:
        def __init__(self, _n: int) -> None: ...
        def acquire(self) -> bool:
            return False

        def release(self) -> None:
            raise AssertionError("released a slot that was never acquired")

    monkeypatch.setattr(partner, "ConcurrencyLimiter", _Full)
    resp = TestClient(_partner_app(tmp_path, _raiser(RuntimeError(MARK)))).post("/api/v1/analyse-facts", json=_req())
    assert resp.status_code == 503 and resp.json()["error"] == "agent_at_capacity"
    assert resp.json()["detail"] == "the nyaya agent is at capacity; retry shortly"


@pytest.mark.parametrize("exc", AGENT_FAULTS, ids=lambda e: type(e).__name__)
def test_partner_job_stores_a_coded_failure_with_no_exception_text(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, exc: BaseException
) -> None:
    monkeypatch.setenv("PRAVRUDHI_ADMINS", ADMIN.id)
    c = TestClient(_partner_app(tmp_path, _raiser(exc), executor=lambda task: task()))
    secret = _key(c, "acme")
    r = c.post(JOBS, json=_meter_req(), headers={H: secret})
    assert r.status_code == 202
    polled = c.get(f"{JOBS}/{r.json()['job_id']}", headers={H: secret})
    assert "MARKER" not in polled.text and "10.1.2.3" not in polled.text
    body = polled.json()
    assert body["status"] == "failed" and json.dumps(body).count("agent_unavailable") >= 1


def test_partner_log_keeps_the_exception_server_side(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    with caplog.at_level("WARNING"):
        TestClient(_partner_app(tmp_path, _raiser(JudgeMisconfigured(MARK)))).post("/api/v1/analyse-facts", json=_req())
    assert any("agent unavailable" in r.getMessage().lower() or r.exc_info for r in caplog.records)


def _nyaya_app(tmp_path: Path) -> TestClient:
    from pravrudhi.application.init import init_project

    init_project(tmp_path)
    app = FastAPI()
    app.include_router(build_nyaya_router(tmp_path))
    return TestClient(app, headers={"host": "127.0.0.1:8008"})


def test_nyaya_audit_and_registry_checker_faults_are_coded_with_no_exception_text(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError(MARK)

    monkeypatch.setattr(nyaya_api.nyaya, "audit", boom)
    monkeypatch.setattr(nyaya_api.nyaya, "registry_elements", boom)
    monkeypatch.setattr(nyaya_api.nyaya, "registry_check", boom)
    c = _nyaya_app(tmp_path)
    cases = [
        (c.post("/api/nyaya/audit", json={"sources": "s", "answer": "a", "checker": "claude-cli"}), "checker_unavailable"),
        (c.get("/api/nyaya/registry/bns69/elements"), "registry_checker_unavailable"),
        (
            c.post("/api/nyaya/registry/check", json={"contract_id": "bns69", "assertions": {"e": True}}),
            "registry_checker_unavailable",
        ),
    ]
    for resp, code in cases:
        assert resp.status_code == 503, resp.text
        assert resp.json()["error"] == code and "MARKER" not in resp.text and "10.1.2.3" not in resp.text


def test_chat_unreachable_endpoint_is_coded_for_the_blocking_route_and_the_stream_event(tmp_path: Path) -> None:
    def model(*_a: Any, **_k: Any) -> Any:
        raise ChatEndpointUnreachable(MARK)

    app = FastAPI()
    app.include_router(build_chat_router(tmp_path, complete=model))
    c = TestClient(app)
    blocking = c.post("/api/chat", json={"message": "hello there", "thread_id": None})
    assert blocking.status_code == 503 and blocking.json()["error"] == "chat_endpoint_unreachable"
    assert "MARKER" not in blocking.text and "10.1.2.3" not in blocking.text
    stream = c.post("/api/chat/stream", json={"message": "hello there", "thread_id": None})
    events = [json.loads(line[6:]) for line in stream.text.splitlines() if line.startswith("data: ")]
    assert events[-1]["type"] == "error" and events[-1]["error"] == "chat_endpoint_unreachable"
    assert "MARKER" not in stream.text and "10.1.2.3" not in stream.text


def test_vendor_not_allowed_is_a_coded_403_with_a_fixed_message_that_does_not_echo_the_vendor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from pravrudhi.application.tenant_vendors import VendorNotAllowed

    def deny(*_a: Any, **_k: Any) -> Any:
        raise VendorNotAllowed(f"vendor not allowed for API callers: {MARK}")

    monkeypatch.setattr(nyaya_api.nyaya, "ask", deny)
    resp = _nyaya_app(tmp_path).post("/api/nyaya/ask", json={"question": "what is s.69?", "vendors": ["x"]})
    assert resp.status_code == 403 and resp.json()["error"] == "vendor_not_allowed"
    assert "MARKER" not in resp.text and "10.1.2.3" not in resp.text


class _RaisingAgent:
    def __init__(self, exc: BaseException) -> None:
        self.exc = exc

    def run(self, *_a: Any, **_k: Any) -> Any:
        raise self.exc


@pytest.mark.parametrize("exc,code", [(ValueError(MARK), "request_rejected"), (UnknownContractError(MARK), "unknown_contract")])
def test_partner_agent_rejections_are_coded_422s_with_no_exception_text_sync_and_in_the_stored_job(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, exc: BaseException, code: str
) -> None:
    sync = TestClient(_partner_app(tmp_path, lambda _r: _RaisingAgent(exc))).post("/api/v1/analyse-facts", json=_req())
    assert sync.status_code == 422 and sync.json()["error"] == code
    assert "MARKER" not in sync.text and "10.1.2.3" not in sync.text
    monkeypatch.setenv("PRAVRUDHI_ADMINS", ADMIN.id)
    c = TestClient(_partner_app(tmp_path, lambda _r: _RaisingAgent(exc), executor=lambda task: task()))
    secret = _key(c, "acme")
    jid = c.post(JOBS, json=_meter_req(), headers={H: secret}).json()["job_id"]
    polled = c.get(f"{JOBS}/{jid}", headers={H: secret})
    assert "MARKER" not in polled.text and "10.1.2.3" not in polled.text and code in polled.text


def test_nyaya_422s_are_coded_with_no_exception_text(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def boom_value(*_a: Any, **_k: Any) -> Any:
        raise ValueError(MARK)

    def boom_key(*_a: Any, **_k: Any) -> Any:
        raise KeyError(MARK)

    c = _nyaya_app(tmp_path)
    for fn, exc in (("ask", boom_value), ("audit", boom_key), ("registry_elements", boom_value), ("registry_check", boom_key)):
        monkeypatch.setattr(nyaya_api.nyaya, fn, exc)
    cases = [
        c.post("/api/nyaya/ask", json={"question": "what is s.69?", "vendors": ["x"]}),
        c.post("/api/nyaya/audit", json={"sources": "s", "answer": "a", "checker": "claude-cli"}),
        c.get("/api/nyaya/registry/bns69/elements"),
        c.post("/api/nyaya/registry/check", json={"contract_id": "bns69", "assertions": {"e": True}}),
    ]
    for resp in cases:
        assert resp.status_code == 422 and resp.json()["error"] == "request_rejected", resp.text
        assert "MARKER" not in resp.text and "10.1.2.3" not in resp.text


def test_nyaya_workspace_resolution_error_has_a_fixed_400_message(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from pravrudhi.api.workspace_root import RootError

    def bad_root(*_a: Any, **_k: Any) -> Any:
        raise RootError(MARK)

    monkeypatch.setattr(nyaya_api, "root_for", bad_root)
    resp = _nyaya_app(tmp_path).get("/api/nyaya/vendors")
    assert resp.status_code == 400 and "MARKER" not in resp.text and "10.1.2.3" not in resp.text


def test_chat_400_and_404_have_fixed_messages_with_no_exception_text(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from pravrudhi.api import chat as chat_api
    from pravrudhi.application.memory import MemoryError as MemoryStoreError
    from pravrudhi.application.memory_store import MemoryAccessError

    app = FastAPI()
    app.include_router(build_chat_router(tmp_path, complete=lambda *_a, **_k: None))
    c = TestClient(app)

    def refuse(*_a: Any, **_k: Any) -> Any:
        raise MemoryAccessError(MARK)

    monkeypatch.setattr(chat_api, "store_for", refuse)
    r400 = c.get("/api/chat/threads")
    assert r400.status_code == 400 and "MARKER" not in r400.text and "10.1.2.3" not in r400.text

    class _Store:
        def thread(self, _tid: str) -> Any:
            raise MemoryStoreError(MARK)

    monkeypatch.setattr(chat_api, "store_for", lambda *_a, **_k: _Store())
    r404 = c.get("/api/chat/threads/abc")
    assert r404.status_code == 404 and "MARKER" not in r404.text and "10.1.2.3" not in r404.text
