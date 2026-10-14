"""The Anthropic transport: allowlist, headers, recording, pagination, no retries, no secrets in errors.
httpx.MockTransport only: no network."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from pravrudhi.application.a0_assist import transport as TR

KEY, WS = "sk-ant-test-SECRET-123", "wrkspc_abc"


def make(handler, record=None, **kw):
    seen = []

    def wrapped(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return handler(req)

    client = httpx.Client(transport=httpx.MockTransport(wrapped))
    return TR.AnthropicTransport(KEY, WS, client=client, record=record, **kw), seen


def ok(body=None, status=200):
    return lambda req: httpx.Response(status, json=body if body is not None else {})


# ---- construction ----------------------------------------
@pytest.mark.parametrize("key,ws", [("", WS), (None, WS), (KEY, ""), (KEY, None), ("  ", WS), (KEY, "  ")])
def test_a_client_without_key_or_workspace_cannot_be_built(key, ws):
    with pytest.raises(TR.TransportRefused):
        TR.AnthropicTransport(key, ws)


@pytest.mark.parametrize("t", [0, -1, 601, True, "60", None])
def test_timeout_must_be_in_range(t):
    with pytest.raises(TR.TransportRefused):
        TR.AnthropicTransport(KEY, WS, timeout_s=t)


def test_repr_never_shows_the_key():
    t, _ = make(ok())
    assert KEY not in repr(t) and KEY not in str(t)


# ---- allowlist ----------------------------------------
@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/v1/files"),
        ("POST", "/v1/sessions/abc/archive"),
        ("GET", "/v1/agents/agent_x"),
        ("DELETE", "/v1/sessions"),
        ("POST", "/v1/messages"),
        ("GET", "/v1/sessions/../agents"),
        ("GET", "/v1/sessions/a b"),
        ("DELETE", "/v1/sessions/a/b"),
        ("POST", "/v1/vaults"),
        ("POST", "/v1/memory_stores"),
        ("GET", "/v1/sessions?agent_id=x"),
        ("PUT", "/v1/sessions/abc"),
        ("GET", "/v1/sessions/" + "a" * 65),
        ("POST", "/v1/sessions/abc/events/stream"),
    ],
)
def test_calls_outside_the_allowlist_are_refused_before_any_request(method, path):
    t, seen = make(ok())
    with pytest.raises(TR.TransportRefused):
        t._send(method, path)
    assert seen == []


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/v1/sessions"),
        ("GET", "/v1/sessions"),
        ("GET", "/v1/sessions/sesn_1"),
        ("DELETE", "/v1/sessions/sesn_1"),
        ("POST", "/v1/sessions/sesn_1/events"),
        ("GET", "/v1/sessions/sesn_1/events"),
        ("GET", "/v1/sessions/sesn_1/events/stream"),
    ],
)
def test_the_allowlisted_calls_are_sent(method, path):
    t, seen = make(ok())
    t._send(method, path)
    assert len(seen) == 1 and seen[0].method == method


def test_the_public_methods_use_only_allowlisted_paths_and_never_upload():
    t, seen = make(lambda r: httpx.Response(200, json={"id": "sesn_1", "data": [], "has_more": False}))
    t.create_session("agent_x", 1, "env_x", 25)
    t.send_events("sesn_1", [{"type": "user.message"}])
    t.interrupt("sesn_1")
    t.get_session("sesn_1")
    t.list_events("sesn_1")
    t.delete_session("sesn_1")
    list(t.list_sessions("agent_x"))
    assert all(any(m == r.method and rx.fullmatch(r.url.path) for m, rx in TR.ALLOWED) for r in seen) and not any(
        "files" in r.url.path or "vault" in r.url.path or "memory" in r.url.path for r in seen
    )


# ---- headers ----------------------------------------
def test_every_call_carries_key_version_beta_and_the_workspace_header():
    t, seen = make(ok({"id": "s", "data": [], "has_more": False}))
    t.create_session("a", 1, "e", 10)
    t.get_session("s")
    t.delete_session("s")
    t.list_events("s")
    list(t.list_sessions("a"))
    for r in seen:
        assert (
            r.headers["x-api-key"] == KEY
            and r.headers["anthropic-workspace-id"] == WS
            and r.headers["anthropic-beta"] == TR.BETA
            and r.headers["anthropic-version"] == "2023-06-01"
        )


# ---- recording ----------------------------------------
@pytest.mark.parametrize("status", [200, 201, 204, 404, 409, 429, 500, 503])
def test_record_gets_the_http_status_of_every_call(status):
    got = []
    t, _ = make(ok(status=status), record=got.append)
    try:
        t._send("GET", "/v1/sessions/sesn_1")
    finally:
        pass
    assert got == [status]


def test_record_gets_599_on_a_timeout_and_a_connection_error():
    for exc in (httpx.ReadTimeout("slow"), httpx.ConnectError("down")):
        got = []

        def boom(req, exc=exc):
            raise exc

        t, _ = make(boom, record=got.append)
        with pytest.raises(TR.UpstreamError) as e:
            t.get_session("sesn_1")
        assert got == [599] and e.value.status == 599


def test_a_failing_recorder_does_not_break_the_call():
    def bad(status):
        raise RuntimeError("recorder down")

    t, _ = make(ok({"id": "x"}), record=bad)
    assert t.get_session("sesn_1") == {"id": "x"}


# ---- calls ----------------------------------------
def test_create_session_body_pins_the_agent_version_and_sets_the_budget_in_cents():
    t, seen = make(ok({"id": "sesn_1"}))
    assert t.create_session("agent_x", 3, "env_x", 25) == {"id": "sesn_1"}
    assert json.loads(seen[0].content) == {
        "agent": {"type": "agent", "id": "agent_x", "version": 3},
        "environment_id": "env_x",
        "budget": {"type": "limit", "max_list_cost": {"amount": "25", "currency": "USD"}},
    }


@pytest.mark.parametrize(
    "args",
    [
        ("a", 1, "e", 0),
        ("a", 1, "e", 101),
        ("a", 1, "e", True),
        ("a", 1, "e", 2.5),
        ("a", 0, "e", 5),
        ("a", True, "e", 5),
        ("", 1, "e", 5),
        ("a", 1, "", 5),
    ],
)
def test_create_session_refuses_bad_arguments_without_a_request(args):
    t, seen = make(ok())
    with pytest.raises(TR.TransportRefused):
        t.create_session(*args)
    assert seen == []


def test_send_events_and_interrupt_bodies():
    t, seen = make(ok())
    t.send_events("sesn_1", [{"type": "user.message", "content": [{"type": "text", "text": "x"}]}])
    t.interrupt("sesn_1")
    assert json.loads(seen[0].content)["events"][0]["type"] == "user.message" and json.loads(seen[1].content) == {
        "events": [{"type": "user.interrupt"}]
    }


def test_list_events_walks_every_page():
    pages = {None: {"data": [{"id": "e1"}, {"id": "e2"}], "has_more": True}, "e2": {"data": [{"id": "e3"}], "has_more": False}}
    t, seen = make(lambda r: httpx.Response(200, json=pages[r.url.params.get("after_id")]))
    assert [e["id"] for e in t.list_events("sesn_1")] == ["e1", "e2", "e3"] and len(seen) == 2


def test_list_sessions_walks_every_page_with_the_agent_filter():
    pages = {
        None: {"data": [{"id": "s1"}, {"id": "s2"}], "next_page": "c2"},
        "c2": {"data": [{"id": "s3"}], "next_page": "c3"},
        "c3": {"data": [{"id": "s4"}], "next_page": None},
    }
    t, seen = make(lambda r: httpx.Response(200, json=pages[r.url.params.get("page")]))
    assert [s["id"] for s in t.list_sessions("agent_x", limit=2)] == ["s1", "s2", "s3", "s4"]
    assert all(r.url.params["agent_id"] == "agent_x" and r.url.params["limit"] == "2" for r in seen) and len(seen) == 3


def test_list_sessions_refuses_a_missing_agent_filter_and_a_bad_limit():
    t, seen = make(ok())
    for args in (("", {}), ("a", {"limit": 0}), ("a", {"limit": 1001}), ("a", {"limit": True})):
        with pytest.raises(TR.TransportRefused):
            list(t.list_sessions(args[0], **args[1]))
    assert seen == []


def test_list_sessions_stops_on_an_endless_cursor():
    t, _ = make(lambda r: httpx.Response(200, json={"data": [], "next_page": "again"}))
    with pytest.raises(TR.UpstreamError):
        list(t.list_sessions("agent_x"))


@pytest.mark.parametrize("status", [200, 204, 404, 409, 500])
def test_delete_session_returns_the_status_and_does_not_raise_on_http_errors(status):
    t, _ = make(lambda r: httpx.Response(status))
    assert t.delete_session("sesn_1") == status


def test_delete_session_raises_only_on_a_transport_failure():
    def boom(req):
        raise httpx.ConnectError("down")

    t, _ = make(boom)
    with pytest.raises(TR.UpstreamError):
        t.delete_session("sesn_1")


# ---- errors, retries, secrets ----------------------------------------
@pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 500, 529])
def test_json_calls_raise_on_http_errors_with_the_status(status):
    t, _ = make(lambda r: httpx.Response(status, text="boom"))
    with pytest.raises(TR.UpstreamError) as e:
        t.get_session("sesn_1")
    assert e.value.status == status


def test_there_are_no_retries_a_500_is_sent_once():
    t, seen = make(lambda r: httpx.Response(500, text="x"))
    with pytest.raises(TR.UpstreamError):
        t.create_session("a", 1, "e", 5)
    assert len(seen) == 1


SENTINEL = "SENTINEL-client-fact-7f3a91"


def test_an_error_body_that_echoes_the_key_or_client_text_never_reaches_the_exception():
    body = json.dumps({"error": {"type": "invalid_request_error", "message": f"bad key {KEY} and {SENTINEL}"}})
    t, _ = make(lambda r: httpx.Response(400, text=body))
    with pytest.raises(TR.UpstreamError) as e:
        t.get_session("sesn_1")
    msg = str(e.value)
    assert KEY not in msg and SENTINEL not in msg and msg == "Anthropic API 400: invalid_request_error"


@pytest.mark.parametrize(
    "body", [f"plain text {SENTINEL}", json.dumps({"error": {"type": f"bad type {SENTINEL}"}}), "[1, 2]", ""]
)
def test_a_body_without_a_clean_error_type_gives_the_status_alone(body):
    t, _ = make(lambda r: httpx.Response(500, text=body))
    with pytest.raises(TR.UpstreamError) as e:
        t.get_session("sesn_1")
    assert str(e.value) == "Anthropic API 500" and SENTINEL not in str(e.value)


def test_the_stream_error_branch_keeps_status_and_type_only():
    body = json.dumps({"error": {"type": "overloaded_error", "message": SENTINEL}})
    t, _ = make(lambda r: httpx.Response(529, text=body))
    with pytest.raises(TR.UpstreamError) as e:
        t.open_stream("sesn_1")
    assert str(e.value) == "Anthropic API 529: overloaded_error" and SENTINEL not in str(e.value)


def test_nothing_in_the_error_path_logs(caplog):
    t, _ = make(lambda r: httpx.Response(400, text=SENTINEL))
    with caplog.at_level("DEBUG"), pytest.raises(TR.UpstreamError):
        t.get_session("sesn_1")
    assert SENTINEL not in caplog.text


def test_a_non_json_answer_is_an_upstream_error():
    t, _ = make(lambda r: httpx.Response(200, content=b"<html>"))
    with pytest.raises(TR.UpstreamError):
        t.get_session("sesn_1")


# ---- the stream ----------------------------------------
def test_open_stream_opens_the_connection_before_it_returns_and_parses_sse():
    sse = b'data: {"id": "e1", "type": "session.status_running"}\n\ndata: {"id": "e2",\ndata: "type": "session.status_idle"}\n\n'
    t, seen = make(lambda r: httpx.Response(200, content=sse, headers={"content-type": "text/event-stream"}))
    s = t.open_stream("sesn_1")
    assert len(seen) == 1  # the request was made BEFORE iteration
    assert [e["id"] for e in s] == ["e1", "e2"]


def test_open_stream_refuses_a_non_200_and_records_it():
    got = []
    t, _ = make(lambda r: httpx.Response(429, text="slow"), record=got.append)
    with pytest.raises(TR.UpstreamError) as e:
        t.open_stream("sesn_1")
    assert e.value.status == 429 and got == [429]


def test_module_has_no_file_or_upload_helpers_and_no_secret_logging():
    src = Path(TR.__file__).read_text()
    assert "/v1/files" not in src and "multipart" not in src and "logging" not in src and "print(" not in src


# ---- the workspace probe -------------------------------------------------------------------------------------------------
def test_workspace_ok_is_true_only_for_a_200_from_models_with_the_workspace_header():
    t, seen = make(lambda r: httpx.Response(200, json={"data": []}))
    assert t.workspace_ok() is True
    assert seen[0].method == "GET" and seen[0].url.path == "/v1/models" and seen[0].headers["anthropic-workspace-id"] == WS


@pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 500])
def test_workspace_ok_is_false_for_any_other_status_and_records_it(status):
    got = []
    t, _ = make(lambda r: httpx.Response(status, text="x"), record=got.append)
    assert t.workspace_ok() is False and got == [status]


def test_workspace_ok_is_false_on_a_transport_failure():
    def boom(r):
        raise httpx.ConnectTimeout("t")

    t, _ = make(boom)
    assert t.workspace_ok() is False
