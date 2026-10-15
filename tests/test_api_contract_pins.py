"""Pins on two contracts the app and the callers rely on, so a change to either is deliberate:

1. The error shape of every 429 and 5xx the API emits (source scan): a 5xx is a JSON body with a machine `error` code, a 429
   carries `detail` and a Retry-After header; a raw `HTTPException` 5xx (which the app cannot read by `body.error`) is allowed
   only for the two known cases below.
2. The fixed-window behaviour of `RateLimiter`: window = int(now // 60), `per_minute` calls per window, a full reset at the window
   edge (so up to 2 x per_minute calls can pass in the seconds around an edge), Retry-After = 60 - int(now % 60).
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

from pravrudhi.api.partner import RateLimiter

API = Path(__file__).resolve().parent.parent / "src" / "pravrudhi" / "api"

#: Raw `HTTPException(5xx, ...)` calls the scan allows: none. (The two GET routes /jobs/{id} and /audit used to answer
#: {"detail": "service_config_missing"}; they now answer {"error": ...} like their POST siblings.)
KNOWN_RAW_5XX: dict[tuple[str, int, object], int] = {}


def _calls(path: Path):
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            yield node


def _name(node: ast.Call) -> str:
    f = node.func
    return f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else "")


def _kw(node: ast.Call, name: str):
    return next((k.value for k in node.keywords if k.arg == name), None)


def _const(node):
    return node.value if isinstance(node, ast.Constant) else None


def _dict_keys(node) -> set[str]:
    return {k.value for k in node.keys if isinstance(k, ast.Constant)} if isinstance(node, ast.Dict) else set()


def test_every_literal_5xx_json_body_carries_a_machine_error_code() -> None:
    seen = 0
    for path in sorted(API.glob("*.py")):
        for call in _calls(path):
            if _name(call) != "JSONResponse":
                continue
            status = _const(_kw(call, "status_code"))
            if not isinstance(status, int) or status < 500:
                continue
            seen += 1
            assert "error" in _dict_keys(_kw(call, "content")), f"{path.name}:{call.lineno}: a {status} body without `error`"
    assert seen >= 6  # not vacuous: the literal 503s of partner.py are in view


def test_every_literal_429_body_carries_detail_and_a_retry_after_header() -> None:
    seen = 0
    for path in sorted(API.glob("*.py")):
        for call in _calls(path):
            name = _name(call)
            if name == "JSONResponse" and _const(_kw(call, "status_code")) == 429:
                seen += 1
                assert "detail" in _dict_keys(_kw(call, "content")), f"{path.name}:{call.lineno}: a 429 body without `detail`"
                headers = _kw(call, "headers")
                assert isinstance(headers, ast.Dict) and "Retry-After" in _dict_keys(headers), (
                    f"{path.name}:{call.lineno}: no Retry-After"
                )
            if name == "HTTPException" and _const(_kw(call, "status_code")) == 429:
                seen += 1
                headers = _kw(call, "headers")
                assert isinstance(headers, ast.Dict) and "Retry-After" in _dict_keys(headers), (
                    f"{path.name}:{call.lineno}: no Retry-After"
                )
    assert seen >= 4


def test_no_raw_http_exception_5xx_is_left() -> None:
    found: dict[tuple[str, int, object], int] = {}
    for path in sorted(API.glob("*.py")):
        for call in _calls(path):
            if _name(call) != "HTTPException" or not call.args:
                continue
            status = _const(call.args[0])
            if isinstance(status, int) and status >= 500:
                detail = _const(call.args[1]) if len(call.args) > 1 else None
                found[(path.name, status, detail)] = found.get((path.name, status, detail), 0) + 1
    assert found == KNOWN_RAW_5XX


def test_the_coded_errors_all_answer_error_and_detail() -> None:
    from pravrudhi.api import errors

    assert errors.AGENT_AT_CAPACITY in errors.MESSAGES
    for code in errors.MESSAGES:
        r = errors.coded_503(code)
        assert r.status_code == 503
        assert json.loads(bytes(r.body)) == {"error": code, "detail": errors.MESSAGES[code]}
        assert "retry-after" not in {k.lower() for k in r.headers}


# ---- RateLimiter fixed-window pin ----------------------------------------------------------------------------------------
def _limiter(per_minute: int = 6):
    clock = [0.0]
    return RateLimiter(per_minute=per_minute, now=lambda: clock[0]), clock


def test_the_window_is_the_wall_minute_and_per_minute_calls_pass_inside_it() -> None:
    lim, clock = _limiter(6)
    clock[0] = 12.0
    assert [lim.allow("k") for _ in range(8)] == [True] * 6 + [False] * 2
    clock[0] = 59.99  # still the same window: still refused
    assert lim.allow("k") is False


def test_the_count_resets_in_full_at_the_window_edge() -> None:
    lim, clock = _limiter(6)
    clock[0] = 59.9
    assert [lim.allow("k") for _ in range(7)] == [True] * 6 + [False]
    clock[0] = 60.0  # the next window: a fresh quota, whatever happened a moment ago
    assert [lim.allow("k") for _ in range(7)] == [True] * 6 + [False]


def test_a_burst_across_an_edge_can_pass_twice_the_quota() -> None:
    """The documented property of a fixed window (not a rolling one): 2 x per_minute calls inside one second around an edge."""
    lim, clock = _limiter(6)
    passed = 0
    for t in (59.5, 59.6, 59.7, 59.8, 59.9, 59.95, 60.0, 60.05, 60.1, 60.2, 60.3, 60.4):
        clock[0] = t
        passed += lim.allow("k")
    assert passed == 12


def test_retry_after_is_the_seconds_to_the_window_edge() -> None:
    lim, clock = _limiter(6)
    for now, want in ((0.0, 60), (0.9, 60), (1.0, 59), (30.5, 30), (59.0, 1), (59.99, 1), (60.0, 60), (125.0, 55)):
        clock[0] = now
        assert lim.retry_after_seconds() == want, now


def test_peek_counts_nothing_and_keys_are_independent() -> None:
    lim, clock = _limiter(2)
    clock[0] = 5.0
    assert lim.peek("a") and lim.peek("a") and lim.peek("a")  # reading never spends a call
    assert lim.allow("a") and lim.allow("a") and not lim.allow("a")
    assert lim.peek("a") is False
    assert lim.allow("b") is True  # another key has its own quota


# ---- cold start: the first verify call after idle answers, it does not time out ------------------------------------------------
def test_the_first_verify_call_with_a_cold_title_cache_answers_inside_the_deadline(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    """A new or long-idle worker has an empty title cache. The first lookup that needs the fuzzy scan builds it itself and
    answers 200 well inside the 5 s deadline (503 verify_timeout is what 0.6.13 gave after a pause)."""
    import time

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from pravrudhi.api.partner import PartnerApiConfig, build_partner_router
    from pravrudhi.application import verify
    from pravrudhi.application.case_index import CaseRecord, insert_case, open_index

    idx = tmp_path / "idx.sqlite3"
    conn = open_index(idx)
    for i in range(3000):
        insert_case(conn, CaseRecord(f"c{i}", f"Party{i} Alpha vs State Of Region{i}", "Supreme Court", 2011, "sc_pdf", "/x", ""))
    cite = "This Court in Aldrik Vennoway v. State of Zorbland (2011) 9 SCC 257 held that"
    insert_case(conn, CaseRecord("citer", "Citer v Other", "Supreme Court", 2011, "sc_pdf", "/y", cite))
    conn.commit()
    conn.close()
    monkeypatch.delenv("PRAVRUDHI_CITATION_INDEX", raising=False)
    verify._TITLE_ROWS.clear()  # a fresh worker
    app = FastAPI()
    cfg = PartnerApiConfig(rate_limit_per_minute=1000, max_concurrent=4, trust_proxy_header=False)
    app.include_router(build_partner_router(tmp_path, config=cfg, citation_index_path=idx))
    t0 = time.perf_counter()
    r = TestClient(app).post("/api/v1/verify-citations", json={"citation": "(2011) 9 SCC 257"})
    assert r.status_code == 200 and r.json()["result"] in ("IN_INDEX", "NOT_IN_INDEX", "CONFLICT")
    assert time.perf_counter() - t0 < 4.0
    assert len(verify._TITLE_ROWS) == 1  # the lookup built the cache (the fuzzy scan ran)


# ---- the two GET routes answer the coded error when the partner config file is missing ---------------------------------
def _keyed_client_without_config(tmp_path: Path):  # noqa: ANN202
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from pravrudhi.api.partner import build_partner_router
    from pravrudhi.application import tenancy

    tenancy.create_org(tmp_path, "acme", "Acme")
    secret = tenancy.create_key(tmp_path, "acme").secret
    assert not (tmp_path / "configs" / "partner_api.yaml").exists()
    app = FastAPI()
    app.include_router(build_partner_router(tmp_path))  # no config injected: the file is read, and it is missing
    return TestClient(app, raise_server_exceptions=False), secret


def test_get_audit_without_a_config_file_answers_the_coded_503(tmp_path: Path) -> None:
    c, secret = _keyed_client_without_config(tmp_path)
    r = c.get("/api/v1/audit", headers={"X-Pravrudhi-Api-Key": secret})
    assert r.status_code == 503 and r.json() == {"error": "service_config_missing"}


def test_get_job_without_a_config_file_answers_the_coded_503(tmp_path: Path) -> None:
    c, secret = _keyed_client_without_config(tmp_path)
    r = c.get("/api/v1/analyse-facts/jobs/j-nope", headers={"X-Pravrudhi-Api-Key": secret})
    assert r.status_code == 503 and r.json() == {"error": "service_config_missing"}
